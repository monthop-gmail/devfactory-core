"""devfactory-observability — the audit and event log.

Issue #6. Spec: RFC-0003 as amended by RFC-0008, with the tenant model from
RFC-0006. The ``Event`` type itself lives in ``devfactory_core.events``; this
package owns storage and intake.
"""

from .errors import (
    AuditLogError,
    DuplicateEvent,
    ExternalSourceRequired,
    FabricatedIdentifier,
    MissingSubject,
    MissingTenant,
)
from .intake import PLACEHOLDERS, accept_external
from .store import EventLog

__all__ = [
    "PLACEHOLDERS",
    "AuditLogError",
    "DuplicateEvent",
    "EventLog",
    "ExternalSourceRequired",
    "FabricatedIdentifier",
    "MissingSubject",
    "MissingTenant",
    "accept_external",
]
