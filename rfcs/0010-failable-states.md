# RFC-0010: Which States May Reach `FAILED`

## Status
Draft — proposed 2026-08-19 · pending maintainer approval per `GOVERNANCE.md`

Amends [RFC-0001](0001-job-state-machine.md) as already amended by
[RFC-0007](0007-job-lifecycle-completeness.md).
Closes [issue #14](https://github.com/monthop-gmail/devfactory-core/issues/14).

## Context

`packages/core/state-machine.md` states that `FAILED` is terminal and lists exactly one
edge into it — `AWAITING_APPROVAL -> FAILED`. It never enumerates which other states may
fail. RFC-0001 does not say. RFC-0007 says *when* a job should fail (orchestration has
exhausted execution-level retries) but not *from where*.

Implementing the state machine in [PR #13](https://github.com/monthop-gmail/devfactory-core/pull/13)
forced the question, because a transition table cannot be built without an answer. That PR
shipped `states.FAILABLE` with a reading marked as needing confirmation, and left an
"Open question" note in `job.py`, `packages/core/README.md`, and `states.py`.

Two consequences follow from leaving it unconfirmed, and both are already live on `main`:

1. The code has made an architectural decision without an RFC, which `CONTRIBUTING.md`
   requires for lifecycle changes.
2. `contract-semantics.yaml` publishes `job_state_machine` under `not_derived` for
   `agent-platform` to reference. It declares `states`, `terminal`, `invariants`, and
   `layering` — but no transitions and no `failable`. A consumer reading the manifest to
   understand job semantics cannot see the rule the code enforces, which sits badly with
   the `event/v1` guarantee of *no silent state change*.

## Problem Statement

Which of the thirteen job states may transition to `FAILED`?

## Decision 1 — `FAILED` is reachable only from the five post-approval working states

```text
TASK_PLANNING · IN_PROGRESS · AWAITING_APPROVAL · VALIDATING · DEPLOYABLE
```

and is **refused** from:

```text
DRAFT · GOVERNANCE_ANALYSIS · APPROVED · REJECTED
```

This ratifies what `states.FAILABLE` already implements. No code change.

**Rationale.** A job fails where work exists to fail. Before `APPROVED` nothing is
executing — RFC-0001's own invariant forbids it — so there is no execution whose failure
`FAILED` could describe. The honest outcomes before approval are already covered and are
each distinct:

| outcome | means |
| --- | --- |
| `REJECTED` | governance decided no — a verdict, not a malfunction |
| `CANCELLED` | a human stopped it |
| `TIMED_OUT` | nobody answered in time |

Allowing `FAILED` before `APPROVED` would let these three collapse into one bucket, which
is the same argument RFC-0007 used to introduce `CANCELLED` and `TIMED_OUT` in the first
place. Preserving the distinction is the point.

`AWAITING_APPROVAL` is included because the executions underneath it are real work that can
fail while the job waits, and `state-machine.md` already listed that edge.

## Decision 2 — a `GOVERNANCE_ANALYSIS` malfunction resolves as `TIMED_OUT`, deliberately

If the governance analyzer itself errors — an infrastructure fault, not a verdict — none of
`REJECTED`, `CANCELLED`, or `FAILED` is available under Decision 1. The reachable terminal
is `TIMED_OUT`, since `GOVERNANCE_ANALYSIS` is in `TIMEOUTABLE`.

**This is accepted, not overlooked.** A job that crashed during analysis waits for its
timeout instead of settling immediately. The cost is latency to terminal state; the benefit
is that `FAILED` keeps meaning *approved work that did not succeed*, which is what the
`supersedes_job_id` recovery path in RFC-0007 assumes. A `FAILED` job that never passed
governance would have nothing coherent to supersede.

Recorded here explicitly so a future reader does not mistake it for an omission.

## Non-Goals

Job-level timeout **policy values** stay out of scope, exactly as RFC-0007 left them.
This RFC only states which terminal a malfunction resolves into.

## Open Question — `APPROVED` can stall with no automatic exit

`APPROVED` is in neither `FAILABLE` nor `TIMEOUTABLE`. Its only exits are `TASK_PLANNING`
and `CANCELLED`. If orchestration dies after the approval is recorded but before planning
starts, the job stays in `APPROVED` indefinitely and only a human cancelling it will move
it. It is the one state in the lifecycle that can stall with no automatic exit at all.

Treating `APPROVED` as instantaneous is a reasonable reading — that is presumably why it was
left out of `TIMEOUTABLE` — but a state that is only instantaneous when nothing goes wrong
is precisely the one worth a timeout.

**Recommendation:** add `APPROVED` to `TIMEOUTABLE` in a follow-up. Deliberately not decided
here — it is a `TIMED_OUT` question, and issue #14 asked about `FAILED`. Deciding it in this
RFC would also mean a code change, whereas everything above is ratification. Suggest a
separate issue.

## Open Question — `not_derived` changes are unversioned

`semantics_version` is tied to the `frozen` block, which governs derived contracts. This RFC
adds declarations under `not_derived.job_state_machine`, which no consumer derives from but
`agent-platform` is invited to reference.

Nothing today tells a consumer that block changed. Bumping `semantics_version` would signal
it, but at an immediate cross-repo cost: `agent-platform` pins the file-level
`semantics_version` in every `derived_from`, so a bump turns its drift check red until it
re-pins — for a change to a block it does not derive from.

**No bump in this change**, on the grounds that the `frozen` scope is untouched. Whether
`not_derived` deserves a signal of its own is left open.

## Architectural Impact

- **Control Plane** — no behavioural change. The rule already in `states.FAILABLE` gains the
  RFC that `CONTRIBUTING.md` requires, and `contract-semantics.yaml` publishes it.
- **Orchestration** — unchanged. RFC-0007 already owns *when* to report job failure; this
  RFC only bounds *from where*.
- **Execution** — no change.
- **Observability** — `STATE_TRANSITION` consumers can now validate transitions against a
  declared table instead of inferring one, which is what *no silent state change* needs in
  order to be checkable by anyone other than this repository.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| A real infrastructure failure during `GOVERNANCE_ANALYSIS` looks like a timeout in the audit log | medium | Accepted in Decision 2; `TIMED_OUT` requires reason metadata already, so the cause is recorded even though the state is shared |
| `APPROVED` stalls unnoticed | medium | Open Question above; unchanged from today's behaviour, now written down instead of implicit |
| Manifest and code drift apart again | medium | The declarations added here are mechanically comparable to `states.py`; a conformance check that compares them is cheap follow-up work |
| Ratifying the implementation makes the RFC a rubber stamp | low | Decision 1's rationale stands independently of the code, and Decisions 2 and the two Open Questions are new findings that implementing surfaced |

## Migration Plan

1. Accept this RFC.
2. `contract-semantics.yaml` records `progression`, `awaiting_from`, `failable`,
   `timeoutable`, and `cancellable` under `not_derived.job_state_machine`.
   **Included in this change.**
3. `packages/core/state-machine.md` enumerates the `FAILED` edges instead of listing one.
   **Included in this change.**
4. The "Open question" notes in `job.py`, `packages/core/README.md`, and `states.py` are
   replaced with a pointer to this RFC. **Included in this change.**
5. RFC-0001 gains an amendment pointer. **Included in this change.**
6. `APPROVED` in `TIMEOUTABLE` — separate issue, separate RFC.

## Future Work

- A conformance check that `contract-semantics.yaml` still matches `states.py`, so the two
  cannot drift silently the way they did between PR #13 and this RFC.
- Whether `not_derived` needs a version signal of its own.
