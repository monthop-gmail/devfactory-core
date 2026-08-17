# RFC-0009: Vocabulary Extension Is Additive

## Status
Draft — Architecture Owner direction agreed 2026-08-18 · pending maintainer approval per `GOVERNANCE.md`

**Amends [RFC-0005](0005-platform-contract-authority.md) Rule 2** and its mirror in
[ADR-0006](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0006-contract-versioning.md).
Everything else in RFC-0005 stands.

Raw proposal that prompted this: [`references/team-notes/2026-08-18-adr-0006-ownership-transfer.md`](../references/team-notes/2026-08-18-adr-0006-ownership-transfer.md)

## Context
RFC-0005 split contract authority: semantics here, canonical wire schema at
`agent-platform`. On 2026-08-18 `agent-platform` accepted that split as ADR-0006
option **C2**, added it as a third option alongside A2 and B2, and published
`contracts/approval/v1` and `contracts/event/v1` as canonical. Both carry
`derived_from` pointers to `contract-semantics.yaml` at `semantics_version: "1.0"`
and mark their semantic sections with 🔒.

The split is therefore in force on both sides, and the two contracts that were
blocked for two days now exist.

A team review of that arrangement raised one objection, and it is correct.

## Problem Statement
RFC-0005 Rule 2 classifies five kinds of change as semantic, each requiring an RFC
in this repository first. The first of those five is:

> adding, removing, or renaming a decision type or event type

**Adding** does not belong in that list — at least not for event types.

An event type is a new category of observation. When `navi-security-agent` needs
one for a sighting, or `enterprise-knowledge` needs one for ACL-aware retrieval,
Rule 2 sends that change through this repository: an RFC, a majority maintainer
vote, an Architecture Owner with no stake in the outcome, for vocabulary Dev
Factory does not use and has no view on. Multiply by the repositories being
planned and Dev Factory becomes the ecosystem's gatekeeper by accident — the
opposite of the intended topology, where `agent-platform` sits at the centre and
Dev Factory is one consumer among several.

The published schema already disagrees with the rule. `event/v1` carries this
under `platform_rules`:

> consumer ที่เจอ `event_type` ที่ไม่รู้จักต้องเก็บ event ไว้แล้วข้ามการตีความ ห้าม drop และห้าม fail

A contract that instructs consumers how to behave when they meet an unknown event
type is a contract that expects its enum to grow. Rule 2 made that growth require
an RFC here. The schema and the ownership rule were describing different futures.

## Goals
- Remove the bottleneck for vocabulary other repositories need.
- Keep the protections that made the split worth having.
- Change as little as possible: ADR-0006 is accepted and two contracts are
  published.

## Non-Goals
- Reopening the ownership question. C2 is accepted on both sides and this RFC
  does not disturb it.
- Changing the wire schema. Which fields exist and what they are called remains
  `agent-platform`'s, exactly as before.

## Decision — separate *adding* from *changing*, and treat events and decisions differently

### Event types: adding is additive

`agent-platform` may add new event types under Rule 1, on its own ADR process,
with no RFC here.

The seven canonical types stay exactly as they are:

```text
JOB_CREATED · STATE_TRANSITION · GOVERNANCE_DECISION · TASK_ASSIGNED
EXECUTION_STARTED · EXECUTION_FAILED · JOB_COMPLETED
```

**Removing, renaming, or redefining any of the seven remains a semantic change**
and still requires an RFC here. The list is now a *required minimum*, not a
closed set: these seven must exist and must keep their meanings, and the enum may
grow past them.

### Decision types: adding stays semantic

The approval vocabulary — `APPROVE`, `REJECT`, `REQUIRE_CHANGES` — stays closed.
Adding a fourth outcome still requires an RFC here.

The asymmetry is deliberate and is the substance of this RFC rather than an
inconsistency in it. A new event type is a new thing to observe; it cannot weaken
a guarantee, because nothing is permitted or forbidden on the basis of an event's
existence. A new approval outcome is different in kind. A value such as
`AUTO_APPROVE`, or an `APPROVE_WITH_CONDITIONS` whose conditions nobody checks,
creates a path by which execution proceeds without a human `APPROVE` — Rule 2's
own fifth clause, and the direction lock this repository exists to hold. That
path can be opened by adding a value, without removing or renaming anything.

So: adding to an observation vocabulary is additive; adding to a decision
vocabulary is not.

### Rule 2, as amended

A change is semantic — requiring an RFC here before `agent-platform` may
implement it — when it:

1. removes, renames, or redefines an existing decision type or event type
2. adds a decision type
3. weakens or removes a stated guarantee
4. changes a semantically required field to optional, or the reverse
5. changes what an existing decision, event, or state *means*
6. introduces a path by which execution can proceed without `APPROVE`

Adding an event type is not on this list. It is additive under Rule 1.

## Consequence — `semantics_version` moves to 1.1

`contract-semantics.yaml` goes to `1.1`, because the `frozen` block changes
meaning: `event_types` becomes a required minimum rather than a closed set.

