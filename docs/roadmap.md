# Roadmap

## Phase 1: Core State Machine
- Define canonical states for intake, plan, execute, review, and merge.
- Document transition guards and approval requirements.
- Produce an RFC for error and retry semantics.

## Phase 2: Governance Engine
- Add policy model for risk classification and escalation.
- Define human-in-the-loop checkpoints and override paths.
- Specify immutable audit logging requirements.

## Phase 3: Orchestration Foundation
- Document task decomposition and agent assignment strategy.
- Define retry and timeout policies per task type.
- Specify provider isolation and cost-control constraints.

## Cross-cutting: Issue Operations
- Standardize issue triage with `docs/issue-triage.md`.
- Enforce structured issue forms and labels (`bug`, `docs`, `question`, `governance`, `architecture`).
- Use priority classes (P0/P1/P2).
- Ensure issues map to roadmap phases or direction-lock decisions.
