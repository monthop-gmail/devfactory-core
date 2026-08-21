"""Intake for events this repository did not produce.

RFC-0008 rules, applied at the boundary and before anything is written:

1. an event with no ``job_id`` is accepted when it has a subject and a resolvable
   tenant
2. ``job_id`` is never synthesised, defaulted, or inferred — absent means absent
3. an event whose tenant cannot be resolved is rejected at intake, never written
4. the originating ``source`` is preserved so the event stays identifiable as
   external forever
5. intake is append-only like everything else — a malformed event is refused
   before the log rather than corrected inside it

An unrecognised ``event_type`` is **not** malformed. ``event/v1`` tells consumers
to keep such an event and skip interpreting it, so intake stores the type as the
string it arrived as.

A **malformed** one is a different thing and is refused. ``EventTypeName``
constrains the shape of the name, not the set of values, so that a vocabulary
which is allowed to grow still reads as a vocabulary. Unknown is a value we have
not met; malformed is not a value at all.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from devfactory_core.events import Event, EventType, new_event_id, utc_now
from devfactory_core.identity import Principal, validate_id

from .errors import (
    ExternalSourceRequired,
    FabricatedIdentifier,
    MalformedSequence,
    MalformedEventType,
    MissingSubject,
    MissingTenant,
)

#: Values that are placeholders wearing an identifier's clothes. An external
#: system sending one of these means "I have no job", and RFC-0008 says that must
#: be recorded as absence rather than passed through as a value.
PLACEHOLDERS = frozenset({"", "-", "none", "null", "n/a", "na", "unknown", "0", "undefined"})

#: Mirrors ``event/v1`` ``$defs.EventTypeName``. Held here for the same reason
#: ``identity.ID_PATTERN`` is held there: intake has to refuse a malformed value
#: before it reaches an immutable record, and the contract lives in another repo.
#: It is the minimum needed to make that refusal, not a second copy of the schema
#: — RFC-0005 Rule 4 forbids the latter.
EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

TenantResolver = Callable[[dict[str, Any]], str | None]


def _looks_fabricated(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDERS


def _parse_time(raw: Any, *, clock: Callable[[], datetime]) -> datetime:
    """Use the sender's timestamp when it is usable, ours when it is absent.

    A stamp we cannot parse is not silently replaced with ours — that would
    misreport when the thing happened, in a record nobody can later correct.
    """
    if raw is None:
        return clock()
    if isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw))


def accept_external(
    payload: dict[str, Any],
    *,
    tenant_resolver: TenantResolver | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Event:
    """Turn an inbound payload into an :class:`Event`, or refuse it.

    ``tenant_resolver`` is consulted only when the payload carries no
    ``tenant_id``. It may return ``None``, which is a rejection — a resolver that
    cannot answer must not be second-guessed with a default.
    """
    if not isinstance(payload, dict):
        raise MissingSubject("payload must be a mapping")

    tenant_id = payload.get("tenant_id")
    if not tenant_id and tenant_resolver is not None:
        tenant_id = tenant_resolver(payload)
    if not tenant_id or _looks_fabricated(str(tenant_id)):
        raise MissingTenant("no tenant_id on the payload and none resolved")
    tenant_id = validate_id("tenant_id", str(tenant_id))

    subject_type = payload.get("subject_type")
    subject_id = payload.get("subject_id")
    if not subject_type or not subject_id:
        raise MissingSubject("subject_type and subject_id are both required")

    # Rule 2. A placeholder is refused rather than stored, and rather than
    # quietly turned into absence — the sender's intent is ambiguous and an
    # immutable log is the wrong place to resolve an ambiguity by guessing.
    job_id = payload.get("job_id")
    if job_id is not None:
        if _looks_fabricated(str(job_id)):
            raise FabricatedIdentifier("job_id", str(job_id))
        job_id = validate_id("job_id", str(job_id))

    source = payload.get("source") or {}
    system = source.get("system") if isinstance(source, dict) else None
    if not system:
        raise ExternalSourceRequired()

    raw_type = payload.get("event_type")
    if not raw_type:
        raise MissingSubject("event_type is required")
    if not EVENT_TYPE_PATTERN.match(str(raw_type)):
        raise MalformedEventType(str(raw_type), EVENT_TYPE_PATTERN.pattern)
    try:
        event_type: EventType | str = EventType(raw_type)
    except ValueError:
        # Unknown but well formed: keep it, do not interpret it, do not fail.
        event_type = str(raw_type)

    actor = payload.get("actor")
    principal = None
    if isinstance(actor, dict) and actor.get("type") and actor.get("id"):
        principal = Principal(
            type=actor["type"], id=actor["id"], display_name=actor.get("display_name")
        )

    workspace_id = payload.get("workspace_id")
    if workspace_id is not None:
        workspace_id = validate_id("workspace_id", str(workspace_id))

    correlation_id = payload.get("correlation_id")
    if correlation_id is not None:
        correlation_id = validate_id("correlation_id", str(correlation_id))

    # Kept rather than recomputed. ADR-0015 added it so a reader can order events
    # a producer wrote from one clock, and only the producer knows that order —
    # dropping it here would silently discard the answer and leave the ties.
    sequence = payload.get("sequence")
    if sequence is not None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise MalformedSequence(sequence)

    return Event(
        event_id=str(payload.get("event_id") or new_event_id()),
        event_type=event_type,
        tenant_id=tenant_id,
        subject_type=str(subject_type),
        subject_id=validate_id("subject_id", str(subject_id)),
        occurred_at=_parse_time(payload.get("occurred_at"), clock=clock),
        job_id=job_id,
        workspace_id=workspace_id,
        actor=principal,
        correlation_id=correlation_id,
        sequence=sequence,
        # Rule 4. The kind is forced rather than trusted: an inbound event is
        # external by the fact of arriving here, whatever it claims about itself.
        source={"kind": "external", "system": str(system)},
        metadata=dict(payload.get("metadata") or {}),
    )
