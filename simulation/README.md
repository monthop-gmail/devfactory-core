# End-to-end flow simulation

Issue [#7](https://github.com/monthop-gmail/devfactory-core/issues/7) — the closing
gate on [milestone v0.1](../docs/governance/MILESTONE_v0.1.md).

A governed job is driven end to end through the real state machine
([`packages/core`](../packages/core/)), everything it emits is filed in the real
audit log ([`packages/observability`](../packages/observability/)), and the trail
is then read back to check it accounts for what happened.

## Run it

```bash
python3 simulation/e2e_flow.py            # the simulation, with findings
python3 simulation/e2e_flow.py --trail    # …and the audit trail it produced
python3 simulation/e2e_flow.py --json     # machine-readable result
python3 -m pytest simulation/tests        # the same guarantees, as a suite
```

Both are run in CI. The script is what the issue asks for and what a person runs
to watch a job move; the suite is what stops the guarantees regressing when
nobody is watching. Exit code is non-zero if any check fails.

## What it checks

| # | issue #7 asks | where |
| --- | --- | --- |
| 1 | the full flow, `DRAFT → … → COMPLETED` | `check_full_flow` |
| 2 | `REJECTED → DRAFT`, then resubmitted | `check_rejection_flow` |
| 3 | `FAILED` with a reason, from a state `FAILABLE` allows | `check_failure_flow` |
| 4 | the governance gate blocks execution without an `APPROVE` | `check_governance_gate` |
| 5 | every transition emits an audit event | `check_every_transition_is_audited` |
| 6 | the log is complete and replays to the same state | `check_replay` |
| 7 | a runnable script or a test suite | this directory — both |

One flow arrived after issue #7 was written: `approval_expired` drives a job whose
`APPROVE` lapsed where it sat, which settles at `TIMED_OUT` rather than at a
failure. It is in the run so the trail checks 5 and 6 work on covers the
`APPROVED → TIMED_OUT` edge and an approval carrying `expires_at`
([RFC-0007 Amendment 1](../rfcs/0007-job-lifecycle-completeness.md#amendment-1--approved-may-time-out-2026-08-19),
issue #17). The refusals that go with it — the engine declining to move a job on a
lapsed approval, and replay declining a trail that shows it happening — are in
`simulation/tests/test_e2e_flow.py` and `conformance/payload_check.py`, since
neither can be *produced* by a run that behaves correctly.

## The forward path is read, not retyped

`packages/core/devfactory_core/states.py` is the only place the transition table
is expressed. A simulation that wrote `APPROVED → TASK_PLANNING` into itself in
order to walk it would be a second declaration with the first one's authority, so
`flows.main_line()` *derives* the path instead: at each state, discard the exits
available from nearly everywhere — `CANCELLED`, `TIMED_OUT`, `FAILED`, `REJECTED`,
and the `AWAITING_APPROVAL` pause — and one successor is left.

The flow issue #7 spells out appears exactly once, as the thing that derivation is
compared *against*. If the table and the issue ever disagree, the comparison fails
and says so, instead of the simulation quietly following the issue and reporting
success.

## Replay is a completeness proof

`devfactory_observability.replay` rebuilds a job from its events alone. Every
`STATE_TRANSITION` names the state it left, so a replay holding a running state
notices a record that is missing or out of order — which the engine cannot,
having written them. A trail that replays cleanly is a trail with no gaps in it,
and that is what makes "the audit log is complete" a checked claim rather than a
stated one.

Two limits, recorded rather than smoothed over:

- **A truncated tail is only detectable for a job that completed.** `JOB_COMPLETED`
  is what says a transition should have followed; nothing says so for `FAILED`,
  `CANCELLED`, or `TIMED_OUT`, or for a job still in flight. Closing that needs a
  per-job sequence number in `event/v1`, which is a contract change.
- **`UnauditedExecution` cannot fire on a table-consistent trail**, because
  `TASK_PLANNING` is reachable only from `APPROVED` and `APPROVED` is refused
  without a decision. It is a structural backstop, kept for the same reason
  `job.py` keeps `ExecutionBeforeApproval`: it fires when the table itself is
  wrong, which is exactly when it is worth having.

## Shared with conformance

`conformance/payload_check.py` drives the same flows from `flows.py`. It asks a
different question — do the payloads conform to the pinned `event/v1` and
`approval/v1` — but it should not be asking it about a *different* journey. Two
files describing the same lifecycle differently is how they end up disagreeing
about it.
