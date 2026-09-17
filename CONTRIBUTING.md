# Contributing Guidelines

Thank you for contributing to AI Dev Factory.

## Design First Rule
Architecture changes require an RFC before implementation.

### When an RFC is accepted

**Merging it to `main` is the acceptance.** There is no separate ceremony, and its
`## Status` line says `Accepted` with the date and the pull request that landed it.

Written down because it was not, and the field had stopped meaning anything: every
RFC through 0014 still read `Draft — pending maintainer approval` while its
decisions were implemented, enforced in CI, and cited by another repository. A
status that says the same thing about every document cannot tell a reader which
decisions are live.

An RFC that is *not* settled does not belong on `main` — open it as a pull request
and leave it there, where "not merged yet" carries the meaning `Draft` was failing
to carry.

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
