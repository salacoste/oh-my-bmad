# Project Overview

## What this is

**oh-my-bmad** is a self-hosted personal autonomous-development platform. Telegram and a local console drive supervised CLI workers through a typed event spine, backed by an append-only JSONL event log and a single-writer materialized state store. The platform is designed so runtimes, MCP tools, browser automation, transports, and deployment hardening can evolve without breaking the spine.

The current repository state is **Phase 17 open** (2026-06-13): Destructive Lifecycle Apply Readiness, a planning/safety-contract continuation of Phase 12-17 replay/lifecycle work. The latest tagged release remains `v1.3.0`; this checkout contains later Phase 10–17 work.

## Status

- **Current phase:** 17 open — Destructive Lifecycle Apply Readiness (planning/readiness only; no destructive apply implementation).
- **Repository type:** monorepo (`uv` workspace, 24 Python members).
- **Language:** Python 3.12 (locked).
- **Deployment:** Docker Compose v2 with named volume (`oh-my-bmad-data`); optional profiles for fleet features.
- **Canonical state:** `_bmad-output/implementation-artifacts/sprint-status.yaml`.

## Shipped phase map

| Phase | Scope |
|---|---|
| 1 | Core platform — event spine, registry, Telegram, console, OMC/Claude worker |
| 2 | Observability/security — trace_id, metrics, HMAC approvals, budgets, Litestream, supply chain |
| 3 | MCP tooling fleet — git, github, verification, memory/wiki, artifact |
| 4 | Browser automation — Playwright MCP container plane |
| 5 | Multi-runtime — Claude Code, Codex, Gemini adapters and handoff |
| 6 | Server execution pool — Postgres option, task FSM, multi-worker assignment |
| 7 | Reliability — heartbeat detection, recovery loops, priority queue, operator tooling |
| 8 | Hardening/debt closure — API versioning, env scoping, zero open GATED deferred items |
| 9 | Operational excellence — PR draft creation, runbooks, stale TODO cleanup |
| 10 | Remote MCP transport — Streamable HTTP + bearer-token auth |
| 11 | mTLS — internal Docker-network TLS profile and CA tooling |
| 12 | Historical event replay — point-in-time replay, validation, snapshots, task history |
| 13 | Event log lifecycle — archive manifest, hot+archive replay, package streaming progress |
| 14 | Event log lifecycle operations — ADR-0025 operator gate, non-destructive dry-run boundary, hot-only task-history lock |
| 15 | Lifecycle documentation reconciliation and backlog triage |
| 16 | Archive-aware task history — read-only hot+archive task-history query |
| 17 | Destructive lifecycle apply readiness — plan-hash/operator-gate/replay/rollback contract only |

## Tech stack summary

| Category | Choice |
|---|---|
| Runtime | Python 3.12 + `uv` workspace; Node.js only inside CLI worker subprocesses |
| HTTP API | FastAPI on `registry-api` |
| Telegram | aiogram v3 on `telegram-gateway` |
| Storage | SQLite WAL default, optional Postgres registry backend, Alembic migrations |
| Event log | Append-only JSONL with canonical JSON and replay/archive validation |
| MCP | stdio by default; Streamable HTTP opt-in for remote MCP transport |
| Workers | Claude Code, Codex, Gemini runtime adapters behind `RuntimeAdapter` |
| Upstream forks | OMC + clawhip under `upstream/`, accessed only through adapter shims |
| Tooling | ruff, mypy `--strict`, pytest, pytest-asyncio strict, Hypothesis, mutation gate |

Exact dependency versions live in `uv.lock`; do not duplicate them in docs.

## Repository structure

```
oh-my-bmad/
├── services/              # 8 deployable/service packages
├── packages/              # 6 shared libraries
├── mcp-servers/           # 9 MCP servers
├── upstream/              # vendored forks (omc, clawhip), adapter-shimmed
├── tests/                 # cross-service test trees
├── scripts/               # CI gates, migrators, operational helpers
├── docs/                  # operator + AI-context documentation
├── _bmad-output/          # BMad planning, implementation, retrospectives, sprint state
├── _bmad/                 # BMad framework + skill configs
├── docker-compose.yml     # base stack
├── docker-compose.macos.yml
├── justfile               # operator recipes
├── pyproject.toml         # uv workspace root
└── uv.lock                # locked dependencies
```

The current member catalog is in [component-inventory.md](./component-inventory.md).

## Architecture in one paragraph

A typed event spine connects operator surfaces to runtime workers and MCP tools. All durable task/session state derives from append-only event records. `registry-state` owns the materialized database projection and the single-writer rules; `registry-api` exposes versioned HTTP read/write surfaces; MCP servers expose bounded tool/resource contracts to workers under capability tiers and approval gates. Replay packages can reconstruct historical state from hot logs and validated archived segments referenced by `lifecycle-manifest.json`. Phase 14 adds the operator-safe lifecycle operations boundary: ADR-0025 permits planning/validation and non-destructive dry-runs only. Phase 16 makes task history archive-aware only when archive manifest configuration is present. Phase 17 opens destructive lifecycle apply readiness as planning/readiness only: snapshots remain hot-log-only, apply remains unimplemented, and any future mutation must satisfy plan-hash, replay-validation, rollback-evidence, and operator-gate preconditions.

## Where to start

- **Operating it?** → [operator-runbook.md](./operator-runbook.md), [backup-restore.md](./backup-restore.md), and [deployment-guide.md](./deployment-guide.md).
- **Developing on it?** → `_bmad-output/project-context.md`, [development-guide.md](./development-guide.md), and [testing-guide.md](./testing-guide.md).
- **Understanding decisions?** → [adr/](./adr/), [architecture.md](./architecture.md), and `_bmad-output/planning-artifacts/`.
- **Working on replay/lifecycle?** → [data-models.md](./data-models.md) §“Historical replay and event-log lifecycle” plus Phase 12–17 planning artifacts and ADR-0025.

## License

MIT. See `LICENSE`.
