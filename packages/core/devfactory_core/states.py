"""Job lifecycle states and the transition table.

Canonical spec: ``packages/core/state-machine.md`` — RFC-0001 as amended by RFC-0007.
This module is the single place the transition table is expressed in code; nothing
else may hard-code an edge.
"""

from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    """The thirteen job states.

    ``str`` mixin so a state serialises as its own name in an event payload
    without a conversion step at every emit site.
    """

    DRAFT = "DRAFT"
    GOVERNANCE_ANALYSIS = "GOVERNANCE_ANALYSIS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TASK_PLANNING = "TASK_PLANNING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    VALIDATING = "VALIDATING"
    DEPLOYABLE = "DEPLOYABLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


#: Terminal states. ``REJECTED`` is deliberately absent — it returns to ``DRAFT``.
TERMINAL: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.TIMED_OUT}
)

#: States from which execution has been authorised. Reaching any of these without
#: passing APPROVED would violate "execution is forbidden before APPROVED".
POST_APPROVAL: frozenset[JobState] = frozenset(
    {
        JobState.TASK_PLANNING,
        JobState.IN_PROGRESS,
        JobState.AWAITING_APPROVAL,
        JobState.VALIDATING,
        JobState.DEPLOYABLE,
    }
)

#: States a job can pause in to wait for a human, and return to afterwards.
APPROVAL_PAUSABLE: frozenset[JobState] = frozenset(
    {JobState.IN_PROGRESS, JobState.VALIDATING, JobState.DEPLOYABLE}
)

#: RFC-0007: TIMED_OUT is reachable from these. AWAITING_APPROVAL is included
#: because an approval nobody answers is how a governed pipeline usually stalls.
TIMEOUTABLE: frozenset[JobState] = frozenset(
    {
        JobState.GOVERNANCE_ANALYSIS,
        JobState.TASK_PLANNING,
        JobState.IN_PROGRESS,
        JobState.AWAITING_APPROVAL,
        JobState.VALIDATING,
    }
)

#: A job fails only where work exists to fail. Before APPROVED nothing is
#: executing, so the honest outcomes there are REJECTED, CANCELLED, or TIMED_OUT.
#: See "Open question" in the module docstring of ``job.py``.
FAILABLE: frozenset[JobState] = frozenset(
    {
        JobState.TASK_PLANNING,
        JobState.IN_PROGRESS,
        JobState.AWAITING_APPROVAL,
        JobState.VALIDATING,
        JobState.DEPLOYABLE,
    }
)

# The lifecycle proper, before the cross-cutting exits are folded in.
_PROGRESSION: dict[JobState, frozenset[JobState]] = {
    JobState.DRAFT: frozenset({JobState.GOVERNANCE_ANALYSIS}),
    JobState.GOVERNANCE_ANALYSIS: frozenset({JobState.APPROVED, JobState.REJECTED}),
    JobState.APPROVED: frozenset({JobState.TASK_PLANNING}),
    JobState.REJECTED: frozenset({JobState.DRAFT}),
    JobState.TASK_PLANNING: frozenset({JobState.IN_PROGRESS}),
    JobState.IN_PROGRESS: frozenset({JobState.VALIDATING, JobState.AWAITING_APPROVAL}),
    JobState.VALIDATING: frozenset({JobState.DEPLOYABLE, JobState.AWAITING_APPROVAL}),
    JobState.DEPLOYABLE: frozenset({JobState.COMPLETED, JobState.AWAITING_APPROVAL}),
    # The return edge out of AWAITING_APPROVAL is `awaiting_from` and is resolved
    # per job at runtime, not from this table.
    JobState.AWAITING_APPROVAL: frozenset(),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.TIMED_OUT: frozenset(),
}


def _build() -> dict[JobState, frozenset[JobState]]:
    table: dict[JobState, set[JobState]] = {
        state: set(targets) for state, targets in _PROGRESSION.items()
    }
    for state in JobState:
        if state in TERMINAL:
            continue
        # CANCELLED is reachable from every non-terminal state — a job can be
        # stopped at any point before it settles.
        table[state].add(JobState.CANCELLED)
        if state in TIMEOUTABLE:
            table[state].add(JobState.TIMED_OUT)
        if state in FAILABLE:
            table[state].add(JobState.FAILED)
    return {state: frozenset(targets) for state, targets in table.items()}


#: Static transition table. ``AWAITING_APPROVAL`` also permits its job's
#: ``awaiting_from``, which cannot live here because it differs per job.
TRANSITIONS: dict[JobState, frozenset[JobState]] = _build()


def static_targets(state: JobState) -> frozenset[JobState]:
    """States reachable from ``state`` without per-job context."""
    return TRANSITIONS[state]


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL
