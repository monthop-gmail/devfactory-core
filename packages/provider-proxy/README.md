# provider-proxy module

Outbound access to model and agent providers, **internal to devfactory-core**.

- **Direction:** outbound. This module calls providers; nothing calls it from outside this repository.
- **Scope:** internal only. It is not the ecosystem `model-gateway` and must never be offered to another repository as a shared service.
- **If `model-gateway` is built:** this module becomes a thin client of it — never a second implementation of it.

Named per [RFC-0005](../../rfcs/0005-platform-contract-authority.md) (was `packages/proxy`).
