"""Errors raised by the audit log.

An audit trail earns its name by refusing bad input at the boundary. Once a
record is written it cannot be corrected — ``event/v1`` guarantees append-only —
so every check here has to happen *before* the write, never after.
"""

from __future__ import annotations


class AuditLogError(Exception):
    """Base class for every refusal from this package."""


class DuplicateEvent(AuditLogError):
    """An event_id already present in the log was appended again.

    Accepting it would put two records with one identity into an immutable log,
    and nothing downstream could tell which one was meant.
    """

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"event_id {event_id!r} is already in the log")


class MissingTenant(AuditLogError):
    """An event arrived with no resolvable tenant.

    RFC-0008: reject at intake rather than guess. Guessing writes one tenant's
    activity into another tenant's audit trail, which is worse than losing the
    event — the loss is visible and the misfiling is not.
    """

    def __init__(self, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        super().__init__(
            f"event has no resolvable tenant, rejected at intake{suffix}"
        )


class MissingSubject(AuditLogError):
    """An event that cannot say what it is about.

    RFC-0008 made ``job_id`` optional and a subject required, precisely so that
    dropping the job requirement does not leave events that answer nothing.
    """

    def __init__(self, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        super().__init__(f"event must name its subject{suffix}")


class FabricatedIdentifier(AuditLogError):
    """Something tried to supply a placeholder in place of a real identifier."""

    def __init__(self, field: str, value: str) -> None:
        self.field = field
        self.value = value
        super().__init__(
            f"{field}={value!r} looks fabricated. RFC-0008: absent means absent — "
            f"do not synthesise an identifier to satisfy a field"
        )


class ExternalSourceRequired(AuditLogError):
    """An inbound external event did not say where it came from."""

    def __init__(self) -> None:
        super().__init__(
            "an external event must name its source system so it stays "
            "identifiable as external forever"
        )
