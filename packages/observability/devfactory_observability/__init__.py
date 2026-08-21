"""devfactory-observability — the audit and event log.

Issue #6. Spec: RFC-0003 as amended by RFC-0008, with the tenant model from
RFC-0006. The ``Event`` type itself lives in ``devfactory_core.events``; this
package owns storage, intake, and reading a trail back (``replay``).
"""

from .errors import (
    AuditLogError,
    BrokenTrail,
    DuplicateEvent,
    EmptyTrail,
    ExecutionAfterExpiry,
    ExternalSourceRequired,
    FabricatedIdentifier,
    MalformedEventType,
    IncompleteSettlement,
    MiscountedTrail,
    PrematureSettlement,
    UnsettledTrail,
    MissingSubject,
    MissingTenant,
    ReplayError,
    UnauditedDecision,
    UnauditedExecution,
    UndeclaredTransition,
    UnstartedTrail,
)
from .intake import EVENT_TYPE_PATTERN, PLACEHOLDERS, accept_external
from .replay import ReplayedJob, ReplayedTransition, replay_job, replay_tenant
from .store import EventLog

__all__ = [
    "EVENT_TYPE_PATTERN",
    "PLACEHOLDERS",
    "AuditLogError",
    "BrokenTrail",
    "DuplicateEvent",
    "EmptyTrail",
    "EventLog",
    "ExecutionAfterExpiry",
    "ExternalSourceRequired",
    "FabricatedIdentifier",
    "MalformedEventType",
    "IncompleteSettlement",
    "MiscountedTrail",
    "PrematureSettlement",
    "UnsettledTrail",
    "MissingSubject",
    "MissingTenant",
    "ReplayError",
    "ReplayedJob",
    "ReplayedTransition",
    "UnauditedDecision",
    "UnauditedExecution",
    "UndeclaredTransition",
    "UnstartedTrail",
    "accept_external",
    "replay_job",
    "replay_tenant",
]
