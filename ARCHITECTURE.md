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

## Multi-Tenancy
Tenant → Workspace → Resource. `tenant_id` is required on every job, decision, and
event; isolation is enforced at the storage layer, not by query filter.
See [RFC-0006](rfcs/0006-tenant-workspace-model.md).

## Ecosystem Position
This repository consumes shared contracts from
[`agent-platform`](https://github.com/monthop-gmail/agent-platform) and retains
authority over the semantics of its own RFCs. Pinned contracts and conformance
status: [`platform-contract.yaml`](platform-contract.yaml) ·
rationale: [RFC-0005](rfcs/0005-platform-contract-authority.md)
