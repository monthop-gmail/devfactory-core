# RFC-0011: Where `REQUIRE_CHANGES` Sends a Job

## Status
Draft — proposed 2026-08-19 · pending maintainer approval per `GOVERNANCE.md`

Amends [RFC-0001](0001-job-state-machine.md) as already amended by
[RFC-0007](0007-job-lifecycle-completeness.md) and [RFC-0010](0010-failable-states.md).
Completes [RFC-0002](0002-governance-decision-contract.md)'s decision vocabulary.

## Context

RFC-0002 declares three governance decisions — `APPROVE`, `REJECT`, `REQUIRE_CHANGES` —
and `contract-semantics.yaml` marks the set **closed**, so all three have to exist in
`DecisionType` or the contract this repository publishes would be narrower than the one
it wrote.

Two of the three have somewhere to send a job. The third does not. `states.py` has
carried a paragraph since PR #13 explaining that `REQUIRE_CHANGES` is deliberately
unmapped and that `Job.decide` raises `UnmappedDecision` for it; `platform-contract.yaml`
lists the missing RFC under `remaining`; `conformance/payload_check.py` has a check whose
job is to confirm the refusal still happens.

`approval/v1` already states what the decision *means*, as a frozen invariant:

> REQUIRE_CHANGES ไม่ใช่ REJECT — งานยังมีชีวิตและกลับมายื่นใหม่ได้

What it does not say — and what no RFC here said — is **which state the job lands in**,
which is the one thing the engine needs. Refusing was the right call while that was
open: an audit record whose recorded meaning is not the meaning that was made is the one
failure governance cannot absorb. But the refusal is a hole in a closed vocabulary, and
it has been open since issue #5.

## Problem Statement

Which state does a job enter when governance decides `REQUIRE_CHANGES`?

## Decision — `REQUIRE_CHANGES` sends a job to `DRAFT`, by a new
`GOVERNANCE_ANALYSIS → DRAFT` edge

```text
GOVERNANCE_ANALYSIS → APPROVED | REJECTED | DRAFT
```

`DECISION_TARGET` gains `REQUIRE_CHANGES → DRAFT`. The job lands in `DRAFT` holding no
approval, ready to be revised and resubmitted through the gate.

**Rationale.**

1. **It is the closest thing to the frozen guarantee.** *"งานยังมีชีวิตและกลับมายื่นใหม่
   ได้"* describes `DRAFT` exactly: it is where a job that has not been decided about sits,
   and the only state from which `GOVERNANCE_ANALYSIS` can be entered. Nothing has to be
   invented for the job to be alive there.

2. **It adds no vocabulary.** No new state, no new decision type, no new event type.
   That keeps the change inside this repository: a fourteenth state (`CHANGES_REQUESTED`)
   would be a `job_state_machine` vocabulary change, which under
   [RFC-0009](0009-vocabulary-extension.md) means `agent-platform` needs an ADR of its own
   before `execution/v1` can reference the state. A transition between two states both
   repositories already know about needs nothing from them.

3. **`REJECTED → DRAFT` was considered and is ruled out.** Routing `REQUIRE_CHANGES`
   through `REJECTED` would record it as a rejection, which contradicts the frozen
   invariant directly. It is not a near-miss; it is the thing the invariant forbids.

## The part that carries this RFC: the two are told apart by **route**, not by record

`REJECT` and `REQUIRE_CHANGES` now end in the same state. That is the obvious objection
to this decision, and it deserves a direct answer rather than an appeal to the decision
record.

They are distinguishable because they take **different paths**, and the path is in the
audit trail as `STATE_TRANSITION` events:

| decision | route | transitions | `REJECTED` in history |
| --- | --- | --- | --- |
| `REJECT` | `GOVERNANCE_ANALYSIS → REJECTED → DRAFT` | two | **yes, permanently** |
| `REQUIRE_CHANGES` | `GOVERNANCE_ANALYSIS → DRAFT` | one | no |

`REJECTED` is a state a rejected job *stands in*. The trail records entering it, and the
history is append-only, so a job that was rejected says so forever — even after it moves
on to `DRAFT` and is approved on a later pass. A job sent back for changes never entered
that state and its trail never claims it did.

