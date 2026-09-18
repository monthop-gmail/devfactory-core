# RFC-0017: The Leaf Rule Is Not a Property of One Contract

## Status
Draft — เสนอ 2026-09-18 · จะเป็น `Accepted` เมื่อ merge ตาม `CONTRIBUTING.md`

Answers [issue #46](https://github.com/monthop-gmail/devfactory-core/issues/46) from
`agent-platform`. Declares one leaf. **Adds no obligation that was not already
implied**, and moves `semantics_version` `1.4` → `1.5` because the declaration
belongs where the rule can be read against it.

## Context

`agent-platform` asked a question they were right not to answer for us:

> invariant ใหม่ผูก `approval` ด้วยหรือไม่

Both readings have evidence, and they laid both out rather than picking the
convenient one:

| reading | evidence |
| --- | --- |
| binds `event` only | the invariant sits under `event.frozen.invariants`; `approval.frozen` did not change a character in `1.3` |
| binds both | `semantics_version` is a property of the file, not of one contract, and the file says *"contract ที่ derive ต้องอัปเดต `derived_from.semantics_version`"* without naming which |

And the fact that makes it undecidable from placement alone: **this repository
already declares `metadata.approval.reason`** — the *destination* of a decision's
reason once it flows into an audit event — while the **source** of that same
sentence, `approval/v1.reason`, sits in a contract the invariant does not address.

```text
approval/v1.reason  ──ไหลเข้า audit──>  event metadata.approval.reason
     (ไม่ถูกเขียนถึง)                       (ประกาศแล้ว)
```

One sentence, declared at one end.

## Problem Statement

RFC-0013 wrote a rule about **what an audit record may hold** and put it under the
audit record's contract. That was the right place to write it and the wrong place
to stop, because the rule is not a property of `event/v1`. It is a property of
**text that cannot be deleted once written**, and `event/v1` is only the first
contract this repository owns where such text arrives.

The invariant's own words do not scope themselves to events:

> leaf ที่อาจถือข้อความของคนต้องถูกประกาศไว้ในสัญญา ที่เหลือทุก leaf เป็นตัวชี้

Nothing in it says *event leaf*. The scoping came from placement, and placement is
not a decision anyone made.

## Decision 1 — It binds every contract whose semantics this repository owns

`approval/v1` included. The rule follows the text, not the filename.

**This changes nothing about what may be written.** It changes what must be
*declared*, which is the whole mechanism: clause 2 asks that a leaf which may hold
human text be named, and clause 3 that its declaration say which layer governs its
retention. A leaf that is named and governed satisfies the rule. Nothing has to be
removed for it to hold.

## Decision 2 — `approval/v1.reason` is declared human text, and it cannot be a pointer

The contract itself settles it:

```yaml
reason:
  type: string
  minLength: 1
  description: 🔒 required meaning — เหตุผลที่ตัดสินอย่างนั้น
```

`required` · `minLength: 1` · and this repository's own `required_meaning` demands
*"เหตุผลที่ตัดสินอย่างนั้น"*. A decision with no stated reason is not auditable,
which is the reason RFC-0002 gave for requiring it in the first place.

So the declaration is not a judgement call. It is a transcription of a decision
already made twice.

## Decision 3 — The asymmetry `agent-platform` raised is a reason for the rule, not against it

They noted, without proposing anything:

> `reason` เป็น `required` และ `minLength: 1` แปลว่าตัดออกไม่ได้เลยแม้ผู้ผลิตอยากตัด
> ต่างจาก `transition.reason` ที่ optional

That is correct, and it points the opposite way from where a reader might expect.

For `transition.reason`, a producer who decides the field is not worth its retention
cost can stop sending it — `event/v1` allows absence. For `approval/v1.reason` there
is no such exit: **the contract requires the sentence, so the only thing that can
ever change is the answer to clause 3.** A leaf whose presence is not negotiable is
the leaf where the retention answer carries the most weight, and it was the one with
no declaration at all.

## Decision 4 — Where the declaration lives

Under `approval`, beside the contract it describes — not folded into `event`'s
`text_fields`. The two are different fields in different contracts with different
producers, and `metadata.approval.reason` is a copy that arrives here, while
`approval/v1.reason` is where the sentence is written.

Declaring both is not duplication. Declaring only the copy is what let the source go
unnamed.

## Alternatives Considered

**Say it binds `event` only, and record why.** `agent-platform` offered to write
exactly this into an ADR, which would have been an honest outcome. It fails on the
evidence: `approval/v1.reason` meets every clause of the definition, and a rule that
excludes the clearest instance of the thing it describes is a rule scoped by
accident.

**Amend RFC-0013's invariant text to say "every contract".** Tempting and worse. The
invariant already says nothing about events; editing it to say so explicitly would
move a frozen subtree to add a word that changes no reading, and every consumer
would follow a version for it. The scope is settled here, where a reader looking for
the answer will find the reasoning rather than a clause.

**Wait for a consumer to hit it.** That is the rule this repository usually follows
and it does not apply: the consumer already hit it. `agent-platform` is asking
because they cannot write their ADR without the answer, and `care-agent-platform`
writes `approval/v1.reason` in production today.

## Architectural Impact

- **Control Plane · Orchestration · Execution** — none.
- **Observability** — none. `Decision.reason` already exists and is already emitted
  into `metadata.approval.reason`, which is already declared and already checked.

`semantics_version` `1.4` → `1.5`. The declaration sits inside `approval`, and
`text_fields` is outside `frozen` — but `required_meaning` gains nothing and
`subject_types` gains nothing, so **the only reason to move the version is that
consumers of `approval/v1` need to know a declaration now exists for a field they
write.** That is a real reason and not a bookkeeping one.

> **Cost, named again:** this is the third move in three days — `1.3` on 2026-09-17,
> `1.4` and `1.5` on 2026-09-18. `agent-platform` follows each one. The alternative
> is batching corrections until the count looks tidier, which trades a version
> number for a period where the contract says something untrue.

## Risk Assessment

| risk | weight |
| --- | --- |
| Reads as "approval records must be purged" | Real if skimmed, which is why Decision 1 says plainly that nothing must be removed. |
| Version churn | Named above. Three moves in three days is unusual and each one is a correction, not a feature. |
| The next contract repeats this | Decision 1 is written to cover contracts not yet written, so the next one starts inside the rule rather than outside it. |

## Migration Plan

1. Accept this RFC.
2. `contract-semantics.yaml`: `approval.text_fields` declares `reason`;
   `semantics_version` → `1.5`.
3. `carried_check` for `approval` gains a marker for it.
4. Answer issue #46 with the decision and this file.

## Open Questions

- **Does `error/v1.details` need the same treatment?** `agent-platform`'s ADR-0030
  is about that field and it is one of the two that caused RFC-0013. It is not a
  contract whose semantics this repository owns, so Decision 1 does not reach it —
  but the same sentence can flow from there into an audit record here, and nothing
  currently says whose declaration governs when it does. Left open because no
  payload in this repository carries it yet.
