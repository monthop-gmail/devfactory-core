# RFC-0002: Governance Decision Contract

## Status
Draft

**Authority scope set by [RFC-0005](0005-platform-contract-authority.md).** This
repository owns the semantics below — the decision vocabulary and the guarantees.
The canonical wire schema derived from it lives in `agent-platform` at
`contracts/approval/`, which may add platform-level fields (`tenant_id`,
`execution_id`, `policy_id`, `expires_at`, `action_risk`, …) without amending this
RFC. Changing what a decision *means* still requires an RFC here first.

Tenant scope on decisions is specified in [RFC-0006](0006-tenant-workspace-model.md).

**Completed by [RFC-0011](0011-require-changes-destination.md)** — this RFC names three
decision types and does not say where each sends a job. RFC-0011 settles the last one:
`REQUIRE_CHANGES` returns a job to `DRAFT`, by a route that never passes through
`REJECTED`. The vocabulary is unchanged.

## Context
Governance is the control plane authority in devfactory-core.
All execution must be gated by explicit governance decisions.

## Problem Statement
Without a clear decision contract:
- Approval logic becomes implicit
- Accountability is lost
- Automation becomes unsafe

## Goals
- Define a minimal governance decision interface
- Ensure governance-before-execution
- Support auditability

## Non-Goals
- Policy engine implementation
- Legal/compliance rule authoring

## Decision Types
- APPROVE
- REJECT
- REQUIRE_CHANGES

## Required Fields
- decision
- reason
- timestamp
- authority

## Guarantees
- Decisions are immutable
- Every APPROVE is auditable
- Execution without APPROVE is forbidden

## Alternatives Considered
- Agent self-approval (rejected)

## Future Work
- Pluggable policy engines
