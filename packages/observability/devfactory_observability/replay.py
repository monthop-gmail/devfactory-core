"""Rebuild a job's state from its audit trail, and refuse a trail that cannot.

[RFC-0003](../../../rfcs/0003-audit-event-log-schema.md) lists *"enable replayable
job history"* as a goal of the event log. This module is that goal made
checkable: hand it the events for one job and it returns where the job ended up,
having derived it from nothing but what was written down.

Two things make that worth having, and neither is "a second state machine".

**It is a completeness proof, not a convenience.** Every ``STATE_TRANSITION``
names the state it left as well as the one it entered. Replay walks the trail
holding a running state and compares: if a transition claims to leave a state the
replay is not standing in, a record between the two was never written, or the
records are out of order. The engine cannot detect that — it wrote them — so the
check has to live here, on the reading side. A trail that replays cleanly is a
trail with no gaps in it.

**It re-checks the guarantees against what was actually written.** The engine
enforces "execution is forbidden before ``APPROVED``" as it moves a job. That is
a claim about the log too, and this replays it: reaching a post-approval state
with no ``APPROVE`` in the trail before it raises
:class:`~devfactory_observability.errors.UnauditedExecution`, whatever the engine
believed at the time. Reaching one *after* the recorded approval's ``expires_at``
raises :class:`~devfactory_observability.errors.ExecutionAfterExpiry` for the same
reason — the deadline and the moment are both in the trail, so "this job ran on an
approval that had lapsed" is a question the log can answer about itself.

What this module does **not** own is the lifecycle. Every edge it accepts is
checked against ``devfactory_core.states.reachable_from`` — the same call the
engine makes — so there is no second copy of the transition table here and no way
for one to drift in. An edge the table does not declare is refused rather than
followed, even though following it would be easier.

Events this repository did not emit are skipped rather than interpreted: RFC-0008
keeps an external event identifiable as external forever, and an outside system
is not an authority on our lifecycle. Unrecognised event types are skipped for
the reason ``event/v1`` gives — keep it, do not interpret it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable

from devfactory_core.decision import DecisionType
from devfactory_core.events import INTERNAL_SOURCE, Event, EventType
from devfactory_core.states import (
    DECISION_BY_EDGE,
    POST_APPROVAL,
    TERMINAL,
    JobState,
    reachable_from,
)

from .errors import (
    BrokenTrail,
    EmptyTrail,
    ExecutionAfterExpiry,
    IncompleteSettlement,
    UnauditedDecision,
    UnauditedExecution,
    UndeclaredTransition,
    UnstartedTrail,
)

if TYPE_CHECKING:  # pragma: no cover
    from .store import EventLog


@dataclass(frozen=True, slots=True)
class ReplayedTransition:
    """One transition as the trail recorded it.

    Deliberately shaped like ``devfactory_core.job.TransitionRecord`` without
    being it: this one is reconstructed rather than remembered, and the two being
    comparable is the whole point of the exercise.
    """

    from_state: JobState
    to_state: JobState
    at: datetime
    reason: str | None
    event_id: str
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class ReplayedJob:
    """A job as its audit trail alone describes it."""

    job_id: str
    tenant_id: str
    workspace_id: str | None
    supersedes_job_id: str | None
    state: JobState
    awaiting_from: JobState | None
    #: The APPROVE this job was last executing under, by id. Cleared by a REJECT,
    #: exactly as the engine clears it — an approval granted to an earlier
    #: revision must not appear to authorise the revised one when read back.
    approval_decision_id: str | None
    #: When that approval stops authorising anything, as the trail recorded it, or
    #: ``None`` when it carried no deadline. Read back rather than assumed: the
    #: whole point of ``expires_at`` is that the log can be judged against it
    #: later, and a replay that dropped it could not do the judging.
    approval_expires_at: datetime | None
    decision_ids: tuple[str, ...]
    history: tuple[ReplayedTransition, ...]
    #: Whether the trail carries the ``JOB_COMPLETED`` that COMPLETED implies.
    #: Always agrees with ``state`` — a trail where it did not raises
    #: :class:`IncompleteSettlement` rather than returning the disagreement.
    completed: bool

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def states_visited(self) -> tuple[JobState, ...]:
        """DRAFT and then every state entered, in order."""
        return (JobState.DRAFT,) + tuple(t.to_state for t in self.history)


def is_ours(event: Event) -> bool:
    """Whether this repository emitted the event.

    ``source`` is unset on events the engine emits and filled in on the wire; an
    event that arrived through intake always carries ``kind: external``.
    """
    return (event.source or INTERNAL_SOURCE).get("kind") == "internal"


def _approval_of(event: Event) -> dict[str, Any] | None:
    """The approval payload a ``GOVERNANCE_DECISION`` carries, if it carries one."""
    approval = (event.metadata or {}).get("approval")
    if not isinstance(approval, dict):
        return None
    if not approval.get("approval_id") or not approval.get("decision"):
        return None
    return approval


def _expiry_of(approval: dict[str, Any] | None) -> datetime | None:
    """The ``expires_at`` an approval payload carries, if it carries a usable one.

    Unparseable or zone-less values are treated as absent rather than raising.
    ``approval/v1`` types the field ``format: date-time`` and the conformance
    check validates it there — a replay is not a second schema (RFC-0005 Rule 4),
    and a malformed deadline is a finding about the *payload*, which the validator
    already makes, not about whether the job moved when it should not have.
    """
    if approval is None:
        return None
    raw = approval.get("expires_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def replay_job(events: Iterable[Event]) -> ReplayedJob:
    """Reconstruct one job from its events, in the order they were logged.

    The order is taken as given rather than sorted. The log is append-only and
    keeps write order, and re-sorting by ``occurred_at`` would quietly repair a
    trail that arrived scrambled — which is a thing worth reporting, not fixing.
    An out-of-order trail surfaces as :class:`BrokenTrail`.
    """
    trail = [event for event in events if is_ours(event)]
    if not trail:
        raise EmptyTrail()

    creation = trail[0]
    if creation.type_value != EventType.JOB_CREATED.value:
        raise UnstartedTrail(creation.job_id, creation.type_value)
    if creation.job_id is None:
        raise EmptyTrail()

    job_id = creation.job_id
    state = JobState.DRAFT
    awaiting_from: JobState | None = None
    approval_decision_id: str | None = None
    approval_expires_at: datetime | None = None
    pending: dict[str, Any] | None = None
    decision_ids: list[str] = []
    history: list[ReplayedTransition] = []
    completed = False

    for event in trail[1:]:
        if event.job_id != job_id:
            raise BrokenTrail(job_id, job_id, str(event.job_id), event.event_id)
        kind = event.type_value

        if kind == EventType.GOVERNANCE_DECISION.value:
            approval = _approval_of(event)
            if approval is not None:
                pending = approval
                decision_ids.append(approval["approval_id"])
            continue

        if kind == EventType.JOB_COMPLETED.value:
            completed = True
            continue

        if kind != EventType.STATE_TRANSITION.value:
            # Everything else is something to keep, not something to interpret.
            continue

        payload = event.transition or {}
        recorded_from, recorded_to = payload.get("from"), payload.get("to")
        if not recorded_from or not recorded_to:
            raise BrokenTrail(job_id, state.value, str(recorded_from), event.event_id)

        from_state, to_state = JobState(recorded_from), JobState(recorded_to)
        if from_state is not state:
            raise BrokenTrail(job_id, state.value, from_state.value, event.event_id)
        if to_state not in reachable_from(state, awaiting_from=awaiting_from):
            raise UndeclaredTransition(
                job_id, from_state.value, to_state.value, event.event_id
            )

        # Whether this edge is a decision, read off ``states.py`` by edge rather
        # than by destination — RFC-0011 gave DRAFT two ways in and only the one
        # from the gate is a verdict.
        decided = DECISION_BY_EDGE.get((state, to_state))
        decision_id = _settle_decision(job_id, decided, to_state, pending, event.event_id)
        settled = pending if decision_id is not None else None
        if decision_id is not None:
            pending = None
        if decided is DecisionType.APPROVE:
            approval_decision_id = decision_id
            approval_expires_at = _expiry_of(settled)
        elif decided is not None:
            # A REJECT or a REQUIRE_CHANGES: read back, neither leaves an approval
            # in force, exactly as neither does in the engine.
            approval_decision_id = None
            approval_expires_at = None

        # The direction lock, checked against the record rather than the engine.
        if to_state in POST_APPROVAL and approval_decision_id is None:
            raise UnauditedExecution(job_id, to_state.value, event.event_id)
        # And its second half: the approval must still have been in force when the
        # record says the job moved. Both timestamps are in the trail, so this is
        # answerable from the log alone — which is what makes it worth checking
        # here as well as in the engine.
        if (
            to_state in POST_APPROVAL
            and approval_expires_at is not None
            and event.occurred_at >= approval_expires_at
        ):
            raise ExecutionAfterExpiry(
                job_id,
                to_state.value,
                event.event_id,
                expired_at=approval_expires_at.isoformat(),
                occurred_at=event.occurred_at.isoformat(),
            )

        if to_state is JobState.AWAITING_APPROVAL:
            awaiting_from = state
        elif state is JobState.AWAITING_APPROVAL:
            awaiting_from = None

        history.append(
            ReplayedTransition(
                from_state=from_state,
                to_state=to_state,
                at=event.occurred_at,
                reason=payload.get("reason"),
                event_id=event.event_id,
                decision_id=decision_id,
            )
        )
        state = to_state

    if completed is not (state is JobState.COMPLETED):
        raise IncompleteSettlement(job_id, state.value, announced=completed)

    return ReplayedJob(
        job_id=job_id,
        tenant_id=creation.tenant_id,
        workspace_id=creation.workspace_id,
        supersedes_job_id=(creation.metadata or {}).get("supersedes_job_id"),
        state=state,
        awaiting_from=awaiting_from,
        approval_decision_id=approval_decision_id,
        approval_expires_at=approval_expires_at,
        decision_ids=tuple(decision_ids),
        history=tuple(history),
        completed=completed,
    )


def _settle_decision(
    job_id: str,
    expected: DecisionType | None,
    to_state: JobState,
    pending: dict[str, Any] | None,
    event_id: str,
) -> str | None:
    """The decision id behind this transition, or None if it needed no decision.

    Which *edges* are decisions is ``DECISION_BY_EDGE``, which lives in
    ``devfactory_core.states`` beside the table it has to agree with. Asking it
    rather than listing the decision states again is what stops this module from
    acquiring an opinion of its own about what counts as a decision — and since
    RFC-0011 it is what keeps a resubmission out of ``REJECTED`` from being read
    back as a verdict, ``REJECTED -> DRAFT`` and ``GOVERNANCE_ANALYSIS -> DRAFT``
    having the same destination and different meanings.
    """
    if expected is None:
        return None
    if pending is None:
        raise UnauditedDecision(job_id, to_state.value, event_id)
    recorded = pending["decision"]
    if DecisionType(recorded) is not expected:
        raise UnauditedDecision(job_id, to_state.value, event_id, recorded=str(recorded))
    return str(pending["approval_id"])


def replay_tenant(log: "EventLog", tenant_id: str) -> dict[str, ReplayedJob]:
    """Replay every job the log holds for one tenant.

    Events that no job caused are skipped rather than filed under a job —
    RFC-0008 rule 2, read back: absent means absent, so there is nothing here to
    attribute them to.
    """
    trails: dict[str, list[Event]] = {}
    for event in log.read(tenant_id):
        if event.job_id is None:
            continue
        trails.setdefault(event.job_id, []).append(event)
    return {job_id: replay_job(events) for job_id, events in trails.items()}
