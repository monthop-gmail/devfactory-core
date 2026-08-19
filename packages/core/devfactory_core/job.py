"""The in-memory job state machine.

Canonical spec: ``packages/core/state-machine.md`` — RFC-0001 as amended by
RFC-0007 and RFC-0010, with the tenant model from RFC-0006.

Scope, per issue #2: in memory, no persistence, no policy engine, no API. What
this module owns is the lifecycle and the guards on it.

Which states may fail is settled by RFC-0010: FAILED from TASK_PLANNING,
IN_PROGRESS, AWAITING_APPROVAL, VALIDATING, and DEPLOYABLE only — the states
where work exists to fail — and refused before APPROVED, where the honest
outcomes are REJECTED, CANCELLED, or TIMED_OUT. See ``states.FAILABLE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .errors import (
    ExecutionBeforeApproval,
    InvalidTransition,
    MissingApprovalContext,
    MissingAuthority,
    MissingPrincipal,
    MissingReason,
    TerminalState,
    WrongResumeState,
)
from .events import Event, EventType, new_event_id, utc_now
from .identity import Principal, validate_id
from .states import (
    APPROVAL_PAUSABLE,
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
AUTHORITY_REQUIRED: frozenset[JobState] = frozenset(
    {JobState.APPROVED, JobState.REJECTED}
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


class Job:
    """A governed unit of work moving through the lifecycle.

    Construction emits ``JOB_CREATED``; every accepted transition emits
    ``STATE_TRANSITION``; reaching COMPLETED also emits ``JOB_COMPLETED``. There
    is no way to change ``state`` without going through :meth:`transition`, which
    is what makes "no silent state change" hold rather than merely be documented.
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
        self._approved = False
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
    ) -> Event:
        """Move to ``to``, or refuse and leave the job untouched.

        Returns the ``STATE_TRANSITION`` event so a caller can forward it without
        reaching back into the history.
        """
        to = JobState(to)
        self._check_not_terminal()
        self._check_edge(to)
        self._check_guards(to, reason=reason, principal=principal)

        previous = self._state
        # AWAITING_APPROVAL's return address is recorded on entry and cleared on
        # exit, so a job paused during DEPLOYABLE cannot resume as IN_PROGRESS.
        if to is JobState.AWAITING_APPROVAL:
            self._awaiting_from = previous
        elif previous is JobState.AWAITING_APPROVAL:
            self._awaiting_from = None

        if to is JobState.APPROVED:
            self._approved = True
        elif to is JobState.REJECTED:
            # A rejected job returns to DRAFT for revision. The approval that was
            # never granted must not carry over, and an approval granted to an
            # earlier revision must not authorise the revised one.
            self._approved = False

        self._state = to

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

    def approve(self, *, authority: Principal, reason: str) -> Event:
        return self.transition(JobState.APPROVED, reason=reason, principal=authority)

    def reject(self, *, authority: Principal, reason: str) -> Event:
        return self.transition(JobState.REJECTED, reason=reason, principal=authority)

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
        if to in POST_APPROVAL and not self._approved:
            raise ExecutionBeforeApproval(to.value)

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
    ) -> Event:
        event = Event(
            event_id=new_event_id(),
            event_type=event_type,
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            subject_type="job",
            subject_id=self._job_id,
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
