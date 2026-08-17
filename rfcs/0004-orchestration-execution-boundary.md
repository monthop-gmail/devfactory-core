# RFC-0004: Orchestration vs Execution Boundary

## Status
Draft

**Authority confirmed by [RFC-0005](0005-platform-contract-authority.md).** This
RFC stays wholly under this repository's authority — it has no derived contract in
`agent-platform`, which may reference it but not redefine it.

## Context
Clear separation is required between orchestration logic
and execution logic to avoid unsafe autonomy.

## Problem Statement
When orchestration and execution are mixed:
- Agents gain excessive authority
- Failures propagate unpredictably
- Governance enforcement weakens

## Goals
- Define responsibilities of each plane
- Prevent authority escalation
- Enable independent evolution

## Orchestration Responsibilities
- Task decomposition
- Dependency resolution
- Retry logic
- Scheduling

## Execution Responsibilities
- Perform assigned tasks
- Report results and failures
- Operate in sandbox

## Forbidden
- Execution making governance decisions
- Orchestration modifying artifacts directly

## Future Work
- Execution isolation models
