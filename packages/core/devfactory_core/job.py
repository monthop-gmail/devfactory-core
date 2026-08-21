"""The in-memory job state machine.

Canonical spec: ``packages/core/state-machine.md`` — RFC-0001 as amended by
RFC-0007, RFC-0010 and RFC-0011, with the tenant model from RFC-0006.

Scope, per issue #2: in memory, no persistence, no policy engine, no API. What
this module owns is the lifecycle and the guards on it.

Which states may fail is settled by RFC-0010: FAILED from TASK_PLANNING,
IN_PROGRESS, AWAITING_APPROVAL, VALIDATING, and DEPLOYABLE only — the states
where work exists to fail — and refused before APPROVED, where the honest
outcomes are REJECTED, CANCELLED, or TIMED_OUT. See ``states.FAILABLE``.

Governance decisions are RFC-0002, added by issue #5. A job holds the decision it
is executing under (``approval``) rather than a boolean, because "approved" is
not a fact about a job — it is a record of who decided what, when, and why.

All three of RFC-0002's decisions are executable since RFC-0011, which settled
where ``REQUIRE_CHANGES`` sends a job: ``DRAFT``, straight from the gate. It
clears the approval exactly as ``REJECT`` does — being told to make changes is not
being told to proceed — and is told apart from a rejection by its *route*, since
it never passes through ``REJECTED``. See ``states.DECISION_TARGET``.

An approval can also say when it stops being one. ``approval/v1`` carries
``expires_at`` and states what it means — *"approval ที่หมดอายุแล้วใช้เดินงานไม่ได้
ต้องขอใหม่"* — so the engine refuses to move a job into execution under a lapsed
approval, and RFC-0007's 2026-08-19 amendment gives such a job somewhere honest to
land by putting ``APPROVED`` in ``states.TIMEOUTABLE`` (issue #17).

Asking again is :meth:`Job.supersede`, and since RFC-0007's Amendment 2 it works
from every terminal that settled without delivering — ``states.SUPERSEDABLE`` —
rather than from ``FAILED`` alone, so the second attempt at a job whose approval
ran out can say what it is a second attempt at (issue #21).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .decision import Decision, DecisionType, Subject, new_decision_id
from .errors import (
    CrossTenantDecision,
    DecisionStateMismatch,
    ExecutionBeforeApproval,
    ExpiredApproval,
    InvalidTransition,
    MissingApprovalContext,
    MissingAuthority,
    MissingPrincipal,
    MissingReason,
    SelfApproval,
    TerminalState,
    UnmappedDecision,
    WrongDecisionSubject,
    WrongResumeState,
)
from .events import Event, EventType, new_event_id, utc_now
from .identity import Principal, validate_id
from .states import (
    APPROVAL_PAUSABLE,
    DECISION_TARGET,
    POST_APPROVAL,
    SUPERSEDABLE,
    TERMINAL,
    JobState,
    decision_for_edge,
    reachable_from,
)

#: States whose entry requires reason metadata — RFC-0001 for FAILED, extended
#: by RFC-0007 to the two terminal states it added.
REASON_REQUIRED: frozenset[JobState] = frozenset(
    {JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT}
)


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One entry in the immutable transition history."""

    from_state: JobState
    to_state: JobState
    at: datetime
    reason: str | None = None
    principal: Principal | None = None
    event_id: str | None = None
    #: The decision that caused this transition, for the two states that have one.
    decision_id: str | None = None


