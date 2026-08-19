"""Reading a trail back — RFC-0003's *"enable replayable job history"*.

The end-to-end flows that exercise replay over whole journeys live in
``simulation/tests/test_e2e_flow.py`` (issue #7). What is here is what this
package owns: the boundary between a trail and everything else in the log.
"""

from __future__ import annotations

import dataclasses

import pytest
from devfactory_core import Event, EventType, JobState, Principal
from devfactory_core.events import new_event_id, utc_now

from devfactory_observability import (
    BrokenTrail,
    EmptyTrail,
    EventLog,
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
    monkeypatch.setattr(replay_module, "DECISION_BY_TARGET", {})
    with pytest.raises(UnauditedExecution) as excinfo:
        replay_job(job.events)
    assert excinfo.value.state == "TASK_PLANNING"


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
