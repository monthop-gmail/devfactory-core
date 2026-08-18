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


class MissingAuthority(JobStateMachineError):
    """APPROVED and REJECTED are decisions and must name who made them."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(
            f"{state} requires an accountable authority and a reason — "
            f"an approval nobody signed is not auditable"
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
