# RFC-0018: One Database Per Tenant

## Status
Draft — เสนอ 2026-09-22 · จะเป็น `Accepted` เมื่อ merge ตาม `CONTRIBUTING.md`

[RFC-0014](0014-durable-event-store.md) Migration Plan **ขั้น 4** — the durable
implementation, in its own RFC, because choosing one is an architecture change.

**Accepting this decides which store. It does not put one in `v0.x`.**
`docs/governance/CORE_BOUNDARY.md` forbids adding a vendor-bound framework in
`v0.x`, and RFC-0014 said step 4 sits outside it. That has not changed. What
changes is that the choice stops being open, and the three questions RFC-0014
left as *"answerable only alongside a concrete store"* get answered.

## Context

RFC-0014 defined what a store owes and refused to name one, for a reason that has
held up: the three pieces of work waiting on it were waiting on the *guarantees*.
Those guarantees now have a conformance suite (`conformance/store_contract.py`,
23 checks) and one implementation that passes it.

This RFC was written after building a second one. A throwaway SQLite-per-tenant
prototype — in the session scratchpad, never in this repository — was run against
that suite. **It passes 23 of 23**, and it survives process restart with its
digest and count intact.

That result is worth more than the passing grade: it is the first evidence that
the contract is not accidentally shaped around the in-memory reference. A contract
with one implementation is a description of that implementation.

## Problem Statement

Obligation 2 is the obligation that decides this, and it decides it early:

> **Tenant isolation at the storage layer.** Not a predicate on a shared table …
> the property RFC-0006 asks for is that **there is no predicate to forget**.

That sentence eliminates the answer everyone reaches for first — one table, a
`tenant_id` column, a filter on every query. It is not a style objection. A shared
table means isolation is upheld by every query author forever, and the failure is
one missing `WHERE` away and silent when it happens.

Everything else in RFC-0014 is satisfiable many ways. Obligation 2 is not.

## Decision 1 — One SQLite database file per tenant

The unit of physical isolation is a file.

| obligation | how this shape meets it |
| --- | --- |
| 1 · append-only | no `UPDATE`/`DELETE` is issued, and the chain in Decision 4 below makes a rewrite detectable |
| 2 · tenant isolation | **a connection to one tenant's file has no way to reach another's rows, because there is no shared table to reach into** |
| 3 · order, total per tenant | `INTEGER PRIMARY KEY AUTOINCREMENT` — one sequence per file |
| 4 · idempotent per tenant | `UNIQUE(event_id)` inside the file **is** per-tenant · the shape cannot express the global form even if someone wanted it |
| 5 · digest | a chain column extended in the same transaction as the insert |

Obligation 4 is the one worth pausing on. We corrected it on 2026-09-18 from
*"idempotent by `event_id`"* to *"…within a tenant"* after finding that a shared
seen-set let `append` answer a question `read` refuses to answer. **This shape does
not have to be told that.** A per-file `UNIQUE` has no global form available. The
correction and the storage layout arrive at the same place from opposite
directions, which is the strongest signal available that the correction was right.

### Why not the other candidates

**Postgres, schema per tenant.** A schema is a real scope and this would work. It
is weaker on the one obligation that decides: `search_path` mistakes cross schemas,
and a query with sufficient privilege can join across them. The isolation is
*administered* rather than *structural*. It also adds a service, a driver, and a
deployment story to a repository whose entire conformance suite currently runs
offline.

**Append-only files (JSONL) per tenant.** The strongest fit for obligation 1 —
`O_APPEND` is append-only in a way no database is. It loses on obligation 4: a
`UNIQUE` index has to be built and kept, along with crash-safety around two files
that must agree. That is a database, written by us, less well.

**An event-store product.** Vendor-bound service, and obligations 2 and 5 become
configuration rather than structure.

**One SQLite database, tenant as a column.** Rejected by obligation 2 as written.
Named here so nobody has to wonder whether it was considered.

## Decision 2 — Deletion is removing the file · the record of it lives in a registry that is nobody's tenant

*Closes RFC-0014 Open Question 2.*

RFC-0014 required that a tenant erasure leave a record, that the record cannot live
inside the partition being erased, and it declined to name where it does live
because *"the shape depends on the technology"*. The technology is now named:

```text
<root>/tenant-<hash>.sqlite3     one per tenant · erasure is removing this file
<root>/registry.sqlite3          tenant existence · erasure records · nobody's tenant
```

The registry is **not** a tenant scope and holds no events. It answers two
questions: which tenants exist, and which used to. `tenants()` reads it.

