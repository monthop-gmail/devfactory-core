"""The audit trail — append-only, complete, and shaped to event/v1."""

from __future__ import annotations

import pytest

from conftest import drive
from devfactory_core import Event, EventType, Job, JobState
from devfactory_core.events import new_event_id
from devfactory_core.identity import ID_PATTERN


def _fresh(alice, clock) -> Job:
    return Job(
        job_id="job-001", tenant_id="acme", workspace_id="ws-core", principal=alice, clock=clock
    )


def test_creation_emits_job_created(alice, clock):
    job = _fresh(alice, clock)
    assert [e.event_type for e in job.events] == [EventType.JOB_CREATED]


def test_every_transition_emits_exactly_one_state_transition(alice, clock):
    """RFC-0003: no silent state change."""
    job = drive(_fresh(alice, clock), JobState.DEPLOYABLE, alice)
    transitions = [e for e in job.events if e.event_type is EventType.STATE_TRANSITION]
    assert len(transitions) == len(job.history)


def test_completion_emits_job_completed(alice, clock):
    """Still emitted, and no longer last — JOB_SETTLED closes the trail (RFC-0012)."""
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    kinds = [e.event_type for e in job.events]
    assert kinds[-2:] == [EventType.JOB_COMPLETED, EventType.JOB_SETTLED]


