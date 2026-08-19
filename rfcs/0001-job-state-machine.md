# RFC-0001: Job State Machine

## Status
Draft

**Amended by [RFC-0007](0007-job-lifecycle-completeness.md)** — adds `CANCELLED`,
`TIMED_OUT`, and `AWAITING_APPROVAL`, and resolves both Open Questions below.
Read the two together; RFC-0007 supersedes this document where they differ.

**Further amended by [RFC-0010](0010-failable-states.md)** — enumerates which states may
reach `FAILED`, which neither this document nor RFC-0007 stated.

## Context
devfactory-core is a governance-first control plane.
A deterministic job lifecycle is required to ensure governance,
auditability, and predictable orchestration.

## Problem Statement
Without a shared and explicit job lifecycle:
- Governance decisions cannot be enforced consistently
- Observability becomes fragmented
- Agents cannot coordinate safely

## Goals
- Define a minimal, stable job lifecycle
- Enable governance-before-execution
- Support future orchestration and execution layers

## Non-Goals
- Execution implementation
- Provider-specific logic
- UI or dashboard concerns

## Proposed States

1. DRAFT  
2. GOVERNANCE_ANALYSIS  
3. APPROVED  
4. REJECTED  
5. TASK_PLANNING  
6. IN_PROGRESS  
7. VALIDATING  
8. DEPLOYABLE  
9. COMPLETED  
10. FAILED  

## State Transition Rules
- All jobs start at DRAFT
- Execution is forbidden before APPROVED
- FAILED is terminal
- REJECTED can only return to DRAFT

## Governance Guarantees
- Every transition is logged
- APPROVED requires explicit decision
- FAILED requires reason metadata

## Alternatives Considered
- Implicit lifecycle (rejected)
- Agent-controlled lifecycle (rejected)

## Open Questions
- ~~Retry semantics for FAILED~~ — resolved by [RFC-0007](0007-job-lifecycle-completeness.md):
  retry is execution-level only; job-level recovery is a new job with `supersedes_job_id`.
- ~~Parallel task substates~~ — resolved by [RFC-0007](0007-job-lifecycle-completeness.md):
  modelled as child executions in `execution/v1`, not as job substates.

## Future Work
- Task-level state machine — covered at the layer below by `contracts/execution/v1`
- SLA / timeout policies — the `TIMED_OUT` state exists per RFC-0007; the policy values do not yet
