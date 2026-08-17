# control-api

Inbound HTTP surface of the devfactory-core control plane — job intake, governance decisions, state queries.

- **Direction:** inbound.
- **Scope:** internal only. It is not the ecosystem `agent-gateway` and terminates no agent traffic on behalf of other repositories.

Named per [RFC-0005](../../rfcs/0005-platform-contract-authority.md) (was `apps/api-gateway`).
[ADR-0003](https://github.com/monthop-gmail/agent-platform/blob/main/decisions/0003-agent-gateway-boundary.md)
forbids the bare word "gateway", so the name drops it rather than qualifying it.
