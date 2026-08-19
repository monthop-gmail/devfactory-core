# Job State Machine Spec

Canonical states per [RFC-0001](../../rfcs/0001-job-state-machine.md) as amended by
[RFC-0007](../../rfcs/0007-job-lifecycle-completeness.md).

## States

- DRAFT
- GOVERNANCE_ANALYSIS
- APPROVED
- REJECTED
- TASK_PLANNING
- IN_PROGRESS
- AWAITING_APPROVAL
- VALIDATING
- DEPLOYABLE
- COMPLETED
- FAILED
- CANCELLED
- TIMED_OUT

Terminal: `COMPLETED` · `FAILED` · `CANCELLED` · `TIMED_OUT`
`REJECTED` is not terminal — it returns to `DRAFT`.

## Transitions

```text
DRAFT               → GOVERNANCE_ANALYSIS
GOVERNANCE_ANALYSIS → APPROVED | REJECTED
APPROVED            → TASK_PLANNING
REJECTED            → DRAFT
TASK_PLANNING       → IN_PROGRESS
IN_PROGRESS         → VALIDATING | AWAITING_APPROVAL
VALIDATING          → DEPLOYABLE | AWAITING_APPROVAL
DEPLOYABLE          → COMPLETED | AWAITING_APPROVAL
AWAITING_APPROVAL   → <awaiting_from> | FAILED
```

Execution is forbidden before `APPROVED`.
`FAILED` is terminal — recovery is a new job carrying `supersedes_job_id`, not a retry.

`CANCELLED` is reachable from every non-terminal state.
`TIMED_OUT` is reachable from `GOVERNANCE_ANALYSIS`, `TASK_PLANNING`, `IN_PROGRESS`,
`AWAITING_APPROVAL`, and `VALIDATING`.
`FAILED` is reachable from `TASK_PLANNING`, `IN_PROGRESS`, `AWAITING_APPROVAL`,
`VALIDATING`, and `DEPLOYABLE` — the states where work exists to fail — and from nowhere
before `APPROVED`, where the honest outcomes are `REJECTED`, `CANCELLED`, or `TIMED_OUT`
([RFC-0010](../../rfcs/0010-failable-states.md)).

## AWAITING_APPROVAL

Entered when at least one execution of the job is in `awaiting_approval`
(`contracts/execution/v1`); left when none remain.

`awaiting_from` is required on entry and names the state to return to. It is
distinct from `GOVERNANCE_ANALYSIS`: that is the gate deciding whether work may
begin, this is a pause inside work already approved to begin.

## Required job fields

Per [RFC-0006](../../rfcs/0006-tenant-workspace-model.md), using `identity/v1`
definitions:

| field | required | notes |
| --- | --- | --- |
| `tenant_id` | ✅ | hard isolation boundary · single-tenant deployments use `default`, never omit |
| `workspace_id` | ✅ | a job is always work inside one workspace |
| `principal` | ✅ | who created the job |
| `awaiting_from` | when `AWAITING_APPROVAL` | state to return to |
| `supersedes_job_id` | when recovering | points at the `FAILED` job this replaces |

## Guarantees

- Every transition emits a `STATE_TRANSITION` event — no silent state change.
- `APPROVED` requires an explicit governance decision.
- `FAILED`, `CANCELLED`, and `TIMED_OUT` all require reason metadata.
- `CANCELLED` records the cancelling principal.
