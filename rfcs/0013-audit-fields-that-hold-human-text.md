# RFC-0013: Audit Fields That Hold Human Text

## Status
Accepted — requested by [`agent-platform`](https://github.com/monthop-gmail/agent-platform) under [RFC-0005](0005-platform-contract-authority.md) Rule 2 · merged to `main` 2026-09-17 in [#34](https://github.com/monthop-gmail/devfactory-core/pull/34)

Proposes **one new invariant** in `contract-semantics.yaml` → `semantics_version` `1.2` → `1.3`.

Does **not** touch event vocabulary, does not add or remove event types, does not disturb any existing guarantee.

## Context

`event/v1` at `agent-platform` derives its semantics from this repository. Its `rules` block already carries one invariant about what may not be written into an audit record:

> ห้ามเก็บ private reasoning / chain-of-thought เป็น audit record

That rule was written about the *model's* private text. This RFC is about the other kind: text written by **people**, and text copied out of domain records into the audit trail.

The request comes with production evidence, not a concern. [`care-agent-platform`](https://github.com/monthop-gmail/care-agent-platform) pins `event/v1` and runs in a patient-care domain. It walked every leaf of **112 real audit events** and reported what was in them.

## Problem Statement

### What was actually in the audit trail

| Where | What |
| --- | --- |
| `attributes` on prescription proposal | **medication name alongside `patient_id`** — health data about an identifiable person |
| on detecting conflicting prescriptions | the entire `detail` blob — medication names, dosing schedule, prescriber |
| patient reminder | the full outgoing message, which interpolates a job label → the medication name arrives by a second path nobody intended |
| on approval | `summary` / `reason` typed by a person — **three places** |
| appointment | department + purpose alongside `patient_id` |
| on stopping a doctor's order | the caregiver's typed reason, **copied into the trail of every job cancelled by that order** |
| `actor.display_name` | a real person's name, in **112 of 112 events** — every event the system has ever written |

### Why this is a semantics problem and not consumer discipline

In the producer's own words:

> Every one of these already has a domain row, and the event already carries `subject_id` pointing at it. Copying the content into the audit record adds **no auditability whatsoever** — but it creates a second store of personal data: one that cannot be deleted because the log is append-only, and one whose read permissions are not bound to consent the way the domain tables are.

The shape of the defect is not *"the right thing kept in the wrong place"*. It is **"a copy that gives nothing back, in exchange for a copy that cannot be deleted."**

### Why the obvious rule — "audit must not contain PII" — is the wrong rule

Two requirements collide on a single field:

```
audit must not hold the subject's personal data
        ✕
the record must answer why a doctor's order was stopped
```

The collision point is `event/v1` `transition.reason`, which today is `{ type: string }` with no constraint of any kind.

The producer cannot move that text out: its own ADR requires the record to answer that question, and the domain row does not store the sentence. All it could do was stop copying it — it now lives on one event instead of on every cancelled job.

> If the rule says only *"audit must not contain PII"*, **the system that did the right thing becomes non-conformant**, and anyone who wants to conform will fix it by deleting the reason — which destroys the audit trail's ability to answer the most important question in that domain.

This RFC therefore proposes a **declaration requirement**, not a prohibition.

## Goals

- Make it impossible to add a place for human text without someone reviewing the decision.
- Keep records that legitimately must hold human text legal under the contract.
- State plainly, inside the invariant, what the invariant cannot enforce.

## Non-Goals

- Banning personal data from audit records outright — see above.
- Closing `metadata` with a central list of permitted keys. This repository does not know consumers' domains, and a list maintained in two places drifts within a month.
- Changing event vocabulary, adding or removing event types, or altering any existing guarantee.

## Decision — one invariant, three clauses

Proposed for the `rules` block, as a sibling of the private-reasoning rule:

```text
1. This rule applies at the leaf of the payload, not at top-level fields.
   Objects and arrays are containers, not values — descend to values with no children.

2. A leaf that may hold human text must be declared in the contract.
   Every other leaf is a pointer — id, code, number, timestamp, boolean, enum.

3. A declared leaf must state which layer its retention and deletion rules live at.
   An append-only store cannot delete per field, and must declare what it does instead.
```

### Clause 1 must be inside the invariant, not in commentary

The producer tested this. Read at the level of top-level fields, **no event in existence passes** — `actor`, `source`, `transition`, `policy_result` and `consent` are all objects. Implementers will read these three clauses, not this document's prose.

### What clause 2 actually changes

```text
before   someone types into a field that accepts it   → nobody sees it
after    someone must change the contract to add one  → someone reviews it
```

### Clause 3 replaces an earlier draft that was unsatisfiable

An earlier version required declared fields to *have* retention and deletion rules. The producer pointed out that this collides with `event/v1`'s own append-only guarantee: deleting a field in an already-written row is impossible **not because nobody wants to, but because being able to would make the guarantee false**.

The clause now asks where the rule lives, not that deletion be possible. Its own report under the new wording reads, in part: *per-field deletion is impossible; deletion is available only at the store or tenant layer; that mechanism does not exist in the system today.* That statement is conformant and true, which is the point.

## What this invariant does not enforce — to be written into the rule, not omitted

**It binds producers, not receivers.**

A validator can say which keys exist. It cannot say **who wrote the value**, which is the real axis of the rule:

> `policy_reason`, composed by the policy engine from `authority_map` and the profile, and `summary`, typed by a person, are **identical `string`s as far as a schema is concerned**.

What is machine-enforceable is the closed set of leaves. What lies inside a declared field remains producer discipline — plus the fact that a reviewer now knows which fields to look at.

That is worth more than it sounds, and the producer measured it:

> The leaf-walker took **under an hour** to write, and it found something present in every event the system has ever written — something **none of our validators had ever seen**, because every one of them asks whether the record is well-formed. **None of them asks what is inside it.**
>
> A rule that says *everything else is a pointer* makes that question **expressible as code**, even though it binds only the producer.

### The declaration must be what the checker reads

A declaration kept as a document, separate from the tool that verifies payloads, drifts — the same failure this RFC rejects a central key list for. The producer's `payload_check` now reads its `text_fields` declaration directly from its manifest and holds no list of its own.

> A manifest that disagrees with reality is the manifest that makes people stop checking.

**This RFC asks that the invariant require the declaration to be the artefact the producer's conformance check reads.**

## Guidance — field naming

Not machine-enforceable; requested by the producer to appear as a first-class section rather than a footnote, because all three shapes below were found in production within two days.

**A name broad enough to accept anything will be used to accept anything.**

The producer deliberately did *not* name its key `reason`, although it is a reason:

> `reason` is a name anyone will drop a person's words into without noticing — **and we had already done it in three places.**

It is named `policy_reason`, and the policy engine composes the sentence itself from `authority_map` and the profile. No user input, no values from the subject's record.

Two further shapes, both found in `agent-platform`'s own contracts:

```text
a name too broad          → people put the wrong thing in without noticing
a field nobody names      → nobody looks to see what is in it
a field the contract adds → everyone assumes it belongs to the other side
```

`error/v1.details` was an open object identical in shape to `metadata`, in a file `agent-platform` owns, and survived three weeks because nobody typed its name — including the requester, who had edited that file once and walked past it.

`identity/v1.Principal.display_name` is the third: not a field the domain fills, but one the contract supplies. The producer walked past it while closing `metadata`, because it looked like the platform's business rather than its own.

## Architectural Impact

**Control Plane** — none. No decision type, no policy shape, no authority rule changes.

**Orchestration** — none. Job state machine and transitions are untouched; only the constraint on what `transition.reason` may carry is added.

**Execution** — producers gain an obligation at emit time: strip or declare. `agent-platform` reports that in the reference case this was a single cut at the emit point, covering nested `on_behalf_of`, plus a ratchet in the producer's conformance check.

**Observability** — this is where the change lands. Audit records become pointers by default. Auditors keep the ability to reach the content through `subject_id` and domain rows, under the domain's own access control, rather than through a second uncontrolled copy.

## Risk Assessment

| Risk | Assessment |
| --- | --- |
| Producers lose the ability to record why something happened | Mitigated by design — clause 2 permits declared text fields. The earlier prohibition-shaped draft carried this risk and was rejected for it. |
| Rule read at the wrong granularity makes everyone non-conformant | Mitigated by clause 1 being part of the invariant. Verified against 112 real events. |
| Declaration becomes a document nobody checks | Mitigated by requiring the declaration to be what the producer's conformance check reads. Producer has implemented and tested this both ways. |
| Producers who never re-examine existing payloads assume they conform | Not mitigated. The only way to find these fields is to walk the leaves of real payloads. This RFC recommends it; it cannot compel it. |
| The rule is unenforceable at the receiver | Accepted and stated inside the invariant rather than left implicit. |

## Migration Plan

Percentages do not work here, and the producer explained why:

> Records already written cannot be amended, because the log is append-only. The usable criterion is therefore not *what percentage of gaps were closed* but **from which point no new record violates the rule**.

Each producer declares two things:

1. **A cutover point** — records written after which version or commit fall under this rule.
2. **What remains outstanding from before it, and how it is handled** — the reference producer's declaration lists medication names alongside `patient_id`, full reminder messages, human-typed reasons, appointment departments, and a person's name in every record written before its cutover commit, with `remediation: none — cannot be deleted, append-only`.

> Conformance then becomes a **statement that can be checked for truth**, instead of a percentage each team reports about itself that nobody can verify.

`agent-platform` will, on acceptance: bump `derived_from.semantics_version` in `event/v1`, write the invariant into the `rules` block, declare `transition.reason` as a field that may hold human text, and record the change and its effect on pinning consumers in an ADR.

## Evidence

The requester closed the equivalent gaps in the contracts it owns outright **before** filing this RFC:

- `error/v1` `v1.1.0` — `details` given the same rule its sibling `message` had carried since `v1.0.0` (ADR-0030)
- `identity/v1` `v1.2.0` — `Principal.display_name` annotated at the source of the field (ADR-0031)

Both were found by the consumer, not by the requester.

The reference producer ran the proposed invariant against its real payloads twice — once against the first draft, finding four defects in it, and again against the revision, reporting that nothing in it makes its running system non-conformant. Figures for leaf counts before and after, and the declaration its checker reads, are available from `care-agent-platform` on request.

Thread of record: `dis-65134078` in the `ai-collaboration-mcp` workspace, `seq 25`–`seq 32`.
