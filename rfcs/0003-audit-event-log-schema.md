# RFC-0003: Audit & Event Log Schema

## Status
Draft

**Amended by [RFC-0008](0008-external-event-intake.md)** — `job_id` becomes
optional with a required subject, and `tenant_id` / `correlation_id` are added.
Read the two together; RFC-0008 supersedes this document where they differ.

**Authority scope set by [RFC-0005](0005-platform-contract-authority.md).** This
repository owns the event vocabulary and guarantees; the canonical wire schema
lives in `agent-platform` at `contracts/event/`.

## Context
Observability is required for trust and accountability.

## Problem Statement
Without a unified event schema:
- Actions cannot be reconstructed
- Debugging is unreliable
- Compliance is impossible

## Goals
- Define canonical event types
- Enable replayable job history
- Support cost and risk analysis

## Non-Goals
- Metrics backend selection
- Dashboard UI

## Core Event Types
- JOB_CREATED
- STATE_TRANSITION
- GOVERNANCE_DECISION
- TASK_ASSIGNED
- EXECUTION_STARTED
- EXECUTION_FAILED
- JOB_COMPLETED

## Required Fields
- event_id
- job_id
- event_type
- timestamp
- source

## Guarantees
- Events are append-only
- No silent state change

## Future Work
- ~~Correlation IDs~~ — added by [RFC-0008](0008-external-event-intake.md)
- Cost attribution — unblocked by the tenant scope in [RFC-0006](0006-tenant-workspace-model.md); the attribution model itself is still open
