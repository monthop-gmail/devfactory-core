# RFC-0006: Tenant and Workspace Model

## Status
Draft — Architecture Owner direction agreed 2026-08-17 · pending maintainer approval per `GOVERNANCE.md`

Closes the highest-severity gap in [issue #8](https://github.com/monthop-gmail/devfactory-core/issues/8).

## Context
No document in this repository contains the words tenant, workspace, or
multi-tenancy. The job state machine, the governance decision contract, and the
audit event schema were all specified as if exactly one organization uses the
system.

[ADR-0007](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0007-multi-tenancy.md)
makes `tenant_id` required on every contract in the ecosystem with no exceptions,
and `contracts/identity/v1` defines the id types every other contract must
`$ref` rather than redeclare.

The upstream consumer analysis rates this the largest gap and places it on this
repository's side, not the contract's: without a tenant model, conformance fails
at the first field of every payload.

## Problem Statement
Single-tenant-by-omission is not a neutral starting point. It is a decision
embedded in three specifications, and it fails in ways that governance cannot
tolerate:

- An immutable audit trail with no tenant scope cannot answer "whose job was
  this" after the fact. Adding the field later does not repair records already
  written.
- A governance decision with no tenant scope permits an approval issued in one
  tenant to authorize execution in another. Nothing in RFC-0002 currently
  forbids it, because RFC-0002 cannot express it.
- Cost isolation per provider is a stated architectural principle. Cost cannot
  be attributed without a scope to attribute it to.

Retrofitting tenancy into a running control plane is the migration everyone
regrets. This repository has no code yet, which makes now the cheapest possible
moment and the only one where the fix is documentation.

## Goals
- Introduce tenant and workspace scope into the job, decision, and event models.
- Reuse `identity/v1` definitions rather than inventing parallel id types.
- Keep single-tenant deployments simple without letting them omit the field.
- State the isolation guarantee at the level where it actually has to hold.

## Non-Goals
- Authentication and authorization implementation. This RFC defines scope, not
  who may act within it.
- Per-tenant billing, quota, or provisioning flows.
- Tenant administration UI — out of bounds for v0.x per `CORE_BOUNDARY.md`.
- Redefining `identity/v1`. Per [RFC-0005](0005-platform-contract-authority.md)
  the schema is `agent-platform`'s; this RFC states which parts of it this
  repository's models carry.

## Proposed Model

### Hierarchy

```text
Tenant          hard isolation boundary — never crossed
  └─ Workspace  unit of work within a tenant
       └─ Job · Decision · Event · Execution
```

Per ADR-0007, `Organization` is a business term for a tenant and not a separate
layer, and `Project` / `Department` are labels on a workspace and not new id
layers. This repository adopts both rulings and will not add a third level.

### Identifiers

All identifiers use the `identity/v1` `Id` form — lowercase, leading
alphanumeric, `[a-z0-9_-]`, at most 63 characters. This repository does not
define its own id pattern.

| field | type in `identity/v1` |
| --- | --- |
| `tenant_id` | `TenantId` |
| `workspace_id` | `WorkspaceId` |
| `principal` | `Principal` |
| `correlation_id` | `Id` |

### Required scope per model

| model | `tenant_id` | `workspace_id` | rationale |
| --- | --- | --- | --- |
| Job | required | required | a job is always work inside one workspace |
| Governance decision | required | required | a decision is about a job, so it inherits the job's scope |
| Event | required | optional | tenant-level events exist and have no workspace, per ADR-0007 Consequences |
| Execution | required | required | inherited from `execution/v1` `ExecutionContext` |

Every job additionally carries the `Principal` that created it. "Who asked for
this" is not derivable from the tenant and is required by the audit trail.

### Isolation guarantee

Tenant isolation is a hard boundary. Per ADR-0007 it must reach the storage
layer — separate database, index, or storage scope — and a query-level `WHERE
tenant_id = ?` filter alone does not satisfy it. A single forgotten predicate
must not be able to leak across tenants.

This repository states the guarantee now so that the first storage decision is
made under it rather than against it.

### Cross-tenant rules

1. A governance decision applies only to jobs in its own tenant. A decision
   whose `tenant_id` differs from the job's is invalid and must be rejected, not
   coerced.
2. A job may not reference, supersede, or await another job in a different
   tenant.
3. `Principal.on_behalf_of` chains may not widen scope. An agent acting for a
   human is bounded by that human's tenant and workspace, per `identity/v1`.
4. Events are written into the tenant they describe. There is no global event
   stream that crosses tenants.

### Single-tenant deployments

A deployment serving one organization uses the literal tenant id `default`.

The field is never omitted, never null, and never implicit. This is what keeps
the eventual second tenant from being a schema migration: the payload shape is
already correct, and only the value changes. It also keeps audit records written
on day one readable on the day tenancy actually matters.

## Architectural Impact

- **Control Plane** — the job model and the governance decision model both gain
  required scope fields. Approval validity becomes tenant-bounded, which is a
  new enforceable rule rather than a restatement.
- **Orchestration** — task decomposition inherits scope from the parent job;
  orchestration may not create work outside the job's workspace.
- **Execution** — aligns with `execution/v1` `ExecutionContext`, which already
  requires all four ids. No change needed on the contract side.
- **Observability** — every event carries `tenant_id`; workspace is optional for
  tenant-level events. Cost attribution becomes possible, which
  [RFC-0003](0003-audit-event-log-schema.md) listed as Future Work.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| Isolation is implemented as a query filter and leaks | high | The storage-level requirement is stated as a guarantee here, before any storage decision exists; a filter-only implementation violates this RFC, not merely a best practice |
| `default` becomes a dumping ground and real tenants are never introduced | medium | The value is a placeholder, not a mode; nothing in the model treats `default` specially, so introducing a second tenant requires no code change and there is no threshold to cross |
| Workspace granularity turns out wrong for real use | medium | Workspace is required on jobs but carries no semantics beyond scope in v0.x; refining what a workspace *contains* does not change the id layer |
| Scope fields drift from `identity/v1` | low | This RFC declares reuse rather than redefinition; `identity/v1` is the source and RFC-0005 Rule 4 forbids a parallel schema |
| Retrofitting the audit trail later | high | Addressed by adopting the field now, while no records exist |

## Migration Plan

1. Accept this RFC.
2. `packages/core/state-machine.md` records the required scope fields on the job
   model. **Included in this change.**
3. [RFC-0008](0008-external-event-intake.md) carries the event-side scope rules,
   since it revises the event model for other reasons at the same time.
4. When `packages/core` gains code, the job type carries `tenant_id`,
   `workspace_id`, and `principal` from its first commit.
5. `identity/v1` is pinned in `platform-contract.yaml`. **Included in this change.**

No data migration exists, because no data exists. That is the entire reason this
RFC is cheap today.

## Open Questions
- Does a job ever legitimately span workspaces within one tenant? Assumed no.
  If a real case appears, it is a new RFC, not a relaxation of the required
  field.
- Should `workspace_id` be required on `GOVERNANCE_DECISION` events specifically,
  given events allow it to be optional? The decision itself always has one, so
  the event will carry it in practice; leaving it optional at the event layer
  avoids a second rule for one event type.
