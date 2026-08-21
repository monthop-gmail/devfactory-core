"""Issue #7 — the end-to-end flow simulation, as a test suite.

The issue asks for "a runnable script or a test suite". This repository gets
both, because they answer different questions: ``simulation/e2e_flow.py`` is what
a person runs to watch a governed job move end to end, and this is what stops the
same guarantees from regressing without anyone watching.

Each section below is one item from the issue's list. Nothing here re-implements
the lifecycle — every flow drives the real engine, and every expectation about
which states exist or connect is read out of ``devfactory_core.states``.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import TENANT, WORKSPACE

from devfactory_core import Job, JobState
from devfactory_core.errors import (
    ExecutionBeforeApproval,
    ExpiredApproval,
    InvalidTransition,
    JobStateMachineError,
    TerminalState,
)
from devfactory_core.states import (
    DECISION_BY_EDGE,
    FAILABLE,
    POST_APPROVAL,
    SUPERSEDABLE,
    TERMINAL,
)
from devfactory_observability import (
    BrokenTrail,
    EmptyTrail,
    ExecutionAfterExpiry,
    IncompleteSettlement,
    ReplayError,
    UnauditedDecision,
    UnauditedExecution,
    UndeclaredTransition,
    UnsettledTrail,
    UnstartedTrail,
    replay_job,
    replay_tenant,
)
from simulation.flows import (
    MAIN_LINE,
    advance_to,
    approval_expired,
    approval_expired_then_resubmitted,
    cancelled_by_a_person,
    failed_at,
    happy_path,
    main_line,
    never_approved,
    rejected_then_resubmitted,
    require_changes_then_resubmitted,
    stalled_awaiting_approval,
)

ROOT = Path(__file__).resolve().parents[2]

#: An hour before the ``clock`` fixture starts, so an approval carrying it has
#: already lapsed by the time anything asks.
EXPIRED_AT = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)

#: The flow issue #7 writes out. Compared against the table rather than used to
#: drive anything — see ``test_the_forward_path_is_the_one_the_issue_asks_for``.
ISSUE_7_FLOW = (
    "DRAFT",
    "GOVERNANCE_ANALYSIS",
    "APPROVED",
    "TASK_PLANNING",
    "IN_PROGRESS",
    "VALIDATING",
    "DEPLOYABLE",
    "COMPLETED",
)


def visited(job: Job) -> tuple[str, ...]:
    return (JobState.DRAFT.value,) + tuple(h.to_state.value for h in job.history)


# ---- [1] the full flow ------------------------------------------------------


def test_the_forward_path_is_the_one_the_issue_asks_for():
    """The table and the issue agree, and the table is what gets walked.

    ``MAIN_LINE`` is derived from ``states.TRANSITIONS``. Asserting it equals the
    flow #7 spells out is the only place the issue's wording is treated as data —
    everywhere else the simulation follows the table, so this test is what would
    fail if the two drifted, rather than the simulation quietly following the
    issue and reporting success.
    """
    assert tuple(s.value for s in MAIN_LINE) == ISSUE_7_FLOW


def test_the_forward_path_is_recomputed_not_cached():
    assert main_line() == MAIN_LINE


def test_full_flow_reaches_completed(new, reviewer):
    job = happy_path(new, job_id="job-001", authority=reviewer)
    assert visited(job) == ISSUE_7_FLOW
    assert job.state is JobState.COMPLETED
    assert job.is_terminal


def test_completion_is_announced_in_the_trail(new, reviewer):
    """Both records, in this order — they answer different questions (RFC-0012).

    ``JOB_COMPLETED`` says the work was delivered; ``JOB_SETTLED`` says the record
    is finished, and closes every terminal rather than only this one.
    """
    job = happy_path(new, job_id="job-001", authority=reviewer)
    kinds = [e.type_value for e in job.events]
    assert kinds.count("JOB_COMPLETED") == 1
    assert kinds.count("JOB_SETTLED") == 1
    assert kinds[-2:] == ["JOB_COMPLETED", "JOB_SETTLED"]


def test_the_full_flow_survives_a_mid_run_approval_pause(new, reviewer):
    """RFC-0007's pause is a detour inside the path, not a different path."""
    job = happy_path(
        new, job_id="job-001", authority=reviewer, pause_in=JobState.DEPLOYABLE
    )
    assert job.state is JobState.COMPLETED
    assert job.awaiting_from is None
    assert "AWAITING_APPROVAL" in visited(job)
    # Drop the pause and the re-entry it returns to, and the same path is left.
    forward = [s for s in visited(job) if s != "AWAITING_APPROVAL"]
    resumed = [s for i, s in enumerate(forward) if i == 0 or s != forward[i - 1]]
    assert tuple(resumed) == ISSUE_7_FLOW


