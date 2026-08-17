# devfactory-core

> Governance-first infrastructure for autonomous software development.

## Vision
Build a self-hosted AI-native development platform where governance,
orchestration, execution, and observability are separated.

This is NOT a chatbot.
This is NOT a coding assistant.
This is AI Infrastructure.

---

## Core Architecture

Control Plane → Orchestration → Execution → Observability

---

## Architectural Principles

1. Governance before execution
2. Explicit state machine workflow
3. Separation of control and execution planes
4. Immutable audit trail
5. Cost isolation per provider
6. No agent has total authority

---

## Planes

### 🧭 Control Plane
- Governance engine
- Risk analysis
- Approval workflow
- State transitions

### 🧩 Orchestration Plane
- Task decomposition
- Agent assignment
- Retry policies

### ⚙️ Execution Plane
- Coding agents
- DevOps agents
- QA agents
- Docker sandbox

### 📊 Observability Plane
- Structured logs
- Cost tracking
- Agent performance metrics

---

## Direction Lock

This project is NOT:
- A prompt playground
- A SaaS MVP
- A chatbot framework

This project IS:
- AI Governance Infrastructure
- Dev Factory Core Engine
- Multi-provider orchestration platform

All contributions must align with this direction.

---

## Governance References

Before opening or reviewing a PR, please read:

- [Project North Star](docs/governance/README_NORTH_STAR.md) — why this project exists
- [Core Boundary](docs/governance/CORE_BOUNDARY.md) — what is and isn't allowed in v0.x
- [Milestone v0.1](docs/governance/MILESTONE_v0.1.md) — definition of done for the current milestone

These documents are the standard basis for accepting or rejecting contributions.

Path ownership is declared in [`.github/CODEOWNERS`](.github/CODEOWNERS) — `rfcs/`
and the governance documents are owned by the Architecture Owner, per `GOVERNANCE.md`.

---

## License

[MIT](LICENSE) — the same license as
[`agent-platform`](https://github.com/monthop-gmail/agent-platform), which derives
canonical ecosystem contracts from the RFCs in this repository.
