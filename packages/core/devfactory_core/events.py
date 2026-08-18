"""Audit events emitted by the state machine.

Shaped to ``event/v1`` from agent-platform so that the payloads this engine
produces are the ones a conformance test will validate (issue #6). This module
deliberately does **not** validate — owning a copy of the schema here would be a
parallel schema, which RFC-0005 Rule 4 forbids. It builds the payload; the
contract judges it.

Invariants carried from RFC-0008 and enforced by construction rather than by
convention:

* ``job_id`` is always present on events this repository emits. The field is
  optional in the schema and not optional in our behaviour.
* identifiers are never fabricated — every id here comes from a real object.
* ``metadata`` carries structured facts only. Private reasoning traces are not
  audit records and must never be placed here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .identity import Principal


class EventType(str, Enum):
    """The canonical vocabulary from RFC-0003.

    RFC-0009 made this a required minimum rather than a closed set at the
    contract level — ``agent-platform`` may add types without an RFC here. All
    seven are declared so the vocabulary lives in one place; which plane emits
    which is noted below and is not enforced by this enum.

    Emitted by the job state machine (``packages/core``):
        ``JOB_CREATED`` · ``STATE_TRANSITION`` · ``JOB_COMPLETED``

    Emitted by governance (issue #5):
        ``GOVERNANCE_DECISION``

    Emitted by orchestration and execution (issue #7 and later):
        ``TASK_ASSIGNED`` · ``EXECUTION_STARTED`` · ``EXECUTION_FAILED``
    """

    JOB_CREATED = "JOB_CREATED"
    STATE_TRANSITION = "STATE_TRANSITION"
    GOVERNANCE_DECISION = "GOVERNANCE_DECISION"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    JOB_COMPLETED = "JOB_COMPLETED"


def new_event_id() -> str:
    """A fresh event id in the identity/v1 ``Id`` form.

    ``uuid4().hex`` is 32 lowercase hex characters, which satisfies the pattern
    without needing to be reshaped.
    """
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


#: What this repository stamps on events it originates. RFC-0008: an external
#: event keeps its own source so it stays identifiable as external forever.
INTERNAL_SOURCE: dict[str, str] = {"kind": "internal", "system": "devfactory-core"}


@dataclass(frozen=True, slots=True)
class Event:
    """One audit record. Frozen — ``event/v1`` guarantees append-only.

    ``event_type`` accepts a plain string as well as an :class:`EventType`.
    ``event/v1`` instructs consumers to keep an unrecognised type and skip
    interpreting it rather than dropping or failing on it, so the type this class
    can hold has to be wider than our own vocabulary.

    ``job_id`` is optional here and **not** optional in our behaviour: RFC-0008
    allows its absence only for events no job caused, and an event this
    repository emits without one is a defect. It is never fabricated to fill the
    field.
    """

    event_id: str
    event_type: EventType | str
    tenant_id: str
    subject_type: str
    subject_id: str
    occurred_at: datetime
    job_id: str | None = None
    workspace_id: str | None = None
    actor: Principal | None = None
    correlation_id: str | None = None
    transition: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def type_value(self) -> str:
        """The wire value, whether the type is known to us or not."""
        return self.event_type.value if isinstance(self.event_type, EventType) else str(self.event_type)

    @property
    def is_recognised(self) -> bool:
        """False for a type outside our vocabulary — keep it, do not interpret it."""
        return isinstance(self.event_type, EventType)

    def as_payload(self) -> dict[str, Any]:
        """Render to the ``event/v1`` wire shape.

        Keys are omitted when unset rather than sent as null or as an empty
        string. RFC-0008 forbids inventing a value to satisfy a field, and an
        empty string is an invented value.
        """
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.type_value,
            "tenant_id": self.tenant_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "occurred_at": self.occurred_at.isoformat(),
            "source": dict(self.source) if self.source else dict(INTERNAL_SOURCE),
        }
        if self.job_id is not None:
            payload["job_id"] = self.job_id
        if self.workspace_id is not None:
            payload["workspace_id"] = self.workspace_id
        if self.actor is not None:
            payload["actor"] = self.actor.as_payload()
        if self.correlation_id is not None:
            payload["correlation_id"] = self.correlation_id
        if self.transition is not None:
            payload["transition"] = self.transition
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload
