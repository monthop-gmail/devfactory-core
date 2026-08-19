# Job State Machine Spec

Canonical states per [RFC-0001](../../rfcs/0001-job-state-machine.md) as amended by
[RFC-0007](../../rfcs/0007-job-lifecycle-completeness.md) and
[RFC-0011](../../rfcs/0011-require-changes-destination.md).

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
GOVERNANCE_ANALYSIS → APPROVED | REJECTED | DRAFT
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
`TIMED_OUT` is reachable from `GOVERNANCE_ANALYSIS`, `APPROVED`, `TASK_PLANNING`,
`IN_PROGRESS`, `AWAITING_APPROVAL`, and `VALIDATING` — `APPROVED` since
[RFC-0007 Amendment 1](../../rfcs/0007-job-lifecycle-completeness.md#amendment-1--approved-may-time-out-2026-08-19),
because an approval must expire (issue #17).
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

| decision | job goes to | route |
| --- | --- | --- |
| `APPROVE` | `APPROVED` | `GOVERNANCE_ANALYSIS → APPROVED` |
| `REJECT` | `REJECTED` | `GOVERNANCE_ANALYSIS → REJECTED → DRAFT` |
| `REQUIRE_CHANGES` | `DRAFT` | `GOVERNANCE_ANALYSIS → DRAFT` |

### `REQUIRE_CHANGES` is not a `REJECT`, and the trail says so

Both end in `DRAFT`. They are told apart by the **route**, which is in the audit log as
`STATE_TRANSITION` records: a rejected job stands in `REJECTED` and its history says so
forever, while a job sent back for changes never entered that state.

That is what keeps `approval/v1`'s invariant — *"REQUIRE_CHANGES ไม่ใช่ REJECT — งานยัง
มีชีวิตและกลับมายื่นใหม่ได้"* — checkable rather than merely stated. A reader holding only
the transitions can tell them apart; so can a reader holding only the decisions; and the
two halves check each other, so a `GOVERNANCE_DECISION` claiming `REJECT` on the direct
`GOVERNANCE_ANALYSIS → DRAFT` hop is refused on replay rather than believed. See
[RFC-0011](../../rfcs/0011-require-changes-destination.md).

A `REQUIRE_CHANGES` clears the approval in force, exactly as a `REJECT` does — being told
to make changes is not being told to proceed.

One consequence worth reading twice: **whether a transition is a decision is a property
of the edge, not of the destination.** `GOVERNANCE_ANALYSIS → DRAFT` is a
`REQUIRE_CHANGES` and needs an authority and a reason; `REJECTED → DRAFT` is the revision
step after a verdict already recorded and needs neither. `states.DECISION_BY_EDGE` is what
answers that question, and both the engine and `devfactory_observability.replay` ask it.

Guarantees the engine enforces, not just documents:

- decisions are immutable — changing one's mind is a second decision citing the first
  (`Decision.supersedes_decision_id`, rendered as `approval/v1`'s
  `supersedes_approval_id`; ids keep our name in Python and take theirs on the wire,
  exactly as `decision_id` → `approval_id` does — see `decision.WIRE_FIELD_NAMES`)
- a decision lives in the same tenant and workspace as the job it decides about;
  a mismatch is rejected, never coerced
- an agent may not `APPROVE` a job it is the principal for — *no agent has total
  authority*
- execution stays locked until the job holds an `APPROVE` record, not merely the
  `APPROVED` state
- an approval past its `expires_at` unlocks nothing: entering a post-approval state
  under one is refused (`ExpiredApproval`), and a trail that records it happening is
  refused on replay (`ExecutionAfterExpiry`). `expires_at` is optional in
  `approval/v1` and stays optional here — an approval with no deadline never expires

## Approval expiry

`approval/v1` carries `expires_at` and states the rule: *"approval ที่หมดอายุแล้วใช้เดินงาน
ไม่ได้ ต้องขอใหม่ · งานที่ค้างรออนุมัติจนเลยกำหนดควรเข้าสถานะ timeout ไม่ใช่รอตลอดไป"*.

```python
job.approve(authority=bob, reason="scope agreed", expires_at=deadline)
job.approval_expires_at   # the deadline, or None
job.approval_expired      # whether it has passed, as of now
```

Once it has passed, the job cannot move into `TASK_PLANNING`, `IN_PROGRESS`,
`AWAITING_APPROVAL`, `VALIDATING`, or `DEPLOYABLE` — the states that mean execution was
authorised, including the way back out of a pause. What remains is a fresh `APPROVE`,
`CANCELLED`, or `TIMED_OUT`;
[RFC-0007 Amendment 1](../../rfcs/0007-job-lifecycle-completeness.md#amendment-1--approved-may-time-out-2026-08-19)
added the last of those so a job stalled in `APPROVED` has an honest terminal.

Timeout **policy** — how long an approval is good for — is not set here. RFC-0007 and
RFC-0010 both leave the values out of scope, so nothing in this repository supplies a
default `expires_at` or fires a timeout on its own.

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
- The trail is complete enough to reconstruct the job from it —
  `devfactory_observability.replay` rebuilds state, `awaiting_from`, the approval in
  force, and the whole history from the events alone, and refuses a trail that has a
  gap in it. This is what RFC-0010 anticipated when it noted that consumers can
  validate transitions "against a declared table instead of inferring one": replay
  checks every recorded edge against `states.reachable_from`, so nothing outside
  `states.py` holds an opinion about the lifecycle. Driven end to end by
  [`simulation/`](../../simulation/) — issue #7.

## Open questions

Recorded rather than answered — each needs an RFC, not a code change.

- How many times may a job be sent back for changes? RFC-0011 sets no limit: each round
  trip is recorded, so the loop is visible in the trail and boundable by policy, but a
  retry counter in the state machine would be a policy value in the lifecycle.

- May a *person* approve a job they filed? `approval/v1` and RFC-0002 both state the
  self-approval invariant about agents only, so the engine refuses agent
  self-approval and allows the human case. Widening it would make this engine
  stricter than the contract it conforms to.
- How long is an approval good for? `expires_at` is enforced when it is set, and nothing
  here sets it. The policy values are Future Work in both
  [RFC-0007](../../rfcs/0007-job-lifecycle-completeness.md) and
  [RFC-0010](../../rfcs/0010-failable-states.md), so an approval granted without a
  deadline is still an approval that never expires — the state now exists for one that
  does. Settling the values is orchestration's, and needs an RFC of its own.
