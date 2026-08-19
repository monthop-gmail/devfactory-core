"""The flows a job can take, defined once and driven through the real engine.

Issue #7 names four of them — the full path, rejection and resubmission, failure
from a state that may fail, and the governance gate refusing execution. Two more
live here because ``conformance/payload_check.py`` was already driving them and
there is no reason for two files to describe the same journey differently.

Why the forward path is not written out here
--------------------------------------------
``packages/core/devfactory_core/states.py`` is the only place the transition
table is expressed, and a simulation that retyped ``APPROVED -> TASK_PLANNING``
in order to walk it would be a second declaration with the first one's authority.
So :func:`main_line` *reads* the path out of the table instead: at every state,
discard the exits that are available everywhere and are not "forward", and what
remains is a single successor. If the lifecycle ever gains a fork, this raises
rather than picking one — which is the right failure, because a fork is a
lifecycle change and belongs in an RFC before it belongs in a simulation.

The only per-state knowledge kept here is *which method to call*, because
entering ``GOVERNANCE_ANALYSIS`` and entering ``APPROVED`` need arguments the
guards require. That is a fact about the call signature, not about the table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from devfactory_core import Job, JobState, Principal
from devfactory_core.states import FAILABLE, TERMINAL, reachable_from


class JobFactory(Protocol):
    """How a flow gets a fresh job — supplied by the caller, so the flows stay
    free of any opinion about tenancy, workspace, or clock."""

    def __call__(self, job_id: str) -> Job: ...


#: Exits available from nearly everywhere, none of which is progress. Removing
#: them from a state's targets is what leaves the forward edge exposed.
#:
#: * the three unhappy terminals — every non-terminal state offers ``CANCELLED``,
#:   and most offer ``TIMED_OUT`` or ``FAILED``
#: * ``REJECTED`` — a verdict, and the flow that follows it is its own
#: * ``AWAITING_APPROVAL`` — a pause inside the path, not a step along it
#: * ``DRAFT`` — since RFC-0011 the gate can send a job back there for changes.
#:   That is a return to the start, which is the opposite of progress; the flow it
#:   begins is its own, exactly as ``REJECTED``'s is. ``DRAFT`` is never a forward
#:   target from anywhere, so excluding it cannot hide a step.
_NOT_FORWARD: frozenset[JobState] = (TERMINAL - {JobState.COMPLETED}) | {
    JobState.REJECTED,
    JobState.AWAITING_APPROVAL,
    JobState.DRAFT,
}


def main_line() -> tuple[JobState, ...]:
    """The forward path from ``DRAFT`` to ``COMPLETED``, read out of the table.

    Raises if the table stops naming exactly one way forward, rather than
    choosing between them.
    """
    path: list[JobState] = [JobState.DRAFT]
    while path[-1] is not JobState.COMPLETED:
        forward = sorted(reachable_from(path[-1]) - _NOT_FORWARD, key=lambda s: s.value)
        if len(forward) != 1:
            raise ValueError(
                f"{path[-1].value} has {len(forward)} ways forward "
                f"({[s.value for s in forward] or 'none'}) — the forward path is no "
                f"longer a line, and which branch a simulation walks is an RFC "
                f"question, not this module's to answer"
            )
        if forward[0] in path:
            raise ValueError(f"the forward path loops back to {forward[0].value}")
        path.append(forward[0])
    return tuple(path)


#: ``DRAFT → GOVERNANCE_ANALYSIS → APPROVED → TASK_PLANNING → IN_PROGRESS →
#: VALIDATING → DEPLOYABLE → COMPLETED``, as the table declares it.
MAIN_LINE: tuple[JobState, ...] = main_line()


def advance_to(job: Job, target: JobState, *, authority: Principal) -> Job:
    """Walk ``job`` forward along :data:`MAIN_LINE` until it is in ``target``."""
    if target not in MAIN_LINE:
        raise ValueError(f"{target.value} is not on the forward path")
    if job.state not in MAIN_LINE:
        raise ValueError(f"a job in {job.state.value} is not on the forward path")
    for state in MAIN_LINE[MAIN_LINE.index(job.state) + 1 : MAIN_LINE.index(target) + 1]:
        _enter(job, state, authority=authority)
    return job


def _enter(job: Job, state: JobState, *, authority: Principal) -> None:
    """Take the one step into ``state``.

    Two states are entered through a named method because the guards require
    arguments that the generic call would have to be told about anyway:
    ``GOVERNANCE_ANALYSIS`` is a submission, and ``APPROVED`` is a decision and
    must name the authority accountable for it.
    """
    if state is JobState.GOVERNANCE_ANALYSIS:
        job.submit_for_governance(reason="ready for governance review")
    elif state is JobState.APPROVED:
        job.approve(authority=authority, reason="scope matches milestone v0.1")
    else:
        job.transition(state)


# ---- the flows -------------------------------------------------------------


def happy_path(
    new: JobFactory,
    *,
    job_id: str,
    authority: Principal,
    pause_in: JobState | None = None,
) -> Job:
    """The whole lifecycle, ``DRAFT`` through ``COMPLETED``.

    ``pause_in`` optionally takes the mid-run approval detour on the way out of
    that state — the same journey, with a human interrupting it once.
    """
    job = new(job_id)
    for state in MAIN_LINE[1:]:
        if pause_in is not None and job.state is pause_in:
            job.pause_for_approval(reason="needs a human before the next step")
            job.resume(reason="signed off", principal=authority)
        _enter(job, state, authority=authority)
    return job


def rejected_then_resubmitted(
    new: JobFactory, *, job_id: str, authority: Principal
) -> Job:
    """Rejected, revised, resubmitted, approved — and then actually executing.

    It carries on into ``TASK_PLANNING`` on purpose. Reaching ``APPROVED`` a
    second time proves the job may be resubmitted; taking the next step proves
    the *second* decision restored the authority the first one withheld, which is
    the part that would be silently broken if a rejection left an approval behind.
    """
    job = new(job_id)
    job.submit_for_governance(reason="first submission")
    job.reject(authority=authority, reason="missing risk analysis")
    job.transition(JobState.DRAFT)
    job.submit_for_governance(reason="risk analysis added")
    job.approve(authority=authority, reason="the gap raised in review is addressed")
    job.transition(JobState.TASK_PLANNING)
    return job


def require_changes_then_resubmitted(
    new: JobFactory, *, job_id: str, authority: Principal
) -> Job:
    """Sent back for changes, revised, resubmitted, approved — RFC-0011.

    The counterpart to :func:`rejected_then_resubmitted`, and the reason both are
    written down: they end in the same place and their trails are not the same
    trail. This one goes ``GOVERNANCE_ANALYSIS -> DRAFT`` in a single step and
    ``REJECTED`` never appears in its history, which is what keeps *"REQUIRE_CHANGES
    ไม่ใช่ REJECT"* true of the record and not only of the wording.

    It carries on into ``TASK_PLANNING`` for the same reason the rejection flow
    does: a ``REQUIRE_CHANGES`` clears the approval, so reaching execution proves
    the *second* decision restored authority the first one withheld.
    """
    job = new(job_id)
    job.submit_for_governance(reason="first submission")
    job.require_changes(authority=authority, reason="add a test plan and resubmit")
    job.submit_for_governance(reason="test plan added")
    job.approve(authority=authority, reason="the changes asked for are in")
    job.transition(JobState.TASK_PLANNING)
    return job


def failed_at(
    new: JobFactory,
    *,
    job_id: str,
    authority: Principal,
    state: JobState,
    reason: str = "orchestration exhausted execution retries",
) -> Job:
    """Drive to ``state`` and fail there.

    ``state`` must be in ``states.FAILABLE`` — RFC-0010. Asking for anything else
    is refused here rather than left to the engine, so that a flow which cannot
    happen is not written down as though it could.
    """
    if state not in FAILABLE:
        raise ValueError(
            f"{state.value} is not in states.FAILABLE — RFC-0010 says a job fails "
            f"only where work exists to fail"
        )
    job = new(job_id)
    if state is JobState.AWAITING_APPROVAL:
        advance_to(job, JobState.IN_PROGRESS, authority=authority)
        job.pause_for_approval(reason="needs a human before merge")
    else:
        advance_to(job, state, authority=authority)
    job.fail(reason=reason)
    return job


def never_approved(new: JobFactory, *, job_id: str) -> Job:
    """A job sitting in ``GOVERNANCE_ANALYSIS`` with no decision made about it.

    The subject of the governance-gate checks: it holds no ``Decision``, so
    nothing about it may execute.
    """
    job = new(job_id)
    job.submit_for_governance(reason="awaiting a verdict")
    return job


def cancelled_by_a_person(new: JobFactory, *, job_id: str, owner: Principal) -> Job:
    job = new(job_id)
    job.submit_for_governance()
    job.cancel(reason="superseded by an urgent request", principal=owner)
    return job


def approval_expired(
    new: JobFactory, *, job_id: str, authority: Principal, expires_at: datetime
) -> Job:
    """An approval that ran out before the work started.

    RFC-0007 Amendment 1's characteristic stall, and the reason ``APPROVED`` is in
    ``TIMEOUTABLE``: orchestration never came for the job, the approval lapsed
    where it sat, and the honest terminal is ``TIMED_OUT`` rather than an
    indefinite wait for someone to notice.

    ``expires_at`` is passed in rather than computed, because it has to be in the
    past relative to whichever clock the caller gave the factory — the flows hold
    no opinion about the clock, and a timeout policy that decided this for them
    would be inventing values two RFCs deliberately left out of scope.
    """
    job = new(job_id)
    job.submit_for_governance(reason="ready for governance review")
    job.approve(
        authority=authority,
        reason="scope matches milestone v0.1",
        expires_at=expires_at,
    )
    job.time_out(reason="approval_expired — the approval lapsed before planning began")
    return job


def stalled_awaiting_approval(
    new: JobFactory, *, job_id: str, authority: Principal
) -> Job:
    """An approval nobody answered — RFC-0007's characteristic stall."""
    job = new(job_id)
    advance_to(job, JobState.IN_PROGRESS, authority=authority)
    job.pause_for_approval(reason="needs a human before merge")
    job.time_out(reason="sla_exceeded — approval request expired after 72h")
    return job


def job_factory(
    *,
    tenant_id: str = "acme",
    workspace_id: str = "ws-core",
    principal: Principal,
    clock: Callable[[], datetime] | None = None,
) -> JobFactory:
    """A :class:`JobFactory` bound to one tenant, workspace, and clock."""

    def new(job_id: str) -> Job:
        kwargs = {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "principal": principal,
        }
        if clock is not None:
            kwargs["clock"] = clock
        return Job(**kwargs)

    return new
