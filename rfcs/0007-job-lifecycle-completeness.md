# RFC-0007: Job Lifecycle Completeness

## Status
Draft — Architecture Owner direction agreed 2026-08-17 · pending maintainer approval per `GOVERNANCE.md`

Amends [RFC-0001](0001-job-state-machine.md). Closes gaps 2, 3, and 4 of
[issue #8](https://github.com/monthop-gmail/devfactory-core/issues/8) and resolves
both Open Questions RFC-0001 left behind.

## Context
RFC-0001 defined ten job states and left two questions open: retry semantics for
`FAILED`, and parallel task substates. It also listed SLA and timeout policies as
Future Work.

`contracts/execution/v1` now exists one layer below the job level. It has states
RFC-0001 does not — `cancelled`, `timed_out`, `awaiting_approval` — and a retry
policy that explicitly answers RFC-0001's open question *at the execution level*.

The upstream analysis confirms the two layers do not conflict and should stay
separate. What it also found is that RFC-0001 is incomplete in three specific
ways that only become visible once a lower layer exists to compare against.

## Problem Statement

**Retry.** RFC-0001 says `FAILED is terminal`. `execution/v1` allows
`failed → queued` when the error is retryable and attempts remain. Read together
without a ruling, the two look contradictory, and the job level has no stated
answer for what "try again" means.

**Missing terminal states.** A job can be cancelled by a human and can exceed an
SLA. RFC-0001 has no state for either, so both must currently be recorded as
`FAILED` — which destroys the distinction between "the work broke", "someone
stopped it", and "it ran out of time". Those three demand different responses.

**Mid-run approval is invisible.** An execution can enter `awaiting_approval`
partway through — for instance before merging. The job containing it stays
`IN_PROGRESS`. Nothing in the job model distinguishes a job that is working from
one that has been waiting on a human for three days. `GOVERNANCE_ANALYSIS`
covers only the pre-execution gate.

The third is the most damaging. A governance-first control plane that cannot show
that it is waiting for a person has failed at its primary job, and the failure is
silent.

## Goals
- Give the job level an explicit answer on retry.
- Add the terminal states the lifecycle is missing, with distinct meanings.
- Make a job blocked on human approval visible as a job state.
- Close RFC-0001's two Open Questions.

## Non-Goals
- Redefining execution-level retry. `execution/v1` owns that and its rules stand.
- Specifying SLA duration values or timeout policy configuration. This RFC adds
  the state a timeout lands in, not the policy that decides when.
- Task-level state machines. Still Future Work, and `execution/v1` covers the
  layer that would have needed it.

## Decision 1 — `FAILED` stays terminal; recovery is a new job

`FAILED is terminal` is confirmed, not relaxed. Retry exists at the execution
level only.

The recovery path for a failed job is a **new job** that records
`supersedes_job_id` pointing at the failed one.

This is the governance-preserving answer rather than the convenient one.
Reviving a `FAILED` job would resume work under an approval that was granted
against a plan which has since failed — execution continuing on a stale
`APPROVED`. A new job re-enters at `DRAFT` and passes `GOVERNANCE_ANALYSIS`
again, which is exactly the guarantee RFC-0001 exists to provide. The audit trail
gains a real chain of attempts instead of a job whose history loops.

A necessary clarification that follows: **a job does not enter `FAILED` because
one execution failed.** Orchestration owns retry per RFC-0004; the job reaches
`FAILED` only when orchestration has exhausted execution-level retries and cannot
proceed. Treating the first execution failure as job failure would make
`execution/v1`'s retry policy unreachable from the job level.

## Decision 2 — Add `CANCELLED` and `TIMED_OUT` as terminal job states

| state | meaning | entered from |
| --- | --- | --- |
| `CANCELLED` | explicitly stopped by a principal before completion | `DRAFT`, `GOVERNANCE_ANALYSIS`, `APPROVED`, `TASK_PLANNING`, `IN_PROGRESS`, `AWAITING_APPROVAL`, `VALIDATING`, `DEPLOYABLE` |
| `TIMED_OUT` | exceeded an SLA or timeout policy without completing | `GOVERNANCE_ANALYSIS`, `TASK_PLANNING`, `IN_PROGRESS`, `AWAITING_APPROVAL`, `VALIDATING` |

Both are terminal. Both require reason metadata, on the same rule RFC-0001
already applies to `FAILED`.

`CANCELLED` records the cancelling `Principal`. "Someone stopped this" is not an
audit record; "this principal stopped this at this time for this reason" is.

`TIMED_OUT` is reachable from `AWAITING_APPROVAL` specifically because an
approval that nobody answers is the most common way a governed pipeline stalls.
An expiring approval request must land somewhere honest rather than waiting
forever.

Neither state is a failure of the work, and neither should be counted as one.
This distinction is the reason for adding them.

## Decision 3 — Add `AWAITING_APPROVAL` as a non-terminal job state

A job enters `AWAITING_APPROVAL` when at least one of its executions is in
`awaiting_approval`, and leaves it when none remain.

```text
IN_PROGRESS  ⇄  AWAITING_APPROVAL
VALIDATING   ⇄  AWAITING_APPROVAL
DEPLOYABLE   ⇄  AWAITING_APPROVAL
```

The job records `awaiting_from` — the state it must return to once the approval
resolves. Without it, a job blocked during `DEPLOYABLE` would resume as
`IN_PROGRESS` and silently lose its position in the lifecycle.

Exits:

| from `AWAITING_APPROVAL` | when |
| --- | --- |
| back to `awaiting_from` | approval granted, or denied but orchestration can proceed without that execution |
| `FAILED` | approval denied and no path forward exists |
| `CANCELLED` | a principal stops the job while it waits |
| `TIMED_OUT` | the approval request expires unanswered |

`AWAITING_APPROVAL` is distinct from `GOVERNANCE_ANALYSIS` and does not replace
it. `GOVERNANCE_ANALYSIS` is the gate that decides whether work may begin;
`AWAITING_APPROVAL` is a pause inside work that has already been approved to
begin. Collapsing them would imply the whole job is being re-evaluated, which is
not what happens.

An explicit state is used rather than a `blocked_on` flag because "explicit state
machine workflow" is an architectural principle of this repository, and because
`STATE_TRANSITION` events already make transitions auditable — a flag change
would not be, and RFC-0003 guarantees no silent state change.

## Amended job lifecycle

```text
DRAFT
  → GOVERNANCE_ANALYSIS
      → APPROVED | REJECTED
APPROVED
  → TASK_PLANNING
      → IN_PROGRESS
          → VALIDATING
              → DEPLOYABLE
                  → COMPLETED
REJECTED         → DRAFT
IN_PROGRESS  ⇄ AWAITING_APPROVAL
VALIDATING   ⇄ AWAITING_APPROVAL
DEPLOYABLE   ⇄ AWAITING_APPROVAL

terminal: COMPLETED · FAILED · CANCELLED · TIMED_OUT
```

Thirteen states. `REJECTED` remains non-terminal and returns to `DRAFT`, as
RFC-0001 specified.

### On `REJECTED` reading differently across layers

`execution/v1` treats its `rejected` as terminal, while this repository's
`REJECTED` returns to `DRAFT`. Both are correct at their own layer — a job can be
revised and resubmitted, while an execution that was refused should not be
resurrected — and this RFC records the difference so that reading the two
documents together does not suggest a conflict.

## Open Questions from RFC-0001 — resolved

**Retry semantics for `FAILED`** — resolved by Decision 1. Retry is
execution-level only; job-level recovery is a new job with `supersedes_job_id`.

**Parallel task substates** — resolved by deferring to `execution/v1`. Parallel
work is modelled as child executions carrying `parent_execution_id`, not as
substates within one unit. The job level needs no parallel substates at all,
which is why the question stayed open: it was being asked at the wrong layer.

## Architectural Impact

- **Control Plane** — three new job states and one new field (`awaiting_from`).
  `supersedes_job_id` links recovery attempts. `FAILED` semantics unchanged.
- **Orchestration** — orchestration owns exhausting execution retries before
  reporting job failure, and propagates `awaiting_approval` up to the job level.
- **Execution** — no change. `execution/v1` retry and cancellation rules are
  adopted as-is.
- **Observability** — `STATE_TRANSITION` events now cover transitions into and
  out of `AWAITING_APPROVAL`, which makes "how long do approvals actually take"
  answerable from the audit log.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| `AWAITING_APPROVAL` and `GOVERNANCE_ANALYSIS` get conflated in implementation | medium | Stated as distinct with different entry conditions; `awaiting_from` has no meaning for `GOVERNANCE_ANALYSIS`, so the two cannot share a code path |
| `awaiting_from` is lost or wrong and a job resumes at the wrong state | medium | The field is required whenever `AWAITING_APPROVAL` is entered; entering without it is an invalid transition |
| Thirteen states is too many to implement correctly | low | Three of the additions are terminal or near-terminal and carry no branching logic; the working path is unchanged from RFC-0001 |
| "New job supersedes failed job" is more friction than a retry button | medium | Accepted deliberately — the friction is re-approval, which is the guarantee, not overhead |
| Job-level `TIMED_OUT` policy is undefined and the state is never used | low | Explicitly deferred; the state exists so the eventual policy has a destination that is not `FAILED` |

## Migration Plan

1. Accept this RFC.
2. `packages/core/state-machine.md` and `ARCHITECTURE.md` record the amended
   lifecycle. **Included in this change.**
3. RFC-0001 gains an amendment pointer to this RFC. **Included in this change.**
4. When `packages/core` gains code, the transition table is implemented from the
   amended lifecycle rather than from RFC-0001 alone.
5. SLA and timeout policy values are a later RFC. This one only provides the
   state they resolve into.

## Future Work
- SLA and timeout policy per job type — the values, not the state.
- Whether `supersedes_job_id` should carry forward artifacts from the failed
  attempt, or start clean.
