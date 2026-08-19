"""The guards — the refusals that make the lifecycle enforceable."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from conftest import drive
from devfactory_core import Job, JobState, Principal
from devfactory_core.errors import (
    ExecutionBeforeApproval,
    ExpiredApproval,
    InvalidIdentifier,
    InvalidTransition,
    MissingApprovalContext,
    MissingAuthority,
    MissingPrincipal,
    MissingReason,
    WrongResumeState,
)
from devfactory_core.states import POST_APPROVAL

#: Either side of the ``clock`` fixture, which starts at 2026-08-18 09:00 UTC.
EXPIRY_PAST = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
EXPIRY_FAR_FUTURE = datetime(2027, 1, 1, tzinfo=timezone.utc)


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


@pytest.mark.parametrize(
    "target",
    [JobState.APPROVED, JobState.REJECTED, JobState.DRAFT],
    ids=lambda s: s.value,
)
def test_decisions_require_an_authority_and_a_reason(target, alice, clock):
    """All three verdicts, including RFC-0011's ``GOVERNANCE_ANALYSIS -> DRAFT``."""
    job = drive(_fresh(alice, clock), JobState.GOVERNANCE_ANALYSIS, alice)
    with pytest.raises(MissingAuthority):
        job.transition(target, reason="because")
    with pytest.raises(MissingAuthority):
        job.transition(target, principal=alice)
    assert job.state is JobState.GOVERNANCE_ANALYSIS


def test_the_revision_step_after_a_rejection_needs_no_authority(alice, clock):
    """Same destination as a ``REQUIRE_CHANGES``, and not a decision.

    The guard keys on the edge for exactly this reason: requiring an authority here
    would demand a second verdict for a rejection already recorded.
    """
    job = drive(_fresh(alice, clock), JobState.REJECTED, alice)
    job.transition(JobState.DRAFT)
    assert job.state is JobState.DRAFT
    assert job.history[-1].decision_id is None


def test_entering_draft_from_the_gate_records_the_decision_it_must_have_been(
    alice, clock
):
    """The generic call mints the same record ``require_changes()`` would."""
    job = drive(_fresh(alice, clock), JobState.GOVERNANCE_ANALYSIS, alice)
    job.transition(JobState.DRAFT, reason="add a test plan", principal=alice)
    assert job.state is JobState.DRAFT
    assert job.decisions[-1].decision.value == "REQUIRE_CHANGES"
    assert job.events[-2].type_value == "GOVERNANCE_DECISION"
    assert job.approval is None


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


# ---- approval expiry, RFC-0007 Amendment 1 ---------------------------------
# ``approval/v1``: "approval ที่หมดอายุแล้วใช้เดินงานไม่ได้ ต้องขอใหม่". The engine
# refuses rather than tolerates, which is what makes that sentence a rule and not
# a comment. Issue #17.


def _approved_until(alice, clock, deadline, authority):
    """A job holding an APPROVE that expires at ``deadline``."""
    job = _fresh(alice, clock)
    job.submit_for_governance()
    job.approve(authority=authority, reason="approved for milestone", expires_at=deadline)
    return job


def test_an_approval_that_has_not_expired_still_authorises_work(alice, clock):
    job = _approved_until(alice, clock, EXPIRY_FAR_FUTURE, alice)
    assert job.approval_expired is False
    job.transition(JobState.TASK_PLANNING)
    assert job.state is JobState.TASK_PLANNING


#: Where each post-approval state is entered from. Expiry can only be tested on an
#: edge the table declares, so reaching every one of them means starting from a
#: different place each time.
_ENTERED_FROM = {
    JobState.TASK_PLANNING: JobState.APPROVED,
    JobState.IN_PROGRESS: JobState.TASK_PLANNING,
    JobState.AWAITING_APPROVAL: JobState.IN_PROGRESS,
    JobState.VALIDATING: JobState.IN_PROGRESS,
    JobState.DEPLOYABLE: JobState.VALIDATING,
}


def test_the_expiry_check_covers_every_state_that_needs_an_approval():
    """Both halves of the direction lock guard the same set, or there is a hole.

    If expiry covered less than ``POST_APPROVAL`` there would be a state reachable
    without a *valid* approval but not without *any* approval, which is a strange
    thing for a governance engine to believe.
    """
    assert set(_ENTERED_FROM) == POST_APPROVAL


