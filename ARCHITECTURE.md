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
