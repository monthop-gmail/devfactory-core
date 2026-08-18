"""The transition table itself — shape, not behaviour."""

from __future__ import annotations

import pytest

from devfactory_core.states import (
    APPROVAL_PAUSABLE,
    FAILABLE,
    POST_APPROVAL,
    TERMINAL,
    TIMEOUTABLE,
    TRANSITIONS,
    JobState,
    is_terminal,
    static_targets,
)


def test_thirteen_states():
    """RFC-0007 amended RFC-0001's ten to thirteen. Issue #2 predates that."""
    assert len(JobState) == 13


def test_every_state_has_a_table_entry():
    assert set(TRANSITIONS) == set(JobState)


def test_terminal_states_are_exactly_four():
    assert TERMINAL == {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.TIMED_OUT,
    }


def test_rejected_is_not_terminal():
    """RFC-0001: REJECTED returns to DRAFT so the job can be revised."""
    assert JobState.REJECTED not in TERMINAL
    assert JobState.DRAFT in static_targets(JobState.REJECTED)


@pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_states_have_no_outgoing_edges(state):
    assert static_targets(state) == frozenset()
    assert is_terminal(state)


@pytest.mark.parametrize(
    "state", [s for s in JobState if s not in TERMINAL], ids=lambda s: s.value
)
def test_cancellable_from_every_non_terminal_state(state):
    """RFC-0007: a job can be stopped at any point before it settles."""
    assert JobState.CANCELLED in static_targets(state)


@pytest.mark.parametrize("state", sorted(TIMEOUTABLE, key=lambda s: s.value))
def test_timeout_reachable_where_specified(state):
    assert JobState.TIMED_OUT in static_targets(state)


def test_timeout_not_reachable_from_draft_or_approved():
    """Nothing is waiting on anything in DRAFT or APPROVED, so nothing can expire."""
    assert JobState.TIMED_OUT not in static_targets(JobState.DRAFT)
    assert JobState.TIMED_OUT not in static_targets(JobState.APPROVED)


@pytest.mark.parametrize("state", sorted(FAILABLE, key=lambda s: s.value))
def test_failed_reachable_only_where_work_exists(state):
    assert JobState.FAILED in static_targets(state)


@pytest.mark.parametrize(
    "state",
    [JobState.DRAFT, JobState.GOVERNANCE_ANALYSIS, JobState.APPROVED, JobState.REJECTED],
    ids=lambda s: s.value,
)
def test_failed_not_reachable_before_execution(state):
    """Before APPROVED nothing runs, so REJECTED, CANCELLED, or TIMED_OUT apply."""
    assert JobState.FAILED not in static_targets(state)


def test_approved_is_the_only_gate_into_execution():
    """Direction lock: every path into POST_APPROVAL passes through APPROVED."""
    entrances = {
        source
        for source, targets in TRANSITIONS.items()
        if targets & POST_APPROVAL and source not in POST_APPROVAL
    }
    assert entrances == {JobState.APPROVED}


def test_awaiting_approval_has_no_static_return_edge():
    """The way back is `awaiting_from`, which differs per job."""
    assert static_targets(JobState.AWAITING_APPROVAL) & APPROVAL_PAUSABLE == frozenset()


@pytest.mark.parametrize("state", sorted(APPROVAL_PAUSABLE, key=lambda s: s.value))
def test_pausable_states_can_reach_awaiting_approval(state):
    assert JobState.AWAITING_APPROVAL in static_targets(state)


def test_every_state_is_reachable_from_draft():
    """No orphans — a state nothing can reach is a state that does not exist."""
    seen = {JobState.DRAFT}
    frontier = [JobState.DRAFT]
    while frontier:
        for target in static_targets(frontier.pop()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    # AWAITING_APPROVAL's return edge is dynamic, so a static walk still reaches
    # every state; nothing depends on it to be discovered.
    assert seen == set(JobState)


def test_state_serialises_as_its_name():
    assert JobState.IN_PROGRESS.value == "IN_PROGRESS"
    assert f"{JobState.IN_PROGRESS}" .endswith("IN_PROGRESS")