# ---- [2] rejection and resubmission -----------------------------------------


def test_rejected_returns_to_draft_and_can_be_resubmitted(new, reviewer):
    job = rejected_then_resubmitted(new, job_id="job-002", authority=reviewer)
    assert visited(job) == (
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "REJECTED",
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "APPROVED",
        "TASK_PLANNING",
    )


def test_resubmission_produces_a_second_decision_not_an_edited_first(new, reviewer):
    job = rejected_then_resubmitted(new, job_id="job-002", authority=reviewer)
    assert [d.decision.value for d in job.decisions] == ["REJECT", "APPROVE"]
    assert job.decisions[1].supersedes_decision_id == job.decisions[0].decision_id


def test_the_second_approval_is_what_authorises_the_work(new, reviewer):
    job = rejected_then_resubmitted(new, job_id="job-002", authority=reviewer)
    assert job.approval is not None
    assert job.approval.decision_id == job.decisions[-1].decision_id
    assert job.state is JobState.TASK_PLANNING


def test_a_rejection_does_not_leave_an_earlier_approval_behind(new, reviewer):
    """The revised job must be re-approved; the old APPROVE does not carry over."""
    approved = new("job-002b")
    approved.submit_for_governance()
    approved.approve(authority=reviewer, reason="first pass")
    approved.transition(JobState.TASK_PLANNING)
    approved.fail(reason="the approved plan does not work")

    replacement = approved.supersede(job_id="job-002c")
    replacement.submit_for_governance(reason="revised plan")
    replacement.reject(authority=reviewer, reason="still wrong")
    replacement.transition(JobState.DRAFT)
    assert replacement.approval is None
    with pytest.raises(InvalidTransition):
        replacement.transition(JobState.TASK_PLANNING)


# ---- [2b] sent back for changes, RFC-0011 -----------------------------------
# The same destination as a rejection, by a different route. Every test here is
# about whether that route survives being written down and read back, because that
# is the only thing keeping ``REQUIRE_CHANGES ไม่ใช่ REJECT`` true of the record.


def test_require_changes_returns_to_draft_without_passing_rejected(new, reviewer):
    job = require_changes_then_resubmitted(new, job_id="job-002d", authority=reviewer)
    assert visited(job) == (
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "APPROVED",
        "TASK_PLANNING",
    )
    assert "REJECTED" not in visited(job)


def test_the_two_ways_back_to_draft_leave_different_trails(new, reviewer):
    """RFC-0011's load-bearing claim, on the two flows side by side."""
    sent_back = require_changes_then_resubmitted(
        new, job_id="job-002d", authority=reviewer
    )
    rejected = rejected_then_resubmitted(new, job_id="job-002", authority=reviewer)

    assert sent_back.state is rejected.state is JobState.TASK_PLANNING
    assert visited(sent_back) != visited(rejected)
    assert "REJECTED" in visited(rejected)
    assert "REJECTED" not in visited(sent_back)
    # One hop to DRAFT rather than two: the gate sent it back itself.
    assert len(sent_back.history) == len(rejected.history) - 1


def test_replay_tells_the_two_apart_from_the_log_alone(new, reviewer):
    """A reader who was not there has only the trail. It is enough."""
    sent_back = replay_job(
        require_changes_then_resubmitted(
            new, job_id="job-002d", authority=reviewer
        ).events
    )
    rejected = replay_job(
        rejected_then_resubmitted(new, job_id="job-002", authority=reviewer).events
    )
    assert sent_back.state is rejected.state is JobState.TASK_PLANNING
    assert JobState.REJECTED not in sent_back.states_visited
    assert JobState.REJECTED in rejected.states_visited
    assert sent_back.states_visited != rejected.states_visited


