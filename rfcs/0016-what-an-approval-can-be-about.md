# RFC-0016: What an Approval Can Be About

## Status
Accepted — เสนอ 2026-09-18 · merged to `main` 2026-09-18 in [#45](https://github.com/monthop-gmail/devfactory-core/pull/45)

Answers [`agent-platform#73`](https://github.com/monthop-gmail/agent-platform/issues/73).
Moves `semantics_version` `1.3` → `1.4`. Touches `frozen`, which is why it is an RFC
and not an edit.

## Context

`care-agent-platform` implemented PDPA erasure requiring two people to approve
([their ADR-0013](https://github.com/monthop-gmail/care-agent-platform/blob/main/decisions/0013-erasure-takes-two-people.md)),
and found there was no value to put in `approval/v1` `$.subject.type` that says what
was being approved.

The set is closed at five:

```text
job · execution · tool_call · artifact · deployment
```

Every one of them belongs to the machinery that runs agents. None of them is a
**domain record**, which is what a consumer in a business domain asks approval for
most often.

### Two places in one codebase picked different values, and both validate

| where | what is being approved | value chosen | their reasoning |
| --- | --- | --- | --- |
| `care_careplan.propose_task` | a doctor's order waiting to take effect | `artifact` | it is *a thing that was produced* |
| `care_patient.request_erasure` | erasing one patient's data | `tool_call` | it is *an action*, not a produced thing |

Both are *"approve doing something to a patient's record"*. Both of their **audit
events** for the same objects already use `subject_type: record` — the value
`event/v1` gained through [`agent-platform#14`](https://github.com/monthop-gmail/agent-platform/issues/14).

So one system describes one thing with two words depending on which contract it is
writing into, and a reader filtering approvals by `subject.type` to find *"approvals
about patient records"* finds neither, because they are scattered across `artifact`
and `tool_call`.

**Nothing fails.** The payloads validate every time. That is the failure mode this
ecosystem has now recorded four times: green while the meaning drifts.

## Problem Statement

### We own this set and never wrote it down

`approval/v1` says so in its own text:

> `subject` ที่นี่คือ **สิ่งที่ถูกอนุมัติ** … 🔒 เป็น semantics ของ devfactory-core —
> เปลี่ยนชื่อที่นี่ไม่ได้

And our manifest agrees by omission: `approval`'s `platform_may_add_freely` lists
`tenant_id`, `workspace_id`, `execution_id`, `agent_id`, `policy_id`, `expires_at`,
`action_risk`, `escalation_target` — **not `subject`**.

But `approval`'s `frozen` block declares `decision_types`, `required_meaning`,
`guarantees`, `invariants`, and **nothing about subject at all**. `required_meaning`
lists the decision, the reason, the authority, and the timestamp — it does not list
*what was decided about*, although `approval/v1` has `subject` in its `required`
array.

**The set is ours, it lives only in their file, and our side never declared it.**
Nothing here could have noticed it was incomplete, and
`conformance/payload_check.py`'s new `carried_check` cannot see it either: that
check compares the derived contract against what we froze, and this was never
frozen.

### The two contracts are governed differently and neither document says so

For `event/v1`, our manifest hands the enum over explicitly:

```yaml
- subject_type / subject_id  # rfcs/0008 ต้องการให้มี ส่วนชื่อและ enum เป็นของ agent-platform
```

Which is why `record` could be added there through their ADR process with no RFC
here, and it was.

For `approval/v1`, no such line exists and the schema marks the semantics as ours.
**Same field name, opposite ownership, stated in neither place.** A consumer hitting
the gap cannot tell which door to knock on — `care-agent-platform` filed against
`agent-platform` and said in the issue that they assumed it had to come back here.
They were right, and they had to guess.

## Goals

- Give a consumer approving a domain record a value that is true.
- Put the set where its owner can see it, so the next missing value is visible here.
- Make the difference in governance between the two contracts explicit.

## Non-Goals

- **Not** changing `decision_types`. `APPROVE` / `REJECT` / `REQUIRE_CHANGES` stays
  closed for the reason RFC-0009 gave.
- **Not** touching `event/v1` `subject_type`. That enum is `agent-platform`'s by our
  own declaration and it already has `record`.
- **Not** defining domain record kinds. `event/v1` puts the real kind in
  `metadata.record_type` and says the platform should not know domain types. Same
  here.

## Decision 1 — Declare the set, in `approval.frozen`

`contract-semantics.yaml` gains `subject_types` under `approval`'s `frozen` block.
It belongs inside `frozen` and not beside it: it is a semantic we own, and a
declaration outside `frozen` says the opposite — the distinction RFC-0015's
clarification had to untangle for `text_fields`.

## Decision 2 — Add `record`, with the definition `event/v1` already uses

`event/v1` defines it:

> `record` = บันทึกของโดเมนที่ระบบเก็บไว้และต้องตามรอยได้ แต่**ไม่ได้เกิดจาก job**
> … ชนิดที่แท้จริงอยู่ใน `metadata.record_type` — platform ไม่รู้จักชนิดของโดเมน
> และไม่ควรรู้ · แยกจาก `artifact` ซึ่งเป็นผลผลิตของ execution

Taking that definition rather than writing a new one is the point: the two contracts
describing one thing with two words is the defect being fixed, and a second
definition would reintroduce it.

It also settles both of `care-agent-platform`'s cases against their own reasoning:

- A doctor's order is not `artifact`, because `artifact` is a **product of
  execution** and no execution produced it.
- An erasure request's subject is not `tool_call`; the tool call is how the approved
  thing gets done. What is being approved is what happens to the patient's records.

## Decision 3 — The set is open, with a required minimum

`event_types` is open, `decision_types` is closed, and RFC-0009 gave the test that
separates them: does adding a value create a path by which execution proceeds
without a human `APPROVE`?

A subject type says **what** was approved, never **whether**. `AUTO_APPROVE` as a
decision creates such a path; `record` as a subject cannot — the decision field is
untouched and every guarantee on it still holds.

```yaml
subject_types:
  closed: false
  required_minimum: [job, execution, tool_call, artifact, deployment, record]
```

So this RFC is the last one needed for a *missing kind of thing*. The next consumer
that approves something none of these names does not wait on us.

**What an open set costs, stated plainly:** two consumers can name the same kind
differently, which is a weaker version of the fragmentation being fixed here. The
required minimum is what bounds it — everything the ecosystem has met so far has a
name, and a consumer needing a new one is in genuinely new territory rather than
working around a hole.

## Decision 4 — `subject` joins `required_meaning`

`approval/v1` has `subject` in `required`, and our statement of what an approval
must mean does not mention it. That is how the set went undeclared: the meaning it
serves was never listed either.

> **เกี่ยวกับอะไร (subject)** — คำตัดสินที่ไม่บอกว่าตัดสินเรื่องอะไร ตามรอยไม่ได้

## Alternatives Considered

**Add `record` and leave the set in their file.** Fixes the case and not the cause.
The set stays somewhere its owner does not look, and the next missing value is found
the same way — by a consumer picking the least-wrong option in production.

**Make it closed with six values.** Honest, and it puts every future consumer behind
an RFC here for something that cannot weaken a guarantee. RFC-0009 already rejected
that trade for events, with reasoning that transfers.

**Hand the set to `agent-platform`, like `event/v1`'s.** Consistent, and wrong in
the direction that matters: the platform's five values are exactly the machinery
vocabulary, and the missing one is a *domain* concept. An owner who only sees the
platform's side of the boundary is the reason it was missing.

## Architectural Impact

- **Control Plane** — none.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — none. `Decision` in `packages/core` does not carry a subject
  type today; this changes no code in this repository.

`semantics_version` `1.3` → `1.4`, because `frozen` changes.

**The cost, named:** `agent-platform` followed `1.3` on 2026-09-17 and would be asked
to follow again a day later. That is a real cost and it is worth paying here rather
than batching, because a consumer is choosing between two wrong values in running
code right now, and every day it stays is more records written under whichever word
was picked that morning.

## Risk Assessment

| risk | weight |
| --- | --- |
| An open set fragments anyway | Real, and smaller than the hole it replaces. The required minimum plus `event/v1`'s definition give the common cases one name each. |
| `record` is too coarse for some domain | By design — `event/v1` says the real kind belongs in `metadata.record_type` and the platform should not know domain types. Same answer here. |
| Another bump so soon | Named above. The alternative is holding a correction back to spare a version number. |

## Migration Plan

1. Accept this RFC.
2. `contract-semantics.yaml`: `subject_types` added under `approval.frozen`,
   `subject` added to `required_meaning`, `semantics_version` → `1.4`.
3. `carried_check` gains markers for both, so the new set is compared against the
   derived contract like every other frozen entry — including a gap entry while
   `approval/v1` has not carried it yet.
4. Report on `agent-platform#73` that the answer is here, and what it is.

Nothing in this plan waits on the durable store or on #32.

## Open Questions

- **Does `consent` belong in the minimum?** `event/v1` gave it its own subject type
  rather than folding it into `record`, because it is a platform-level contract
  (`consent/v1`) and not a domain record. Whether an approval is ever *about* a
  consent, rather than recorded alongside one, is not something this repository has
  evidence for. Left out until a consumer hits it, on the same terms that produced
  this RFC.
- **`frozen` says a change moves the version "major", and practice moves the
  minor.** RFC-0013 added an invariant to `frozen` and went `1.2` → `1.3`; this RFC
  follows that. The comment and the practice disagree, and fixing the comment is
  prose — no version move — but it should be decided rather than left for the next
  reader to notice.