This matters because it makes the distinction **checkable by a reader who was not
there**, from the log alone:

- A reader holding only the transitions can tell them apart: one trail passes through
  `REJECTED` and one does not.
- A reader holding only the decisions can tell them apart: one says `REJECT` and one says
  `REQUIRE_CHANGES`.
- The two halves check each other. A forged `GOVERNANCE_DECISION` claiming `REJECT` on the
  direct `GOVERNANCE_ANALYSIS → DRAFT` hop does not match the edge it sits on, and
  `devfactory_observability.replay` refuses the trail (`UnauditedDecision`) rather than
  believing the record.

So the guarantee *"REQUIRE_CHANGES ไม่ใช่ REJECT"* is not preserved by convention or by a
field that a reader has to trust. It is preserved by the shape of the trail, which the
engine cannot produce any other way and a replay re-derives independently. That is the
standard the rest of this lifecycle is held to, and this decision meets it.

`simulation/tests/test_e2e_flow.py` drives both flows and asserts the replayed routes
differ; `simulation/e2e_flow.py` check `[2b]` does the same in the runnable script.

## Consequence — a `REQUIRE_CHANGES` clears the approval

Being told to make changes is not being told to proceed. `REQUIRE_CHANGES` therefore
clears `Job.approval` exactly as `REJECT` does, and `replay` clears it on the reading side
for the same reason. An approval granted to an earlier revision must not authorise the
revised one; that rule was already in force for rejections and is not weakened here.

The direction lock is untouched: a job in `DRAFT` cannot reach any post-approval state
without passing `APPROVED` again, and `POST_APPROVAL` does not gain a member.

## Consequence — "is this transition a decision?" is a property of the edge

`DRAFT` is now reachable two ways and only one of them is a verdict:

- `GOVERNANCE_ANALYSIS → DRAFT` — a `REQUIRE_CHANGES`. Requires an authority and a
  reason, mints a `Decision`, emits `GOVERNANCE_DECISION`.
- `REJECTED → DRAFT` — the revision step that follows a rejection **already recorded**.
  Requires neither, and mints nothing.

Anything that answered that question by looking at the destination state — the engine's
`AUTHORITY_REQUIRED`, replay's `DECISION_BY_TARGET` — now has to look at the edge.
`states.DECISION_BY_EDGE` and `states.decision_for_edge()` replace `DECISION_BY_TARGET`,
and both the engine and the replay ask them. This is a real cost of sharing a
destination, and it is recorded here rather than discovered later: reading by destination
would demand a second verdict for a rejection already made, and would let a replay report
a resubmission as a decision nobody took.

## Non-Goals

- **Who may issue a `REQUIRE_CHANGES`.** Same authority rules as any other decision;
  RFC-0002 owns them and nothing here narrows or widens them. Note that the agent
  self-approval invariant is about `APPROVE` only, so — as with `REJECT` — an agent may
  ask for changes to its own job.
- **How many times a job may be sent back.** No limit is introduced. A loop between
  `DRAFT` and `GOVERNANCE_ANALYSIS` is a policy question, not a lifecycle one; see Risk.
- **What "the changes" are.** The reason string carries them. Structured change requests
  would be a payload change and belong to `agent-platform` under RFC-0005 Rule 1.
- **Timeout policy values**, exactly as RFC-0007 and RFC-0010 left them.

## Architectural Impact

- **Control Plane** — one edge added to `_PROGRESSION`, one entry to `DECISION_TARGET`,
  and the decision-or-not question re-keyed from destination to edge. `Job.require_changes()`
  joins `approve()` and `reject()`. `UnmappedDecision` keeps its place as the refusal a
  future fourth decision type meets if it declares a value without a destination.
- **Orchestration** — a job may now return to `DRAFT` without having been rejected.
  Anything that treated "reached `DRAFT` again" as implying a rejection was already wrong
  about `REJECTED → DRAFT` and is now wrong twice; the edge is the thing to read.
- **Execution** — no change. Nothing executes on either side of this edge.
- **Observability** — `GOVERNANCE_DECISION` payloads now really carry
  `decision: REQUIRE_CHANGES`, which `conformance/payload_check.py` validates against
  `approval/v1` as an emitted payload rather than as an assertion. Replay gains the
  ability to distinguish the two routes, which is what makes the guarantee auditable.