def test_the_require_changes_decision_survives_the_round_trip(new, reviewer):
    job = require_changes_then_resubmitted(new, job_id="job-002d", authority=reviewer)
    seen = replay_job(job.events)
    assert [d.decision.value for d in job.decisions] == ["REQUIRE_CHANGES", "APPROVE"]
    assert list(seen.decision_ids) == [d.decision_id for d in job.decisions]
    # The approval in force is the APPROVE, not the REQUIRE_CHANGES that preceded it.
    assert seen.approval_decision_id == job.decisions[-1].decision_id


def test_a_require_changes_leaves_no_approval_behind(new, reviewer):
    job = new("job-002e")
    job.submit_for_governance()
    job.require_changes(authority=reviewer, reason="needs a test plan")
    assert job.state is JobState.DRAFT
    assert job.approval is None
    job.submit_for_governance(reason="revised")
    with pytest.raises(InvalidTransition):
        job.transition(JobState.TASK_PLANNING)


def test_a_trail_that_calls_a_require_changes_a_rejection_is_refused(new, reviewer):
    """Forge the decision record and the edge no longer matches it.

    The two halves check each other: the route says "sent back for changes" and the
    record would say "rejected", so the trail contradicts itself and replay says so
    rather than believing the record.
    """
    job = new("job-002g")
    job.submit_for_governance()
    job.require_changes(authority=reviewer, reason="needs a test plan")
    forged = [
        dataclasses.replace(
            e,
            metadata={
                **e.metadata,
                "approval": {**e.metadata["approval"], "decision": "REJECT"},
            },
        )
        if e.type_value == "GOVERNANCE_DECISION"
        else e
        for e in job.events
    ]
    with pytest.raises(UnauditedDecision) as excinfo:
        replay_job(forged)
    assert excinfo.value.recorded == "REJECT"


def test_resubmitting_after_a_rejection_needs_no_decision_of_its_own(new, reviewer):
    """``REJECTED -> DRAFT`` shares a destination with a decision and is not one."""
    job = new("job-002h")
    job.submit_for_governance()
    job.reject(authority=reviewer, reason="out of scope")
    job.transition(JobState.DRAFT)
    seen = replay_job(job.events)
    assert seen.state is JobState.DRAFT
    assert len(seen.decision_ids) == 1
    assert [t.decision_id for t in seen.history] == [None, job.decisions[0].decision_id, None]


# ---- [3] failure ------------------------------------------------------------


@pytest.mark.parametrize(
    "state", sorted(FAILABLE, key=lambda s: s.value), ids=lambda s: s.value
)
def test_failure_from_every_state_rfc_0010_allows(state, new, reviewer):
    reason = f"orchestration exhausted execution retries in {state.value}"
    job = failed_at(
        new, job_id="job-003", authority=reviewer, state=state, reason=reason
    )
    assert job.state is JobState.FAILED
    assert job.history[-1].from_state is state
    assert job.history[-1].reason == reason


@pytest.mark.parametrize(
    "state", sorted(FAILABLE, key=lambda s: s.value), ids=lambda s: s.value
)
def test_the_failure_reason_reaches_the_audit_trail(state, new, reviewer):
    reason = f"orchestration exhausted execution retries in {state.value}"
    job = failed_at(
        new, job_id="job-003", authority=reviewer, state=state, reason=reason
    )
    entering = [
        e
        for e in job.events
        if e.type_value == "STATE_TRANSITION" and e.transition["to"] == "FAILED"
    ]
    assert len(entering) == 1
    assert entering[0].transition["reason"] == reason


@pytest.mark.parametrize(
    "state",
    sorted(set(JobState) - FAILABLE - TERMINAL, key=lambda s: s.value),
    ids=lambda s: s.value,
)
def test_failure_is_refused_everywhere_else(state, new, reviewer):
    """RFC-0010: a job fails only where work exists to fail."""
    job = new("job-003-x")
    _park_in(job, state, reviewer)
    assert job.state is state
    with pytest.raises(JobStateMachineError):
        job.fail(reason="pretending there is work to fail")
    assert job.state is state


