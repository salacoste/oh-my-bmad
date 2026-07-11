# Project Overview

## What this is

**oh-my-bmad** is a self-hosted personal autonomous-development platform. Telegram and a local console drive supervised CLI workers through a typed event spine, backed by an append-only JSONL event log and a single-writer materialized state store. The platform is designed so runtimes, MCP tools, browser automation, transports, and deployment hardening can evolve without breaking the spine.

The current repository state is **Phase 50 complete / Phase 51 in progress** as of 2026-07-11: Phase 50 / Epic 133 DB mTLS readiness is complete locally, Story 134.1 is complete locally and merged via PR #124, Story 134.2 is complete locally and merged via PR #125, Story 134.3 is complete locally and merged via PR #126, Story 134.4 is complete locally and merged via PR #127, and Story 134.5 is complete locally inside Phase 51 / Epic 134 controlled production activation evidence planning. The latest tagged release remains `v1.3.0`; this checkout contains later BMad work through Phase 51 planning. No live activation is performed or claimed by Stories 134.1-134.5; no live rehearsal, live database cutover, remote Postgres activation, registry DB mTLS production activation, rollback/restore execution, destructive operation, migration execution, provisioning, production host mutation, credentials/certs, real certificate material, private key material, plaintext fallback, operator/deployment/rollback/restore/migration/activation/production script change, or production-state change is performed.

## Status

- **Current phase:** 51 planning in progress.
  Story 134.1 is complete locally and merged via PR #124 for controlled production activation evidence schema/preflight validation.
  Story 134.2 is complete locally and merged via PR #125 as future/operator-gated split-deployment activation smoke evidence planning.
  Story 134.3 is complete locally and merged via PR #126 as future/operator-gated remote Postgres activation smoke and migration evidence planning.
  Story 134.4 is complete locally and merged via PR #127 as future/operator-gated registry DB mTLS activation smoke/failure evidence planning.
  Story 134.5 is complete locally as future/operator-gated combined split deployment, remote Postgres, and DB mTLS rehearsal evidence planning.
  Story 134.6 go/no-go closure evidence stays backlog/future-operator-gated with no live activation and no live database cutover.
  These local packages are not proof activation occurred.
- **Recently complete:** Story 134.5 is complete locally as static docs/status/checker work for combined rehearsal evidence planning; no live activation, live rehearsal, rollback/restore execution, destructive operation, production host mutation, credentials/certs, migration execution, operator/deployment/rollback/restore/migration/activation/production script change, or production-state change is performed.
- **Repository type:** monorepo (`uv` workspace, 24 Python members).
- **Language:** Python 3.12 (locked).
- **Deployment:** Docker Compose v2 with named volume (`oh-my-bmad-data`); optional profiles remain operator-gated and inactive until future approved activation evidence exists.
- **Canonical state:** `_bmad-output/implementation-artifacts/sprint-status.yaml`.