- **`agent-platform`** — nothing required. No vocabulary changed, so no ADR and no
  re-pin; see the version note below.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| A reader treats `REQUIRE_CHANGES` and `REJECT` as the same outcome because the destination is the same | **high** | The routes differ and the difference is in the trail; asserted in `simulation/tests/test_e2e_flow.py` on replayed histories, not just on live jobs. This is the decision's central claim and is tested as one |
| Code elsewhere keys "is this a decision?" on the destination state and mis-reads `REJECTED → DRAFT` | medium | `DECISION_BY_TARGET` is **removed**, not left alongside the new map, so a stale reader fails to import rather than reading the wrong answer. `states.py` remains the only declaration |
| A job ping-pongs between `DRAFT` and `GOVERNANCE_ANALYSIS` forever | medium | Out of scope by design (Non-Goals): each round trip is fully recorded, so the loop is visible in the trail and boundable by policy. Giving the lifecycle a retry counter would put a policy value in the state machine, which RFC-0007 and RFC-0010 both refuse |
| The new edge weakens "execution is forbidden before `APPROVED`" | low | `DRAFT` is on the far side of the gate and `POST_APPROVAL` is unchanged. Asserted directly in `test_states.py::test_the_new_edge_does_not_open_a_way_into_execution` and structurally by `test_approved_is_the_only_gate_into_execution` |
| The forward-path derivation in `simulation/flows.py` picks the wrong branch now that the gate has three exits | low | `DRAFT` joins `_NOT_FORWARD` — a return to the start is not progress. `main_line()` raises rather than choosing if the path ever genuinely forks, and `MAIN_LINE` is still asserted equal to issue #7's flow |
| A `REQUIRE_CHANGES` is used to smuggle a job past governance | low | It clears the approval, so it strictly reduces authority. There is no path from it into execution that does not pass `APPROVED` |

## `semantics_version` — no bump

`contract-semantics.yaml` gains the new edge under
`not_derived.job_state_machine.progression`, and its `contracts.approval.implementation_status`
block stops saying `REQUIRE_CHANGES` is unmapped.

Neither is inside a `frozen:` block. `decision_types` is untouched — the same three values,
still closed. `guarantees` and `invariants` under `contracts.approval` are untouched, and
the invariant this RFC is about (*"REQUIRE_CHANGES ไม่ใช่ REJECT"*) is now **enforced**
rather than merely stated, which is not a change to it.

So `semantics_version` stays at `1.1`, on the same grounds RFC-0010 used: the `frozen`
scope is untouched, and bumping would turn `agent-platform`'s drift check red for a block
it does not derive from. RFC-0010's Open Question — whether `not_derived` deserves a
signal of its own — remains open and this RFC does not settle it.

## Migration Plan

1. Accept this RFC.
2. `states.py` declares the edge and maps the decision; `DECISION_BY_TARGET` becomes
   `DECISION_BY_EDGE`. **Included in this change.**
3. `job.py` accepts `REQUIRE_CHANGES`, clears the approval, and gains
   `require_changes()`. **Included in this change.**
4. `replay.py` reads decisions by edge. **Included in this change.**
5. `contract-semantics.yaml` publishes the edge; `platform-contract.yaml` drops the item
   from `remaining`. **Included in this change.**
6. `conformance/payload_check.py`'s check inverts — from confirming the refusal to
   confirming the destination and the route. **Included in this change.**
7. `simulation/flows.py` gains `require_changes_then_resubmitted`, and the simulation
   proves replay separates it from the rejection flow. **Included in this change.**
8. `state-machine.md`, `packages/core/README.md`, and `ARCHITECTURE.md` stop describing
   the refusal. **Included in this change.**

## Future Work

- Whether a job sent back for changes should carry a pointer to the decision that sent it
  back, the way `supersedes_job_id` points at a `FAILED` job. Today the link is
  reconstructible from the trail; making it a field would be an `approval/v1` change and
  therefore `agent-platform`'s.
- Whether repeated `REQUIRE_CHANGES` rounds should be bounded, and by whom. Policy, not
  lifecycle.