Both published contracts pin `semantics_version: "1.0"` in `derived_from`, so
both need their pointer updated to `1.1`. This is the first real exercise of the
drift mechanism, and it behaves as designed — a change to what is frozen produces
a version move, which produces a visible, required update at every derived
contract. Sequencing: this repository merges first, then `agent-platform` updates
the two pointers. The window between the two merges is a known, deliberate
mismatch, and ADR-0006 checks `derived_from` at contract-change time rather than
in CI, so nothing fires spuriously in between.

## Alternatives Considered

**Transfer ownership of `approval` and `event` to `agent-platform` outright (A2).**
This was the proposal that prompted the review, made on the understanding that
ADR-0006 was still `Pending`. It had in fact been accepted with C2 hours earlier,
and both contracts had been published.

Rejected because it costs far more than the problem does. It would reverse a
decision the platform's Architecture Authority signed the same day, rewrite the
ownership framing of two canonical contracts, and remove twelve 🔒 markers — to
solve a bottleneck that one clause of one rule creates. The narrow amendment
removes the bottleneck completely, and there is no second problem that the
transfer would additionally solve.

It would also discard what the same review asked to preserve. The proposal's own
closing point was **Owner ≠ unilateral authority** — ownership must not mean the
owner may redefine governance at will. That is precisely what C2 Rule 5 already
provides: semantics resolve at this repository's Architecture Owner, schema shape
and versioning at `agent-platform`'s, and neither may settle the other's half.
Transferring everything and then re-deriving that protection through ADR review
would arrive at a weaker version of what is already in force.

**Do nothing.** Leaves the bottleneck in place. It costs nothing today, because
no other consumer repository exists yet, and it becomes expensive precisely when
the ecosystem starts to work — the first time a second consumer needs a word for
something.

## Confirmations to `agent-platform`

- **Owner ≠ unilateral authority** — agreed and already satisfied by C2 Rule 5.
  No change needed.
- **Breaking changes require ADR and review** — agreed, per ADR-0006's existing
  definition of breaking. This RFC adds one clause: weakening or removing a
  guarantee counts as breaking even when the wire format stays compatible. A
  schema can remain compatible while the meaning underneath it does not, and that
  case must not pass as additive.
- **Checking `derived_from` at contract-change time rather than in CI** —
  accepted. ADR-0008 forbids implementation in that repository and a workflow
  that fetches and hashes another repository's manifest is code to maintain.
  `hash_scope: frozen` stays available for when the stated review conditions are
  met.
- **`subject_id` and `job_id` both kept** — agreed, including the added schema
  rule that they must match when `subject_type: job`.

## What still cannot be claimed

The team's Definition of Done ends with:

> `devfactory-core` ลงทะเบียนเป็น **first conforming consumer**

ADR-0006 defines a consumer as having a manifest, a CI conformance test over
**real payloads**, and a release gate. This repository has the manifest.
`packages/*` has no code, so there is no payload to validate and no release to
gate.

`conformance.status` stays `unknown` and the registry entry reads **registered,
not conforming**. `platform-contract.yaml` now carries a `registration` field so
the two are not conflated by a reader skimming the table. The item closes when
[issue #2](https://github.com/monthop-gmail/devfactory-core/issues/2) produces
code that emits real payloads — five of the six DoD items are done, this one is
blocked on implementation rather than on agreement.

## Architectural Impact

- **Control Plane** — none. The approval vocabulary is unchanged and stays closed.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — the event vocabulary becomes extensible by
  `agent-platform`. The seven canonical types and all eight event guarantees are
  unchanged.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| An added event type quietly changes what an existing one means | medium | Redefinition is clause 1 of the amended rule and stays semantic; adding a near-duplicate that drains meaning from an existing type is a redefinition in substance and is treated as one |
| The event enum sprawls with no editor | low | `agent-platform` owns it under its own ADR process, which is where every other enum in the contract set already lives |
| The asymmetry between events and decisions is read as an oversight | medium | Stated as the substance of the decision with the failure mode it prevents (`AUTO_APPROVE`), not as an exception |
| The 1.0 → 1.1 pointer update is forgotten upstream | medium | Both contracts carry the pointer explicitly and ADR-0006 declares a stale pointer out of conformance; the update is two lines in files that are reviewed on change |
| A future consumer needs a decision type and hits the bottleneck this RFC removes for events | low | Accepted deliberately — that is the case where a review here is worth its cost, since it is the direction lock being touched |

## Migration Plan

1. Accept this RFC.
2. `contract-semantics.yaml` → `semantics_version: "1.1"`; `event_types` marked
   as a required minimum; `platform_may_add_freely` gains event types.
   **Included in this change.**
3. `platform-contract.yaml` pins `approval/v1` and `event/v1`, which now exist,
   and records `registration: registered`. **Included in this change.**
4. `agent-platform` amends ADR-0006 Rule 2, updates both `derived_from` pointers
   to `1.1`, and adjusts the 🔒 note on `EventType` in `event/v1`.
5. Conformance stays blocked on code — issue #2.

## Open Questions
- Should an added event type still be announced to consumers somehow, or is the
  `CHANGELOG.md` for `event/v1` enough? The changelog is enough for now; a
  notification mechanism is worth having only once there are consumers to notify.