The shape of an erasure record follows `care-agent-platform`
[ADR-0012](https://github.com/monthop-gmail/care-agent-platform/blob/main/decisions/0012-erasure-cuts-the-bridge-not-the-trail.md),
which solved the adjacent problem in a regulated domain and reached a conclusion
worth borrowing rather than re-deriving: **erasure cuts the bridge to identity, it
does not delete the trail.** The record is pointers only — which tenant, when,
under whose authority, how many records went — and it holds nothing from inside
what was erased. It cannot, because the file is gone.

## Decision 3 — Workspaces stay inside the tenant file

*Closes RFC-0014 Open Question 3.*

RFC-0006 makes the tenant the hard boundary and the workspace a unit of work within
it. A file per workspace would multiply open handles, and turn a within-tenant
replay — which `replay_tenant` does today — into a merge across files, for a
boundary nobody has asked to harden.

This is the one decision here that is cheap to revisit: it is a file layout, not a
contract. If a workspace ever needs erasing independently, this is where to look
first, and the contract does not change when it does.

## Decision 4 — The checkpoint is `digest()` plus `count()`, and both already exist

*Closes the remainder of RFC-0014 Open Question 1.*

The open question was whether a checkpoint needs more than `digest()` — a digest
says *the prefix I hold has not changed*, not *I hold everything written*.

The half that depended on someone else was answered on 2026-09-20, when
`ecosystem-intelligence` made the content-bound `event_id` a frozen promise. **No
cursor is needed**; re-reading a producer's whole feed is idempotent by contract.

The half that is ours is answered by this shape being a database: the append count
is `SELECT count(*)`, and `count(tenant_id)` is already on the contract surface.

**The checkpoint therefore adds nothing to `EventStore`.** A reader holds
`(digest, count)` per tenant; the digest says the prefix is untouched and the count
says how much there was. That two existing methods turn out to be the whole answer
is the useful part — it means RFC-0014's surface was drawn in the right place.

## Decision 5 — Decision 5 is met by one transaction mode, not by choosing SQLite

RFC-0014 Decision 5 had no check because nothing could violate it. The prototype
can, and the numbers are the reason this decision is written separately rather than
folded into Decision 1.

Same prototype, same four threads appending to one tenant, one line different:

| transaction | landed | errors | chain |
| --- | --- | --- | --- |
| `BEGIN IMMEDIATE` | **160 / 160** | 0 | intact |
| `BEGIN DEFERRED` | 42 / 160 | 3 × `database is locked` | intact, 118 lost |
| none | **160 / 160** | **0** | **forked in 5 places** |

The third row is the one that matters. Every record present, no error raised,
nothing to notice — and a digest that no longer means *this history is untouched*.
That is precisely the failure Decision 5 predicted, in the form hardest to see.

So the implementation obligation is specific, and belongs in the RFC rather than in
someone's memory: **the chain tip must be read inside the same write transaction
that inserts, and that transaction must take the write lock up front.** A
connection per thread, because Python's `sqlite3` refuses cross-thread use of one.

`conformance/store_contract.py` gained a check for this on 2026-09-22
([#57](https://github.com/monthop-gmail/devfactory-core/pull/57)), written without
knowing how any digest is built: replay the order the store reports into a fresh
store and require the digests to match. Verified against the prototype both ways.

## What this gives up, named rather than discovered later

| ceiling | why it is acceptable here |
| --- | --- |
| **One writing host.** | Decision 5 put distributed writers out of scope already. A second writing host is not a tuning change; it is a different store, and the contract exists so that can be swapped. |
| **Many tenants means many files.** | Thousands of tenants means thousands of files and open handles. This is a real ceiling and the first one that will be hit. |
| **Append-only is convention plus detection, not WORM.** | Anyone holding the file can rewrite it. The chain makes a rewrite *detectable*, not impossible. The same is true of Postgres, and a store that claims otherwise is describing its API, not its storage. |
| **Cross-tenant questions cost N file opens.** | *"How many events across all tenants"* is a loop, not a query. That is obligation 2 being paid for, not a defect. |

## Architectural Impact

- **Control Plane · Orchestration · Execution** — none.
- **Observability** — a second `EventStore` implementation. `EventLog` stays as the
  reference. No caller changes, no contract change, no `semantics_version` move.

`sqlite3` is in the Python standard library. No driver, no service, no new
dependency — which is the narrowest reading of *"ไม่ผูก vendor"* any durable option
can claim, though it does not make this a `v0.x` change.

## Risk Assessment

| risk | weight |
| --- | --- |
| The file-count ceiling arrives sooner than expected | Real, and visible when it happens rather than silent. The contract and its suite are what make replacing this a bounded job. |
| `BEGIN IMMEDIATE` is forgotten in a later edit | Checked now. This is the risk that motivated the check rather than the other way round. |
| SQLite is read as "not a real database" | Named to be argued with. The obligation that decides is isolation, and on that one a file is stronger than a schema. |

## Migration Plan

1. Accept this RFC.
2. **Outside `v0.x`** — implement it, and run `conformance/store_contract.py
   --implementation` against it. The suite is the acceptance criterion, not a
   review.
3. On that landing, RFC-0014 step 5: the `Job` constructor closes the rest of
   RFC-0007 Amendment 2, `contract-semantics.yaml`'s retention layer is restated,
   and [#32](https://github.com/monthop-gmail/devfactory-core/issues/32)'s trigger
   fires.

Nothing in step 2 begins while `CORE_BOUNDARY.md` reads as it does. Changing that
is a separate decision and is not asked for here.

## Open Questions

- **When does `v0.x` end?** This RFC is blocked on that and on nothing else. The
  question has not been asked in this repository before, and it should be asked on
  its own rather than answered as a side effect of wanting a store.
- **Does the registry need its own digest?** It records erasures, which is exactly
  the kind of record someone would want to remove. Decision 2 puts it outside every
  tenant, which answers *where*, not *what protects it*. Answerable with the first
  implementation.
- **Does a recurring condition need to be visible?** Carried over from RFC-0014 and
  unchanged by this choice — it is a question about the producer's emitter, not
  about storage.
