"""In-memory append-only audit log, partitioned by tenant.

Scope, per issue #6: in memory for v0.1. No metrics backend, no dashboard.

Why partitions rather than one list with a tenant filter
--------------------------------------------------------
RFC-0006 states tenant isolation as a **storage-layer** guarantee and says
explicitly that a ``WHERE tenant_id = ?`` filter does not satisfy it. The
in-memory analogue of that is a separate partition per tenant, so a read has
nowhere to look outside the tenant it names. A single list guarded by a filter
would put one forgotten predicate between two tenants; there is no predicate to
forget here, because there is no shared list.

Every read takes a ``tenant_id``. There is deliberately no method that returns
events across tenants.

Why there is no update and no delete
------------------------------------
``event/v1`` guarantees append-only. That is expressed here by the absence of the
methods rather than by a flag someone can pass — an API with no way to mutate
history cannot be talked into mutating history.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Iterator

from devfactory_core.events import Event

from .errors import DuplicateEvent, MissingSubject, MissingTenant


class EventLog:
    """Append-only event store, isolated per tenant."""

    def __init__(self) -> None:
        self._partitions: dict[str, list[Event]] = {}
        self._seen: set[str] = set()

    # ---- write -------------------------------------------------------------

    def append(self, event: Event) -> Event:
        """Record one event, or refuse it and change nothing.

        The partition key comes from the event itself, so an event cannot be
        filed under a tenant other than its own.
        """
        if not event.tenant_id:
            raise MissingTenant(f"event_id={event.event_id}")
        if not event.subject_type or not event.subject_id:
            raise MissingSubject(f"event_id={event.event_id}")
        if event.event_id in self._seen:
            raise DuplicateEvent(event.event_id)

        self._partitions.setdefault(event.tenant_id, []).append(event)
        self._seen.add(event.event_id)
        return event

    def extend(self, events: Iterable[Event]) -> tuple[Event, ...]:
        """Append several events.

        Not atomic, and deliberately so: a partial append leaves the events that
        were valid in the log and reports the one that was not. Discarding
        accepted records to punish a later bad one would lose history that
        actually happened.
        """
        return tuple(self.append(event) for event in events)

    # ---- read --------------------------------------------------------------

    def read(
        self,
        tenant_id: str,
        *,
        job_id: str | None = None,
        subject_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]:
        """Events for one tenant, in the order they were appended.

        An unknown tenant reads as empty rather than raising, so probing for the
        existence of another tenant tells the caller nothing.
        """
        events: Iterable[Event] = self._partitions.get(tenant_id, ())
        if job_id is not None:
            events = [e for e in events if e.job_id == job_id]
        if subject_id is not None:
            events = [e for e in events if e.subject_id == subject_id]
        if event_type is not None:
            events = [e for e in events if e.type_value == event_type]
        return tuple(events)

    def payloads(self, tenant_id: str, **filters: str | None) -> list[dict]:
        """The trail in ``event/v1`` wire shape — what conformance validates."""
        return [event.as_payload() for event in self.read(tenant_id, **filters)]

    def tenants(self) -> tuple[str, ...]:
        """Tenants that have at least one event.

        Returns identifiers only. It exists so an operator can enumerate
        partitions to check; it never returns anything from inside one.
        """
        return tuple(sorted(self._partitions))

    def count(self, tenant_id: str) -> int:
        return len(self._partitions.get(tenant_id, ()))

    def __len__(self) -> int:
        return sum(len(events) for events in self._partitions.values())

    def __iter__(self) -> Iterator[str]:
        """Iterating an EventLog yields tenant ids, not events.

        Reading events requires naming a tenant, so there is no sensible
        cross-tenant iteration to offer here.
        """
        return iter(self.tenants())

    # ---- integrity ---------------------------------------------------------

    def digest(self, tenant_id: str) -> str:
        """A hash chain over one tenant's event ids, in append order.

        Append-only is a claim; this makes it checkable. Take the digest, carry
        on, take it again: if an earlier record were altered or removed, the
        prefix would no longer reproduce and the digest would change for a reason
        other than growth.
        """
        chain = hashlib.sha256()
        for event in self._partitions.get(tenant_id, ()):
            chain.update(event.event_id.encode("utf-8"))
            chain.update(b"\x00")
        return chain.hexdigest()

    def __repr__(self) -> str:
        sizes = ", ".join(f"{t}={len(e)}" for t, e in sorted(self._partitions.items()))
        return f"EventLog({sizes or 'empty'})"
