# Project Overview

## What this is

**oh-my-bmad** is a self-hosted personal autonomous-development platform. Telegram and a local console drive a Claude Code worker through a typed event spine, backed by a single-writer SQLite WAL + append-only JSONL event log that survives restarts. The architecture is built to swap in additional CLI agents (Codex, Gemini, GLM) and a dedicated browser-automation plane later without changing the spine.

This is the Phase 1 implementation. Phase 1 ships across **10 epics / 88 stories** (all `done` as of 2026-05-15). The MVP Ship-Blocker Checklist at the bottom of `_bmad-output/planning-artifacts/epics.md` is the definitive "Phase 1 shipped" criterion.

## Status

- **Phase:** 1 (shipped baseline).
- **Repository type:** monorepo (uv workspace, 14 members).
- **Language:** Python 3.12 (locked).
- **Deployment:** Docker Compose v2 + named volume (`oh-my-bmad-data`).

See `_bmad-output/implementation-artifacts/sprint-status.yaml` for current state and `_bmad-output/planning-artifacts/epics.md` for the full backlog.

## Tech stack (summary)

| Category | Choice |
|---|---|
| Runtime | Python 3.12 + `uv` workspace; Node.js only inside the Claude Code worker subprocess |
| HTTP API | FastAPI on `registry-api` |
| Telegram | aiogram v3 on `telegram-gateway` |
| Storage | SQLite WAL + `aiosqlite` + Alembic on `registry-state` |
| Event log | Append-only JSONL on the host volume |
| MCP | stdio transport only (Phase 1) |
| Worker | Claude Code CLI subprocess, supervised by `worker-wrapper` |
| Upstream forks | OMC + clawhip, vendored under `upstream/` behind adapter shims |
| Logging | structlog (JSON) + secret-hygiene sanitizer in the processor chain |
| Tooling | ruff, mypy `--strict`, pytest + pytest-asyncio strict, hypothesis, pre-commit |

Exact versions live in `uv.lock`. Don't duplicate them in docs.

## Repository structure

```
oh-my-bmad/
├── services/              # 7 backend services (deployable processes)
├── packages/              # 4 shared libraries imported by services + MCP servers
├── mcp-servers/           # 3 MCP servers (stdio, tool-contract surfaces)
├── upstream/              # vendored forks (omc, clawhip), via `just sync-upstream`
├── tests/                 # cross-service test trees (separability, crash-injection, etc.)
├── scripts/               # CI gates, migrator, sync-upstream tooling
├── docs/                  # operator + AI-context documentation (this directory)
├── _bmad-output/          # planning artifacts (product brief, PRD, architecture, sprint state)
├── _bmad/                 # BMad framework + skill configs
├── docker-compose.yml     # base stack (Linux)
├── docker-compose.macos.yml  # macOS overlay (bind-mounts permitted)
├── justfile               # operator recipes (single source of truth)
├── pyproject.toml         # uv workspace root
├── uv.lock                # locked deps (regenerate via `uv lock`; never hand-edit)
├── .env.example           # documents every env var with default + comment
└── README.md              # human-facing quickstart
```

The 14 uv-workspace members are documented in [component-inventory.md](./component-inventory.md). The annotated source tree lives in [source-tree-analysis.md](./source-tree-analysis.md).

## Architecture in one paragraph

A typed event spine connects three operator surfaces (Telegram, console, future browser) to a Claude Code worker subprocess via an orchestrator adapter. All state lives in the event log; `registry-state` is the single writer that materializes the log into SQLite for query, owns idempotency dedup (UUIDv7 keys, 7-day retention), and emits service-lifecycle events. MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) expose tool/resource contracts to the worker. Capability tiers gate every MCP tool call. Upstream forks (OMC, clawhip) integrate only via adapter shims so they can be swapped without changing the spine.

For the full design rationale, read `_bmad-output/planning-artifacts/architecture.md`. For the operator-oriented summary, read [architecture.md](./architecture.md).

## Where to start

- **Operating it?** → [operator-runbook.md](./operator-runbook.md), [backup-restore.md](./backup-restore.md), and the deployment guides under [deployment/](./deployment/).
- **Developing on it?** → [development.md](./development.md) (tooling quirks) + [testing-guide.md](./testing-guide.md).
- **Implementing a new story?** → `_bmad-output/project-context.md` (the AI-agent rule digest).
- **Understanding *why* something is the way it is?** → [adr/](./adr/) + `docs/exceptions.md` + `_bmad-output/planning-artifacts/architecture.md`.
- **Mapping all the parts?** → [component-inventory.md](./component-inventory.md), [api-contracts.md](./api-contracts.md), [data-models.md](./data-models.md).

## License

MIT. See `LICENSE`.
