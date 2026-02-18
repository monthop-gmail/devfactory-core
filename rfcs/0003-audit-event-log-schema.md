# RFC-0003: Audit & Event Log Schema

## Status
Draft

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
- Correlation IDs
- Cost attribution
