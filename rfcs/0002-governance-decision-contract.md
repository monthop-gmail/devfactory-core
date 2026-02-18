# RFC-0002: Governance Decision Contract

## Status
Draft

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
