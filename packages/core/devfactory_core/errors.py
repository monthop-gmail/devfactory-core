"""Errors raised by the job state machine.

Every one of these is a refusal, not a fallback. The lifecycle exists so that
governance can be enforced; an engine that repairs a bad call instead of
rejecting it would defeat the reason for having it.
"""

from __future__ import annotations


class JobStateMachineError(Exception):
    """Base class, so a caller can catch every refusal from this module."""


class InvalidTransition(JobStateMachineError):
    """The requested edge is not in the transition table for the current state."""

    def __init__(self, current: str, requested: str, allowed: list[str]) -> None:
        self.current = current
        self.requested = requested
        self.allowed = allowed
        permitted = ", ".join(allowed) if allowed else "nothing — this state is terminal"
        super().__init__(
            f"{current} -> {requested} is not a valid transition. Allowed: {permitted}"
        )


class TerminalState(JobStateMachineError):
    """A transition was requested out of a terminal state.

    Recovery from FAILED is a new job carrying ``supersedes_job_id``, never a
    transition out of it — RFC-0007 keeps FAILED terminal so that recovery has
    to pass GOVERNANCE_ANALYSIS again rather than resume under a stale APPROVED.
    """

    def __init__(self, current: str) -> None:
        self.current = current
        super().__init__(
            f"{current} is terminal. Recovery is a new job with supersedes_job_id, "
            f"not a transition out of {current}."
        )


class MissingReason(JobStateMachineError):
    """FAILED, CANCELLED, and TIMED_OUT each require reason metadata."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"{state} requires a reason — 'it stopped' is not an audit record")


class MissingPrincipal(JobStateMachineError):
    """CANCELLED records who cancelled it."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(
            f"{state} requires the principal responsible — "
            f"'someone stopped this' is not an audit record"
        )


class MissingApprovalContext(JobStateMachineError):
    """AWAITING_APPROVAL cannot be entered without knowing where to return to."""

    def __init__(self, current: str) -> None:
        self.current = current
        super().__init__(
            f"AWAITING_APPROVAL cannot be entered from {current} — "
            f"only IN_PROGRESS, VALIDATING, or DEPLOYABLE can pause for approval"
        )


class WrongResumeState(JobStateMachineError):
    """A paused job tried to resume somewhere other than where it paused."""

    def __init__(self, awaiting_from: str, requested: str) -> None:
        self.awaiting_from = awaiting_from
        self.requested = requested
        super().__init__(
            f"job paused in {awaiting_from} cannot resume into {requested} — "
            f"resuming elsewhere would silently lose its place in the lifecycle"
        )


class ExecutionBeforeApproval(JobStateMachineError):
    """The direction lock: no execution before an explicit APPROVE."""

    def __init__(self, requested: str) -> None:
        self.requested = requested
        super().__init__(
            f"cannot reach {requested} without passing APPROVED — "
            f"execution is forbidden before governance approves"
        )


class ExpiredApproval(JobStateMachineError):
    """The approval in force has run out, so it authorises nothing further.

    ``approval/v1`` states the rule on ``expires_at``: *"approval ที่หมดอายุแล้ว
    ใช้เดินงานไม่ได้ ต้องขอใหม่"*. Letting the job proceed anyway would be exactly
    the stale-``APPROVED`` execution RFC-0007 Decision 1 refuses — work running
    under a verdict formed in a context that has since lapsed.

    The remedy is a fresh decision, not a retry of this call. A job stuck here can
    still be cancelled, and since RFC-0007's 2026-08-19 amendment an ``APPROVED``
    job whose approval has lapsed can also be timed out (issue #17).
    """

    def __init__(self, requested: str, *, expired_at: str, now: str) -> None:
        self.requested = requested
        self.expired_at = expired_at
        self.now = now
        super().__init__(
            f"cannot reach {requested}: the approval in force expired at {expired_at} "
            f"and it is now {now} — an expired approval cannot be used to run work, "
            f"it has to be granted again"
        )


