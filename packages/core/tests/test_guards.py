"""The guards — the refusals that make the lifecycle enforceable."""

from __future__ import annotations

import pytest

from conftest import drive
from devfactory_core import Job, JobState, Principal
from devfactory_core.errors import (
    ExecutionBeforeApproval,
    InvalidIdentifier,
    InvalidTransition,
    MissingApprovalContext,
    MissingAuthority,
    MissingPrincipal,
    MissingReason,
    WrongResumeState,
)


def _fresh(alice, clock, **kw) -> Job:
    base = dict(
        job_id="job-001", tenant_id="default", workspace_id="ws-core", principal=alice, clock=clock
    )
    base.update(kw)
    return Job(**base)


# ---- tenant scope, RFC-0006 ------------------------------------------------


def test_tenant_and_workspace_are_required(alice, clock):
    with pytest.raises(TypeError):
        Job(job_id="job-001", tenant_id="default", principal=alice, clock=clock)


@pytest.mark.parametrize(
    "bad", ["", "Job-001", "job 001", "_job", "-job", "j" * 64, "JOB"], ids=repr
)
def test_identifiers_must_match_identity_v1(bad, alice, clock):
    with pytest.raises(InvalidIdentifier):
        _fresh(alice, clock, job_id=bad)


def test_single_tenant_uses_the_default_literal(alice, clock):
    """RFC-0006: never omit the field — the payload shape is already right."""
    from devfactory_core import DEFAULT_TENANT

    job = _fresh(alice, clock, tenant_id=DEFAULT_TENANT)
    assert job.tenant_id == "default"
    assert all(e.tenant_id == "default" for e in job.events)


def test_principal_must_be_a_principal(alice, clock):
    with pytest.raises(TypeError):
        _fresh(alice, clock, principal="alice")


def test_principal_type_is_constrained():
    with pytest.raises(ValueError):
        Principal("robot", "r2")


def test_delegation_chain_is_recorded(clock):
    alice = Principal("human", "alice")
    agent = Principal("agent", "planner-1", on_behalf_of=alice)
    job = _fresh(alice, clock, principal=agent)
    payload = job.events[0].as_payload()
    assert payload["actor"]["on_behalf_of"]["id"] == "alice"


# ---- reason and authority --------------------------------------------------


@pytest.mark.parametrize(
    "target", [JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT], ids=lambda s: s.value
)
def test_stopping_states_require_a_reason(target, alice, clock):
    job = drive(_fresh(alice, clock), JobState.VALIDATING, alice)
    with pytest.raises(MissingReason):
        job.transition(target, principal=alice)
    assert job.state is JobState.VALIDATING


@pytest.mark.parametrize("blank", ["", "   ", "\n"], ids=repr)
def test_blank_reason_is_not_a_reason(blank, alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    with pytest.raises(MissingReason):
        job.fail(reason=blank)


def test_cancel_records_who_cancelled(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    with pytest.raises(MissingPrincipal):
        job.transition(JobState.CANCELLED, reason="stopped")
    job.cancel(reason="stopped", principal=alice)
    assert job.history[-1].principal is alice


@pytest.mark.parametrize("target", [JobState.APPROVED, JobState.REJECTED], ids=lambda s: s.value)
def test_decisions_require_an_authority_and_a_reason(target, alice, clock):
    job = drive(_fresh(alice, clock), JobState.GOVERNANCE_ANALYSIS, alice)
    with pytest.raises(MissingAuthority):
        job.transition(target, reason="because")
    with pytest.raises(MissingAuthority):
        job.transition(target, principal=alice)
    assert job.state is JobState.GOVERNANCE_ANALYSIS


# ---- the direction lock ----------------------------------------------------


def test_execution_is_forbidden_before_approval(alice, clock):
    job = _fresh(alice, clock)
    for target in (JobState.TASK_PLANNING, JobState.IN_PROGRESS, JobState.VALIDATING):
        with pytest.raises(InvalidTransition):
            job.transition(target)


def test_approval_flag_is_a_backstop_not_the_only_check(alice, clock):
    """If the table were edited wrongly, the guard still refuses."""
    job = drive(_fresh(alice, clock), JobState.GOVERNANCE_ANALYSIS, alice)
    job._state = JobState.APPROVED  # bypass the engine, simulating a bad edit
    with pytest.raises(ExecutionBeforeApproval):
        job.transition(JobState.TASK_PLANNING)


# ---- mid-run approval, RFC-0007 -------------------------------------------


@pytest.mark.parametrize(
    "state", [JobState.IN_PROGRESS, JobState.VALIDATING, JobState.DEPLOYABLE], ids=lambda s: s.value
)
def test_pause_and_resume_returns_to_where_it_paused(state, alice, clock):
    job = drive(_fresh(alice, clock), state, alice)
    job.pause_for_approval(reason="needs sign-off")
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.awaiting_from is state
    job.resume(reason="approved", principal=alice)
    assert job.state is state
    assert job.awaiting_from is None


def test_cannot_pause_before_execution(alice, clock):
    job = drive(_fresh(alice, clock), JobState.TASK_PLANNING, alice)
    with pytest.raises(MissingApprovalContext):
        job.pause_for_approval()


def test_resuming_into_the_wrong_state_is_refused(alice, clock):
    job = drive(_fresh(alice, clock), JobState.DEPLOYABLE, alice)
    job.pause_for_approval()
    with pytest.raises(WrongResumeState) as excinfo:
        job.transition(JobState.IN_PROGRESS)
    assert excinfo.value.awaiting_from == "DEPLOYABLE"
    assert job.state is JobState.AWAITING_APPROVAL


def test_resume_outside_awaiting_approval_is_refused(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    with pytest.raises(InvalidTransition):
        job.resume()


def test_denied_mid_run_approval_can_fail_the_job(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    job.pause_for_approval()
    job.fail(reason="approval denied and no path forward")
    assert job.state is JobState.FAILED


def test_unanswered_approval_times_out(alice, clock):
    """RFC-0007: an approval nobody answers is how a governed pipeline stalls."""
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    job.pause_for_approval()
    job.time_out(reason="approval request expired")
    assert job.state is JobState.TIMED_OUT


# ---- recovery, RFC-0007 ----------------------------------------------------


def test_failed_job_is_superseded_not_revived(alice, clock):
    job = drive(_fresh(alice, clock), JobState.FAILED, alice)
    replacement = job.supersede(job_id="job-002")
    assert replacement.state is JobState.DRAFT
    assert replacement.supersedes_job_id == "job-001"
    assert replacement.tenant_id == job.tenant_id
    assert replacement.workspace_id == job.workspace_id


def test_replacement_must_pass_governance_again(alice, clock):
    job = drive(_fresh(alice, clock), JobState.FAILED, alice)
    replacement = job.supersede(job_id="job-002")
    with pytest.raises(InvalidTransition):
        replacement.transition(JobState.TASK_PLANNING)


def test_only_a_failed_job_can_be_superseded(alice, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, alice)
    with pytest.raises(InvalidTransition):
        job.supersede(job_id="job-002")


def test_supersede_records_the_link_in_the_audit_trail(alice, clock):
    job = drive(_fresh(alice, clock), JobState.FAILED, alice)
    replacement = job.supersede(job_id="job-002")
    created = replacement.events[0].as_payload()
    assert created["metadata"]["supersedes_job_id"] == "job-001"
