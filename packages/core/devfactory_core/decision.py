"""Governance decisions — the interface RFC-0002 specifies.

Canonical spec: ``rfcs/0002-governance-decision-contract.md``. This repository
owns the *semantics* of a decision (the vocabulary and the guarantees); the wire
shape is ``approval/v1`` in ``agent-platform``, which owns field names, types and
structure — RFC-0005 Rule 1. :meth:`Decision.as_payload` therefore renders to
*their* field names rather than inventing ours, and this module deliberately does
not validate: owning a copy of the schema here would be a parallel schema, which
Rule 4 forbids. It builds the payload; the contract judges it.

Where the two names differ it is always an *identifier*, and that is a rule rather
than a coincidence — see :data:`WIRE_FIELD_NAMES`:

====================================  ==========================
here (semantics)                      ``approval/v1`` (wire)
====================================  ==========================
``Decision.decision_id``              ``approval_id``
``Decision.supersedes_decision_id``   ``supersedes_approval_id``
``Decision.decision``                 ``decision``
``Decision.subject``                  ``subject`` — ``{type, id}``
====================================  ==========================

An approval *is* a decision to us and a record to them; keeping our own name and
mapping it at the boundary is what the authority split looks like in code. The
table above is prose for a reader; :data:`WIRE_FIELD_NAMES` is the same map as
data, and it — not this table — is what :meth:`Decision.as_payload` renders
through, so the next rename is one line rather than a search.

What a decision may **not** do is move a job somewhere the lifecycle does not
already go. The map from decision to destination lives in :mod:`.states`
(``DECISION_TARGET``) precisely so that it is checked against the transition
table rather than expressed twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from .errors import IncompleteDecision
from .identity import Principal, validate_id


class DecisionType(str, Enum):
    """RFC-0002's decision vocabulary — a **closed** set.

    ``contract-semantics.yaml`` marks this closed on purpose, unlike
    :class:`~devfactory_core.events.EventType`, which RFC-0009 opened. The
    asymmetry is deliberate: a new approval outcome (``AUTO_APPROVE``,
    ``APPROVE_WITH_CONDITIONS`` with nobody checking the conditions) can let
    execution proceed without a human ``APPROVE`` *by adding a value*, which is
    the one thing Rule 2 exists to stop. A new event type only adds something to
    observe.

    All three values are declared because the set is closed: declaring two of
    three would quietly narrow the contract this repository publishes. All three
    are also executable since RFC-0011 gave ``REQUIRE_CHANGES`` a destination —
    see ``states.DECISION_TARGET``, which is also where the reason a
    ``REQUIRE_CHANGES`` stays distinguishable from a ``REJECT`` is written down.

    ``str`` mixin so a decision serialises as its own name.
    """

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUIRE_CHANGES = "REQUIRE_CHANGES"


#: ``approval/v1`` ``subject.type`` — what a decision can be *about*. The enum is
#: agent-platform's (Rule 1); it is mirrored here for the same reason
#: :mod:`.identity` mirrors the ``Id`` pattern — to refuse a malformed subject
#: before it reaches an audit record, not to own the vocabulary.
SUBJECT_TYPES: frozenset[str] = frozenset(
    {"job", "execution", "tool_call", "artifact", "deployment"}
)

SubjectTypeName = Literal["job", "execution", "tool_call", "artifact", "deployment"]


#: Our attribute name -> ``approval/v1``'s field name, for the fields where the two
#: differ. The single place the mapping is written as data; :meth:`Decision.as_payload`
#: is the only caller, so renaming a wire field is a one-line change here rather than a
#: hunt through the renderer.
#:
#: Both entries are identifiers, and that is the whole rule: **an id keeps our name
#: internally and takes theirs on the wire** — RFC-0005 Rule 1 gives ``agent-platform``
#: the field names and this repository the semantics. A decision is what we record; an
#: approval is what they receive; ``supersedes_decision_id`` therefore stays
#: ``supersedes_decision_id`` in Python for the same reason ``decision_id`` does, and
#: becomes ``supersedes_approval_id`` at the boundary for the same reason too. Their
#: name is also the more accurate one on their side of it — the value really is an
#: ``approval_id``, since ``decision`` there is an enum with no id to point at, and
#: ``decision`` already means the machine's verdict in ``policy/v1``.
WIRE_FIELD_NAMES: dict[str, str] = {
    "decision_id": "approval_id",
    "supersedes_decision_id": "supersedes_approval_id",
}


def new_decision_id() -> str:
    """A fresh decision id in the identity/v1 ``Id`` form.

    ``uuid4().hex`` is 32 lowercase hex characters, which satisfies the pattern
    without needing to be reshaped.
    """
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Subject:
    """What a decision is about — ``approval/v1`` ``subject``.

    A decision that cannot say what it decided about is not auditable, which is
    why both halves are required and neither has a default.
    """

    type: SubjectTypeName
    id: str

    def __post_init__(self) -> None:
        if self.type not in SUBJECT_TYPES:
            raise ValueError(
                f"subject type must be one of {sorted(SUBJECT_TYPES)} — got {self.type!r}"
            )
        validate_id("subject.id", self.id)

    def as_payload(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id}


@dataclass(frozen=True, slots=True)
class Decision:
    """One governance decision. Frozen — RFC-0002 guarantees decisions are immutable.

    "Immutable" is enforced here rather than documented: changing your mind is a
    *new* decision that cites the one it replaces, which is what
    ``supersedes_decision_id`` carries — ``supersedes_approval_id`` on the wire.

    RFC-0002's four required meanings map to ``decision``, ``reason``,
    ``authority`` and ``decided_at``. ``tenant_id`` is required by RFC-0006 and
    carries the invariant that a decision lives in the same tenant as the thing
    it decides about — checked by the engine, never coerced.
    """

    decision_id: str
    tenant_id: str
    subject: Subject
    decision: DecisionType
    reason: str
    authority: Principal
    decided_at: datetime
    workspace_id: str | None = None

    #: When this decision stops authorising anything — ``approval/v1``
    #: ``expires_at``.
    #:
    #: Optional there and optional here, which is the whole of what the contract
    #: says: an approval with no expiry never expires, and requiring one would
    #: make this engine stricter than the contract it conforms to. What is *not*
    #: optional is the meaning when it is present — "approval ที่หมดอายุแล้วใช้เดินงาน
    #: ไม่ได้ ต้องขอใหม่" — which :class:`~devfactory_core.job.Job` enforces by
    #: refusing to move a job into execution under an expired one.
    expires_at: datetime | None = None

    #: The decision this one replaces — ``approval/v1`` ``supersedes_approval_id``.
    #:
    #: ``approval/v1`` states the guarantee — "การเปลี่ยนใจคือ approval ใบใหม่ที่
    #: อ้างใบเดิม" — and for a while the schema had nowhere to put the citation, so
    #: this repository invented ``supersedes_decision_id`` and registered the gap.
    #: Since ``approval/v1`` v1.1.0 (agent-platform @ 3a01ab9) the field is theirs
    #: and is called ``supersedes_approval_id``; the gap is closed and what remained
    #: was the rename, as predicted.
    #:
    #: Only the wire name changed. The attribute keeps ours because the value is a
    #: ``decision_id`` on this side of the boundary — see :data:`WIRE_FIELD_NAMES`
    #: for why an id is spelled twice on purpose.
    #:
    #: Optional, and absent means *"claims to be the first"* rather than *"nobody
    #: changed their mind"* — the schema is explicit about that reading. The
    #: invariants it names but cannot check (the cited approval exists, in the same
    #: tenant, about the same subject, never itself, never a cycle) are the
    #: producer's. :meth:`devfactory_core.job.Job.decide` normally fills the citation
    #: from the job's own decision list, which satisfies all five by construction; a
    #: value passed in explicitly is not checked against them yet.
    supersedes_decision_id: str | None = None

    def __post_init__(self) -> None:
        validate_id("decision_id", self.decision_id)
        validate_id("tenant_id", self.tenant_id)
        if self.workspace_id is not None:
            validate_id("workspace_id", self.workspace_id)
        if self.supersedes_decision_id is not None:
            validate_id("supersedes_decision_id", self.supersedes_decision_id)
        if not isinstance(self.subject, Subject):
            raise IncompleteDecision("subject")
        # A plain string is accepted the way ``transition()`` accepts one, and an
        # unknown value raises ValueError rather than being kept as-is: the
        # vocabulary is closed, so there is no "unrecognised but keep it" case
        # here of the kind event/v1 requires for event types.
        object.__setattr__(self, "decision", DecisionType(self.decision))
        if not isinstance(self.authority, Principal):
            raise IncompleteDecision("authority")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise IncompleteDecision("reason")
        if not isinstance(self.decided_at, datetime):
            raise IncompleteDecision("decided_at")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime):
                raise ValueError(
                    f"expires_at must be a datetime — got {type(self.expires_at).__name__}"
                )
            # ``approval/v1`` types it ``format: date-time``, which is RFC 3339 and
            # carries an offset. A naive value cannot be compared against the
            # engine's clock without assuming a zone, and assuming one is how an
            # approval silently expires at the wrong moment.
            if self.expires_at.tzinfo is None:
                raise ValueError(
                    "expires_at must be timezone-aware — an approval that expires at "
                    "an unstated offset expires at a different time for every reader"
                )

    def is_expired(self, now: datetime) -> bool:
        """Whether this decision no longer authorises anything, as of ``now``.

        An approval with no ``expires_at`` is never expired: the field is optional
        in ``approval/v1``, and absent means "no deadline was set", not "expired".

        The boundary is inclusive — at the instant named the approval is already
        spent. ``expires_at`` is the moment it stops being usable, not the last
        moment it can be used, and rounding that in the permissive direction would
        let a job start on an approval that had run out.
        """
        return self.expires_at is not None and now >= self.expires_at

    def as_payload(self) -> dict[str, Any]:
        """Render to the ``approval/v1`` wire shape.

        Keys are omitted when unset rather than sent as null or an empty string —
        RFC-0008's rule against inventing a value to satisfy a field applies to
        every payload this repository produces, not only to events.
        """
        payload: dict[str, Any] = {
            # agent-platform's name for it (RFC-0005 Rule 1) — ours is decision_id.
            WIRE_FIELD_NAMES["decision_id"]: self.decision_id,
            "tenant_id": self.tenant_id,
            "subject": self.subject.as_payload(),
            "decision": self.decision.value,
            "reason": self.reason,
            "authority": self.authority.as_payload(),
            "decided_at": self.decided_at.isoformat(),
        }
        if self.workspace_id is not None:
            payload["workspace_id"] = self.workspace_id
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at.isoformat()
        if self.supersedes_decision_id is not None:
            # Theirs since approval/v1 v1.1.0 — see WIRE_FIELD_NAMES.
            payload[WIRE_FIELD_NAMES["supersedes_decision_id"]] = (
                self.supersedes_decision_id
            )
        return payload

    def __repr__(self) -> str:
        return (
            f"Decision(decision={self.decision.value}, "
            f"subject={self.subject.type}:{self.subject.id}, "
            f"authority={self.authority.id!r})"
        )
