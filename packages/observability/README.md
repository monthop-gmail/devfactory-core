# observability module

The audit and event log — issue #6.

Spec: [RFC-0003](../../rfcs/0003-audit-event-log-schema.md) as amended by
[RFC-0008](../../rfcs/0008-external-event-intake.md), with the tenant model from
[RFC-0006](../../rfcs/0006-tenant-workspace-model.md).

In memory for v0.1. No metrics backend, no dashboard — out of scope per the issue
and per [`CORE_BOUNDARY.md`](../../docs/governance/CORE_BOUNDARY.md).

The `Event` type itself lives in `devfactory_core.events`, because the state machine
emits events. This package owns **storage** and **intake**.

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

`source.kind` is forced to `external` rather than trusted: an inbound event is
external by the fact of arriving here, whatever it claims about itself.

Guessing a tenant is treated as worse than losing the event. A lost event is a visible
gap; one tenant's activity written into another tenant's immutable trail is not.

## Tests

```bash
cd packages/observability
python -m pytest                                # 59 tests
python -m pytest --cov=devfactory_observability  # gate at 90%, currently 100%
```

Payload conformance against the pinned contracts is
[`conformance/`](../../conformance/) at the repository root.
