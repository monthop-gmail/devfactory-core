# Issue Triage Guide

This guide defines a lightweight process for handling incoming GitHub issues in `devfactory-core`.

## 1) Intake
- Confirm the issue is reproducible.
- Collect: expected behavior, actual behavior, reproduction steps, and environment.
- Tag with one of: `bug`, `docs`, `architecture`, `governance`, `question`.
- Prefer structured issue forms in `.github/ISSUE_TEMPLATE/*.yml` to normalize inputs.
- Ensure labels are from the supported set: `bug`, `docs`, `architecture`, `governance`, `question`.

### Intake from restricted environments
If GitHub API access is blocked (e.g., `403` from unauthenticated requests):
- Use the issue web URL and manually capture title/body/comments into triage notes.
- Request a token-backed mirror process in CI for `issues.json` export.
- Do not block triage; continue with reproducibility and alignment checks using available context.

## 2) Alignment Check
Before implementation, verify alignment with project direction:
- Governance-first infrastructure
- Control/Orchestration/Execution/Observability plane separation
- Explicit state-machine workflows

Reject or defer issues that push the project toward chatbot-only or SaaS-MVP scope.

## 3) Prioritization
Use this simple matrix:
- **P0**: breaks architecture direction or blocks core state machine work
- **P1**: major feature gap within existing roadmap
- **P2**: quality/docs/ergonomics

## 4) Resolution Checklist
- Add/update docs and architecture notes where behavior is defined.
- Reference related RFCs when introducing process or policy changes.
- Record follow-up tasks in `docs/roadmap.md`.

## 5) Close Criteria
Close an issue only when all are true:
- Reproduction no longer fails (or docs clarify intentional behavior)
- Scope is aligned with project direction lock
- Linked commit/PR clearly documents the change

## 6) Response targets
- **First response:** within 1 business day for P0/P1, 3 business days for P2.
- **Triage decision:** within 2 business days after first response.

## 7) Weekly issue operations cadence
- **Daily:** 15-minute intake sweep and label hygiene.
- **Twice weekly:** prioritization review for P0/P1 items.
- **Weekly:** roadmap mapping check (every open issue must map to a phase, or be explicitly deferred).