def test_a_flow_that_cannot_happen_is_refused_before_it_is_written_down(new, reviewer):
    with pytest.raises(ValueError, match="FAILABLE"):
        failed_at(
            new, job_id="job-003", authority=reviewer, state=JobState.GOVERNANCE_ANALYSIS
        )


def test_recovery_is_a_new_job_that_passes_governance_again(new, reviewer):
    origin = failed_at(
        new, job_id="job-003", authority=reviewer, state=JobState.IN_PROGRESS
    )
    replacement = origin.supersede(job_id="job-003-next")
    assert replacement.state is JobState.DRAFT
    assert replacement.supersedes_job_id == "job-003"
    with pytest.raises(InvalidTransition):
        replacement.transition(JobState.TASK_PLANNING)


def _park_in(job: Job, state: JobState, authority) -> None:
    """Put a fresh job into one of the states RFC-0010 refuses ``FAILED`` from."""
    if state is JobState.DRAFT:
        return
    job.submit_for_governance()
    if state is JobState.GOVERNANCE_ANALYSIS:
        return
    if state is JobState.REJECTED:
        job.reject(authority=authority, reason="out of scope")
        return
    job.approve(authority=authority, reason="approved")


# ---- [3b] an approval that ran out ------------------------------------------
# RFC-0007 Amendment 1 (issue #17). Not one of the issue's six items — the flow did
# not exist when #7 was written — but it belongs beside failure, because it is the
# other way a job settles without the work having gone wrong.


def test_an_approval_that_lapsed_ends_the_job_at_timed_out(new, reviewer):
    job = approval_expired(
        new, job_id="job-007", authority=reviewer, expires_at=EXPIRED_AT
    )
    assert visited(job) == ("DRAFT", "GOVERNANCE_ANALYSIS", "APPROVED", "TIMED_OUT")
    assert job.is_terminal
    assert "approval_expired" in job.history[-1].reason


def test_the_lapsed_approval_is_still_in_the_record_that_settled_the_job(
    new, reviewer
):
    """The job did not fail and was not cancelled: the approval expired, and the
    trail can say so — the deadline is on the decision it names."""
    job = approval_expired(
        new, job_id="job-007", authority=reviewer, expires_at=EXPIRED_AT
    )
    assert job.approval is not None
    assert job.approval.expires_at == EXPIRED_AT
    assert job.approval_expired is True
    assert replay_job(job.events).approval_expires_at == EXPIRED_AT


def test_the_lapsed_approval_authorises_nothing_further(new, reviewer):
    job = new("job-007b")
    job.submit_for_governance()
    job.approve(authority=reviewer, reason="approved", expires_at=EXPIRED_AT)
    with pytest.raises(ExpiredApproval):
        job.transition(JobState.TASK_PLANNING)
    assert job.state is JobState.APPROVED


def test_a_trail_showing_work_on_a_lapsed_approval_is_refused_on_replay(new, reviewer):
    """The engine's refusal, checked against the log by a reader who was not there."""
    job = new("job-007c")
    job.submit_for_governance()
    job.approve(authority=reviewer, reason="approved")
    job.transition(JobState.TASK_PLANNING)
    forged = [
        dataclasses.replace(
            e,
            metadata={
                **e.metadata,
                "approval": {**e.metadata["approval"], "expires_at": EXPIRED_AT.isoformat()},
            },
        )
        if e.type_value == "GOVERNANCE_DECISION"
        else e
        for e in job.events
    ]
    with pytest.raises(ExecutionAfterExpiry):
        replay_job(forged)


# ---- [3c] asking again, with the chain intact -------------------------------
# RFC-0007 Amendment 2 (issue #21). ``approval/v1`` asks for two things when an
# approval expires: that it stop authorising work, and that the work be asked for
# again. [3b] covers the first. Until Amendment 2 the second produced a job with no
# way to say what it was a second attempt at, because ``TIMED_OUT`` could not reach
# ``FAILED`` and ``supersedes_job_id`` was reserved for ``FAILED``.


