"""devfactory-core — governance-first control plane.

Phase 1: the job state machine, in memory. See ``packages/core/state-machine.md``.
"""

from .errors import (
    ExecutionBeforeApproval,
    InvalidIdentifier,
    InvalidTransition,
    JobStateMachineError,
    MissingApprovalContext,
    MissingAuthority,
    MissingPrincipal,
    MissingReason,
    TerminalState,
    WrongResumeState,
)
from .events import Event, EventType
from .identity import DEFAULT_TENANT, Principal
from .job import Job, TransitionRecord
from .states import TERMINAL, TRANSITIONS, JobState

__all__ = [
    "DEFAULT_TENANT",
    "TERMINAL",
    "TRANSITIONS",
    "Event",
    "EventType",
    "ExecutionBeforeApproval",
    "InvalidIdentifier",
    "InvalidTransition",
    "Job",
    "JobState",
    "JobStateMachineError",
    "MissingApprovalContext",
    "MissingAuthority",
    "MissingPrincipal",
    "MissingReason",
    "Principal",
    "TerminalState",
    "TransitionRecord",
    "WrongResumeState",
]
