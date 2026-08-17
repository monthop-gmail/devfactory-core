# RFC-0005: Platform Contract Authority and Plane Boundaries

## Status
Draft — Architecture Owner direction agreed 2026-08-17 · pending maintainer approval per `GOVERNANCE.md`

Answers [issue #8](https://github.com/monthop-gmail/devfactory-core/issues/8), filed by the
[`agent-platform`](https://github.com/monthop-gmail/agent-platform) team and tracked upstream as
[agent-platform#6](https://github.com/monthop-gmail/agent-platform/issues/6).

## Context
`agent-platform` owns the shared contract set that every repo in the ecosystem consumes.
Two of those contracts are derived directly from RFCs in this repository:

| contract in `agent-platform` | derived from |
| --- | --- |
| `contracts/approval/` | [RFC-0002](0002-governance-decision-contract.md) |
| `contracts/event/` | [RFC-0003](0003-audit-event-log-schema.md) |

Both are deliberately unwritten and marked `external-authority-pending`, because
`GOVERNANCE.md` gives the Architecture Owner of *this* repository final decision
authority on system direction, and
[ADR-0006](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0006-contract-versioning.md)
splits its own status accordingly: versioning `Accepted`, ownership `Pending external confirmation`.

Nothing can move until this repository answers. `contracts/approval/` and
`contracts/event/` stay empty, this repository cannot register as a consumer, and
the drift that ADR-0006 exists to prevent stays theoretical only because neither
side has written anything yet.

## Problem Statement
The upstream question is posed as a binary — either authority moves to
`agent-platform`, or this repository keeps it and `agent-platform` may only
reference. Both answers are wrong in the same way: they treat *meaning* and
*wire format* as one indivisible thing.

- If authority moves wholesale, `agent-platform` becomes free to redefine what
  `APPROVE` means, and "no execution without APPROVE" — a direction-lock
  principle of this repository, not a serialization detail — becomes editable by
  another repo's ADR process.
- If this repository keeps everything, `agent-platform` must open an RFC here to
  add `tenant_id` or `correlation_id` — fields this repository has no opinion
  about — and every platform-level need queues behind our review cycle. ADR-0006
  correctly calls federated ownership the problem the platform repo was created
  to solve.

The two failure modes have different subjects. What must not drift is the
*semantics*. What must be centrally versioned is the *schema*.

## Goals
- Answer issue #8 with something enforceable, not a statement of goodwill.
- Keep governance semantics — decision vocabulary and audit guarantees — under
  this repository's RFC process.
- Let `agent-platform` publish, version, and extend the canonical wire schema
  without waiting on this repository for changes that carry no semantic weight.
- Make drift detectable rather than merely forbidden.
- Settle the two plane-boundary name collisions before any code exists.

## Non-Goals
- Writing the `approval/v1` or `event/v1` schemas. That is `agent-platform`'s
  work once this RFC is accepted.
- Changing ADR-0006's versioning scheme. Directory-per-major with additive-only
  changes inside a major is accepted here as-is.
- Claiming authority over `execution/v1`, `identity/v1`, `policy/v1`, or any
  other contract that does not derive from an RFC in this repository.

## Decision 1 — Authority splits along semantics / schema

**This repository retains authority over semantics. `agent-platform` holds
authority over the canonical wire schema.**

### What this repository owns

For `approval/` (from RFC-0002):

- the decision vocabulary: `APPROVE`, `REJECT`, `REQUIRE_CHANGES`
- the guarantees: decisions are immutable · every `APPROVE` is auditable ·
  execution without `APPROVE` is forbidden
- the rule that an approval names an accountable `authority` and a `reason`

For `event/` (from RFC-0003):

- the canonical event vocabulary: `JOB_CREATED`, `STATE_TRANSITION`,
  `GOVERNANCE_DECISION`, `TASK_ASSIGNED`, `EXECUTION_STARTED`,
  `EXECUTION_FAILED`, `JOB_COMPLETED`
- the guarantees: events are append-only · no silent state change
- what each event type means and when it must be emitted

For [RFC-0001](0001-job-state-machine.md) (job state machine) and
[RFC-0004](0004-orchestration-execution-boundary.md) (orchestration/execution
boundary): **full authority, semantics and structure both.** Neither has a
counterpart contract. `execution/v1` sits one layer below the job level and says
so explicitly; the job level is not the platform's to define.

### What `agent-platform` owns

- field names, types, JSON Schema structure, and `$ref` composition in
  `contracts/approval/` and `contracts/event/`
- version boundaries and deprecation, per ADR-0006
- the conformance test suite and the consumer registry

### The rules that make the split hold

1. **Additive platform fields need no RFC here.** `agent-platform` may add
   fields that carry no semantic change to a decision or an event —
   `tenant_id`, `workspace_id`, `execution_id`, `agent_id`, `correlation_id`,
   `policy_id`, `expires_at`, `action_risk`, `escalation_target`, cost
   attribution — under its own ADR process alone. This resolves
   [consumer analysis §4.6](https://github.com/monthop-gmail/agent-platform/blob/main/architecture/consumer-devfactory-core.md):
   RFC-0002 is not "missing" those fields, it is silent about them by design.

2. **Semantic changes require an RFC here first.** Any of the following is a
   semantic change and must be accepted as an RFC in this repository before an
   ADR in `agent-platform` may implement it:
   - adding, removing, or renaming a decision type or event type
   - weakening or removing a stated guarantee
   - making a semantically required field optional, or the reverse
   - changing what an existing decision, event, or state *means*
   - introducing a path by which execution can proceed without `APPROVE`

3. **Every derived contract carries its provenance.** Each of
   `contracts/approval/` and `contracts/event/` must record a `derived_from`
   pointer naming the RFC file **and the commit SHA** it was derived from. Drift
   is then a mechanical check — compare the pointer against this repository's
   `main` — rather than a matter of trust. A contract whose pointer no longer
   resolves is out of conformance regardless of what its `CHANGELOG.md` says.

4. **No parallel schema here.** This repository consumes the published schema as
   the single wire format and does not maintain its own. Our RFCs are intent
   specifications; they are not serialization formats and must not be read as
   such.

5. **Escalation is explicit.** Disagreement is raised as an issue in both
   repositories. The Architecture Owner of this repository has final say on
   semantics; the Architecture Owner of `agent-platform` has final say on schema
   shape and versioning. Neither may resolve the other's half unilaterally.

### Why this over the binary options

The split matches where the knowledge actually is. This repository knows why
`APPROVE` must precede execution and cannot be delegated to an agent; it has no
view on whether the tenant scope arrives as `tenant_id` or nested under
`context`. `agent-platform` knows what every consumer needs on the wire and has
no mandate to decide what accountability means.

It is also the only option that keeps ADR-0006's own reasoning intact.
ADR-0006 rejected federated ownership because it produces "schema เดียวกันคนละ
field" — same schema, different fields. That failure is a *schema* failure, and
centralizing the schema fixes it. Centralizing the semantics as well was never
required to solve it.

## Decision 2 — Plane boundaries: both modules are internal, and both get renamed

[ADR-0003](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0003-agent-gateway-boundary.md)
forbids the bare word "gateway" and reserves `model-gateway` for outbound
provider access and `agent-gateway` for inbound agent traffic across the
ecosystem. Two modules in this repository collide with that.

| before | after | direction | scope |
| --- | --- | --- | --- |
| `packages/proxy` | `packages/provider-proxy` | outbound | internal to devfactory-core |
| `apps/api-gateway` | `apps/control-api` | inbound | internal to devfactory-core |

**`packages/provider-proxy`** is outbound provider access *for this repository
only*. It is not the ecosystem `model-gateway` and must never be offered to
another repository as a shared service. If `model-gateway` is built, this module
becomes a thin client of it — never a second implementation of it. That
commitment is the point of the decision; the rename only makes it visible.

**`apps/control-api`** is the inbound HTTP surface of this repository's control
plane — job intake, governance decisions, state queries. It is not
`agent-gateway`; it terminates no agent traffic on behalf of the ecosystem. The
rename drops the forbidden word rather than qualifying it.

Both renames happen now, while every affected file is a one-line `README.md`.
`CONTRIBUTING.md` is updated in the same change: the Core list entry
"Provider proxy" becomes "Provider proxy (internal, `packages/provider-proxy`)".

## Decision 3 — Consumer manifest is adopted, conformance stays `unknown`

`platform-contract.yaml` is added at the repository root, pinning `identity/v1`,
`execution/v1`, `policy/v1`, and `error/v1`.

`conformance.status` is `unknown` with `last_verified: null`, and stays that way
honestly. ADR-0006 requires three things of a consumer — a manifest, a
conformance test in CI validating **real payloads**, and a release gate. This
repository has the first and cannot have the other two: `packages/*` contains no
code, so there is no payload to validate. `unknown` is the accurate value, and
`passing` would be a lie the registry would then repeat.

`approval/v1` and `event/v1` are left commented out — not because authority is
still pending, which this RFC settles, but because the schemas do not exist yet.
They are pinned when `agent-platform` publishes them.

## Architectural Impact

- **Control Plane** — governance decision semantics stay under this repository's
  RFC process; the serialization is external. No change to the approval flow
  itself.
- **Orchestration** — none from Decision 1. Decision 2 renames `apps/api-gateway`
  to `apps/control-api`.
- **Execution** — `packages/proxy` becomes `packages/provider-proxy` and is
  bound to internal scope. Future `model-gateway` integration becomes a client
  relationship rather than a merge.
- **Observability** — event vocabulary and the append-only guarantee stay here;
  field-level structure moves to `contracts/event/`. See
  [RFC-0008](0008-external-event-intake.md) for inbound events.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| "Semantic vs additive" is argued case by case and the boundary erodes | medium | Rule 2 lists the five semantic-change classes explicitly; anything on that list is semantic by definition, not by debate |
| `derived_from` pointers go stale and nobody notices | medium | The pointer includes a commit SHA, making staleness mechanically detectable; a stale pointer is a conformance failure |
| Two owners means a change needing both stalls | low | Rule 5 splits final say by subject, so neither half can be blocked by the other's silence on a matter outside it |
| `provider-proxy` grows into a de facto shared `model-gateway` anyway | medium | Decision 2 forbids exposing it to other repositories; a request from another repo is the signal to build `model-gateway` instead |
| Renaming later, once code exists, is expensive | low | Both renames land now, while the modules are one-line READMEs |

## Migration Plan

1. Accept this RFC (maintainer approval per `GOVERNANCE.md`).
2. `git mv packages/proxy packages/provider-proxy` · `git mv apps/api-gateway apps/control-api`; update both READMEs to state direction and internal scope; update `CONTRIBUTING.md`. **Included in this change.**
3. Add `platform-contract.yaml`. **Included in this change.**
4. `agent-platform` records the ownership half of ADR-0006 as accepted with this split, and adds `derived_from` pointers to `contracts/approval/` and `contracts/event/`.
5. `agent-platform` publishes `approval/v1` and `event/v1`.
6. This repository pins both in `platform-contract.yaml`.
7. `conformance.status` moves off `unknown` only when code produces real payloads and CI validates them — no earlier.

Steps 4 and 5 are upstream work and are not blocked by anything here once this
RFC is accepted.

## Open Questions
- Does `agent-platform` want the `derived_from` pointer checked automatically in
  CI, or is a manual check at contract-change time enough? Either satisfies
  Rule 3; the automated version is upstream's call since the check runs there.
- When `model-gateway` is built, does `packages/provider-proxy` disappear
  entirely or remain as an internal adapter? Deferred until `model-gateway`
  exists — deciding now would be speculation.
