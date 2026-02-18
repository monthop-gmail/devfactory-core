# RFC-0001: Job State Machine

## Status
Draft

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
- Retry semantics for FAILED
- Parallel task substates

## Future Work
- Task-level state machine
- SLA / timeout policies