@pytest.fixture
def asked_again(new, reviewer):
    return approval_expired_then_resubmitted(
        new,
        job_id="job-007",
        replacement_job_id="job-007-next",
        authority=reviewer,
        expires_at=EXPIRED_AT,
    )


def test_the_resubmission_names_the_job_it_replaces(asked_again):
    expired, again = asked_again
    assert expired.state is JobState.TIMED_OUT
    assert again.supersedes_job_id == expired.job_id
    assert visited(again) == ("DRAFT", "GOVERNANCE_ANALYSIS", "APPROVED", "TASK_PLANNING")


def test_the_resubmission_executes_under_a_fresh_approval(asked_again):
    """Not the lapsed one, which is still standing in the predecessor's record."""
    expired, again = asked_again
    assert again.approval.decision_id != expired.approval.decision_id
    assert again.approval.expires_at is None
    assert expired.approval.expires_at == EXPIRED_AT and expired.approval_expired


def test_the_predecessor_is_superseded_not_revived(asked_again):
    """Amendment 2 relaxed who may be referred to, not what may happen to them."""
    expired, _ = asked_again
    with pytest.raises(TerminalState):
        expired.transition(JobState.TASK_PLANNING)
    assert expired.state is JobState.TIMED_OUT


def test_replay_reads_the_whole_chain_out_of_the_log_alone(asked_again, log):
    """The audit question this closes: *why is this the second time?*"""
    expired, again = asked_again
    for job in (expired, again):
        log.extend(job.events)
    seen = replay_tenant(log, TENANT)

    chain = []
    cursor = again.job_id
    while cursor is not None and cursor not in chain:
        chain.append(cursor)
        cursor = seen[cursor].supersedes_job_id
    assert chain == ["job-007-next", "job-007"]
    assert seen["job-007"].state is JobState.TIMED_OUT
    assert seen["job-007"].approval_expires_at == EXPIRED_AT
    assert seen["job-007-next"].approval_expires_at is None


@pytest.mark.parametrize(
    "terminal", sorted(SUPERSEDABLE, key=lambda s: s.value), ids=lambda s: s.value
)
def test_every_terminal_that_did_not_deliver_can_be_superseded(terminal, new, reviewer):
    job = _settled_in(new(f"job-007-{terminal.value.lower().replace('_', '-')}"), terminal, reviewer)
    replacement = job.supersede(job_id=f"{job.job_id}-next")
    assert replacement.state is JobState.DRAFT
    assert replacement.supersedes_job_id == job.job_id


def test_a_completed_job_is_not_an_attempt_awaiting_another(new, reviewer):
    delivered = happy_path(new, job_id="job-007-done", authority=reviewer)
    assert delivered.state is JobState.COMPLETED
    with pytest.raises(InvalidTransition):
        delivered.supersede(job_id="job-007-done-again")


def test_a_job_that_has_not_settled_cannot_be_superseded(new, reviewer):
    running = advance_to(new("job-007-live"), JobState.IN_PROGRESS, authority=reviewer)
    with pytest.raises(InvalidTransition):
        running.supersede(job_id="job-007-live-next")


def _settled_in(job: Job, terminal: JobState, authority):
    """Drive a fresh job to one of the terminals a replacement may name."""
    if terminal is JobState.CANCELLED:
        job.submit_for_governance()
        job.cancel(reason="no longer wanted", principal=authority)
        return job
    advance_to(job, JobState.IN_PROGRESS, authority=authority)
    if terminal is JobState.FAILED:
        job.fail(reason="orchestration exhausted execution retries")
    else:
        job.time_out(reason="sla_exceeded — nobody answered")
    return job


# ---- [4] the governance gate ------------------------------------------------


@pytest.mark.parametrize(
    "target", sorted(POST_APPROVAL, key=lambda s: s.value), ids=lambda s: s.value
)
def test_no_execution_state_is_reachable_without_a_decision(target, new):
    job = never_approved(new, job_id="job-004")
    assert job.approval is None
    with pytest.raises(JobStateMachineError):
        job.transition(target)
    assert job.state is JobState.GOVERNANCE_ANALYSIS


