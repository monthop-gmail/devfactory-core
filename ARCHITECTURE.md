# devfactory-core Architecture

## Overview
devfactory-core is a governance-first control plane for autonomous software development.

## Core Planes
- Control Plane (Governance, State Machine)
- Orchestration Plane (Task Graph)
- Execution Plane (Agents)
- Observability Plane (Logs, Cost, Metrics)

## Job Lifecycle
DRAFT → GOVERNANCE_ANALYSIS → APPROVED / REJECTED →
TASK_PLANNING → IN_PROGRESS → VALIDATING →
DEPLOYABLE → COMPLETED / FAILED

IN_PROGRESS / VALIDATING / DEPLOYABLE ⇄ AWAITING_APPROVAL

Terminal: COMPLETED · FAILED · CANCELLED · TIMED_OUT

Full spec: [packages/core/state-machine.md](packages/core/state-machine.md) ·
[RFC-0001](rfcs/0001-job-state-machine.md) as amended by [RFC-0007](rfcs/0007-job-lifecycle-completeness.md)
and [RFC-0011](rfcs/0011-require-changes-destination.md)

## Governance Decisions
`APPROVE`, `REJECT` and `REQUIRE_CHANGES` move a job out of `GOVERNANCE_ANALYSIS`; each is
recorded as an immutable decision and emitted as `GOVERNANCE_DECISION` next to the
`STATE_TRANSITION` it caused, so no job reaches `APPROVED` without a record of who decided
it and why. `REQUIRE_CHANGES` sends a job back to `DRAFT`
([RFC-0011](rfcs/0011-require-changes-destination.md)) and stays distinguishable from a
rejection by its route: a `REJECT` passes through `REJECTED` and leaves it in the job's
history, a `REQUIRE_CHANGES` never enters that state. See
[RFC-0002](rfcs/0002-governance-decision-contract.md) and
`packages/core/devfactory_core/decision.py`.

An approval can also carry `expires_at`, and one that has passed it authorises nothing:
the engine refuses to move the job into execution, and a job left holding a lapsed
approval reaches `TIMED_OUT` rather than waiting indefinitely
([RFC-0007 Amendment 1](rfcs/0007-job-lifecycle-completeness.md#amendment-1--approved-may-time-out-2026-08-19)).

## Multi-Tenancy
Tenant → Workspace → Resource. `tenant_id` is required on every job, decision, and
event; isolation is enforced at the storage layer, not by query filter.
See [RFC-0006](rfcs/0006-tenant-workspace-model.md).

## Ecosystem Position
This repository consumes shared contracts from
[`agent-platform`](https://github.com/monthop-gmail/agent-platform) and retains
authority over the semantics of its own RFCs. Pinned contracts and conformance
status: [`platform-contract.yaml`](platform-contract.yaml) ·
semantics this repository owns: [`contract-semantics.yaml`](contract-semantics.yaml) ·
rationale: [RFC-0005](rfcs/0005-platform-contract-authority.md)
