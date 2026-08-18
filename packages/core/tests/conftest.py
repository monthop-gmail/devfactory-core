from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devfactory_core import Job, JobState, Principal  # noqa: E402


@pytest.fixture
def alice() -> Principal:
    return Principal("human", "alice", display_name="Alice")


@pytest.fixture
def planner() -> Principal:
    return Principal("agent", "planner-1")


@pytest.fixture
def clock():
    """Monotonic fake clock, so ordering assertions do not depend on wall time."""
    start = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return start + timedelta(seconds=state["n"])

    return tick


@pytest.fixture
def job(alice, clock) -> Job:
    return Job(
        job_id="job-001",
        tenant_id="default",
        workspace_id="ws-core",
        principal=alice,
        clock=clock,
    )


def drive(job: Job, target: JobState, authority: Principal) -> Job:
    """Walk a fresh job along the happy path until it reaches ``target``.

    TIMED_OUT is reached from VALIDATING rather than DEPLOYABLE — DEPLOYABLE is
    deliberately not in ``TIMEOUTABLE``, since nothing there is waiting on a clock.
    """
    if target is JobState.DRAFT:
        return job
    job.submit_for_governance()
    if target is JobState.GOVERNANCE_ANALYSIS:
        return job
    if target is JobState.REJECTED:
        job.reject(authority=authority, reason="out of scope")
        return job
    job.approve(authority=authority, reason="approved for milestone")
    if target is JobState.APPROVED:
        return job
    job.transition(JobState.TASK_PLANNING)
    if target is JobState.TASK_PLANNING:
        return job
    job.transition(JobState.IN_PROGRESS)
    if target is JobState.IN_PROGRESS:
        return job
    if target is JobState.AWAITING_APPROVAL:
        job.pause_for_approval()
        return job
    job.transition(JobState.VALIDATING)
    if target is JobState.VALIDATING:
        return job
    if target is JobState.TIMED_OUT:
        job.time_out(reason="sla exceeded")
        return job
    if target is JobState.FAILED:
        job.fail(reason="orchestration exhausted retries")
        return job
    if target is JobState.CANCELLED:
        job.cancel(reason="stopped by owner", principal=authority)
        return job
    job.transition(JobState.DEPLOYABLE)
    if target is JobState.DEPLOYABLE:
        return job
    if target is JobState.COMPLETED:
        job.transition(JobState.COMPLETED)
        return job
    raise AssertionError(f"drive() has no path to {target}")
