# RFC-0012: Every Terminal Emits a Closing Record

## Status
Draft — pending maintainer approval per `GOVERNANCE.md`

Answers [issue #26](https://github.com/monthop-gmail/devfactory-core/issues/26), which
[agent-platform#23](https://github.com/monthop-gmail/agent-platform/issues/23) and
[ADR-0015](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0015-event-sequence-and-trail-closure.md)
sent back here: the completeness half of that problem is an event-log guarantee, and
[RFC-0005](0005-platform-contract-authority.md) Rule 2 puts guarantees on this side.

Extends [RFC-0003](0003-audit-event-log-schema.md) as amended by
[RFC-0008](0008-external-event-intake.md).

## Context
Replay verifies a trail by having **each record vouch for the one before it**: a
`STATE_TRANSITION` names the state it left, and if that is not where the replay is
standing, a record is missing or out of order (`BrokenTrail`).

That check has no purchase on the **last** record, because nothing comes after it to
do the vouching. One case escapes today, and only one: `COMPLETED` implies
`JOB_COMPLETED`, so a trail claiming to have finished successfully without that
record is caught (`IncompleteSettlement`).

The other three terminals have no such implication:

| job settled as | trail truncated at the end |
| --- | --- |
| `COMPLETED` | ✅ caught — `JOB_COMPLETED` is missing |
| `FAILED` · `CANCELLED` · `TIMED_OUT` | ❌ **replays clean**, reporting the state before the lost record |
| still running | ❌ same, and see Decision 4 |

A truncated trail reading as a complete one is the worst failure an audit log has.
It does not break visibly; it answers confidently and wrongly.

`sequence` was proposed upstream and shipped in `event/v1` v1.3.0, but for **ordering
only**. ADR-0015 established why it cannot close this: a number carried on an event
cannot say what number the last event should have been, because that answer is not on
any event. The statement has to come from outside the sequence.

## Problem Statement
`JOB_COMPLETED` already is the closing record for one of four terminals, and it works.
What is missing is not a mechanism — it is the **rule that the mechanism applies to
every ending, not only the one that ended well**.

Three of four terminals emit nothing that says "this is the end", so there is nothing
for a reader to find missing.

## Goals
- Make end-of-trail truncation detectable for every job that has settled.
- Reuse the mechanism already proven by `JOB_COMPLETED` rather than inventing one.
- Keep the cost at the producer, paid once when a job settles — not on every write,
  which is what ADR-0015 rejected.
- Say plainly what still cannot be verified, rather than appearing to cover it.

## Non-Goals
- Changing `sequence`, or asking for it to become contiguous. ADR-0015 settled that
  and this RFC does not reopen it.
- Defining closure for subjects whose contracts belong to `agent-platform`. See
  Decision 2.
- Verifying a trail that has not ended. See Decision 4.

## Decision 1 — `JOB_SETTLED`, emitted on every terminal

A new event type. **Every** job entering a terminal state emits exactly one
`JOB_SETTLED` — `COMPLETED`, `FAILED`, `CANCELLED`, and `TIMED_OUT` alike.

`JOB_COMPLETED` is unchanged and is still emitted when a job reaches `COMPLETED`. A
successful job therefore emits both.

### Why a new type rather than widening `JOB_COMPLETED`

The two records answer different questions, and the whole bug came from having only
one record trying to answer both:

```text
JOB_COMPLETED  → did the work get delivered?      a claim about the outcome
JOB_SETTLED    → is this trail finished?          a claim about the record
```

Widening `JOB_COMPLETED` to cover failure would make the type's name contradict its
contents, and every existing reader that treats it as success would silently start
counting failures as deliveries. Under [RFC-0009](0009-vocabulary-extension.md) that
is a redefinition of an existing type — the one kind of vocabulary change that stays
semantic precisely because it changes what readers already believe.

### Why `COMPLETED` emits both rather than being the exception

The uniform rule is what makes it checkable. If `JOB_SETTLED` covered only the three
unhappy terminals, a reader would have to know which terminal to expect which record
for, and "which endings are special" is exactly the knowledge whose absence caused
this bug. One extra record per job, once, buys a rule with no exceptions in it.

### The name

`settled` is already this repository's word for it — `states.SUPERSEDABLE` is
described as "every terminal that settled without delivering the work", and
`state-machine.md` speaks of a job before it settles. `TERMINATED` would have implied
being stopped, which is `CANCELLED`'s meaning specifically.

## Decision 2 — The rule binds `job`; other subjects are out of scope

`event/v1` `SubjectType` also has `execution`, `step`, `agent`, `tool_call`,
`artifact`, `approval`, `external`, and `consent`. This RFC binds **`job`** only.

- `execution` has terminal states, but they are defined by `execution/v1`, which is
  `agent-platform`'s. Declaring when an execution's trail closes would be declaring a
  guarantee about their contract, which RFC-0005 forbids in exactly the direction it
  forbids them declaring ours.
- `record` and `external` describe things with no lifecycle here. A sighting does not
  end; there is nothing to close.

The pattern generalises and is stated so that a subject with a defined terminal may
adopt it — but adoption is a decision for whoever owns that subject, not this RFC.

## Decision 3 — The closing record carries `event_count`

`JOB_SETTLED` carries, in `metadata`, the total number of events carrying that
**`job_id`**, including the closing record itself:

```json
{ "event_type": "JOB_SETTLED", "subject_type": "job", "subject_id": "job-001",
  "metadata": { "settled_as": "FAILED", "event_count": 12 } }
```

A reader with the trail checks `len(trail) == event_count`.

This is worth the field because it notices records that **no structural check asks
for**. Chaining verifies transitions; other checks verify the records that a
transition implies. What neither can see is a record nothing demands — an extra or
forged one, or a missing one of a type that has no check of its own.

> **Correction (implementation, 2026-08-21).** This paragraph originally cited a
> missing `GOVERNANCE_DECISION` as the example, and that was wrong twice over.
> Decision events carry `subject_type: approval` and their own `subject_id`, so a
> subject-scoped count would not have covered them at all — the count is scoped by
> `job_id`, which is the unit `replay_tenant` actually groups by. And the case is
> already caught: `UnauditedDecision` fires when a decision transition has no
> decision record before it. The count's real reach is stated above, and it grows
> with the vocabulary: `TASK_ASSIGNED`, `EXECUTION_STARTED`, and `EXECUTION_FAILED`
> have no structural check of their own and will be covered by nothing else when
> orchestration begins emitting them.

The cost is paid **once, by the producer, at close**, which is the difference that
made this affordable where contiguous `sequence` was not: that would have serialised
every write for every consumer, and `care-agent-platform` was right to object.

`settled_as` records which terminal it was, so a reader holding only the closing
record knows the outcome without replaying.

### Why `metadata` rather than a new top-level field

`metadata` is already an object with no shape imposed, so nothing upstream has to
change and this ships today. Field placement is `agent-platform`'s under RFC-0005
Rule 1 — if they would rather promote `event_count` to a first-class field later,
that is additive, needs no RFC here, and this repository follows.

The `metadata` invariant is unaffected: a count is structured metadata, which is what
that field is for. Nothing about reasoning traces changes.

## Decision 4 — A trail that has not ended cannot be closed, and we say so

There is no honest way for the event contract to certify a running trail. "Complete"
means "nothing is missing up to the end", and a trail still being written has no end
to be up to. Any field claiming otherwise would be measuring the moment it was
written, not the trail.

So this is **not** closed at the contract level, and the gap is stated rather than
papered over.

Where it can be answered is the **store**, which knows what it holds right now, and
this repository already has the primitive: `EventLog.digest(tenant_id)` is a hash
chain over a tenant's event ids in append order. Take it, carry on, take it again — a
prefix that no longer reproduces means history changed. That is a checkpoint, and
checkpoints are a store concern, exactly as issue #26 suggested.

Turning `digest` into a durable checkpoint API is left to the store's own RFC when a
store that outlives a process exists. Today's is in memory.

## What replay gains

`IncompleteSettlement` generalises from one terminal to all four: a trail whose job is
terminal and carries no `JOB_SETTLED` is incomplete. A new failure appears for the
count disagreeing with the trail's length.

Both are end-of-trail checks, which is the class replay had exactly one of.

## Alternatives Considered

**Widen `JOB_COMPLETED`.** Rejected under Decision 1 — it makes the name lie and
silently reclassifies failures as deliveries for every existing reader.

**`JOB_SETTLED` on the three unhappy terminals only.** Rejected under Decision 1 —
avoiding one record per successful job costs the property that makes the rule
checkable, which is having no exceptions in it.

**Contiguous `sequence`.** Settled upstream by ADR-0015 and not reopened here. It
cannot answer what the last number should have been, and it charges every writer for
an answer it does not give.

**A closing record with no count.** Would close end-truncation, which is the reported
bug, and leave mid-trail non-transition records unverifiable. The count is nearly free
at that point — the producer is already writing a record and already knows the number.

**Nothing — accept that three terminals are unverifiable.** This is what today does,
and the reason the issue exists.

## Architectural Impact

- **Control Plane** — the state machine emits one more record when a job settles. No
  state, transition, or guard changes.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — a new event type; replay gains end-of-trail checks for all four
  terminals plus a completeness check that covers non-transition records.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| `event_count` disagrees with what a reader counts because the two count different sets | high | Defined as events carrying that `job_id`, including the closing record itself — the unit `replay_tenant` groups by, so producer and reader count the same set; the conformance scenario checks the equality it defines |
| A reader treats `JOB_SETTLED` as success | medium | It carries `settled_as`; the type name says settled, not completed; `JOB_COMPLETED` keeps its meaning and its role |
| Two records at `COMPLETED` read as duplication | low | They answer different questions, stated in Decision 1; the alternative is an exception in the rule |
| `agent-platform` has not added `JOB_SETTLED` to `EventType` yet | low | Nothing blocks: `event_type` refs `EventTypeName`, an open set, so the payload validates before the enum entry exists. Adding it is additive on their side under RFC-0009 and needs no RFC here |
| The count is wrong when a producer writes events it does not own | medium | Only the producer that owns the subject may close it, which for `job` is this engine; an external system's events are a different subject |

## Migration Plan

1. Accept this RFC.
2. `EventType` gains `JOB_SETTLED`; the state machine emits it on entering any
   terminal, carrying `settled_as` and `event_count`.
3. Replay generalises `IncompleteSettlement` and adds the count check.
4. `packages/core/state-machine.md` and `contract-semantics.yaml` record the
   guarantee; `semantics_version` moves, since a new guarantee is a change to the
   `frozen` block — the derived contracts' `derived_from` pointers follow, as they did
   for 1.0 → 1.1.
5. Tell `agent-platform` so they can add the enum entry and, if they want it, promote
   `event_count` to a field.

No trail migration: existing trails stay as they are. A trail written before this rule
has no `JOB_SETTLED` and replay must not treat its absence as truncation, so the check
applies from the rule's adoption forward — see Open Questions.

## Open Questions
- **How does replay tell "written before the rule" from "truncated"?** Both look like a
  terminal trail with no closing record. Options: a `platform_contract_version` stamp
  on the trail, a cutoff date, or accepting that pre-rule trails replay with a warning
  rather than an error. Recommended: the warning, since no production trail exists yet
  and the ambiguity disappears once every trail is written under the rule.
- **Should `JOB_SETTLED` also carry the digest of the trail it closes?** It would let a
  reader verify content rather than only count. Deferred: the count answers the
  reported problem, and a digest needs agreement on exactly what is hashed before it
  means anything to a second reader.
