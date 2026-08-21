# observability module

The audit and event log — issue #6.

Spec: [RFC-0003](../../rfcs/0003-audit-event-log-schema.md) as amended by
[RFC-0008](../../rfcs/0008-external-event-intake.md), with the tenant model from
[RFC-0006](../../rfcs/0006-tenant-workspace-model.md).

In memory for v0.1. No metrics backend, no dashboard — out of scope per the issue
and per [`CORE_BOUNDARY.md`](../../docs/governance/CORE_BOUNDARY.md).

The `Event` type itself lives in `devfactory_core.events`, because the state machine
emits events. This package owns **storage**, **intake**, and **replay**.

## Use

```python
from devfactory_core import Job, Principal
from devfactory_observability import EventLog, accept_external

log = EventLog()

job = Job(job_id="job-001", tenant_id="acme", workspace_id="ws-core",
          principal=Principal("human", "alice"))
job.submit_for_governance(reason="ready")
log.extend(job.events)

log.read("acme")               # events for one tenant, in append order
log.read("acme", job_id="job-001")
log.payloads("acme")           # event/v1 wire shape
log.digest("acme")             # hash chain over the tenant's event ids
```

## Tenant isolation is a partition, not a filter

RFC-0006 states isolation as a **storage-layer** guarantee and says a
`WHERE tenant_id = ?` filter does not satisfy it. The in-memory analogue is a
separate partition per tenant, so a read has nowhere to look outside the tenant it
names. A single list guarded by a filter would put one forgotten predicate between
two tenants; there is no predicate to forget here, because there is no shared list.

Every read takes a `tenant_id`. There is deliberately no method that returns events
across tenants — `tenants()` and iteration yield identifiers only, and `len()` is a
total that carries no tenant's content. An unknown tenant reads as empty rather than
raising, so probing for another tenant's existence tells the caller nothing.

## Append-only is the absence of the methods

There is no `update`, `delete`, `clear`, or `truncate`. An API with no way to mutate
history cannot be talked into mutating history, which is stronger than a flag someone
can pass.

`digest(tenant_id)` is a hash chain over one tenant's event ids in append order. It
turns the append-only claim into something checkable: take it, carry on, take it
again — growth changes it, and so would an altered or removed earlier record.

`extend()` is deliberately **not** atomic. A partial append keeps the events that were
valid and reports the one that was not; discarding accepted records to punish a later
bad one would lose history that actually happened.

## Intake — RFC-0008

`accept_external()` turns an inbound payload into an `Event`, or refuses it.

| situation | outcome |
| --- | --- |
| no `job_id` | **accepted** — a sighting is a real event that no job caused |
| `job_id` is `"none"`, `"-"`, `"0"`, … | `FabricatedIdentifier` |
| no resolvable tenant | `MissingTenant` — rejected at intake, never defaulted |
| resolver returns `None` | `MissingTenant` — a resolver that cannot answer is not second-guessed |
| no subject | `MissingSubject` |
| no `source.system` | `ExternalSourceRequired` |
| unparseable `occurred_at` | `ValueError` — substituting our clock would misreport when it happened |
| unrecognised `event_type` | **accepted and kept**, `is_recognised` is `False` |
| malformed `event_type` | `MalformedEventType` — see below |

### Unknown is kept; malformed is refused

`event/v1` tells consumers to keep an `event_type` they do not recognise and skip
interpreting it, and intake does exactly that — `SIGHTING_RECORDED` from `navi-ims`
is stored whole. But `EventTypeName` constrains the *shape* of the name
(`^[A-Z][A-Z0-9_]{2,63}$`) so that a vocabulary allowed to grow still reads as a
vocabulary rather than as arbitrary text.

Unknown is a value we have not met. Malformed is not a value at all, so it is
refused at the boundary rather than written into a record nobody can correct.

`EVENT_TYPE_PATTERN` mirrors the contract for the same reason `identity.ID_PATTERN`
does: intake has to make the refusal and the contract lives in another repository.
It is the minimum needed to refuse, not a second copy of the schema — RFC-0005
Rule 4 forbids the latter.

`source.kind` is forced to `external` rather than trusted: an inbound event is
external by the fact of arriving here, whatever it claims about itself.

Guessing a tenant is treated as worse than losing the event. A lost event is a visible
gap; one tenant's activity written into another tenant's immutable trail is not.

## Replay — RFC-0003

`replay_job(events)` rebuilds a job from its trail alone; `replay_tenant(log, tenant)`
does it for every job in one partition.

```python
from devfactory_observability import replay_job, replay_tenant

seen = replay_job(log.read("acme", job_id="job-001"))
seen.state                 # JobState.COMPLETED
seen.awaiting_from         # where a paused job would return to
seen.approval_decision_id  # the APPROVE it was executing under
seen.approval_expires_at   # when that APPROVE stopped authorising, if it said
seen.history               # every transition, reconstructed
```

RFC-0003 lists *"enable replayable job history"* as a goal. This makes it checkable,
and it is a **completeness proof** rather than a convenience: every `STATE_TRANSITION`
names the state it left, so a replay holding a running state notices a record that is
missing or out of order — which the engine cannot, having written them.

It also re-checks the guarantees against what was actually written, by something that
was not there when it happened. Every edge is validated against
`devfactory_core.states.reachable_from`, the same call the engine makes, so there is
no second transition table here to drift.

| the trail | outcome |
| --- | --- |
| nothing to replay | `EmptyTrail` |
| does not start at `JOB_CREATED` | `UnstartedTrail` |
| a transition leaves a state the replay is not in | `BrokenTrail` |
| records an edge `states.py` does not declare | `UndeclaredTransition` |
| enters `APPROVED`/`REJECTED` with no matching decision | `UnauditedDecision` |
| begins execution with no `APPROVE` before it | `UnauditedExecution` |
| begins execution after that `APPROVE`'s `expires_at` | `ExecutionAfterExpiry` |
| `COMPLETED` and `JOB_COMPLETED` disagree | `IncompleteSettlement` |
| settled in any terminal with no `JOB_SETTLED` | `UnsettledTrail` |
| a closing record on a job that is still running | `PrematureSettlement` |
| the closing record's `event_count` disagrees with the trail | `MiscountedTrail` |
| an unrecognised event type | **skipped** — `event/v1`: keep it, do not interpret it |
| an external event | **skipped** — RFC-0008: another system is not an authority on our lifecycle |

A trail truncated at the end used to be noticeable only for a job that completed,
because `JOB_COMPLETED` was the one record saying something should have followed.
[RFC-0012](../../rfcs/0012-terminal-closing-record.md) closed that: every terminal now
emits `JOB_SETTLED` last, so `FAILED`, `CANCELLED`, and `TIMED_OUT` are checked the same
way `COMPLETED` always was.

The prediction recorded here before — that closing it needed a per-job sequence number
in `event/v1` — was wrong, and worth keeping visible. ADR-0015 established that a number
carried on an event cannot say what number the last event should have been, because that
answer is not on any event. What closed it needed no contract change at all.

**A job still in flight remains unverifiable this way**, and that is not an oversight:
"complete" means "nothing missing up to the end", and a running trail has no end. That
belongs to the store, where `digest()` already is the primitive.

## Tests

```bash
cd packages/observability
python -m pytest                                 # 89 tests
python -m pytest --cov=devfactory_observability  # gate at 90%
```

The end-to-end flows that exercise replay over whole journeys are
[`simulation/`](../../simulation/) at the repository root — issue #7.

Payload conformance against the pinned contracts is
[`conformance/`](../../conformance/) at the repository root.
