"""The in-memory job state machine.

Canonical spec: ``packages/core/state-machine.md`` — RFC-0001 as amended by
RFC-0007 and RFC-0010, with the tenant model from RFC-0006.

Scope, per issue #2: in memory, no persistence, no policy engine, no API. What
this module owns is the lifecycle and the guards on it.

Which states may fail is settled by RFC-0010: FAILED from TASK_PLANNING,
IN_PROGRESS, AWAITING_APPROVAL, VALIDATING, and DEPLOYABLE only — the states
where work exists to fail — and refused before APPROVED, where the honest
outcomes are REJECTED, CANCELLED, or TIMED_OUT. See ``states.FAILABLE``.

Governance decisions are RFC-0002, added by issue #5. A job holds the decision it
is executing under (``approval``) rather than a boolean, because "approved" is
not a fact about a job — it is a record of who decided what, when, and why.
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
    DECISION_BY_TARGET,
    DECISION_TARGET,
    POST_APPROVAL,
    TERMINAL,
    TRANSITIONS,
    JobState,
)

#: States whose entry requires reason metadata — RFC-0001 for FAILED, extended
#: by RFC-0007 to the two terminal states it added.
REASON_REQUIRED: frozenset[JobState] = frozenset(
    {JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT}
)

#: States whose entry is a decision and must name an accountable authority.
#: Derived from ``DECISION_TARGET`` rather than listed again, so a decision type
#: gaining a destination cannot leave its destination state ungoverned.
AUTHORITY_REQUIRED: frozenset[JobState] = frozenset(DECISION_TARGET.values())


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
    ``GOVERNANCE_DECISION``; reaching COMPLETED also emits ``JOB_COMPLETED``.
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
        job and so cannot live in the static table.
        """
        targets = set(TRANSITIONS[self._state])
        if self._state is JobState.AWAITING_APPROVAL and self._awaiting_from is not None:
            targets.add(self._awaiting_from)
        return frozenset(targets)

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
        self._check_guards(to, reason=reason, principal=principal)

        record: Decision | None = None
        if to in AUTHORITY_REQUIRED:
            # "Every APPROVE is auditable" is a guarantee about the state, not
            # about which method the caller reached for. Entering APPROVED or
            # REJECTED through the generic API therefore mints the same record
            # decide() would have: the guards above have already established that
            # an authority and a reason are present.
            record = decision if decision is not None else self._decision_for(
                to, authority=principal, reason=reason
            )
            self._check_decision(record, to)
        elif decision is not None:
            raise DecisionStateMismatch(decision.decision.value, to.value)

        previous = self._state
        # AWAITING_APPROVAL's return address is recorded on entry and cleared on
        # exit, so a job paused during DEPLOYABLE cannot resume as IN_PROGRESS.
        if to is JobState.AWAITING_APPROVAL:
            self._awaiting_from = previous
        elif previous is JobState.AWAITING_APPROVAL:
            self._awaiting_from = None

        if to is JobState.APPROVED:
            self._approval = record
        elif to is JobState.REJECTED:
            # A rejected job returns to DRAFT for revision. The approval that was
            # never granted must not carry over, and an approval granted to an
            # earlier revision must not authorise the revised one.
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
        return event

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
    ) -> Decision:
        """Record a governance decision and move the job where it sends it.

        Returns the :class:`~devfactory_core.decision.Decision` — the record is
        what a caller wants to forward, and the events it produced are on
        :attr:`events`. The job moves and the decision is written in one call
        because they are one act: a decision that does not move the job is not a
        decision, and a move without a decision is what this whole module exists
        to prevent.

        Refuses ``REQUIRE_CHANGES`` with ``UnmappedDecision``: RFC-0002 declares
        it, no RFC here says where it sends a job, and the engine will not invent
        a destination. See ``states.DECISION_TARGET``.
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
            # "Changing your mind is a new approval that cites the old one"
            # (approval/v1). The citation is filled from this job's own history
            # rather than left to the caller to remember — it points at a
            # decision that really was made, so nothing is being fabricated.
            supersedes_decision_id=(
                supersedes_decision_id
                if supersedes_decision_id is not None
                else (self._decisions[-1].decision_id if self._decisions else None)
            ),
        )
        self.transition(target, reason=reason, principal=authority, decision=record)
        return record

    def approve(self, *, authority: Principal, reason: str) -> Decision:
        return self.decide(DecisionType.APPROVE, authority=authority, reason=reason)

    def reject(self, *, authority: Principal, reason: str) -> Decision:
        return self.decide(DecisionType.REJECT, authority=authority, reason=reason)

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
        """Create the replacement job for a FAILED one.

        RFC-0007: recovery is a new job, not a revival. The replacement starts at
        DRAFT and passes GOVERNANCE_ANALYSIS again, which is the guarantee —
        resuming the old one would continue under an APPROVED granted to a plan
        that has since failed.
        """
        if self._state is not JobState.FAILED:
            raise InvalidTransition(
                self._state.value, "supersede", ["only a FAILED job can be superseded"]
            )
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
        self, to: JobState, *, reason: str | None, principal: Principal | None
    ) -> None:
        if to in REASON_REQUIRED and not (reason and reason.strip()):
            raise MissingReason(to.value)
        if to is JobState.CANCELLED and principal is None:
            raise MissingPrincipal(to.value)
        if to in AUTHORITY_REQUIRED and (principal is None or not (reason and reason.strip())):
            raise MissingAuthority(to.value)
        # Structural backstop for the direction lock. The table already makes
        # APPROVED the only way in, so this can only fire if the table is edited
        # wrongly — which is exactly when it is worth having.
        if to in POST_APPROVAL and self._approval is None:
            raise ExecutionBeforeApproval(to.value)

    def _decision_for(
        self, to: JobState, *, authority: Principal | None, reason: str | None
    ) -> Decision:
        """Mint the decision that entering ``to`` must have been.

        Only reachable for states in ``AUTHORITY_REQUIRED``, and only after the
        guards have established that both an authority and a reason are present —
        so nothing here is invented to fill a field.
        """
        assert authority is not None and reason is not None  # guaranteed by _check_guards
        return Decision(
            decision_id=new_decision_id(),
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            subject=Subject("job", self._job_id),
            decision=DECISION_BY_TARGET[to],
            reason=reason,
            authority=authority,
            decided_at=self._clock(),
            supersedes_decision_id=(
                self._decisions[-1].decision_id if self._decisions else None
            ),
        )

    def _check_decision(self, record: Decision, to: JobState) -> None:
        """Refuse a decision that does not belong to this job or this transition."""
        if DECISION_TARGET.get(record.decision) is not to:
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