def test_the_gate_is_the_record_not_the_state(new):
    """Forcing APPROVED without a ``Decision`` still does not authorise anything.

    The state alone is not the authorisation — the record is. Poking ``_state``
    simulates a wrongly edited transition table, which is the only way this guard
    can fire and the reason it exists.
    """
    job = never_approved(new, job_id="job-004b")
    job._state = JobState.APPROVED
    with pytest.raises(ExecutionBeforeApproval):
        job.transition(JobState.TASK_PLANNING)


def test_what_authorises_execution_answers_who_decided_and_why(new, reviewer):
    job = never_approved(new, job_id="job-004c")
    record = job.approve(authority=reviewer, reason="scope matches milestone v0.1")
    job.transition(JobState.TASK_PLANNING)
    assert job.approval is record
    assert record.authority.id == reviewer.id
    assert record.reason
    assert record.decided_at is not None
    assert record.subject.id == job.job_id


def test_the_gate_holds_when_the_trail_is_read_back(new, reviewer):
    """Strip the decision out of the trail and the replay refuses it."""
    job = never_approved(new, job_id="job-004c")
    job.approve(authority=reviewer, reason="approved")
    job.transition(JobState.TASK_PLANNING)

    tampered = [e for e in job.events if e.type_value != "GOVERNANCE_DECISION"]
    with pytest.raises(UnauditedDecision):
        replay_job(tampered)


def test_a_decision_that_does_not_produce_the_transition_is_refused_on_replay(
    new, reviewer
):
    job = never_approved(new, job_id="job-004d")
    job.approve(authority=reviewer, reason="approved")
    forged = [
        dataclasses.replace(
            e,
            metadata={**e.metadata, "approval": {**e.metadata["approval"], "decision": "REJECT"}},
        )
        if e.type_value == "GOVERNANCE_DECISION"
        else e
        for e in job.events
    ]
    with pytest.raises(UnauditedDecision) as excinfo:
        replay_job(forged)
    assert excinfo.value.recorded == "REJECT"


def test_the_direction_lock_is_a_backstop_on_replay_too(new, reviewer, monkeypatch):
    """If replay forgot APPROVED were a decision, execution would still be refused.

    Mirrors ``test_the_gate_is_the_record_not_the_state``: the check can only fire
    when something upstream is already wrong, and that is exactly when it earns
    its place.
    """
    from devfactory_observability import replay as replay_module

    job = never_approved(new, job_id="job-004e")
    job.approve(authority=reviewer, reason="approved")
    job.transition(JobState.TASK_PLANNING)

    monkeypatch.setattr(replay_module, "DECISION_BY_EDGE", {})
    with pytest.raises(UnauditedExecution) as excinfo:
        replay_job(job.events)
    assert excinfo.value.state == "TASK_PLANNING"


# ---- [5] every transition emits an audit event ------------------------------


@pytest.fixture
def run(new, owner, reviewer, log):
    """Every flow, driven once, with everything they emitted filed in the log."""
    jobs = [
        happy_path(new, job_id="job-001", authority=reviewer),
        happy_path(
            new, job_id="job-001b", authority=reviewer, pause_in=JobState.DEPLOYABLE
        ),
        rejected_then_resubmitted(new, job_id="job-002", authority=reviewer),
        require_changes_then_resubmitted(new, job_id="job-002d", authority=reviewer),
        failed_at(new, job_id="job-003", authority=reviewer, state=JobState.IN_PROGRESS),
        never_approved(new, job_id="job-004"),
        cancelled_by_a_person(new, job_id="job-005", owner=owner),
        stalled_awaiting_approval(new, job_id="job-006", authority=reviewer),
    ]
    failed = next(j for j in jobs if j.state is JobState.FAILED)
    jobs.append(failed.supersede(job_id="job-003-next"))
    for job in jobs:
        log.extend(job.events)
    return jobs, log


