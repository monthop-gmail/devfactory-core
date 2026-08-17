# Contributing Guidelines

Thank you for contributing to AI Dev Factory.

## Design First Rule
Architecture changes require an RFC before implementation.

## Core vs Extension

Core:
- State machine
- Governance engine
- Provider proxy (internal, outbound — `packages/provider-proxy`; not the ecosystem `model-gateway`)
- Orchestration engine
- Control API (internal, inbound — `apps/control-api`)

Module direction and scope are set by [RFC-0005](rfcs/0005-platform-contract-authority.md).

Extension:
- New agents
- UI dashboard
- Additional providers

Core changes require maintainer approval.

## No Feature Creep
If it does not align with Dev Factory architecture, it will be rejected.

## Pull Request Requirements

- Clear problem statement
- Alignment explanation
- Impacted plane specified
- Risk analysis included
