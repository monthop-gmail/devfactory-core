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

## Governance decisions

Per [RFC-0002](../../rfcs/0002-governance-decision-contract.md), rendered on the wire
as `approval/v1`. A decision is what moves a job out of `GOVERNANCE_ANALYSIS`; the
engine records it and emits `GOVERNANCE_DECISION` alongside the `STATE_TRANSITION` it
caused — an approval that leaves no record is not auditable, so there is no path to
`APPROVED` that skips one.

| decision | job goes to |
| --- | --- |
| `APPROVE` | `APPROVED` |
| `REJECT` | `REJECTED` |
| `REQUIRE_CHANGES` | **refused — `UnmappedDecision`** |

`REQUIRE_CHANGES` is part of the vocabulary (a closed set: dropping it would narrow
the contract this repository publishes) and has no destination, because no RFC here
says which state a job returned for changes lands in. `REJECTED` is ruled out by the
invariant *"REQUIRE_CHANGES ไม่ใช่ REJECT"*; `DRAFT` needs a `GOVERNANCE_ANALYSIS →
DRAFT` edge nothing declares; a fourteenth state is a lifecycle change. The engine
refuses rather than guessing — see `states.DECISION_TARGET`, and
[the open questions](#open-questions) below.

Guarantees the engine enforces, not just documents:

- decisions are immutable — changing one's mind is a second decision citing the first
  (`supersedes_decision_id`)
- a decision lives in the same tenant and workspace as the job it decides about;
  a mismatch is rejected, never coerced
- an agent may not `APPROVE` a job it is the principal for — *no agent has total
  authority*
- execution stays locked until the job holds an `APPROVE` record, not merely the
  `APPROVED` state

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
- `APPROVED` requires an explicit governance decision, recorded and emitted.
- `FAILED`, `CANCELLED`, and `TIMED_OUT` all require reason metadata.
- `CANCELLED` records the cancelling principal.

## Open questions

Recorded rather than answered — each needs an RFC, not a code change.

- Where does `REQUIRE_CHANGES` send a job? Until an RFC says, the engine refuses it.
- May a *person* approve a job they filed? `approval/v1` and RFC-0002 both state the
  self-approval invariant about agents only, so the engine refuses agent
  self-approval and allows the human case. Widening it would make this engine
  stricter than the contract it conforms to.
- `APPROVED` is in neither `FAILABLE` nor `TIMEOUTABLE`, so an approval nobody acts on
  has no automatic exit — [RFC-0010](../../rfcs/0010-failable-states.md) records this,
  and it is issue #17.