def test_every_transition_emits_exactly_one_state_transition(run):
    jobs, _ = run
    for job in jobs:
        emitted = [e for e in job.events if e.type_value == "STATE_TRANSITION"]
        assert len(emitted) == len(job.history), job.job_id


def test_every_transition_points_at_an_event_that_is_really_in_the_log(run):
    jobs, log = run
    by_id = {e.event_id: e for e in log.read(TENANT)}
    for job in jobs:
        for record in job.history:
            event = by_id[record.event_id]
            assert event.transition["from"] == record.from_state.value
            assert event.transition["to"] == record.to_state.value
            assert event.transition.get("reason") == record.reason


def test_every_decision_edge_is_accompanied_by_a_decision_event(run):
    """Read off ``states.py`` by edge, not by destination.

    Since RFC-0011 the destination alone no longer says whether a transition was a
    verdict: ``GOVERNANCE_ANALYSIS -> DRAFT`` is one and ``REJECTED -> DRAFT`` is
    not, and this counts both correctly only by asking about the edge.
    """
    jobs, _ = run
    for job in jobs:
        entries = [h for h in job.history if (h.from_state, h.to_state) in DECISION_BY_EDGE]
        emitted = [e for e in job.events if e.type_value == "GOVERNANCE_DECISION"]
        assert len(entries) == len(emitted) == len(job.decisions), job.job_id
        assert [h.decision_id for h in entries] == [d.decision_id for d in job.decisions]


def test_the_whole_run_reaches_one_tenant_partition(run):
    jobs, log = run
    assert log.tenants() == (TENANT,)
    assert len(log) == sum(len(job.events) for job in jobs)
    assert all(p["workspace_id"] == WORKSPACE for p in log.payloads(TENANT))


# ---- [6] the trail is complete and replays ----------------------------------


def test_replay_returns_every_job_the_run_produced(run):
    jobs, log = run
    assert set(replay_tenant(log, TENANT)) == {job.job_id for job in jobs}


def test_replay_recovers_the_state_each_job_is_really_in(run):
    jobs, log = run
    replayed = replay_tenant(log, TENANT)
    for job in jobs:
        assert replayed[job.job_id].state is job.state, job.job_id


def test_replay_recovers_the_whole_history_not_just_the_last_state(run):
    jobs, log = run
    replayed = replay_tenant(log, TENANT)
    for job in jobs:
        seen = replayed[job.job_id]
        assert [(t.from_state, t.to_state, t.reason) for t in seen.history] == [
            (h.from_state, h.to_state, h.reason) for h in job.history
        ], job.job_id
        assert [t.event_id for t in seen.history] == [
            h.event_id for h in job.history
        ], job.job_id


def test_replay_recovers_the_governance_context(run):
    jobs, log = run
    replayed = replay_tenant(log, TENANT)
    for job in jobs:
        seen = replayed[job.job_id]
        expected = job.approval.decision_id if job.approval else None
        assert seen.approval_decision_id == expected, job.job_id
        assert list(seen.decision_ids) == [d.decision_id for d in job.decisions]


def test_replay_recovers_the_pause_a_job_is_sitting_in(new, reviewer):
    job = advance_to(new("job-006"), JobState.IN_PROGRESS, authority=reviewer)
    job.pause_for_approval(reason="needs a human before merge")
    seen = replay_job(job.events)
    assert seen.state is JobState.AWAITING_APPROVAL
    assert seen.awaiting_from is JobState.IN_PROGRESS


def test_replay_recovers_the_identity_and_the_supersession_link(run):
    jobs, log = run
    replayed = replay_tenant(log, TENANT)
    for job in jobs:
        seen = replayed[job.job_id]
        assert seen.tenant_id == job.tenant_id
        assert seen.workspace_id == job.workspace_id
        assert seen.supersedes_job_id == job.supersedes_job_id


def test_replay_agrees_about_completion(run):
    jobs, log = run
    replayed = replay_tenant(log, TENANT)
    for job in jobs:
        assert replayed[job.job_id].completed is (job.state is JobState.COMPLETED)