class Job:
    """A governed unit of work moving through the lifecycle.

    Construction emits ``JOB_CREATED``; every accepted transition emits
    ``STATE_TRANSITION``; entering a decision state also emits
    ``GOVERNANCE_DECISION``; reaching COMPLETED also emits ``JOB_COMPLETED``;
    and settling in **any** terminal emits ``JOB_SETTLED`` last of all
    (RFC-0012).
    There is no way to change ``state`` without going through
    :meth:`transition`, which is what makes "no silent state change" hold rather
    than merely be documented — and no way to reach ``APPROVED`` without leaving
    a decision record, which is what makes "every APPROVE is auditable" hold.
    """

    def __init__(
        self,
        *,
        job_id: str,
        tenant_id: str,
        workspace_id: str,
        principal: Principal,
        supersedes_job_id: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        # RFC-0006: tenant and workspace are required on every job. A
        # single-tenant deployment passes the literal "default"; it never omits
        # the field, so the payload is already correct when a second tenant appears.
        self._job_id = validate_id("job_id", job_id)
        self._tenant_id = validate_id("tenant_id", tenant_id)
        self._workspace_id = validate_id("workspace_id", workspace_id)
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal — 'who created this' is required")
        self._principal = principal
        self._supersedes_job_id = (
            validate_id("supersedes_job_id", supersedes_job_id)
            if supersedes_job_id is not None
            else None
        )
        self._clock = clock

        self._state = JobState.DRAFT
        self._awaiting_from: JobState | None = None
        # RFC-0002: what authorises execution is a decision, not a flag. Holding
        # the record means the engine can always answer "on whose authority?"
        self._approval: Decision | None = None
        self._decisions: list[Decision] = []
        self._history: list[TransitionRecord] = []
        self._events: list[Event] = []

        metadata: dict[str, Any] = {}
        if self._supersedes_job_id is not None:
            metadata["supersedes_job_id"] = self._supersedes_job_id
        self._emit(EventType.JOB_CREATED, actor=principal, metadata=metadata)

    # ---- read-only surface -------------------------------------------------
    # Everything below is exposed as a copy or an immutable view. The audit trail
    # is append-only, so handing out the live list would let a caller edit history.

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def principal(self) -> Principal:
        return self._principal

    @property
    def supersedes_job_id(self) -> str | None:
        return self._supersedes_job_id

    @property
    def state(self) -> JobState:
        return self._state

    @property
    def awaiting_from(self) -> JobState | None:
        """Where a paused job returns to. Set only while in AWAITING_APPROVAL."""
        return self._awaiting_from

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL

    @property
    def approval(self) -> Decision | None:
        """The APPROVE this job executes under, or None.

        Cleared by a REJECT: an approval granted to an earlier revision must not
        authorise the revised one.
        """
        return self._approval

    @property
    def approval_expires_at(self) -> datetime | None:
        """When the approval in force stops authorising anything, if it says.

        ``None`` covers both "no approval" and "an approval with no deadline" —
        two different situations that this property is not the place to tell
        apart, since :attr:`approval` already does.
        """
        return self._approval.expires_at if self._approval is not None else None

    @property
    def approval_expired(self) -> bool:
        """Whether the approval in force has run out as of now.

        Reads the clock, so it is a question about this moment rather than a
        stored fact — an approval expires by time passing, not by anything
        happening to the job. The clock is only consulted when there is a deadline
        to compare against, so a job whose approval carries no ``expires_at``
        answers this without asking what time it is.
        """
        return self.approval_expires_at is not None and self._approval.is_expired(
            self._clock()
        )

    @property
    def decisions(self) -> tuple[Decision, ...]:
        """Every governance decision made about this job, in order.

        Immutable records of immutable objects — a decision is never edited, so
        changing one's mind appears here as a second decision citing the first.
        """
        return tuple(self._decisions)

    @property
    def history(self) -> tuple[TransitionRecord, ...]:
        return tuple(self._history)

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def event_payloads(self) -> list[dict[str, Any]]:
        """The audit trail in ``event/v1`` wire shape — what conformance validates."""
        return [event.as_payload() for event in self._events]

    def allowed_targets(self) -> frozenset[JobState]:
        """States reachable right now, including this job's return edge.

        ``AWAITING_APPROVAL``'s way back is ``awaiting_from``, which differs per
        job and so cannot live in the static table; ``states.reachable_from``
        folds it in, and is the same call anything else reading the table makes.
        """
        return reachable_from(self._state, awaiting_from=self._awaiting_from)

    # ---- the engine --------------------------------------------------------

    def transition(
        self,
        to: JobState,
        *,
        reason: str | None = None,
        principal: Principal | None = None,
        decision: Decision | None = None,
    ) -> Event:
        """Move to ``to``, or refuse and leave the job untouched.

        Returns the ``STATE_TRANSITION`` event so a caller can forward it without
        reaching back into the history.

        ``decision`` is how :meth:`decide` hands its record to the one method that
        may change state. Entering a decision state without one is still allowed
        and still produces a record — see :meth:`_decision_for`.
        """
        to = JobState(to)
        if decision is not None and not isinstance(decision, Decision):
            raise TypeError("decision must be a Decision — 'who decided what' is required")
        self._check_not_terminal()
        self._check_edge(to)
        # Whether this move *is* a governance decision is a property of the edge,
        # not of the destination — since RFC-0011, DRAFT is reached both by a
        # REQUIRE_CHANGES from the gate and by the ordinary revision step out of
        # REJECTED, and only the first is a verdict.
        decides = decision_for_edge(self._state, to)
        self._check_guards(to, reason=reason, principal=principal, decides=decides)

        record: Decision | None = None
        if decides is not None:
            # "Every APPROVE is auditable" is a guarantee about the state, not
            # about which method the caller reached for. Entering a decision state
            # through the generic API therefore mints the same record decide()
            # would have: the guards above have already established that an
            # authority and a reason are present.
            record = decision if decision is not None else self._decision_for(
                decides, authority=principal, reason=reason
            )
            self._check_decision(record, decides, to)
        elif decision is not None:
            raise DecisionStateMismatch(decision.decision.value, to.value)

        previous = self._state
        # AWAITING_APPROVAL's return address is recorded on entry and cleared on
        # exit, so a job paused during DEPLOYABLE cannot resume as IN_PROGRESS.
        if to is JobState.AWAITING_APPROVAL:
            self._awaiting_from = previous
        elif previous is JobState.AWAITING_APPROVAL:
            self._awaiting_from = None

        if decides is DecisionType.APPROVE:
            self._approval = record
        elif decides is not None:
            # A verdict that is not an APPROVE leaves the job unauthorised, and it
            # goes back to DRAFT for revision — REJECT by way of REJECTED,
            # REQUIRE_CHANGES directly. Either way the approval that was never
            # granted must not carry over, and an approval granted to an earlier
            # revision must not authorise the revised one. RFC-0011 keeps
            # REQUIRE_CHANGES on this side of the line deliberately: "งานยังมีชีวิต"
            # says the job may come back, not that it may proceed.
            self._approval = None

        self._state = to

        if record is not None:
            self._decisions.append(record)
            # The decision is emitted before the transition it caused, so the
            # trail reads in causal order. Its subject is the approval itself,
            # with job_id naming the job it belongs to — event/v1's own example
            # of the distinction (EXECUTION_STARTED is about the execution and
            # carries the job it sits under).
            self._emit(
                EventType.GOVERNANCE_DECISION,
                actor=record.authority,
                subject_type="approval",
                subject_id=record.decision_id,
                metadata={"approval": record.as_payload()},
            )

        event = self._emit(
            EventType.STATE_TRANSITION,
            actor=principal or self._principal,
            transition=self._transition_payload(previous, to, reason),
        )
        self._history.append(
            TransitionRecord(
                from_state=previous,
                to_state=to,
                at=event.occurred_at,
                reason=reason,
                principal=principal,
                event_id=event.event_id,
                decision_id=record.decision_id if record is not None else None,
            )
        )
        if to is JobState.COMPLETED:
            self._emit(EventType.JOB_COMPLETED, actor=principal or self._principal)
        if to in TERMINAL:
            self._settle(to, actor=principal or self._principal)
        return event

    def _settle(self, terminal: JobState, *, actor: Principal) -> Event:
        """Emit the closing record. RFC-0012.

        Nothing that comes after a job's last record can vouch for it, which is
        why replay could only ever catch a trail truncated after ``COMPLETED`` —
        that terminal implies ``JOB_COMPLETED`` and the other three implied
        nothing. This record is what the other three were missing, and
        ``COMPLETED`` emits it too so the rule has no exception in it for a
        reader to have to know about.

        ``event_count`` is scoped by ``job_id``, not by subject. That is the unit
        replay actually verifies — ``replay_tenant`` groups by ``job_id`` — and it
        is the scope that matters: ``GOVERNANCE_DECISION`` is about the approval
        and carries its own ``subject_id``, so counting by subject would miss the
        very records the count exists to notice.
        """
        counted = sum(1 for event in self._events if event.job_id == self._job_id)
        return self._emit(
            EventType.JOB_SETTLED,
            actor=actor,
            metadata={
                "settled_as": terminal.value,
                # +1 for this record: a reader holding the trail compares the
                # count against everything it holds, this record included.
                "event_count": counted + 1,
            },
        )

    # ---- named transitions -------------------------------------------------
    # These exist so the arguments a guard requires are visible in the call
    # signature rather than discovered at runtime.

    def submit_for_governance(self, *, reason: str | None = None) -> Event:
        return self.transition(JobState.GOVERNANCE_ANALYSIS, reason=reason)

    # ---- governance decisions, RFC-0002 ------------------------------------

    def decide(
        self,
        decision: DecisionType | str,
        *,
        authority: Principal,
        reason: str,
        supersedes_decision_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> Decision:
        """Record a governance decision and move the job where it sends it.

        Returns the :class:`~devfactory_core.decision.Decision` — the record is
        what a caller wants to forward, and the events it produced are on
        :attr:`events`. The job moves and the decision is written in one call
        because they are one act: a decision that does not move the job is not a
        decision, and a move without a decision is what this whole module exists
        to prevent.

        All three of RFC-0002's decisions are accepted. A decision type that
        somehow has no destination is refused with ``UnmappedDecision`` rather
        than sent somewhere invented — see that error for when it can arise.

        ``expires_at`` is ``approval/v1``'s deadline for the decision, and it is
        the caller's to set: the timeout *policy* — how long an approval is good
        for, per job type — is out of scope for RFC-0007 and RFC-0010 alike, so
        nothing here invents a default. Passing nothing produces an approval with
        no deadline, which is the pre-existing behaviour and stays valid.
        """
        decision = DecisionType(decision)
        target = DECISION_TARGET.get(decision)
        if target is None:
            raise UnmappedDecision(decision.value)
        record = Decision(
            decision_id=new_decision_id(),
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            subject=Subject("job", self._job_id),
            decision=decision,
            reason=reason,
            authority=authority,
            decided_at=self._clock(),
            expires_at=expires_at,
            # "Changing your mind is a new approval that cites the old one"
            # (approval/v1 — the citation rides the wire as supersedes_approval_id).
            # It is filled from this job's own history rather than left to the caller
            # to remember: that makes it point at a decision that really was made,
            # about this job, in this tenant, which is four of the five invariants
            # approval/v1 asks the producer to hold up. The fifth — no cycles — comes
            # free from a freshly minted decision_id.
            supersedes_decision_id=(
                supersedes_decision_id
                if supersedes_decision_id is not None
                else (self._decisions[-1].decision_id if self._decisions else None)
            ),
        )
        self.transition(target, reason=reason, principal=authority, decision=record)
        return record

    def approve(
        self,
        *,
        authority: Principal,
        reason: str,
        expires_at: datetime | None = None,
    ) -> Decision:
        return self.decide(
            DecisionType.APPROVE,
            authority=authority,
            reason=reason,
            expires_at=expires_at,
        )

    def reject(self, *, authority: Principal, reason: str) -> Decision:
        return self.decide(DecisionType.REJECT, authority=authority, reason=reason)

    def require_changes(self, *, authority: Principal, reason: str) -> Decision:
        """Send the job back for changes — RFC-0011.

        The job lands in ``DRAFT`` holding no approval, ready to be revised and
        resubmitted. It is not a rejection and its trail does not say it was: the
        route skips ``REJECTED`` entirely.
        """
        return self.decide(
            DecisionType.REQUIRE_CHANGES, authority=authority, reason=reason
        )

    def pause_for_approval(self, *, reason: str | None = None) -> Event:
        return self.transition(JobState.AWAITING_APPROVAL, reason=reason)

    def resume(self, *, reason: str | None = None, principal: Principal | None = None) -> Event:
        """Return to the state this job paused in."""
        if self._state is not JobState.AWAITING_APPROVAL or self._awaiting_from is None:
            raise InvalidTransition(
                self._state.value, "resume", sorted(s.value for s in self.allowed_targets())
            )
        return self.transition(self._awaiting_from, reason=reason, principal=principal)

    def fail(self, *, reason: str, principal: Principal | None = None) -> Event:
        return self.transition(JobState.FAILED, reason=reason, principal=principal)

    def cancel(self, *, reason: str, principal: Principal) -> Event:
        return self.transition(JobState.CANCELLED, reason=reason, principal=principal)

    def time_out(self, *, reason: str, principal: Principal | None = None) -> Event:
        return self.transition(JobState.TIMED_OUT, reason=reason, principal=principal)

    def supersede(self, *, job_id: str, principal: Principal | None = None) -> "Job":
        """Create the replacement job for one that settled without delivering.

        RFC-0007: recovery is a new job, not a revival. The replacement starts at
        DRAFT and passes GOVERNANCE_ANALYSIS again, which is the guarantee —
        resuming the old one would continue under an APPROVED granted to a plan
        that has since failed.

        Amendment 2 widened *which* job may be pointed back at, from ``FAILED``
        alone to every terminal in ``states.SUPERSEDABLE``. An approval that
        lapses lands its job in ``TIMED_OUT``, and ``approval/v1`` answers that
        with "ต้องขอใหม่"; asking again is this call, and before the amendment it
        had no way to say what it was a second attempt at. ``COMPLETED`` is not in
        the set: a job that delivered is not an attempt awaiting another one.
        """
        if self._state not in SUPERSEDABLE:
            # Two refusals with the same shape and different reasons: one job has
            # not finished trying, the other has nothing left to try.
            if self._state is JobState.COMPLETED:
                why = (
                    "COMPLETED delivered its work — what follows it is new work, "
                    "not another attempt at the same work"
                )
            else:
                settled = ", ".join(sorted(s.value for s in SUPERSEDABLE))
                why = f"a job is superseded once it has settled without delivering: {settled}"
            raise InvalidTransition(self._state.value, "supersede", [why])
        return Job(
            job_id=job_id,
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            principal=principal or self._principal,
            supersedes_job_id=self._job_id,
            clock=self._clock,
        )

    # ---- guards ------------------------------------------------------------

    def _check_not_terminal(self) -> None:
        if self._state in TERMINAL:
            raise TerminalState(self._state.value)

    def _check_edge(self, to: JobState) -> None:
        allowed = self.allowed_targets()
        if to in allowed:
            return
        # A refusal that has a specific reason says the specific reason. A generic
        # table miss is the last resort, not the first answer.
        if self._state is JobState.AWAITING_APPROVAL and to in APPROVAL_PAUSABLE:
            raise WrongResumeState(
                self._awaiting_from.value if self._awaiting_from else "unknown", to.value
            )
        if to is JobState.AWAITING_APPROVAL:
            raise MissingApprovalContext(self._state.value)
        raise InvalidTransition(
            self._state.value, to.value, sorted(s.value for s in allowed)
        )

    def _check_guards(
        self,
        to: JobState,
        *,
        reason: str | None,
        principal: Principal | None,
        decides: DecisionType | None,
    ) -> None:
        if to in REASON_REQUIRED and not (reason and reason.strip()):
            raise MissingReason(to.value)
        if to is JobState.CANCELLED and principal is None:
            raise MissingPrincipal(to.value)
        # A decision names who made it and why, whichever decision it is. Asking
        # the edge rather than a list of destination states means a decision type
        # gaining a destination cannot leave that edge ungoverned.
        if decides is not None and (principal is None or not (reason and reason.strip())):
            raise MissingAuthority(to.value, decision=decides.value)
        # Structural backstop for the direction lock. The table already makes
        # APPROVED the only way in, so this can only fire if the table is edited
        # wrongly — which is exactly when it is worth having.
        if to in POST_APPROVAL and self._approval is None:
            raise ExecutionBeforeApproval(to.value)
        # The same lock in its second half. Holding an approval is not enough if
        # the approval has run out: ``approval/v1`` says an expired one cannot be
        # used to run work and has to be granted again. POST_APPROVAL is the set
        # of states that mean execution has been authorised, so it is exactly the
        # set an expired authorisation must not open — including the way back out
        # of AWAITING_APPROVAL, where a pause is what let the deadline pass.
        if to in POST_APPROVAL and self.approval_expired:
            raise ExpiredApproval(
                to.value,
                expired_at=self._approval.expires_at.isoformat(),
                now=self._clock().isoformat(),
            )

    def _decision_for(
        self, decides: DecisionType, *, authority: Principal | None, reason: str | None
    ) -> Decision:
        """Mint the decision this edge must have been.

        Only reachable for an edge ``states.decision_for_edge`` names, and only
        after the guards have established that both an authority and a reason are
        present — so nothing here is invented to fill a field.
        """
        assert authority is not None and reason is not None  # guaranteed by _check_guards
        return Decision(
            decision_id=new_decision_id(),
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            subject=Subject("job", self._job_id),
            decision=decides,
            reason=reason,
            authority=authority,
            decided_at=self._clock(),
            supersedes_decision_id=(
                self._decisions[-1].decision_id if self._decisions else None
            ),
        )

    def _check_decision(
        self, record: Decision, decides: DecisionType, to: JobState
    ) -> None:
        """Refuse a decision that does not belong to this job or this transition.

        Compared against the decision the *edge* is, not merely against the
        destination: since RFC-0011 two decisions can share a destination by way of
        different routes, and a REJECT offered for the direct
        ``GOVERNANCE_ANALYSIS -> DRAFT`` hop would otherwise record a rejection on
        a trail that never entered ``REJECTED``.
        """
        if record.decision is not decides:
            raise DecisionStateMismatch(record.decision.value, to.value)
        if record.subject != Subject("job", self._job_id):
            raise WrongDecisionSubject(
                record.subject.type, record.subject.id, self._job_id
            )
        # RFC-0006: a decision belongs to the same scope as what it decides about.
        # Reject the mismatch; never quietly rewrite it to match.
        if record.tenant_id != self._tenant_id:
            raise CrossTenantDecision("tenant_id", record.tenant_id, self._tenant_id)
        if record.workspace_id is not None and record.workspace_id != self._workspace_id:
            raise CrossTenantDecision(
                "workspace_id", record.workspace_id, self._workspace_id
            )
        if record.decision is DecisionType.APPROVE and self._is_self_approval(
            record.authority
        ):
            raise SelfApproval(record.authority.id, self._job_id)

    def _is_self_approval(self, authority: Principal) -> bool:
        """Whether this APPROVE would be an agent approving its own work.

        Scoped to agents on purpose. ``approval/v1`` states the invariant as
        "agent ออก APPROVE ให้งานของตัวเองไม่ได้ — authority.id ต้องไม่ใช่ agent_id
        ของงานที่กำลังอนุมัติ", and RFC-0002 rejects "agent self-approval" by name.
        A person approving a job they filed is a *different* rule: it may well be
        one this repository wants, but adopting it here would make the engine
        stricter than the contract it conforms to, and stricter is still
        different. That belongs in an RFC, not in an implementation detail.

        The job has no ``agent_id`` of its own, so the principal accountable for
        the job — the one that created it — is what ``agent_id`` maps to here.
        """
        return authority.type == "agent" and authority.id == self._principal.id

    # ---- emit --------------------------------------------------------------

    def _transition_payload(
        self, previous: JobState, to: JobState, reason: str | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"from": previous.value, "to": to.value}
        # RFC-0008 forbids fabricating a value to fill a field. "No reason given"
        # is expressed by the key being absent, never by an empty string.
        if reason:
            payload["reason"] = reason
        return payload

    def _emit(
        self,
        event_type: EventType,
        *,
        actor: Principal | None = None,
        transition: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        subject_type: str = "job",
        subject_id: str | None = None,
    ) -> Event:
        # The subject defaults to the job because almost every event this engine
        # emits is about the job. GOVERNANCE_DECISION is about the approval and
        # says so; job_id stays on every event either way, which is the rule
        # RFC-0008 holds us to.
        event = Event(
            event_id=new_event_id(),
            event_type=event_type,
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            subject_type=subject_type,
            subject_id=subject_id or self._job_id,
            job_id=self._job_id,
            occurred_at=self._clock(),
            actor=actor,
            transition=transition,
            metadata=metadata or {},
        )
        self._events.append(event)
        return event

    def __repr__(self) -> str:
        return (
            f"Job(job_id={self._job_id!r}, tenant_id={self._tenant_id!r}, "
            f"state={self._state.value}, transitions={len(self._history)})"
        )
