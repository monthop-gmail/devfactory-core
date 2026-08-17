# RFC-0008: External Event Intake

## Status
Draft — Architecture Owner direction agreed 2026-08-17 · pending maintainer approval per `GOVERNANCE.md`

Amends [RFC-0003](0003-audit-event-log-schema.md). Closes gap 5 of
[issue #8](https://github.com/monthop-gmail/devfactory-core/issues/8) and carries the
event-side scope rules from [RFC-0006](0006-tenant-workspace-model.md).

## Context
RFC-0003 requires `job_id` on every event. That is correct for events this
repository produces — every one of them originates from a job.

It is wrong for events arriving from elsewhere. `agent-platform` is making
`job_id` optional in `contracts/event/` because events from systems such as
`navi-ims` — a sighting, a geofence crossing — are real events that no job
caused. The upstream analysis notes the resulting compatibility is one-directional:
this repository's events are safe to send outward, while inbound platform events
break against a required `job_id`.

## Problem Statement
A required `job_id` on inbound events leaves three options, and two of them are
worse than the problem:

1. **Reject the event.** An append-only audit log that refuses records because
   they lack a field they cannot have is not complete, and completeness is the
   entire value of the log.
2. **Synthesize a placeholder `job_id`.** This writes a fabricated identifier
   into an immutable record. Every later reader is misled, and the log's own
   immutability guarantee is what prevents the correction. This is the worst
   available option precisely because it looks like the easy one.
3. **Make `job_id` optional and require a subject instead.** Chosen here.

The underlying mistake in RFC-0003 is that `job_id` was serving two jobs at once:
identifying what the event is *about*, and linking it to a job. Those coincide
only when a job caused the event.

## Goals
- Accept events that no job caused, without fabricating data.
- Keep every event answering "what is this about" — optional `job_id` must not
  become optional subject.
- Adopt the tenant scope from RFC-0006 on the event model.
- Add `correlation_id`, which RFC-0003 already listed as Future Work.
- State what must never be written into the audit log.

## Non-Goals
- The wire schema for `event/v1`. Per [RFC-0005](0005-platform-contract-authority.md)
  that is `agent-platform`'s; this RFC settles the semantics it derives from.
- Metrics backends, dashboards, or event transport. Unchanged from RFC-0003's
  Non-Goals.
- Ingesting events from a specific external system. This RFC defines intake
  rules, not integrations.

## Proposed Changes to RFC-0003

### `job_id` becomes optional; `subject_type` and `subject_id` become required

| field | before | after |
| --- | --- | --- |
| `event_id` | required | required |
| `job_id` | required | **optional** |
| `subject_type` | — | **required** |
| `subject_id` | — | **required** |
| `event_type` | required | required |
| `timestamp` | required | required |
| `source` | required | required |
| `tenant_id` | — | **required** |
| `workspace_id` | — | optional |
| `correlation_id` | — | optional |

`subject_type` and `subject_id` name what the event is about — a job, an
execution, an artifact, or an external entity. Every event has a subject, so
nothing becomes unanswerable by dropping the `job_id` requirement.

**Events this repository emits must still carry `job_id`.** The field is optional
in the schema, not optional in our behaviour. An event produced by this
repository without a `job_id` is a defect, and its absence is only valid on
inbound external events. This keeps the outbound direction exactly as strict as
RFC-0003 intended while making the inbound direction possible.

### Tenant scope

`tenant_id` is required, per RFC-0006. `workspace_id` is optional, because
tenant-level events exist and have no workspace, per ADR-0007's Consequences.

An inbound event without a resolvable tenant is rejected at intake rather than
assigned one. Guessing tenant scope is the same error as synthesizing a `job_id`,
with a worse blast radius: it writes one tenant's activity into another's audit
trail.

### `correlation_id`

Added as optional. It links events, requests, and executions belonging to one
logical unit of work across systems — including across the job boundary, which
`job_id` cannot do. RFC-0003 anticipated this under Future Work.

### Event vocabulary unchanged

The seven canonical event types stand as RFC-0003 defined them, and the
`append-only` and `no silent state change` guarantees are unchanged.

Events arriving from outside use the same vocabulary where it applies. An
external event that matches no canonical type is recorded with its `source` and
`subject`, and inventing new canonical types is a semantic change requiring an
RFC per RFC-0005 Rule 2 — not something an intake path may do implicitly.

## Intake rules

1. An inbound event with no `job_id` is accepted if it has `subject_type`,
   `subject_id`, and a resolvable `tenant_id`.
2. A `job_id` is never synthesized, defaulted, or inferred. Absent means absent.
3. An event whose `tenant_id` cannot be resolved is rejected at intake and never
   written.
4. Inbound events are recorded with their originating `source` preserved. An
   external event must remain identifiable as external forever.
5. Intake is append-only like everything else. A malformed event is rejected
   before the log, never corrected inside it.

## What must never be written to the audit log

Private reasoning traces — an agent's chain of thought — are not audit records
and must not be stored as them. What is recorded is structured metadata: the
decision, the authority, the risk classification, the inputs, and the outcome.

This is adopted as a semantic guarantee of this repository's audit log rather
than inherited as a platform constraint, because it follows from the log's
purpose. An audit trail exists to establish accountability for what was decided
and done. Reasoning text is neither, and storing it creates an immutable,
indefinitely retained record of model-internal content that no one can later
redact.

## Observability depth

An event stream with no sub-steps is valid. External agent providers do not
expose their internal loop, so a trace may legitimately arrive at turn
granularity rather than step granularity.

An execution with no step events has not been shown to have done nothing — it has
been shown to be unobservable at that depth. The two must not be conflated, and
the audit log must not treat missing detail as evidence of inactivity.

## Architectural Impact

- **Control Plane** — none directly. Governance decisions still emit
  `GOVERNANCE_DECISION` events with a `job_id`.
- **Orchestration** — none.
- **Execution** — none.
- **Observability** — the event model gains a subject, tenant scope, and
  correlation; `job_id` becomes optional on intake only. The log can now hold
  events from systems outside this repository without distortion.

## Risk Assessment

| risk | severity | mitigation |
| --- | --- | --- |
| Optional `job_id` degrades into missing `job_id` on our own events | medium | Stated as a defect, not a choice: outbound events without `job_id` are invalid regardless of what the schema permits |
| `subject_type` becomes an unbounded free-text field | medium | Enumerated at the schema layer by `agent-platform`; a new subject type is an additive schema change under RFC-0005 Rule 1 |
| External events flood the log and drown job events | low | `source` is preserved on every event, so filtering by origin is always possible; retention is a later operational decision |
| Tenant resolution at intake becomes a guessing heuristic | high | Rule 3 forbids it outright — unresolvable means rejected, and rejection happens before the log so nothing unfixable is written |
| The chain-of-thought ban is read as "log nothing" | low | The alternative is stated explicitly: structured metadata covering decision, authority, risk, inputs, outcome |

## Migration Plan

1. Accept this RFC.
2. RFC-0003 gains an amendment pointer to this RFC. **Included in this change.**
3. `agent-platform` publishes `event/v1` with `job_id` optional and a required
   subject, per RFC-0005's authority split.
4. This repository pins `event/v1` in `platform-contract.yaml`.
5. When `packages/observability` gains code, intake rules 1–5 are implemented at
   the boundary, before anything is written.

No records exist to migrate.

## Open Questions
- Should `subject_type: job` with a `subject_id` make `job_id` redundant for our
  own events? Keeping both is deliberate for now — `job_id` states the causal
  link, `subject_id` states the topic, and they differ for an event about an
  execution belonging to a job. Collapsing them is a schema-level question for
  `agent-platform`.
- Retention policy for external events. Deferred; it is an operational decision
  and out of scope for v0.x per `CORE_BOUNDARY.md`.