def test_states_visited_is_the_path_the_job_took(new, reviewer):
    job = happy_path(new, job_id="job-001", authority=reviewer)
    assert tuple(s.value for s in replay_job(job.events).states_visited) == ISSUE_7_FLOW


# ---- [6b] the trail is complete because losing a record is detectable -------


@pytest.mark.parametrize("index", range(1, 8), ids=lambda i: f"drop-{i}")
def test_dropping_any_transition_breaks_the_replay(index, new, reviewer):
    """Completeness is only a claim if a gap would be noticed. Here it is.

    Every transition but the last is caught by the one after it naming the state
    it left. The last one is caught by ``JOB_COMPLETED`` having nothing to
    announce — which is why this covers a completed job, and why the same is not
    true of a trail cut short at ``FAILED``; see :class:`IncompleteSettlement`.
    """
    job = happy_path(new, job_id="job-001", authority=reviewer)
    transitions = [e for e in job.events if e.type_value == "STATE_TRANSITION"]
    assert len(transitions) == 7
    dropped = [e for e in job.events if e is not transitions[index - 1]]
    with pytest.raises(ReplayError):
        replay_job(dropped)


def test_a_missing_completion_announcement_is_noticed(new, reviewer):
    job = happy_path(new, job_id="job-001", authority=reviewer)
    without = [e for e in job.events if e.type_value != "JOB_COMPLETED"]
    with pytest.raises(IncompleteSettlement) as excinfo:
        replay_job(without)
    assert excinfo.value.announced is False


def test_a_trail_cut_short_at_a_terminal_other_than_completed_is_now_caught(
    new, reviewer
):
    """This test recorded the hole; RFC-0012 closed it, so it now records the fix.

    It used to assert that a ``FAILED`` trail missing its last record replayed
    cleanly into the state before it, and predicted the fix would be a per-job
    sequence number in ``event/v1``. **That prediction was wrong** — ADR-0015
    established that a number carried on an event cannot say what number the last
    event should have been, because that answer is not on any event. What closed
    it was a closing record on every terminal, which needed no contract change at
    all: ``event_type`` already refs an open set.
    """
    job = failed_at(
        new, job_id="job-003", authority=reviewer, state=JobState.IN_PROGRESS
    )
    truncated = [e for e in job.events if e.type_value != "JOB_SETTLED"]
    with pytest.raises(UnsettledTrail):
        replay_job(truncated)


def test_reordering_the_trail_breaks_the_replay(new, reviewer):
    """Two transitions swapped — named rather than indexed, so the closing record
    cannot quietly change what this test is swapping."""
    job = happy_path(new, job_id="job-001", authority=reviewer)
    events = list(job.events)
    moves = [i for i, e in enumerate(events) if e.type_value == "STATE_TRANSITION"]
    first, second = moves[-2], moves[-1]
    events[first], events[second] = events[second], events[first]
    with pytest.raises(BrokenTrail):
        replay_job(events)


def test_a_forged_edge_is_refused_rather_than_followed(new, reviewer):
    """``states.py`` is the only transition table, on the reading side as well."""
    job = happy_path(new, job_id="job-001", authority=reviewer)
    forged = [
        dataclasses.replace(e, transition={**e.transition, "to": "COMPLETED"})
        if e.type_value == "STATE_TRANSITION" and e.transition["from"] == "IN_PROGRESS"
        else e
        for e in job.events
    ]
    with pytest.raises(UndeclaredTransition) as excinfo:
        replay_job(forged)
    assert (excinfo.value.from_state, excinfo.value.to_state) == (
        "IN_PROGRESS",
        "COMPLETED",
    )


def test_a_trail_with_no_beginning_is_refused(new, reviewer):
    job = happy_path(new, job_id="job-001", authority=reviewer)
    with pytest.raises(UnstartedTrail):
        replay_job(job.events[1:])


def test_an_empty_trail_is_refused():
    with pytest.raises(EmptyTrail):
        replay_job([])


# ---- [7] the deliverable is runnable ----------------------------------------


def test_the_simulation_script_runs_and_reports_no_failures():
    """Issue #7 asks for a runnable script. This is that claim, checked."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "simulation" / "e2e_flow.py"), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL=0" in proc.stdout
