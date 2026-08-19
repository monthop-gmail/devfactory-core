"""Reading a trail back — RFC-0003's *"enable replayable job history"*.

The end-to-end flows that exercise replay over whole journeys live in
``simulation/tests/test_e2e_flow.py`` (issue #7). What is here is what this
package owns: the boundary between a trail and everything else in the log.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from devfactory_core import Event, EventType, JobState, Principal
from devfactory_core.events import new_event_id, utc_now

from devfactory_observability import (
    BrokenTrail,
    EmptyTrail,
    EventLog,
    ExecutionAfterExpiry,
    IncompleteSettlement,
    ReplayError,
    UnauditedDecision,
    UnauditedExecution,
    UndeclaredTransition,
    UnstartedTrail,
    accept_external,
    replay_job,
    replay_tenant,
)
from devfactory_observability.replay import is_ours


def approved(job, reviewer):
    job.submit_for_governance(reason="ready")
    job.approve(authority=reviewer, reason="approved")
    return job


@pytest.fixture
def reviewer() -> Principal:
    return Principal("human", "bob")


# ---- what replay reconstructs ----------------------------------------------


def test_a_fresh_job_replays_as_draft(make_job):
    seen = replay_job(make_job().events)
    assert seen.state is JobState.DRAFT
    assert seen.history == ()
    assert seen.awaiting_from is None
    assert seen.approval_decision_id is None
    assert seen.is_terminal is False


def test_identity_comes_from_the_creation_event(make_job):
    job = make_job(job_id="job-009", tenant_id="globex", workspace_id="ws-platform")
    seen = replay_job(job.events)
    assert (seen.job_id, seen.tenant_id, seen.workspace_id) == (
        "job-009",
        "globex",
        "ws-platform",
    )
    assert seen.supersedes_job_id is None


def test_the_supersession_link_survives_the_round_trip(make_job, reviewer):
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="the plan does not work")
    replacement = job.supersede(job_id="job-002")
    assert replay_job(replacement.events).supersedes_job_id == "job-001"


def test_decisions_are_recovered_in_order(make_job, reviewer):
    job = make_job()
    job.submit_for_governance()
    job.reject(authority=reviewer, reason="no")
    job.transition(JobState.DRAFT)
    job.submit_for_governance()
    job.approve(authority=reviewer, reason="yes")
    seen = replay_job(job.events)
    assert list(seen.decision_ids) == [d.decision_id for d in job.decisions]
    assert seen.approval_decision_id == job.approval.decision_id


def test_a_rejection_clears_the_approval_on_replay_too(make_job, reviewer):
    """The engine drops a stale approval; so must anything reading the trail."""
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="failed")
    replacement = job.supersede(job_id="job-002")
    replacement.submit_for_governance()
    replacement.reject(authority=reviewer, reason="still wrong")
    seen = replay_job(replacement.events)
    assert seen.state is JobState.REJECTED
    assert seen.approval_decision_id is None


def test_states_visited_starts_at_draft(make_job, reviewer):
    job = approved(make_job(), reviewer)
    assert replay_job(job.events).states_visited == (
        JobState.DRAFT,
        JobState.GOVERNANCE_ANALYSIS,
        JobState.APPROVED,
    )


def test_a_pause_and_its_return_address_are_recovered(make_job, reviewer):
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    job.transition(JobState.IN_PROGRESS)
    job.pause_for_approval(reason="needs a human")
    paused = replay_job(job.events)
    assert paused.state is JobState.AWAITING_APPROVAL
    assert paused.awaiting_from is JobState.IN_PROGRESS

    job.resume(reason="signed off", principal=reviewer)
    resumed = replay_job(job.events)
    assert resumed.state is JobState.IN_PROGRESS
    assert resumed.awaiting_from is None


def test_a_terminal_job_replays_as_terminal(make_job):
    job = make_job()
    job.submit_for_governance()
    job.cancel(reason="stopped", principal=Principal("human", "alice"))
    seen = replay_job(job.events)
    assert seen.state is JobState.CANCELLED
    assert seen.is_terminal
    assert seen.history[-1].reason == "stopped"


# ---- what replay refuses ---------------------------------------------------


def test_an_empty_trail_is_refused():
    with pytest.raises(EmptyTrail):
        replay_job([])


def test_a_trail_of_nothing_but_external_events_is_refused(external):
    with pytest.raises(EmptyTrail):
        replay_job([accept_external(external())])


def test_a_trail_that_does_not_start_at_creation_is_refused(make_job):
    job = make_job()
    job.submit_for_governance()
    with pytest.raises(UnstartedTrail) as excinfo:
        replay_job(job.events[1:])
    assert excinfo.value.first_event_type == "STATE_TRANSITION"


def test_a_gap_in_the_trail_is_refused(make_job, reviewer):
    job = approved(make_job(), reviewer)
    without_submission = [job.events[0], *job.events[2:]]
    with pytest.raises(BrokenTrail) as excinfo:
        replay_job(without_submission)
    assert (excinfo.value.expected, excinfo.value.recorded) == (
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
    )


def test_a_transition_event_with_no_transition_payload_is_refused(make_job):
    job = make_job()
    job.submit_for_governance()
    stripped = [
        dataclasses.replace(e, transition=None)
        if e.type_value == "STATE_TRANSITION"
        else e
        for e in job.events
    ]
    with pytest.raises(BrokenTrail):
        replay_job(stripped)


def test_an_edge_the_table_does_not_declare_is_refused(make_job):
    job = make_job()
    job.submit_for_governance()
    forged = [
        dataclasses.replace(e, transition={"from": "DRAFT", "to": "DEPLOYABLE"})
        if e.type_value == "STATE_TRANSITION"
        else e
        for e in job.events
    ]
    with pytest.raises(UndeclaredTransition) as excinfo:
        replay_job(forged)
    assert excinfo.value.to_state == "DEPLOYABLE"


def test_another_jobs_event_in_the_trail_is_refused(make_job):
    mine, theirs = make_job(job_id="job-001"), make_job(job_id="job-002")
    theirs.submit_for_governance()
    with pytest.raises(BrokenTrail):
        replay_job([*mine.events, theirs.events[-1]])


def test_entering_approved_with_no_decision_is_refused(make_job, reviewer):
    job = approved(make_job(), reviewer)
    with pytest.raises(UnauditedDecision) as excinfo:
        replay_job([e for e in job.events if e.type_value != "GOVERNANCE_DECISION"])
    assert excinfo.value.recorded is None


def test_a_decision_event_carrying_no_approval_counts_as_no_decision(
    make_job, reviewer
):
    job = approved(make_job(), reviewer)
    hollow = [
        dataclasses.replace(e, metadata={})
        if e.type_value == "GOVERNANCE_DECISION"
        else e
        for e in job.events
    ]
    with pytest.raises(UnauditedDecision):
        replay_job(hollow)


def test_a_decision_that_does_not_produce_the_transition_is_refused(make_job, reviewer):
    job = approved(make_job(), reviewer)
    forged = [
        dataclasses.replace(
            e,
            metadata={
                "approval": {**e.metadata["approval"], "decision": "REQUIRE_CHANGES"}
            },
        )
        if e.type_value == "GOVERNANCE_DECISION"
        else e
        for e in job.events
    ]
    with pytest.raises(UnauditedDecision) as excinfo:
        replay_job(forged)
    assert excinfo.value.recorded == "REQUIRE_CHANGES"


def test_execution_with_no_approve_behind_it_is_refused(make_job, reviewer, monkeypatch):
    """The direction lock as a backstop, the way ``job.py`` keeps its own.

    A table-consistent trail cannot reach this: ``TASK_PLANNING`` is only
    reachable from ``APPROVED``, and ``APPROVED`` is refused without a decision.
    Emptying the decision map is how a wrongly edited table would look from here,
    and the point is that the lock still holds when it happens.
    """
    from devfactory_observability import replay as replay_module

    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    monkeypatch.setattr(replay_module, "DECISION_BY_EDGE", {})
    with pytest.raises(UnauditedExecution) as excinfo:
        replay_job(job.events)
    assert excinfo.value.state == "TASK_PLANNING"


def test_a_require_changes_replays_as_a_decision_that_cleared_the_approval(
    make_job, reviewer
):
    """RFC-0011, read back: the verdict is in the trail and so is its effect."""
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="the approved plan does not work")
    revised = job.supersede(job_id="job-001b")
    revised.submit_for_governance(reason="revised plan")
    revised.require_changes(authority=reviewer, reason="still needs a test plan")

    seen = replay_job(revised.events)
    assert seen.state is JobState.DRAFT
    assert seen.decision_ids == (revised.decisions[0].decision_id,)
    assert seen.history[-1].decision_id == revised.decisions[0].decision_id
    assert seen.approval_decision_id is None


def test_the_revision_step_out_of_rejected_is_not_read_as_a_decision(
    make_job, reviewer
):
    """``REJECTED -> DRAFT`` and ``GOVERNANCE_ANALYSIS -> DRAFT`` share a
    destination and are different moves. Reading by destination would demand a
    ``GOVERNANCE_DECISION`` here for a verdict nobody made, and refuse a trail that
    is entirely correct.
    """
    job = make_job()
    job.submit_for_governance(reason="ready")
    job.reject(authority=reviewer, reason="out of scope")
    job.transition(JobState.DRAFT)

    seen = replay_job(job.events)
    assert seen.state is JobState.DRAFT
    assert len(seen.decision_ids) == 1
    assert seen.history[-1].decision_id is None


def test_a_completion_the_transitions_do_not_reach_is_refused(make_job):
    """The one truncation replay can notice — see ``IncompleteSettlement``."""
    job = make_job()
    announcement = Event(
        event_id=new_event_id(),
        event_type=EventType.JOB_COMPLETED,
        tenant_id=job.tenant_id,
        subject_type="job",
        subject_id=job.job_id,
        job_id=job.job_id,
        occurred_at=utc_now(),
    )
    with pytest.raises(IncompleteSettlement) as excinfo:
        replay_job([*job.events, announcement])
    assert excinfo.value.announced is True


def test_a_completion_with_nothing_announcing_it_is_refused(make_job, reviewer):
    job = approved(make_job(), reviewer)
    for target in (
        JobState.TASK_PLANNING,
        JobState.IN_PROGRESS,
        JobState.VALIDATING,
        JobState.DEPLOYABLE,
        JobState.COMPLETED,
    ):
        job.transition(target)
    silent = [e for e in job.events if e.type_value != "JOB_COMPLETED"]
    with pytest.raises(IncompleteSettlement) as excinfo:
        replay_job(silent)
    assert excinfo.value.announced is False


def test_a_creation_event_with_no_job_is_not_a_job_trail(make_job):
    """``job_id`` is never fabricated, so an absent one has nothing to replay."""
    job = make_job()
    with pytest.raises(EmptyTrail):
        replay_job([dataclasses.replace(job.events[0], job_id=None)])


def test_every_refusal_is_a_replay_error(make_job):
    with pytest.raises(ReplayError):
        replay_job(make_job().events[1:])


# ---- what replay leaves alone ----------------------------------------------


def test_an_event_type_replay_does_not_know_is_kept_not_interpreted(
    make_job, external
):
    """``event/v1``: keep it, skip interpreting it. Skipping is not failing."""
    job = make_job()
    job.submit_for_governance()
    noise = dataclasses.replace(
        job.events[-1],
        event_id=new_event_id(),
        event_type="TASK_ASSIGNED",
        transition=None,
    )
    assert replay_job([*job.events, noise]).state is JobState.GOVERNANCE_ANALYSIS


def test_an_external_event_is_not_an_authority_on_our_lifecycle(make_job, external):
    """RFC-0008 keeps an external event identifiable as external forever.

    A forged ``STATE_TRANSITION`` from another system is kept in the log and has
    no effect on what this repository says its own job did.
    """
    job = make_job()
    job.submit_for_governance()
    intruder = accept_external(
        external(
            event_type="STATE_TRANSITION",
            subject_type="job",
            subject_id=job.job_id,
            job_id=job.job_id,
        )
    )
    assert is_ours(intruder) is False
    assert replay_job([*job.events, intruder]).state is JobState.GOVERNANCE_ANALYSIS


def test_our_own_events_are_ours(make_job):
    assert all(is_ours(e) for e in make_job().events)


# ---- reading a whole partition ---------------------------------------------


def test_replay_tenant_returns_one_entry_per_job(make_job, reviewer):
    log = EventLog()
    first = approved(make_job(job_id="job-001"), reviewer)
    second = make_job(job_id="job-002")
    log.extend(first.events)
    log.extend(second.events)

    replayed = replay_tenant(log, "acme")
    assert set(replayed) == {"job-001", "job-002"}
    assert replayed["job-001"].state is JobState.APPROVED
    assert replayed["job-002"].state is JobState.DRAFT


def test_replay_tenant_skips_events_no_job_caused(make_job, external):
    log = EventLog()
    job = make_job()
    log.extend(job.events)
    log.append(accept_external(external()))
    assert set(replay_tenant(log, "acme")) == {job.job_id}


def test_replay_tenant_reads_only_the_tenant_it_names(make_job, reviewer):
    log = EventLog()
    log.extend(make_job(job_id="job-001", tenant_id="acme").events)
    log.extend(make_job(job_id="job-002", tenant_id="globex").events)
    assert set(replay_tenant(log, "acme")) == {"job-001"}
    assert set(replay_tenant(log, "globex")) == {"job-002"}


def test_an_unknown_tenant_replays_as_nothing(make_job):
    assert replay_tenant(EventLog(), "nobody") == {}


# ---- approval expiry, read back off the log --------------------------------
# The engine refuses to move a job on a lapsed approval. That is a claim about the
# log too: the deadline and the moment are both recorded, so a reader who was not
# there can check it. Issue #17 · RFC-0007 Amendment 1.

EXPIRY_PAST = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
EXPIRY_FAR_FUTURE = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _recorded_expiry(events, expires_at):
    """The same trail, with the recorded approval carrying ``expires_at``.

    Forged rather than produced, because the engine will not produce it — which is
    the whole reason replay checks the claim independently.
    """
    return [
        dataclasses.replace(
            event,
            metadata={
                **event.metadata,
                "approval": {**event.metadata["approval"], "expires_at": expires_at},
            },
        )
        if event.type_value == EventType.GOVERNANCE_DECISION.value
        else event
        for event in events
    ]


def test_replay_recovers_the_deadline_the_approval_was_granted_with(make_job, reviewer):
    job = make_job()
    job.submit_for_governance(reason="ready")
    job.approve(authority=reviewer, reason="approved", expires_at=EXPIRY_FAR_FUTURE)
    assert replay_job(job.events).approval_expires_at == EXPIRY_FAR_FUTURE


def test_an_approval_with_no_deadline_replays_as_having_none(make_job, reviewer):
    job = approved(make_job(), reviewer)
    assert replay_job(job.events).approval_expires_at is None


def test_the_deadline_survives_the_transitions_that_follow_it(make_job, reviewer):
    job = make_job()
    job.submit_for_governance(reason="ready")
    job.approve(authority=reviewer, reason="approved", expires_at=EXPIRY_FAR_FUTURE)
    job.transition(JobState.TASK_PLANNING)
    seen = replay_job(job.events)
    assert seen.approval_expires_at == EXPIRY_FAR_FUTURE
    assert seen.state is JobState.TASK_PLANNING


def test_a_rejection_clears_the_deadline_along_with_the_approval(make_job, reviewer):
    job = make_job()
    job.submit_for_governance(reason="ready")
    job.approve(authority=reviewer, reason="approved", expires_at=EXPIRY_FAR_FUTURE)
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="the approved plan does not work")
    replacement = job.supersede(job_id="job-002")
    replacement.submit_for_governance(reason="revised")
    replacement.reject(authority=reviewer, reason="still wrong")
    seen = replay_job(replacement.events)
    assert seen.approval_decision_id is None
    assert seen.approval_expires_at is None


def test_a_trail_that_runs_work_on_a_lapsed_approval_is_refused(make_job, reviewer):
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    with pytest.raises(ExecutionAfterExpiry) as excinfo:
        replay_job(_recorded_expiry(job.events, EXPIRY_PAST.isoformat()))
    assert excinfo.value.state == "TASK_PLANNING"
    assert excinfo.value.expired_at == EXPIRY_PAST.isoformat()


def test_a_trail_that_stops_at_approved_replays_even_with_a_lapsed_deadline(
    make_job, reviewer
):
    """Holding an expired approval is not itself a defect in the log.

    The job sat in ``APPROVED`` and never used it. What the trail must not show is
    the job *moving* on it — so replay accepts this and refuses the one above.
    """
    seen = replay_job(
        _recorded_expiry(approved(make_job(), reviewer).events, EXPIRY_PAST.isoformat())
    )
    assert seen.state is JobState.APPROVED
    assert seen.approval_expires_at == EXPIRY_PAST


def test_the_approved_to_timed_out_edge_replays(make_job, reviewer):
    """RFC-0007 Amendment 1's edge, read back off the log rather than asserted."""
    job = approved(make_job(), reviewer)
    job.time_out(reason="approval_expired — the approval lapsed before planning began")
    seen = replay_job(job.events)
    assert seen.state is JobState.TIMED_OUT
    assert seen.history[-1].from_state is JobState.APPROVED


@pytest.mark.parametrize(
    "recorded", ["not a date", "", 17, None, "2026-08-18T08:00:00"], ids=repr
)
def test_a_deadline_replay_cannot_read_is_treated_as_absent(recorded, make_job, reviewer):
    """``approval/v1`` types the field; the conformance check judges the format.

    Raising here would make this module a second schema, which RFC-0005 Rule 4
    forbids, and would turn a payload defect into a trail defect — a different
    finding about a different thing. The last case is a deadline with no offset,
    which names no moment that can be compared against a recorded one.
    """
    job = approved(make_job(), reviewer)
    job.transition(JobState.TASK_PLANNING)
    seen = replay_job(_recorded_expiry(job.events, recorded))
    assert seen.approval_expires_at is None
    assert seen.state is JobState.TASK_PLANNING
