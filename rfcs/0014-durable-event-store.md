# RFC-0014: What a Durable Event Store Owes

## Status
Draft — Architecture Owner direction agreed 2026-09-17 · pending maintainer approval per `GOVERNANCE.md`

Defines the obligations a durable store must meet. **It does not choose one.**
`CORE_BOUNDARY.md` permits *"interface / contract (ไม่ผูก tech)"* in v0.x and
forbids *"framework ที่ผูก vendor"*, and that boundary is the right one here for a
reason beyond compliance: three separate pieces of work are waiting on the
*guarantees*, and none of them is waiting on a database.

## Context

`EventLog` holds events in a dictionary and dies with the process. Everything
built on it is correct and none of it survives a restart.

Three commitments already made are blocked on that, and they have been
accumulating rather than competing:

| waiting since | what it needs |
| --- | --- |
| [RFC-0012](0012-terminal-closing-record.md) Decision 4 | a checkpoint for trails that have not settled — stated there as belonging to the store layer, not to a field on an event |
| [issue #32](https://github.com/monthop-gmail/devfactory-core/issues/32) | the agreed trigger for deciding transport: *"เมื่อ `EventLog` มีที่เก็บถาวรเมื่อไหร่ เคาะท่อทันที เพราะตอนนั้นข้อจำกัดจริงจะชัดแล้ว"* |
| [RFC-0007](0007-job-lifecycle-completeness.md) Amendment 2 | a way to check that a superseded job exists, shares the tenant, and settled without delivering — `Job.supersede()` holds both jobs and enforces it; the constructor cannot |

A fourth is quieter. [RFC-0013](0013-audit-fields-that-hold-human-text.md) clause 3
requires every declared human-text leaf to state which layer its retention and
deletion rules live at, and our answer today is *"no store or tenant layer exists
in this system"*. That answer is true and it is not one we should still be giving
once records outlive a process.

## Problem Statement

The obvious reading — *"add persistence to `EventLog`"* — hides the decision. What
makes a store usable here is not that it writes to disk; it is whether it can keep
the promises the rest of this repository has already made on its behalf.

Two of those promises stop being free the moment the log outlives one process:

**Tenant isolation.** [RFC-0006](0006-tenant-workspace-model.md) states isolation
as a **storage-layer** guarantee and says in terms that a `WHERE tenant_id = ?`
filter does not satisfy it. In memory that was cheap — a dictionary of partitions,
with no shared list for a forgotten predicate to leak across. A durable store is
where that promise gets expensive, and it is the promise most likely to be quietly
downgraded to a filter because every database makes a filter the easy path.

**The digest means what it means because there is one writer.** `digest()` is a
hash chain over the order events were appended. One process appending in sequence
makes "the order they were appended" a fact. Two writers make it a race, and a
hash chain over a racing order reports a difference that means nothing — or worse,
agrees by luck. Nothing in the current code says this, because nothing could
violate it yet.

## Goals

- State what a durable store owes, in terms that can be checked.
- Keep every caller of `EventLog` unchanged.
- Let the three blocked pieces of work proceed against the contract rather than
  against an implementation.
- Say which promises get harder, rather than discovering them during migration.

## Non-Goals

- **Choosing a database, or writing one.** Out of bounds for v0.x, and the
  decisions above do not depend on the choice.
- Deployment, migration tooling, or backup policy.
- Deciding transport for issue #32. This RFC is that decision's trigger, not its
  answer.
- Query languages, indexes, or projections beyond what Decision 3 needs.

## Decision 1 — `EventLog` becomes the contract; in-memory becomes one implementation

The surface stays exactly as it is — `append` · `extend` · `read` · `payloads` ·
`tenants` · `count` · `digest` — and gains no method. A durable implementation
satisfies the same surface, and callers do not learn which one they hold.

Keeping the surface frozen is the point rather than a convenience. It already
encodes decisions that were argued once and should not be reopened by a storage
change: there is no `update`, no `delete`, and no method that reads across
tenants. **Append-only is expressed by the absence of the methods**, and an
implementation cannot be talked into mutating history by a flag it does not have.

The in-memory implementation stays, and not only for tests. A store that runs with
no external dependency is what keeps `conformance/payload_check.py` able to run
offline and deterministically, which is the property `ecosystem-intelligence`
independently arrived at for its own vendored schemas.

## Decision 2 — Five obligations, each traceable to a promise already made

| # | obligation | where it comes from |
| --- | --- | --- |
| 1 | **Append-only.** A written record is never modified or removed. | `event/v1` guarantee |
| 2 | **Tenant isolation at the storage layer.** Not a predicate on a shared table. | RFC-0006 |
| 3 | **Append order is preserved and total within a tenant.** | replay reads order as given |
| 4 | **Idempotent by `event_id`.** Re-appending a record already held is refused, not duplicated. | `EventLog.append` today, and issue #32 depends on it |
| 5 | **A digest over the append order of one tenant.** | RFC-0012 Decision 4 |

Obligation 4 is load-bearing beyond this repository. The #32 thread established
that re-reading a producer's whole feed is safe **because** duplicates are refused
at our boundary — which is what allows a consumer to skip keeping a cursor, and
what made "cursor is an optimisation, not a correctness requirement" true. A store
that allowed duplicates would silently withdraw that.

### What obligation 2 rules out

A single table with a `tenant_id` column and a filter applied by every query.

That is not a style preference. The failure it prevents is one forgotten predicate
in one query path, and the property RFC-0006 asks for is that **there is no
predicate to forget**. An implementation satisfies it by giving each tenant its own
physical scope — a separate database, schema, file, or prefix, whichever the chosen
technology calls it — such that a query issued against one tenant has no way to
reach another's rows.

An implementation that cannot do this does not meet the contract, and should be
rejected for that reason rather than adopted with a note.

## Decision 3 — The job registry is a read model, not a second source of truth

The supersede door needs to answer *does job X exist, in this tenant, and did it
settle without delivering.* The temptation is a jobs table.

**There must not be one.** `replay_job` already reconstructs a job's entire state
from its events, and `replay_tenant` does it for every job a tenant has. The trail
is the record — that is the claim this repository has defended in RFC-0003,
RFC-0012 and every replay check since. A jobs table would be a second place where
a job's state is written down, and the first time the two disagree, the question
*"which one is right"* has no answer that does not undermine the log.

So the registry is **derived**: a lookup over the store that replays what it needs.
An index may exist to make it fast, on one condition — an index is a cache of the
log and must be rebuildable from it. If the index is lost, the answer does not
change; only the time to get it does.

This also settles what the constructor can check once a store exists: given one,
the remaining half of RFC-0007 Amendment 2 becomes checkable, and
`platform-contract.yaml` loses its last open door.

## Decision 4 — Deletion exists at the tenant boundary, and nowhere finer

RFC-0013 clause 3 requires each declared human-text leaf to say where its retention
and deletion rules live. With a durable store the honest answer becomes:

```text
per-field deletion   impossible — obligation 1, and being able to would make the
                     append-only guarantee false rather than merely unenforced
per-record deletion  same
per-tenant deletion  available — the unit of physical isolation is the unit of
                     erasure, which is what makes obligation 2 worth its cost
```

That deletion must leave a record, and the record cannot live inside the partition
being erased. It belongs to a scope above the tenant — which this RFC names as an
obligation without specifying its shape, because the shape depends on the
technology and the obligation does not.

Anything finer than a tenant is not offered rather than approximated. A store that
claims per-record deletion has given up obligation 1 and should say so plainly
instead of describing it as a feature.

## Decision 5 — Concurrent writers must be serialised per tenant, or the digest is meaningless

`digest()` is a hash chain over append order. A single process made that order a
fact for free. A durable store invites more than one writer, and then:

- two appends racing produce an order neither writer chose
- a reader comparing digests sees a difference that reflects scheduling, not history
- worse, two orderings can agree by luck, which reads as *"nothing changed"*

So a durable implementation must **serialise appends within a tenant**. Across
tenants there is nothing to serialise — obligation 2 already keeps them apart, and
that is a second reason isolation earns its cost.

This is the obligation most likely to be missed, because nothing today can violate
it. It is stated here so that the first implementation meets it deliberately rather
than by being single-threaded and lucky.

## Alternatives Considered

**Add persistence to `EventLog` directly and skip the contract.** Faster, and it
decides by accident: whichever database is reached for first becomes the definition
of what a store owes. The five obligations above would then be whatever that
database happens to provide, and the two that are expensive — isolation and
serialisation — are exactly the ones a quick implementation drops.

**A jobs table alongside the event store.** Rejected in Decision 3: a second source
of truth for state the log already determines, whose disagreement with the log has
no principled resolution.

**Defer until transport is decided (#32).** Backwards. #32's own agreed trigger is
this store existing, because the transport question turns on constraints — where a
cursor lives, whether replay is available — that only a real store settles.

**Per-record deletion for erasure requests.** Rejected in Decision 4. It does not
bend obligation 1, it removes it, and an audit log whose records can be deleted on
request is not an audit log. Tenant-level erasure answers the same need at the
boundary that is already physical.

## Architectural Impact

- **Control Plane** — none. The job state machine does not know where events go.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — `EventLog` becomes a contract with more than one
  implementation. Replay is unchanged: it reads what it is given, in the order it
  is given, which Decision 2 obligation 3 now guarantees explicitly rather than
  incidentally.

## Risk Assessment

| Risk | Assessment |
| --- | --- |
| Isolation is implemented as a query filter | The likeliest failure, because every database makes the filter the easy path. Stated as a rejection criterion in Decision 2 rather than a preference, so an implementation that does it fails the contract instead of arriving with a note |
| Serialisation is skipped and the digest quietly stops meaning anything | Not mitigated by anything in the code today, because nothing can violate it yet. This RFC is the mitigation: the first implementation meets it knowingly or fails review |
| A jobs table appears later for performance | Decision 3 permits an index and requires it to be rebuildable from the log, which keeps the log the only source of truth |
| The contract is written to fit whatever is implemented first | The obligations are each traced to a promise made before any store existed, so they can be checked against their source rather than against the implementation |
| Tenant-level erasure is treated as sufficient for every erasure request | Not claimed. Decision 4 states what is available, not that it satisfies any particular obligation a deployment may be under |

## Migration Plan

1. Accept this RFC.
2. `EventLog`'s surface becomes the declared contract, with the in-memory
   implementation named as the reference one. No caller changes.
3. A conformance suite for the contract — the same five obligations, run against
   any implementation. The in-memory one passes it on day one, which is what makes
   it a reference rather than a special case.
4. A durable implementation, in its own RFC, since choosing one is an architecture
   change under `CONTRIBUTING.md`.
5. On that landing: the constructor closes the rest of RFC-0007 Amendment 2, the
   retention layer in `contract-semantics.yaml` is restated, and #32's trigger
   fires.

Steps 2 and 3 are inside v0.x. Step 4 is not, and this RFC does not smuggle it in.

## Open Questions

- **Does the checkpoint for a running trail need anything beyond `digest()`?** A
  digest says *the prefix I hold has not changed*. It does not say *I hold
  everything written*. Closing the second one may need the store to report its own
  append count per tenant — cheap to add, and worth deciding with the first
  implementation rather than now.
- **Where does the record of a tenant erasure live?** Decision 4 requires it to sit
  above the erased partition and does not name the scope. It is answerable only
  alongside a concrete store.
- **Do workspaces need physical isolation too, or only tenants?** RFC-0006 makes
  the tenant the hard boundary and the workspace a unit of work within it. Nothing
  so far has asked for more, and adding it speculatively would double the cost of
  obligation 2.
