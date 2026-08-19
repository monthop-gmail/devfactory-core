"""devfactory-core — governance-first control plane.

Phase 1: the job state machine, in memory. See ``packages/core/state-machine.md``.
The governance decision interface it is gated by is ``decision.py`` — RFC-0002.
"""

from .decision import Decision, DecisionType, Subject, new_decision_id
from .errors import (
    CrossTenantDecision,
    DecisionStateMismatch,
    ExecutionBeforeApproval,
    ExpiredApproval,
    IncompleteDecision,
    InvalidIdentifier,
    InvalidTransition,
    JobStateMachineError,
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
from .events import INTERNAL_SOURCE, Event, EventType
from .identity import DEFAULT_TENANT, Principal
from .job import Job, TransitionRecord
from .states import DECISION_TARGET, TERMINAL, TRANSITIONS, JobState

__all__ = [
    "DECISION_TARGET",
    "DEFAULT_TENANT",
    "TERMINAL",
    "TRANSITIONS",
    "INTERNAL_SOURCE",
    "CrossTenantDecision",
    "Decision",
    "DecisionStateMismatch",
    "DecisionType",
    "Event",
    "EventType",
    "ExecutionBeforeApproval",
    "ExpiredApproval",
    "IncompleteDecision",
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
    "SelfApproval",
    "Subject",
    "TerminalState",
    "TransitionRecord",
    "UnmappedDecision",
    "WrongDecisionSubject",
    "WrongResumeState",
    "new_decision_id",
]