@pytest.mark.parametrize(
    "target", sorted(POST_APPROVAL, key=lambda s: s.value), ids=lambda s: s.value
)
def test_no_post_approval_state_is_reachable_under_an_expired_approval(
    target, alice, clock
):
    """The set that means "execution was authorised" is the set expiry closes."""
    origin = _ENTERED_FROM[target]
    job = drive(_fresh(alice, clock), origin, alice)
    job._approval = dataclasses.replace(job.approval, expires_at=EXPIRY_PAST)
    with pytest.raises(ExpiredApproval) as excinfo:
        job.transition(target)
    assert excinfo.value.requested == target.value
    assert job.state is origin


def test_a_refused_expired_transition_leaves_no_trace(alice, clock):
    job = _approved_until(alice, clock, EXPIRY_PAST, alice)
    before = len(job.events), len(job.history)
    with pytest.raises(ExpiredApproval):
        job.transition(JobState.TASK_PLANNING)
    assert (len(job.events), len(job.history)) == before


def test_an_approval_can_lapse_while_the_job_is_already_working(alice, clock):
    """The deadline is on the approval, not on the step it authorised."""
    job = _approved_until(alice, clock, EXPIRY_FAR_FUTURE, alice)
    job.transition(JobState.TASK_PLANNING)
    job.transition(JobState.IN_PROGRESS)
    job._approval = dataclasses.replace(job.approval, expires_at=EXPIRY_PAST)
    with pytest.raises(ExpiredApproval):
        job.transition(JobState.VALIDATING)


def test_a_pause_that_outlasts_the_approval_cannot_be_resumed(alice, clock):
    """Waiting for a human is the likeliest way a deadline gets passed."""
    job = _approved_until(alice, clock, EXPIRY_FAR_FUTURE, alice)
    job.transition(JobState.TASK_PLANNING)
    job.transition(JobState.IN_PROGRESS)
    job.pause_for_approval()
    job._approval = dataclasses.replace(job.approval, expires_at=EXPIRY_PAST)
    with pytest.raises(ExpiredApproval):
        job.resume()
    assert job.state is JobState.AWAITING_APPROVAL


def test_a_job_holding_an_expired_approval_can_still_time_out(alice, clock):
    """RFC-0007 Amendment 1 — the reason APPROVED joined TIMEOUTABLE."""
    job = _approved_until(alice, clock, EXPIRY_PAST, alice)
    job.time_out(reason="approval_expired — the approval lapsed before planning began")
    assert job.state is JobState.TIMED_OUT
    assert job.history[-1].from_state is JobState.APPROVED


def test_a_job_holding_an_expired_approval_can_still_be_cancelled(alice, clock):
    job = _approved_until(alice, clock, EXPIRY_PAST, alice)
    job.cancel(reason="no longer wanted", principal=alice)
    assert job.state is JobState.CANCELLED


def test_the_only_ways_out_of_an_expired_approval_are_the_two_terminals(alice, clock):
    """Recorded as it is, not as it might be nicer.

    ``approval/v1`` says the remedy is "ต้องขอใหม่", and this lifecycle has no edge
    for asking again: ``APPROVED`` reaches only ``TASK_PLANNING`` (now shut),
    ``CANCELLED``, and — since RFC-0007 Amendment 1 — ``TIMED_OUT``. So the job
    settles and a re-request is a *new* job. Giving ``APPROVED`` a way back to
    ``GOVERNANCE_ANALYSIS`` would be a lifecycle change and belongs in an RFC, so
    this test asserts today's shape rather than inventing tomorrow's.
    """
    job = _approved_until(alice, clock, EXPIRY_PAST, alice)
    reachable = {s for s in job.allowed_targets()}
    assert reachable == {JobState.TASK_PLANNING, JobState.CANCELLED, JobState.TIMED_OUT}
    with pytest.raises(ExpiredApproval):
        job.transition(JobState.TASK_PLANNING)


def test_an_approval_without_a_deadline_never_expires(alice, clock):
    """The pre-existing behaviour, asserted so the new guard cannot swallow it."""
    job = _fresh(alice, clock)
    job.submit_for_governance()
    job.approve(authority=alice, reason="approved for milestone")
    assert job.approval_expires_at is None
    assert job.approval_expired is False
    job.transition(JobState.TASK_PLANNING)
    assert job.state is JobState.TASK_PLANNING


def test_a_job_with_no_approval_at_all_is_not_reported_as_expired(alice, clock):
    job = _fresh(alice, clock)
    assert job.approval_expires_at is None
    assert job.approval_expired is False
