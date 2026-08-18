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
    contract level — agent-platform may add types. These are the ones the job
    state machine itself emits.
    """

    JOB_CREATED = "JOB_CREATED"
    STATE_TRANSITION = "STATE_TRANSITION"
    JOB_COMPLETED = "JOB_COMPLETED"


def new_event_id() -> str:
    """A fresh event id in the identity/v1 ``Id`` form.

    ``uuid4().hex`` is 32 lowercase hex characters, which satisfies the pattern
    without needing to be reshaped.
    """
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Event:
    """One audit record. Frozen — ``event/v1`` guarantees append-only."""

    event_id: str
    event_type: EventType
    tenant_id: str
    subject_type: str
    subject_id: str
    occurred_at: datetime
    job_id: str
    workspace_id: str | None = None
    actor: Principal | None = None
    transition: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        """Render to the ``event/v1`` wire shape.

        ``source.kind`` is ``internal`` because this engine is the origin. An
        event arriving from another system keeps its own ``source`` — RFC-0008
        requires an external event to stay identifiable as external forever.
        """
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "tenant_id": self.tenant_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "job_id": self.job_id,
            "occurred_at": self.occurred_at.isoformat(),
            "source": {"kind": "internal", "system": "devfactory-core"},
        }
        if self.workspace_id is not None:
            payload["workspace_id"] = self.workspace_id
        if self.actor is not None:
            payload["actor"] = self.actor.as_payload()
        if self.transition is not None:
            payload["transition"] = self.transition
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload
