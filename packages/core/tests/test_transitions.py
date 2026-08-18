"""Every valid edge is walkable; every invalid one is refused."""

from __future__ import annotations

import pytest

from conftest import drive
from devfactory_core import Job, JobState, Principal
from devfactory_core.errors import InvalidTransition, JobStateMachineError, TerminalState
from devfactory_core.states import TERMINAL, TRANSITIONS


def _fresh(alice: Principal, clock, name: str = "job-001") -> Job:
    return Job(
        job_id=name, tenant_id="default", workspace_id="ws-core", principal=alice, clock=clock
    )


def _args_for(target: JobState, authority: Principal) -> dict:
    """The guard-required arguments for entering ``target``."""
    if target in (JobState.APPROVED, JobState.REJECTED):
        return {"reason": "decided", "principal": authority}
    if target is JobState.CANCELLED:
        return {"reason": "stopped", "principal": authority}
    if target in (JobState.FAILED, JobState.TIMED_OUT):
        return {"reason": "stopped"}
    return {}


@pytest.mark.parametrize("state", list(JobState), ids=lambda s: s.value)
def test_drive_reaches_every_state(state, alice, clock):
    job = drive(_fresh(alice, clock), state, alice)
    assert job.state is state


@pytest.mark.parametrize(
    "source,target",
    [
        (source, target)
        for source, targets in TRANSITIONS.items()
        for target in sorted(targets, key=lambda s: s.value)
    ],
    ids=lambda v: v.value if isinstance(v, JobState) else str(v),
)
def test_every_static_edge_is_walkable(source, target, alice, clock):
    job = drive(_fresh(alice, clock), source, alice)
    assert job.state is source
    job.transition(target, **_args_for(target, alice))
    assert job.state is target


@pytest.mark.parametrize(
    "source,target",
    [
        (source, target)
        for source in JobState
        if source not in TERMINAL
        for target in JobState
        if target not in TRANSITIONS[source]
        # AWAITING_APPROVAL's return edge is dynamic; covered in test_guards.
        and not (source is JobState.AWAITING_APPROVAL and target is JobState.IN_PROGRESS)
    ],
    ids=lambda v: v.value if isinstance(v, JobState) else str(v),
)
def test_every_non_edge_is_refused(source, target, alice, clock):
    job = drive(_fresh(alice, clock), source, alice)
    before = job.state
    # Any refusal is correct here; which specific one is asserted in test_guards.
    with pytest.raises(JobStateMachineError):
        job.transition(target, **_args_for(target, alice))
    assert job.state is before, "a refused transition must leave the job untouched"


@pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_states_refuse_everything(state, alice, clock):
    job = drive(_fresh(alice, clock), state, alice)
    for target in JobState:
        with pytest.raises(TerminalState):
            job.transition(target, **_args_for(target, alice))
    assert job.state is state


def test_rejected_returns_to_draft_and_can_be_resubmitted(alice, clock):
    job = drive(_fresh(alice, clock), JobState.REJECTED, alice)
    job.transition(JobState.DRAFT)
    assert job.state is JobState.DRAFT
    job.submit_for_governance()
    job.approve(authority=alice, reason="scope fixed")
    assert job.state is JobState.APPROVED


def test_rejection_clears_a_previous_approval(alice, clock):
    """An approval granted to an earlier revision must not authorise a revised one."""
    job = _fresh(alice, clock)
    job.submit_for_governance()
    job.approve(authority=alice, reason="first pass")
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="plan does not work")

    replacement = job.supersede(job_id="job-002")
    replacement.submit_for_governance()
    replacement.reject(authority=alice, reason="still wrong")
    replacement.transition(JobState.DRAFT)
    with pytest.raises(InvalidTransition):
        replacement.transition(JobState.TASK_PLANNING)


def test_invalid_transition_names_what_was_allowed(alice, clock):
    job = _fresh(alice, clock)
    with pytest.raises(InvalidTransition) as excinfo:
        job.transition(JobState.COMPLETED)
    assert excinfo.value.current == "DRAFT"
    assert excinfo.value.requested == "COMPLETED"
    assert "GOVERNANCE_ANALYSIS" in excinfo.value.allowed


def test_terminal_error_points_at_supersede(alice, clock):
    job = drive(_fresh(alice, clock), JobState.FAILED, alice)
    with pytest.raises(TerminalState) as excinfo:
        job.transition(JobState.IN_PROGRESS)
    assert "supersedes_job_id" in str(excinfo.value)


def test_transition_accepts_a_plain_string(alice, clock):
    job = _fresh(alice, clock)
    job.transition("GOVERNANCE_ANALYSIS")
    assert job.state is JobState.GOVERNANCE_ANALYSIS