## Shipped and planning phase map

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
| 18 | Destructive lifecycle apply product scope and next non-destructive candidate selection |
| 19 | Read-only dashboard shell and panel/read-only guardrails |
| 20 | Dashboard live-read contracts and aggregate/session unavailable decision |
| 21 | Dashboard rendering readiness and live-read wiring decision gate |
| 22 | Health/readiness runtime boundary for `GET /v1/health` |
| 23 | Task-detail runtime boundary for `GET /v1/tasks/{task_id}` |
| 24 | Event timeline/transitions runtime boundary |
| 25 | Trace correlation runtime boundary |
| 26 | History/replay runtime boundary |
| 27 | Lifecycle/snapshot listing and passive lifecycle-readiness display |
| 28 | Snapshot creation authorization runtime boundary |
| 29 | Aggregate/session/digest route-selection |
| 30 | Aggregate task-list route-selection/runtime boundary |
| 31 | Session-list runtime boundary |
| 32 | Session-detail runtime boundary |
| 33 | Digest-stream route-selection/runtime boundary |
| 34 | Task status filter route-selection/runtime boundary |
| 35 | Task-list limit route-selection/runtime boundary |
| 36 | Task status+limit route-selection/runtime boundary |
| 37 | Task status+limit browser consumption |
| 38 | Task-list pagination / next-window API boundary |
| 39 | Task-list pagination browser consumption |
| 40 | Manual task-list pagination navigation |
| 41 | Task status+limit+offset API-local composition |
| 42 | Task status+limit+offset browser consumption |
| 43 | Task-list sort API-local boundary |
| 44 | Task-list sort browser controls |
| 45 | API-local finite task-list sort vocabulary |
| 46 | Browser sort vocabulary, API sort composition, search/discovery planning, dashboard wiring guard |
| 47 | Browser full selector composition |
| 48 | Production-readiness portfolio: search/discovery, dashboard cleanup, lifecycle mutation, retention, production ops, split deployment, DB mTLS |
| 49 | Production-readiness execution/reconciliation toward split deployment and remote Postgres readiness |
| 50 | DB connection mTLS readiness local closure; production activation remains deferred/operator-gated |
| 51 | Controlled production activation evidence planning; Stories 134.1-134.5 static evidence contracts complete locally, Story 134.6 go/no-go closure evidence remains future/operator-gated/backlog |

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

A typed event spine connects operator surfaces to runtime workers and MCP tools. All durable task/session state derives from append-only event records. `registry-state` owns the materialized database projection and single-writer rules; `registry-api` exposes versioned HTTP read/write surfaces; MCP servers expose bounded tool/resource contracts to workers under capability tiers and approval gates. Replay packages reconstruct historical state from hot logs and validated archived segments referenced by `lifecycle-manifest.json`. Recent production-readiness work added bounded lifecycle mutation controls, object-storage retention readiness, production operations readiness, split-deployment/remote Postgres readiness, and Phase 50 DB mTLS readiness. Phase 51 is only controlled activation evidence planning: Story 134.1 supplies the local static schema/preflight gate, Story 134.2 supplies the local split-deployment activation smoke evidence package contract, Story 134.3 supplies future/operator-gated evidence planning only for remote Postgres smoke/migration, Story 134.4 supplies future/operator-gated evidence planning only for registry DB mTLS smoke/failure diagnostics, and Story 134.5 supplies future/operator-gated evidence planning only for combined split deployment, remote Postgres, and DB mTLS rehearsal; Story 134.6 go/no-go evidence remains backlog; no live activation, no live database cutover, and these packages remain planning-only evidence.

## Where to start

- **Operating it?** → [operator-runbook.md](./operator-runbook.md), [backup-restore.md](./backup-restore.md), and [deployment-guide.md](./deployment-guide.md).
- **Developing on it?** → `_bmad-output/project-context.md`, [development-guide.md](./development-guide.md), and [testing-guide.md](./testing-guide.md).
- **Understanding decisions?** → [adr/](./adr/), [architecture.md](./architecture.md), and `_bmad-output/planning-artifacts/`.
- **Working on replay/lifecycle?** → [data-models.md](./data-models.md) §“Historical replay and event-log lifecycle” plus ADR-0025.
- **Working on controlled activation planning?** → `_bmad-output/planning-artifacts/phase-51-prd-amendment.md`, `_bmad-output/planning-artifacts/phase-51-architecture-amendment.md`, `_bmad-output/planning-artifacts/phase-51-controlled-activation-epics.md`, Story 134.1 `docs/controlled-activation-evidence.json`, Story 134.2 `docs/split-deployment-activation-smoke-evidence.json`, Story 134.3 `docs/remote-postgres-activation-smoke-migration-evidence.json`, Story 134.4 `docs/registry-db-mtls-activation-smoke-failure-evidence.json`, and Story 134.5 `docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json`.

## License

MIT. See `LICENSE`.