def test_history_and_events_are_read_only_copies(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    before = len(job.events)
    job.events  # a tuple — the caller cannot append to the live log
    assert isinstance(job.events, tuple)
    assert isinstance(job.history, tuple)
    with pytest.raises(AttributeError):
        job.events.append("forged")  # type: ignore[attr-defined]
    assert len(job.events) == before


def test_events_are_frozen(alice, clock):
    job = _fresh(alice, clock)
    with pytest.raises(Exception):
        job.events[0].tenant_id = "other"  # type: ignore[misc]


def test_refused_transition_writes_nothing(alice, clock):
    """A rejected call must not leave a trace — the log records what happened."""
    job = _fresh(alice, clock)
    before = len(job.events)
    with pytest.raises(Exception):
        job.transition(JobState.COMPLETED)
    assert len(job.events) == before
    assert job.history == ()


def test_event_ids_are_unique_and_well_formed(alice, clock):
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    ids = [e.event_id for e in job.events]
    assert len(set(ids)) == len(ids)
    assert all(ID_PATTERN.match(i) for i in ids)


def test_new_event_id_matches_identity_v1():
    assert all(ID_PATTERN.match(new_event_id()) for _ in range(100))


def test_events_are_ordered_by_occurrence(alice, clock):
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    stamps = [e.occurred_at for e in job.events]
    assert stamps == sorted(stamps)


# ---- event/v1 wire shape ---------------------------------------------------

REQUIRED = {"event_id", "event_type", "tenant_id", "subject_type", "subject_id", "occurred_at", "source"}


def test_every_payload_carries_the_required_fields(alice, clock):
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    for payload in job.event_payloads():
        assert REQUIRED <= set(payload), f"missing {REQUIRED - set(payload)}"


def test_our_events_always_carry_job_id(alice, clock):
    """RFC-0008: optional in the schema, not optional in our behaviour."""
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    assert all(p["job_id"] == "job-001" for p in job.event_payloads())


def test_subject_is_always_answerable(alice, clock):
    """Every event says what it is about — and a decision is about the approval."""
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    approvals = {d.decision_id for d in job.decisions}
    for payload in job.event_payloads():
        if payload["event_type"] == "GOVERNANCE_DECISION":
            assert payload["subject_type"] == "approval"
            assert payload["subject_id"] in approvals
        else:
            assert payload["subject_type"] == "job"
            assert payload["subject_id"] == "job-001"


def test_tenant_scope_on_every_event(alice, clock):
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    assert all(p["tenant_id"] == "acme" for p in job.event_payloads())
    assert all(p["workspace_id"] == "ws-core" for p in job.event_payloads())


def test_source_marks_events_as_internal(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    for payload in job.event_payloads():
        assert payload["source"] == {"kind": "internal", "system": "devfactory-core"}


def test_transition_payload_carries_from_and_to(alice, clock):
    job = _fresh(alice, clock)
    event = job.submit_for_governance(reason="ready")
    assert event.transition == {
        "from": "DRAFT",
        "to": "GOVERNANCE_ANALYSIS",
        "reason": "ready",
    }


def test_absent_reason_is_absent_not_empty(alice, clock):
    """RFC-0008: nothing is fabricated to fill a field. No reason means no key."""
    job = _fresh(alice, clock)
    event = job.submit_for_governance()
    assert "reason" not in event.transition


def test_optional_keys_are_omitted_when_unset(alice, clock):
    job = _fresh(alice, clock)
    payload = job.events[0].as_payload()
    assert "transition" not in payload
    assert "metadata" not in payload


def test_decision_events_name_the_authority(alice, planner, clock):
    """Every APPROVE is auditable — both records say who signed it."""
    job = _fresh(alice, clock)
    job.submit_for_governance()
    job.approve(authority=planner, reason="meets milestone")
    signed = [p["actor"]["id"] for p in job.event_payloads()[-2:]]
    assert signed == ["planner-1", "planner-1"]


def test_transition_returns_the_emitted_event(alice, clock):
    job = _fresh(alice, clock)
    event = job.submit_for_governance()
    assert isinstance(event, Event)
    assert event is job.events[-1]
    assert job.history[-1].event_id == event.event_id


def test_awaiting_approval_is_visible_in_the_trail(alice, clock):
    """The point of RFC-0007: a job waiting on a human must be visible as one."""
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    job.pause_for_approval(reason="merge needs sign-off")
    paused = [
        p for p in job.event_payloads()
        if p.get("transition", {}).get("to") == "AWAITING_APPROVAL"
    ]
    assert len(paused) == 1
    assert paused[0]["transition"]["reason"] == "merge needs sign-off"


def test_repr_is_useful(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    text = repr(job)
    assert "job-001" in text and "IN_PROGRESS" in text


def test_default_clock_is_utc():
    """Tests inject a fake clock; the real one still has to be timezone-aware."""
    from datetime import timezone

    from devfactory_core.events import utc_now

    assert utc_now().tzinfo is timezone.utc


def test_job_exposes_its_identity_and_settlement(alice, clock):
    job = _fresh(alice, clock)
    assert job.job_id == "job-001"
    assert job.principal is alice
    assert job.is_terminal is False
    drive(job, JobState.COMPLETED, alice)
    assert job.is_terminal is True


def test_the_canonical_event_types_are_declared():
    """RFC-0003's seven, plus JOB_SETTLED from RFC-0012. The engine emits five."""
    assert {t.value for t in EventType} == {
        "JOB_CREATED",
        "STATE_TRANSITION",
        "GOVERNANCE_DECISION",
        "TASK_ASSIGNED",
        "EXECUTION_STARTED",
        "EXECUTION_FAILED",
        "JOB_COMPLETED",
        "JOB_SETTLED",
    }


def test_the_state_machine_emits_only_its_own_five(alice, clock):
    """The other three belong to orchestration and execution — issue #7 and later."""
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    emitted = {e.event_type for e in job.events}
    assert emitted <= {
        EventType.JOB_CREATED,
        EventType.STATE_TRANSITION,
        EventType.GOVERNANCE_DECISION,
        EventType.JOB_COMPLETED,
        EventType.JOB_SETTLED,
    }
    assert EventType.GOVERNANCE_DECISION in emitted


# ---- the wider surface RFC-0008 needs for external events -------------------


def test_an_unrecognised_type_is_kept_not_rejected(clock):
    """event/v1: keep an unknown event_type, skip interpreting it, never drop it."""
    event = Event(
        event_id=new_event_id(),
        event_type="SIGHTING_RECORDED",
        tenant_id="acme",
        subject_type="external",
        subject_id="sighting-9",
        occurred_at=clock(),
    )
    assert event.is_recognised is False
    assert event.type_value == "SIGHTING_RECORDED"
    assert event.as_payload()["event_type"] == "SIGHTING_RECORDED"


def test_a_known_type_reports_itself_as_recognised(alice, clock):
    job = _fresh(alice, clock)
    assert job.events[0].is_recognised is True
    assert job.events[0].type_value == "JOB_CREATED"


def test_job_id_is_omitted_when_there_is_no_job(clock):
    """RFC-0008: absent means absent. Never a placeholder."""
    event = Event(
        event_id=new_event_id(),
        event_type="GEOFENCE_CROSSED",
        tenant_id="acme",
        subject_type="external",
        subject_id="device-4",
        occurred_at=clock(),
    )
    assert "job_id" not in event.as_payload()


def test_an_external_source_is_preserved(clock):
    event = Event(
        event_id=new_event_id(),
        event_type="SIGHTING_RECORDED",
        tenant_id="acme",
        subject_type="external",
        subject_id="sighting-9",
        occurred_at=clock(),
        source={"kind": "external", "system": "navi-ims"},
    )
    assert event.as_payload()["source"] == {"kind": "external", "system": "navi-ims"}


def test_our_own_events_are_stamped_internal(alice, clock):
    from devfactory_core.events import INTERNAL_SOURCE

    job = _fresh(alice, clock)
    assert job.events[0].as_payload()["source"] == INTERNAL_SOURCE


def test_correlation_id_is_carried_when_given(clock):
    event = Event(
        event_id=new_event_id(),
        event_type=EventType.JOB_CREATED,
        tenant_id="acme",
        subject_type="job",
        subject_id="job-1",
        occurred_at=clock(),
        job_id="job-1",
        correlation_id="corr-1",
    )
    assert event.as_payload()["correlation_id"] == "corr-1"
    assert "correlation_id" not in Event(
        event_id=new_event_id(),
        event_type=EventType.JOB_CREATED,
        tenant_id="acme",
        subject_type="job",
        subject_id="job-1",
        occurred_at=clock(),
        job_id="job-1",
    ).as_payload()


# ---- the closing record, RFC-0012 ------------------------------------------


@pytest.mark.parametrize(
    "terminal",
    [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT],
    ids=lambda s: s.value,
)
def test_every_terminal_closes_the_trail(terminal, alice, clock):
    """The rule with no exception in it — that is what makes it checkable."""
    job = drive(_fresh(alice, clock), terminal, alice)
    last = job.events[-1]
    assert last.event_type is EventType.JOB_SETTLED
    assert last.metadata["settled_as"] == terminal.value


def test_the_closing_record_is_last(alice, clock):
    """Its count includes itself, so nothing may follow it."""
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    settled = [i for i, e in enumerate(job.events) if e.event_type is EventType.JOB_SETTLED]
    assert settled == [len(job.events) - 1]


@pytest.mark.parametrize(
    "terminal",
    [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT],
    ids=lambda s: s.value,
)
def test_the_count_matches_the_trail(terminal, alice, clock):
    job = drive(_fresh(alice, clock), terminal, alice)
    assert job.events[-1].metadata["event_count"] == len(job.events)


def test_the_count_is_scoped_by_job_id_not_subject(alice, clock):
    """GOVERNANCE_DECISION is about the approval and carries its own subject_id.

    Counting by subject would miss it; replay groups by job_id, so that is the
    scope where producer and reader count the same set.
    """
    job = drive(_fresh(alice, clock), JobState.FAILED, alice)
    subjects = {e.subject_type for e in job.events}
    assert subjects == {"job", "approval"}, "the trail must span more than one subject"
    counted = sum(1 for e in job.events if e.job_id == job.job_id)
    assert job.events[-1].metadata["event_count"] == counted == len(job.events)


def test_a_running_job_has_no_closing_record(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    assert all(e.event_type is not EventType.JOB_SETTLED for e in job.events)


def test_rejected_is_not_a_settlement(alice, clock):
    """REJECTED returns to DRAFT, so the job has not settled and nothing closes."""
    job = drive(_fresh(alice, clock), JobState.REJECTED, alice)
    assert all(e.event_type is not EventType.JOB_SETTLED for e in job.events)


def test_the_closing_record_is_about_the_job(alice, clock):
    job = drive(_fresh(alice, clock), JobState.CANCELLED, alice)
    payload = job.events[-1].as_payload()
    assert payload["subject_type"] == "job"
    assert payload["subject_id"] == payload["job_id"] == job.job_id


def test_a_superseding_job_closes_its_own_trail(alice, clock):
    """Each attempt is a separate trail and each closes itself."""
    failed = drive(_fresh(alice, clock), JobState.FAILED, alice)
    replacement = failed.supersede(job_id="job-002")
    assert failed.events[-1].metadata["settled_as"] == "FAILED"
    assert all(e.event_type is not EventType.JOB_SETTLED for e in replacement.events)


def test_sequence_round_trips_when_present(clock):
    """event/v1 v1.3.0. We do not produce it, but we must not lose it."""
    event = Event(
        event_id=new_event_id(),
        event_type="SIGHTING_RECORDED",
        tenant_id="acme",
        subject_type="external",
        subject_id="round-1",
        occurred_at=clock(),
        sequence=2,
    )
    assert event.as_payload()["sequence"] == 2


def test_the_engine_emits_no_sequence_of_its_own(alice, clock):
    """We write one event at a time, so we have no batch order to state.

    Claiming a position we do not hold would be inventing a value, which RFC-0008
    forbids for the same reason it forbids inventing a job_id.
    """
    job = drive(_fresh(alice, clock), JobState.COMPLETED, alice)
    assert all("sequence" not in e.as_payload() for e in job.events)
