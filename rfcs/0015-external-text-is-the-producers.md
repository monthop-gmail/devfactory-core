# RFC-0015: The Leaf Rule Holds for Records We Did Not Write

## Status
Draft — เสนอ 2026-09-17 · จะเป็น `Accepted` เมื่อ merge ตาม `CONTRIBUTING.md`

Adds a **non-frozen** block to `contract-semantics.yaml`. Changes no frozen subtree,
so `semantics_version` stays `1.3` and `agent-platform` has nothing to follow — see
[Architectural Impact](#architectural-impact).

## Context

[RFC-0013](0013-audit-fields-that-hold-human-text.md) landed on 2026-09-17 and put
one invariant into `event/v1`: every leaf either holds declared human text, or it is
a pointer. `conformance/payload_check.py` enforces it over every payload in the log.

The full `ADVISORY_ISSUED` payload from `ecosystem-intelligence` has been sitting in
[issue #32](https://github.com/monthop-gmail/devfactory-core/issues/32) since
2026-08-21. It was run through `accept_external()` back then and through `event/v1`
validation, and it passed both. **Nobody ran it again after the rule existed.**

Run today, through intake and then through the leaf check:

```
รับเข้า log ได้: 1 ระเบียน
string leaf: 18 · ประกาศแล้ว 10 · ยังไม่ได้ประกาศ 8

❗ metadata.question = 'ทีมเราควรทำอะไรต่อ?'
❗ metadata.title    = 'ทบทวน ecosystem.yaml ว่ายังตรงกับความจริง'
❗ metadata.why      = 'ทีม care-team ไม่มีงานค้างที่ graph มองเห็น — ...'
❗ metadata.team · metadata.references[] · metadata.ecosystem_as_of
❗ metadata.generated_by.model · metadata.generated_by.provider

check_text_fields → FAIL
```

The suite is green today because the external events in the conformance scenario
carry `metadata={'record_type': 'sighting'}` and `metadata={}`. **The fixture is
smaller than the thing it stands for**, which is the same failure this repository
and `care-agent-platform` have each found once already, and the reason ADR-0006 asks
for real payloads rather than declared ones.

### The same class, found next door, one week ago

[`care-agent-platform` ADR-0012](https://github.com/monthop-gmail/care-agent-platform/blob/main/decisions/0012-erasure-cuts-the-bridge-not-the-trail.md)
declared in its manifest that it had **one** path holding human-typed text, and
reported that to `agent-platform`. Walking it properly for the erasure work found
**eight**, six of which already had a domain column. Their own diagnosis:

> ตัวตรวจ leaf บอกได้ว่า **leaf ไหน** ไม่ใช่ตัวชี้ แต่บอกไม่ได้ว่า **ใครเป็นคนเขียนค่า**
> ตอนประกาศจึงเหลือเป็นการอ่านโค้ดด้วยตา และเราอ่านไม่ครบ

They close by naming the line RFC-0013 wrote about itself — that the rule is
enforceable at the producer only — and saying they had now met it from the other
side.

## Problem Statement

### This is not the checker being too strict

The first reading is that `check_text_fields` over-applies an invariant whose own
text says it binds the producer:

> บังคับได้เฉพาะฝั่งผู้ผลิต: validator บอกได้ว่ามี key อะไร แต่บอกไม่ได้ว่าใครพิมพ์ค่านั้น

That reading is wrong, and the code says so. `intake.py` already refuses inbound
`actor.display_name`, with the reason written next to it:

> RFC-0013: our log holds pointers unless a leaf is declared, **and that rule does
> not get an exception for text someone else sent us** — an undeclared person-name
> written into an append-only store is the same undeletable second copy whichever
> system typed it.

That position is right and this RFC does not disturb it. A name we cannot delete is
a name we cannot delete regardless of who typed it.

### The actual hole

Intake holds that line for **one field it knows the name of**, and passes the rest
through whole:

```python
metadata=dict(payload.get("metadata") or {}),
```

So the position is *"the rule has no exception for someone else's text"*, and the
enforcement is *"one named field"*. `payload_check` is not over-reporting. It is
reporting the distance between the two.

### Why the obvious fixes are worse

| | why not |
| --- | --- |
| refuse the event | [RFC-0008](0008-external-event-intake.md) says an external event we do not recognise is **kept**, not dropped. Refusing the advisory outright ends the #32 integration to satisfy a rule about one field in it. |
| strip undeclared metadata leaves | Keeps the event and holds the rule — and silently discards what the producer said. That is the `sequence` failure exactly: a field lost at the edge, invisible from both sides, found only because someone compared a round trip. |
| declare each producer's fields in our manifest | Every schema change of theirs turns our CI red until we amend. And it makes us assert what `metadata.why` holds and which retention layer governs it — **about a field we did not write**. A confident wrong answer is worse than the gap. |

## Goals

- Hold RFC-0013's position for records we did not write, without guessing at their contents.
- Keep external events whole, as RFC-0008 requires.
- Make *"we are holding text under someone else's rules"* something a reader of the manifest can see, with a date on it.

## Non-Goals

- **Not** amending RFC-0013's invariant. It is correct as written.
- **Not** relaxing the `display_name` refusal at intake.
- **Not** choosing the transport in #32, and not blocking on it.

## Decision 1 — An external `metadata` block is declared as one leaf, whose rules are the producer's

The leaf rule asks of every leaf: *pointer, or declared text?* For `metadata.why` on
someone else's advisory, the honest answer is neither — it is **content under
another repository's contract**, and the thing we can state truthfully is whose.

So `contract-semantics.yaml` gains a non-frozen `external_text` block, and the unit
of declaration is **the producer**, keyed by `source.system` — which `intake.py`
already requires and refuses an event without (`ExternalSourceRequired`).

This satisfies all three clauses of the invariant for an external record:

| clause | how |
| --- | --- |
| (1) applied at leaf level | the declared leaf is the `metadata` subtree of that producer's events, not the event |
| (2) a leaf that may hold human text is declared | it is declared, as content we hold and do not author |
| (3) it says which layer governs retention | **the producer's** — named, with a link, not implied to be ours |

An external record whose `source.system` has no entry fails the check. That ratchet
is satisfiable: **one entry per producer, not one per field.**

## Decision 2 — A producer with no declaration is recorded as `none`, with a review date

`ecosystem-intelligence`'s `platform-contract.yaml` has no `text_fields` block today
— checked, not assumed. `care-agent-platform`'s does, with a `written_by` on each
path.

The entry for a producer that declares nothing is `declaration: none` **written
down**, not omitted. Silence would read as "checked and fine"; the whole point is
that it is neither.

And it carries `review_by`, for the reason `known_gaps` carries an expiry and
ADR-0006 forbids a `waived` that never lapses: an unreviewed `none` left alone stops
being a note about work outstanding and becomes a permanent exemption nobody reads.

## Decision 3 — What we owe as a holder, stated as a check

The rule this repository can enforce about someone else's text is not *what it
contains*. It is **that we are not the reason it spreads**:

> No value from an external event's `metadata` is ever copied into a field this
> repository writes.

That is true today and nothing asserts it. A future convenience — lifting
`metadata.title` into a `transition.reason` to make a trail read better — would put
a producer's text into a leaf we declared as ours, under our retention answer, and
every existing check would stay green.

## Decision 4 — The limit is written down rather than papered over

We refuse `actor.display_name` because we know that field by name. **We cannot find a
person's name inside a producer's `metadata`**, and this RFC does not pretend
otherwise.

That is precisely why the answer is to point at the producer's declaration instead
of re-deriving it: they know who writes their fields, and `care-agent-platform` has
already shown what it costs to answer that question by reading code with your eyes.

## Alternatives Considered

**Exempt external events from the leaf check.** One line, and the check goes blind
exactly where the risk is highest — text arriving from outside, stored forever,
under no declared retention rule. This is the shape of the waiver that stayed behind
after its cause was fixed and kept swallowing failures at the same spot.

**Hash or redact external metadata on arrival.** Holds the rule perfectly and makes
the event useless for the purpose it was accepted for. RFC-0008 keeps unrecognised
events so they can be read later; an unreadable record is a dropped one with extra
storage cost.

**Wait until transport lands in #32.** The payload is already known, the rule is
already live, and the failure is already reproducible. Waiting means the first real
advisory turns CI red, and the fix gets designed against a deadline.

## Architectural Impact

- **Control Plane** — none.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — `check_text_fields` splits by `source.kind`; one new check for Decision 3; `intake.py` unchanged.

`semantics_version` stays **`1.3`**. Nothing in any `frozen:` subtree changes, and
the manifest's own rule is that a non-frozen addition does not move the version.
This is worth stating rather than leaving implicit: `agent-platform` followed `1.3`
on 2026-09-17 ([#71](https://github.com/monthop-gmail/agent-platform/pull/71)), and a
bump we did not need would ask them to follow again the next day for nothing.

## Risk Assessment

| risk | weight |
| --- | --- |
| A producer entry becomes a rubber stamp — added to turn CI green, never re-read | Real. `review_by` is the answer, and it is the same answer `known_gaps` uses because the same thing happened there. |
| `declaration: none` is read as approval to send us anything | Mitigated by writing the consequence in the entry itself, not in this RFC. |
| Decision 3's check passes trivially today | It does — nothing copies external metadata now. It is a ratchet, like RFC-0013's, and ratchets are cheap before the thing they prevent exists. |

## Migration Plan

1. Accept this RFC.
2. `contract-semantics.yaml` gains `external_text`, with `ecosystem-intelligence` as
   its first entry — `declaration: none`, `review_by` set.
3. `check_text_fields` applies the leaf list to records we produced, and the producer
   list to records we did not.
4. Add the Decision 3 check, and a fixture carrying the real `ADVISORY_ISSUED`
   payload — the one from #32, not a reduced stand-in, since a smaller fixture is
   what let this through.
5. Tell `ecosystem-intelligence` in #32 that their fields are held under their own
   declaration, and that there isn't one yet.

Steps 2–4 are inside v0.x. None of them waits on the durable store or on transport.

## Open Questions

- **Does `review_by` on a `none` entry need a failing check, or a warning?**
  `known_gaps` fails on expiry. A producer's declaration is not ours to write, so
  failing our build over their unfinished work is arguably punishing the wrong repo
  — and not failing is how a note becomes permanent. Decidable with the first entry
  that actually expires, not now.
- **Do the two manifests need one shape?** `care-agent-platform` declares
  `path` / `written_by` / `detail`; this repository declares `leaf` /
  `who_writes_it` / `why_it_cannot_be_a_pointer` / `retention_layer`. Same idea,
  different keys. Unifying them is `agent-platform`'s call under RFC-0005 Rule 2,
  not ours to decide for them.