class MissingAuthority(JobStateMachineError):
    """A transition that *is* a governance decision must name who made it.

    Which transitions those are is the edge, not the destination state:
    ``GOVERNANCE_ANALYSIS -> DRAFT`` is a ``REQUIRE_CHANGES`` and needs an
    authority, while ``REJECTED -> DRAFT`` is the revision step after a verdict
    already recorded and does not. See ``states.decision_for_edge``.
    """

    def __init__(self, state: str, *, decision: str | None = None) -> None:
        self.state = state
        self.decision = decision
        named = f"entering {state} as a {decision}" if decision else state
        super().__init__(
            f"{named} requires an accountable authority and a reason — "
            f"a decision nobody signed is not auditable"
        )


class UnmappedDecision(JobStateMachineError):
    """A declared decision type with nowhere in the lifecycle to send a job.

    **Nothing raises this today**, and that is the point of keeping it. Since
    RFC-0011 gave ``REQUIRE_CHANGES`` its destination, all three of RFC-0002's
    decision types are in ``states.DECISION_TARGET``. The vocabulary is a closed
    set, so a fourth value can only arrive through an RFC here — and this is what
    that RFC's implementation runs into if it adds the value to
    :class:`~devfactory_core.decision.DecisionType` without also declaring the edge
    it travels and the entry in ``DECISION_TARGET`` that names it.

    Deleting it would turn that omission into a ``KeyError`` from inside
    :meth:`~devfactory_core.job.Job.decide` — an accident rather than a refusal.
    Governance code should refuse loudly and say why, and the failure it guards
    against is the one governance can least afford: an audit record whose recorded
    meaning is not the meaning that was made.
    """

    def __init__(self, decision: str) -> None:
        self.decision = decision
        super().__init__(
            f"{decision} is a declared decision type with no entry in "
            f"states.DECISION_TARGET, so no RFC in this repository says which state "
            f"it moves a job to — the engine will not guess a destination. A new "
            f"decision type needs the edge and the destination declared together."
        )


class IncompleteDecision(JobStateMachineError):
    """A decision missing one of the four meanings RFC-0002 requires."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(
            f"a decision without {field} is not auditable — RFC-0002 requires the "
            f"decision, the reason for it, the authority accountable for it, and when "
            f"it was made"
        )


class SelfApproval(JobStateMachineError):
    """An agent tried to APPROVE the work it is itself accountable for.

    "No agent has total authority" — RFC-0002 rejects agent self-approval by
    name and ``approval/v1`` states it as an invariant on ``authority``.
    """

    def __init__(self, authority_id: str, job_id: str) -> None:
        self.authority_id = authority_id
        self.job_id = job_id
        super().__init__(
            f"agent {authority_id!r} cannot APPROVE {job_id} — it is the principal "
            f"accountable for that job, and no agent has total authority over its own work"
        )


class CrossTenantDecision(JobStateMachineError):
    """A decision from one tenant tried to decide another tenant's job.

    RFC-0006 and ``approval/v1``: a mismatch is invalid and must be **rejected,
    never coerced** — silently rewriting the tenant is how an isolation boundary
    stops being one.
    """

    def __init__(self, field: str, decision_value: str, subject_value: str) -> None:
        self.field = field
        self.decision_value = decision_value
        self.subject_value = subject_value
        super().__init__(
            f"decision {field}={decision_value!r} does not match the job's "
            f"{field}={subject_value!r} — a decision must live in the same scope as what "
            f"it decides about, and a mismatch is rejected, never coerced"
        )


class WrongDecisionSubject(JobStateMachineError):
    """A decision about something else was offered as this job's decision."""

    def __init__(self, subject_type: str, subject_id: str, job_id: str) -> None:
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.job_id = job_id
        super().__init__(
            f"decision is about {subject_type}:{subject_id}, not job:{job_id} — "
            f"an approval granted to one subject cannot authorise another"
        )


class DecisionStateMismatch(JobStateMachineError):
    """The decision offered does not produce the transition being made."""

    def __init__(self, decision: str, requested: str) -> None:
        self.decision = decision
        self.requested = requested
        super().__init__(
            f"a {decision} decision does not move a job to {requested} — the audit "
            f"trail would record a decision that was never made"
        )


class InvalidIdentifier(JobStateMachineError):
    """Identifiers must match the identity/v1 Id form."""

    def __init__(self, field: str, value: str) -> None:
        self.field = field
        self.value = value
        super().__init__(
            f"{field}={value!r} is not a valid identity/v1 Id "
            f"(lowercase, leading alphanumeric, [a-z0-9_-], max 63 chars)"
        )
