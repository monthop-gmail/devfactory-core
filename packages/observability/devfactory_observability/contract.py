"""What a durable event store must meet — the contract declared by RFC-0014.

RFC-0014 Migration Plan step 2: ``EventLog``'s surface becomes the declared
contract and the in-memory implementation becomes the *reference* one rather
than the only one. This module is that declaration.

It holds no storage of its own. It exists so that the five obligations have one
written form that both the conformance suite and a future implementation read,
instead of each carrying its own copy of the list and drifting apart. The same
reason ``contract-semantics.yaml`` holds the text-field declaration that
``conformance/payload_check.py`` reads rather than restating it.

Why the obligations are data and not prose in a docstring
---------------------------------------------------------
``conformance/store_contract.py`` enumerates ``OBLIGATIONS`` and refuses to run
if it cannot pair every one of them with a check. An obligation that gets added
here and nowhere else fails the suite instead of passing silently, so the
written contract cannot quietly grow past what is actually verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol, runtime_checkable

from devfactory_core.events import Event


@dataclass(frozen=True)
class Obligation:
    """One thing a store owes, and the promise it is owed to."""

    number: int
    title: str
    statement: str
    source: str


OBLIGATIONS: tuple[Obligation, ...] = (
    Obligation(
        number=1,
        title="append-only",
        statement="A written record is never modified or removed.",
        source="event/v1 guarantee",
    ),
    Obligation(
        number=2,
        title="tenant isolation at the storage layer",
        statement=(
            "Each tenant has its own physical scope. A read issued against one "
            "tenant has no way to reach another's records, and no predicate "
            "stands between them that could be forgotten."
        ),
        source="RFC-0006",
    ),
    Obligation(
        number=3,
        title="append order preserved and total within a tenant",
        statement=(
            "Reading a tenant returns its records in the order they were "
            "appended, with no two records sharing a position."
        ),
        source="replay reads order as given",
    ),
    Obligation(
        number=4,
        title="idempotent by event_id within a tenant",
        statement=(
            "Re-appending a record the tenant already holds is refused, not "
            "duplicated, and the refusal writes nothing."
        ),
        source="EventLog.append today, and issue #32 depends on it",
    ),
    Obligation(
        number=5,
        title="a digest over the append order of one tenant",
        statement=(
            "A tenant's digest reproduces while its history is untouched and "
            "changes when a record is altered or removed."
        ),
        source="RFC-0012 Decision 4",
    ),
)


# Obligation 1 is stated as the *absence* of a way to mutate history, which is
# why it is checked by name rather than by behaviour: there is no call to make
# that should fail, and a store that grew an ``update`` would satisfy every
# behavioural check while withdrawing the guarantee.
#
# ``clear`` and ``pop`` are here because a container-shaped store is the likely
# accident — someone reaches for the collections idiom and takes the mutating
# half of it with them.
MUTATING_NAMES: frozenset[str] = frozenset(
    {
        "update",
        "delete",
        "remove",
        "clear",
        "pop",
        "insert",
        "replace",
        "truncate",
        "drop",
        "__setitem__",
        "__delitem__",
    }
)


@runtime_checkable
class EventStore(Protocol):
    """The surface an event store offers.

    Every read names a tenant. There is deliberately no method that returns
    records across tenants, and no method that changes one.
    """

    def append(self, event: Event) -> Event:
        """Record one event, or refuse it and change nothing."""

    def extend(self, events: Iterable[Event]) -> tuple[Event, ...]:
        """Append several events, reporting the first that is refused."""

    def read(
        self,
        tenant_id: str,
        *,
        job_id: str | None = None,
        subject_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]:
        """One tenant's events, in append order."""

    def payloads(self, tenant_id: str, **filters: str | None) -> list[dict]:
        """The same trail in ``event/v1`` wire shape."""

    def tenants(self) -> tuple[str, ...]:
        """Tenant identifiers only — never anything from inside a partition."""

    def count(self, tenant_id: str) -> int:
        """How many records one tenant holds."""

    def digest(self, tenant_id: str) -> str:
        """A hash chain over one tenant's append order."""

    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[str]: ...


__all__ = ["EventStore", "Obligation", "OBLIGATIONS", "MUTATING_NAMES"]
