# Component inventory

The current `uv` workspace has **24 Python members**: 8 services, 7 shared packages, and 9 MCP servers. Sizes are approximate and intentionally not used as gates; the authoritative dependency graph is `pyproject.toml` + each member's `pyproject.toml`.

## Services (`services/*`)

Deployable backend processes or operator binaries. Each declares a `[project] name` and an import root under `src/`.

| Member | Status | Purpose |
|---|---|---|
| **clawhip-daemon** | production | Supervises the vendored clawhip/event sink path and outbound rendering. |
| **console-cli** | production | Local Typer CLI with parity to Telegram/operator HTTP flows. |
| **metrics-subscriber** | production | Read-only event-log subscriber deriving bounded-cardinality metrics. |
| **orchestrator-adapter** | production | OMC/runtime orchestration adapter and task-driver integration. |
| **registry-api** | production | FastAPI HTTP application surface: tasks, decisions, replay, history, snapshots, health. |
| **registry-state** | production | Single-writer materializer for registry state, event application, migrations, snapshots, and recovery. |
| **telegram-gateway** | production | aiogram webhook/dispatcher, allowlist auth, Telegram command and rendering surface. |
| **worker-wrapper** | production | Runtime subprocess supervision, worktree locks, budget enforcement, MCP client group. |

## Packages (`packages/*`)

Shared libraries imported by services and MCP servers. Packages must not import services or MCP servers.

| Member | Public role |
|---|---|
| **capabilities** | Capability tier types and enforcement helpers. |
| **events** | Event envelope, canonical JSON, event payload/types helpers, schema/version primitives. |
| **idempotency** | UUIDv7 idempotency cache and persistence helpers. |
| **mcp_auth** | Bearer-token/JWT helpers for remote MCP transport (Phase 10). |
| **mtls** | TLS context and certificate helper package for internal mTLS (Phase 11). |
| **replay** | Historical replay, validation, snapshots, archive manifest, streaming progress (Phases 12–13). |
| **secret-hygiene** | Secret scanner, audited settings/secret wrappers, structlog redaction, audit events. |

## MCP servers (`mcp-servers/*`)

MCP servers expose tool/resource contracts to workers. Stdio remains the default transport; Phase 10 adds Streamable HTTP where explicitly configured. Every server follows the three-name convention: directory `<x>`, project `<x>-mcp`, import root `<x>_mcp`.

| Member | Purpose |
|---|---|
| **artifact** | Content-addressed artifact storage and retrieval. |
| **browser** | Playwright-backed browser automation plane. |
| **clawhip-bridge** | Append-only event-emission surface into the spine. |
| **git** | Sandboxed git read/write/push tools with capability tiers. |
| **github** | Scoped GitHub API tools with Tier-3 approval gates for writes. |
| **memory** | SQLite FTS5 memory/wiki store. |
| **session-registry** | Session lifecycle tools/resources. |
| **task-registry** | Task resources and bounded task-note/artifact/event tools. |
| **verification** | Sandboxed build/test recipe execution. |

## Workspace topology

```text
mcp-servers/*         services/*          operator surfaces
       │                  │
       └────────┬─────────┘
                ▼
            packages/*
                │
                ▼
            stdlib + pinned third-party deps
```

Rules enforced by `scripts/check_imports.py`:

- `services/*` may import `packages/*`, not other services.
- `packages/*` may not import `services/*` or `mcp-servers/*`.
- `mcp-servers/*` may import `packages/*` and only declared public APIs.
- `upstream/*` is reachable only via adapter shims.

## Phase-added components

| Phase | Component additions |
|---|---|
| 2 | `metrics-subscriber` service and observability/security packages/hooks. |
| 3 | `git`, `github`, `verification`, `memory`, `artifact` MCP servers. |
| 4 | `browser` MCP server. |
| 10 | `mcp_auth` package and Streamable HTTP transport support. |
| 11 | `mtls` package and `scripts/omb-ca/` tooling. |
| 12 | `replay` package and replay HTTP routes. |
| 13 | replay archive manifest + package streaming extensions. |

## Cross-references

- [architecture.md](./architecture.md) — runtime view and phase summaries.
- [api-contracts.md](./api-contracts.md) — HTTP and MCP contract surfaces.
- [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) — enforceable rules digest.
