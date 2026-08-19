# core module

The job state machine and the governance decision interface that gates it.

Spec: [`state-machine.md`](state-machine.md) — [RFC-0001](../../rfcs/0001-job-state-machine.md)
as amended by [RFC-0007](../../rfcs/0007-job-lifecycle-completeness.md) and
[RFC-0011](../../rfcs/0011-require-changes-destination.md), with the tenant model from
[RFC-0006](../../rfcs/0006-tenant-workspace-model.md) and decisions from
[RFC-0002](../../rfcs/0002-governance-decision-contract.md).

In memory only. No persistence, no policy engine, no API. *Approval* is a decision by an
authority; *policy* — whether approval was needed at all — is `policy/v1` and is not
here.

## Use

```python
from devfactory_core import Job, JobState, Principal

alice = Principal("human", "alice")
bob = Principal("human", "bob")
job = Job(
    job_id="job-001",
    tenant_id="default",      # RFC-0006: never omitted, even single-tenant
    workspace_id="ws-core",
    principal=alice,
)

job.submit_for_governance(reason="ready for review")

decision = job.approve(authority=bob, reason="scope matches milestone v0.1")
assert job.approval is decision          # what execution runs under, not a boolean
assert decision.as_payload()["decision"] == "APPROVE"   # approval/v1 wire shape

job.transition(JobState.TASK_PLANNING)
job.transition(JobState.IN_PROGRESS)

job.pause_for_approval(reason="merge needs sign-off")
assert job.state is JobState.AWAITING_APPROVAL
assert job.awaiting_from is JobState.IN_PROGRESS
job.resume(reason="approved", principal=bob)

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
| any post-approval state under an approval past its `expires_at` | `ExpiredApproval` — it has to be granted again |
| `FAILED` / `CANCELLED` / `TIMED_OUT` without a reason | `MissingReason` |
| `CANCELLED` without a principal | `MissingPrincipal` |
| a transition that *is* a decision, without an authority and reason | `MissingAuthority` — including `GOVERNANCE_ANALYSIS → DRAFT`, which is a `REQUIRE_CHANGES` |
| pausing outside `IN_PROGRESS` / `VALIDATING` / `DEPLOYABLE` | `MissingApprovalContext` |
| resuming into a state other than `awaiting_from` | `WrongResumeState` |
| a malformed identifier | `InvalidIdentifier` — `identity/v1` `Id` form |
| a decision missing decision, reason, authority, or timestamp | `IncompleteDecision` |
| an agent approving the job it is the principal for | `SelfApproval` |
| a decision from another tenant or workspace | `CrossTenantDecision` — rejected, never coerced |
| a decision about another job | `WrongDecisionSubject` |
| a decision that does not produce the transition being made | `DecisionStateMismatch` |

A refused call leaves the job untouched and writes nothing to the audit trail.

## Events

Construction emits `JOB_CREATED`; every accepted transition emits `STATE_TRANSITION`;
an edge that *is* a governance decision also emits `GOVERNANCE_DECISION`; reaching
`COMPLETED` also emits `JOB_COMPLETED`. There is no way to change state without going
through `transition()`, which is what makes *no silent state change* hold rather than
merely be documented — and no way to reach `APPROVED` without a decision record, which
is what makes *every APPROVE is auditable* hold.

`event_payloads()` renders the trail in `event/v1` wire shape, and
`Decision.as_payload()` renders a decision in `approval/v1` shape. Neither is validated
here — owning a copy of the schema would be a parallel schema, which
[RFC-0005](../../rfcs/0005-platform-contract-authority.md) Rule 4 forbids.
`conformance/payload_check.py` validates both against the pinned contracts, and
`devfactory_observability.replay` reads the trail back to check it can account for how
the job got where it is — driven end to end by [`simulation/`](../../simulation/).

## Decisions

```python
from devfactory_core import DecisionType

job.decide(DecisionType.APPROVE, authority=bob, reason="scope agreed")  # == job.approve(...)
job.decisions          # every decision made about this job, immutable, in order
job.approval           # the APPROVE it executes under, or None

job.require_changes(authority=bob, reason="add tests")   # → DRAFT, RFC-0011
```

All three of RFC-0002's decisions are executable.
[RFC-0011](../../rfcs/0011-require-changes-destination.md) settled the last one:
`REQUIRE_CHANGES` sends a job to `DRAFT` by the `GOVERNANCE_ANALYSIS → DRAFT` edge,
clearing the approval on the way — being told to make changes is not being told to
proceed.

It stays distinguishable from a `REJECT`, which ends in the same place, by the **route**:
a rejection goes `GOVERNANCE_ANALYSIS → REJECTED → DRAFT` and leaves `REJECTED` standing
in the job's history forever, while a `REQUIRE_CHANGES` goes straight there and never
enters that state. The distinction is in the trail, so a reader who was not present can
make it from the log alone — which is what makes *"REQUIRE_CHANGES ไม่ใช่ REJECT"* a
checked guarantee rather than a stated one.

One thing to read carefully if you consume the lifecycle: **whether a transition is a
decision is a property of the edge, not the destination.** `GOVERNANCE_ANALYSIS → DRAFT`
is a `REQUIRE_CHANGES`; `REJECTED → DRAFT` is the ordinary revision step and is not a
decision at all. Ask `states.decision_for_edge()`.

## Tests

```bash
cd packages/core
python -m pytest                       # the full core suite
python -m pytest --cov=devfactory_core # coverage gate at 90%
```

## Which states may fail

Settled by [RFC-0010](../../rfcs/0010-failable-states.md): `FAILED` is reachable from
`TASK_PLANNING`, `IN_PROGRESS`, `AWAITING_APPROVAL`, `VALIDATING`, and `DEPLOYABLE` —
the states where work exists to fail — and refused before `APPROVED`, where the honest
outcomes are `REJECTED`, `CANCELLED`, or `TIMED_OUT`. See `states.FAILABLE`.

## Approval expiry

`approval/v1` carries `expires_at` and says what it means: an approval past it cannot be
used to run work and has to be granted again. `Decision` stores it, `approve()` takes it,
and the engine refuses to move a job into a post-approval state under a lapsed one.

```python
job.approve(authority=bob, reason="scope agreed", expires_at=deadline)
job.approval_expires_at   # the deadline, or None
job.approval_expired      # whether it has passed, as of now
```

`expires_at` is optional in the contract and optional here — an approval without one never
expires. What changed with issue #17 is that `APPROVED` is now in `TIMEOUTABLE`
([RFC-0007 Amendment 1](../../rfcs/0007-job-lifecycle-completeness.md#amendment-1--approved-may-time-out-2026-08-19)),
so a job holding an approval that ran out has an honest terminal to reach instead of
waiting for a human to cancel it. The timeout *policy* — how long an approval is good for
— stays out of scope, as it is in RFC-0007 and RFC-0010.
