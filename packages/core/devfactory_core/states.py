"""Job lifecycle states and the transition table.

Canonical spec: ``packages/core/state-machine.md`` — RFC-0001 as amended by RFC-0007.
This module is the single place the transition table is expressed in code; nothing
else may hard-code an edge. ``DECISION_TARGET`` lives here for the same reason:
where a governance decision (RFC-0002) sends a job has to be checked against the
table, not asserted separately from it.
"""

from __future__ import annotations

from enum import Enum

from .decision import DecisionType


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
#:
#: ``APPROVED`` was added by RFC-0007's 2026-08-19 amendment (issue #17). The
#: argument is governance rather than liveness: **an approval must expire.** A job
#: that may sit in APPROVED forever can start executing a week later under a
#: verdict formed in a context that no longer holds — the same stale APPROVED that
#: RFC-0007 Decision 1 refuses when it forbids reviving a FAILED job. ``approval/v1``
#: already says so on ``expires_at``: "approval ที่หมดอายุแล้วใช้เดินงานไม่ได้ ต้องขอใหม่ ·
#: งานที่ค้างรออนุมัติจนเลยกำหนดควรเข้าสถานะ timeout ไม่ใช่รอตลอดไป". Without this edge
#: there was no state for that job to land in.
TIMEOUTABLE: frozenset[JobState] = frozenset(
    {
        JobState.GOVERNANCE_ANALYSIS,
        JobState.APPROVED,
        JobState.TASK_PLANNING,
        JobState.IN_PROGRESS,
        JobState.AWAITING_APPROVAL,
        JobState.VALIDATING,
    }
)

#: A job fails only where work exists to fail. Before APPROVED nothing is
#: executing, so the honest outcomes there are REJECTED, CANCELLED, or TIMED_OUT.
#: Settled by RFC-0010.
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


#: Where a governance decision sends a job — RFC-0002 meeting RFC-0001.
#:
#: Every destination here is an edge ``_PROGRESSION`` already declares out of
#: ``GOVERNANCE_ANALYSIS``. A decision may not invent a transition: if a decision
#: needs a new edge, that edge is a lifecycle change and belongs in an RFC first
#: (``docs/governance/CORE_BOUNDARY.md``). ``test_decisions.py`` asserts this
#: containment so the rule cannot quietly lapse.
#:
#: ``REQUIRE_CHANGES`` is deliberately absent, and its absence is the decision,
#: not an oversight:
#:
#: * RFC-0002 declares it and ``contract-semantics.yaml`` marks the vocabulary a
#:   closed set, so :class:`~devfactory_core.decision.DecisionType` must carry all
#:   three values. Dropping it would narrow the contract we publish.
#: * ``approval/v1`` says what it *means* — "REQUIRE_CHANGES ไม่ใช่ REJECT — งานยัง
#:   มีชีวิตและกลับมายื่นใหม่ได้" — but nothing in this repository says which state
#:   the job lands in, and that is the part the engine would need.
#:
#: Each candidate destination needs something that does not exist yet:
#:
#: * ``REJECTED`` — forbidden outright by the invariant above: it is not a REJECT,
#:   and recording it as one would make the audit trail say the wrong thing.
#: * ``DRAFT`` — needs a ``GOVERNANCE_ANALYSIS -> DRAFT`` edge that neither
#:   RFC-0001 nor RFC-0007 declares, and it would erase the difference from the
#:   ``REJECTED -> DRAFT`` path, which is exactly the distinction the invariant
#:   asks us to keep.
#: * a fourteenth state (``CHANGES_REQUESTED``) — a new state, which is a
#:   lifecycle change and needs an RFC.
#:
#: So :meth:`~devfactory_core.job.Job.decide` raises ``UnmappedDecision`` for it.
#: Guessing would be the one failure mode governance cannot afford: an audit
#: record whose meaning is not the meaning that was decided. When an RFC settles
#: the destination, the fix is one entry in this dict plus whatever edge that RFC
#: declares — deliberately a small change, sitting behind a decision only a human
#: can make.
DECISION_TARGET: dict[DecisionType, JobState] = {
    DecisionType.APPROVE: JobState.APPROVED,
    DecisionType.REJECT: JobState.REJECTED,
}

#: The inverse. Entering ``APPROVED`` or ``REJECTED`` through the generic
#: ``transition()`` still has to produce a decision record — "every APPROVE is
#: auditable" is a guarantee about the state, not about which method was called —
#: and this is how that path names the decision it must have been.
DECISION_BY_TARGET: dict[JobState, DecisionType] = {
    target: decision for decision, target in DECISION_TARGET.items()
}


def static_targets(state: JobState) -> frozenset[JobState]:
    """States reachable from ``state`` without per-job context."""
    return TRANSITIONS[state]


def reachable_from(
    state: JobState, *, awaiting_from: JobState | None = None
) -> frozenset[JobState]:
    """States reachable from ``state``, including the one dynamic edge.

    ``AWAITING_APPROVAL``'s way back differs per job and so cannot be a row in
    ``TRANSITIONS``. Folding it in here rather than at each call site means
    "reading the table correctly" has one implementation: the engine asks this
    before it moves a job, and a replay reading the trail back asks the same
    thing before it believes a recorded edge. Neither gets to hold an opinion
    about the lifecycle that this module does not already state.
    """
    targets = TRANSITIONS[state]
    if state is JobState.AWAITING_APPROVAL and awaiting_from is not None:
        return targets | {awaiting_from}
    return targets


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL
