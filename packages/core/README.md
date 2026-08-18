# core module

The job state machine — the control plane's lifecycle engine.

Spec: [`state-machine.md`](state-machine.md) — [RFC-0001](../../rfcs/0001-job-state-machine.md)
as amended by [RFC-0007](../../rfcs/0007-job-lifecycle-completeness.md), with the tenant
model from [RFC-0006](../../rfcs/0006-tenant-workspace-model.md).

In memory only. No persistence, no policy engine, no API — those are issues #5, #6, and #7.

## Use

```python
from devfactory_core import Job, JobState, Principal

alice = Principal("human", "alice")
job = Job(
    job_id="job-001",
    tenant_id="default",      # RFC-0006: never omitted, even single-tenant
    workspace_id="ws-core",
    principal=alice,
)

job.submit_for_governance(reason="ready for review")
job.approve(authority=alice, reason="scope matches milestone v0.1")
job.transition(JobState.TASK_PLANNING)
job.transition(JobState.IN_PROGRESS)

job.pause_for_approval(reason="merge needs sign-off")
assert job.state is JobState.AWAITING_APPROVAL
assert job.awaiting_from is JobState.IN_PROGRESS
job.resume(reason="approved", principal=alice)

job.transition(JobState.VALIDATING)
job.transition(JobState.DEPLOYABLE)
job.transition(JobState.COMPLETED)

job.event_payloads()   # audit trail in event/v1 wire shape
```

## What the engine refuses

Every refusal below is deliberate. The lifecycle exists so governance can be
enforced, so the engine rejects rather than repairs.

| call | refusal |
| --- | --- |
| an edge not in the table | `InvalidTransition`, naming what *was* allowed |
| anything out of `COMPLETED` / `FAILED` / `CANCELLED` / `TIMED_OUT` | `TerminalState` — recovery is `supersede()`, not a revival |
| `TASK_PLANNING` before `APPROVED` | `InvalidTransition` — execution is forbidden before approval |
| `FAILED` / `CANCELLED` / `TIMED_OUT` without a reason | `MissingReason` |
| `CANCELLED` without a principal | `MissingPrincipal` |
| `APPROVED` / `REJECTED` without an authority and reason | `MissingAuthority` |
| pausing outside `IN_PROGRESS` / `VALIDATING` / `DEPLOYABLE` | `MissingApprovalContext` |
| resuming into a state other than `awaiting_from` | `WrongResumeState` |
| a malformed identifier | `InvalidIdentifier` — `identity/v1` `Id` form |

A refused call leaves the job untouched and writes nothing to the audit trail.

## Events

Construction emits `JOB_CREATED`; every accepted transition emits `STATE_TRANSITION`;
reaching `COMPLETED` also emits `JOB_COMPLETED`. There is no way to change state
without going through `transition()`, which is what makes *no silent state change*
hold rather than merely be documented.

`event_payloads()` renders the trail in `event/v1` wire shape. It is **not** validated
here — owning a copy of the schema would be a parallel schema, which
[RFC-0005](../../rfcs/0005-platform-contract-authority.md) Rule 4 forbids. Validation
against the pinned contract is issue #6, and these payloads are what it will validate.

## Tests

```bash
cd packages/core
python -m pytest                       # 235 tests
python -m pytest --cov=devfactory_core # coverage gate at 90%, currently 100%
```

## Open question

`state-machine.md` says `FAILED` is terminal and lists `AWAITING_APPROVAL -> FAILED`,
but never enumerates which other states may fail. This module permits `FAILED` from
`TASK_PLANNING`, `IN_PROGRESS`, `AWAITING_APPROVAL`, `VALIDATING`, and `DEPLOYABLE` —
the states where work exists to fail — and refuses it before `APPROVED`, where the
honest outcomes are `REJECTED`, `CANCELLED`, or `TIMED_OUT`. That reading needs
confirming in an RFC; see `states.FAILABLE`.
