# Component inventory

The 14 uv-workspace members. Sizes are approximate LOC of non-test Python source. "Scaffold" means `signal.pause()` + healthcheck-touch placeholder until the owning story replaces it (see [exceptions.md](./exceptions.md)).

## Services (`services/*`)

Deployable backend processes. Each ships a Dockerfile, declares a `[project] name` in its own `pyproject.toml`, and exposes one `__main__.py` entry point.

| Member | LOC | Status | Purpose |
|---|---|---|---|
| **clawhip-daemon** | ~2,600 | scaffold (Story 7.8 pending) | Supervises the vendored clawhip subprocess; will own outbound sink rendering (event log → operator surface text). |
| **console-cli** | ~1,400 | production | Local Typer CLI; full command-surface parity with Telegram (FR12). Published as a GHCR image but intentionally NOT in `docker compose up` — invoked ad-hoc on the host. |
| **orchestrator-adapter** | ~1,500 | scaffold (Story 5.10 pending) | OMC subprocess supervisor; translates platform-task events into OMC contract. Public surface: `OMCRunner`. |
| **registry-api** | ~2,600 | production | FastAPI HTTP application surface. Handles task creation, read paths (FR4/FR5/FR6), and operator decisions. Stateless container; delegates persistence to `registry-state`. Endpoints in [api-contracts.md](./api-contracts.md). |
| **registry-state** | ~4,200 | production | **Single writer** for the SQLite WAL store + JSONL event log. Owns the materializer, idempotency cache (FR28, 7-day TTL), snapshots (FR25), and recovery (NFR-R2). Public exports: `Task`, `SessionRow`, `Event`, `IdempotencyCache`, `Snapshot`, `EventLogWriter`, `Materializer`. |
| **telegram-gateway** | ~4,900 | production | aiogram v3 webhook + dispatcher. `AllowlistMiddleware` is the single auth gate (ADR-0001). Renders typed events into Telegram messages per [message-design.md](./message-design.md). |
| **worker-wrapper** | ~3,100 | production | Claude Code CLI subprocess supervisor (Story 2.12). Emits typed events via the MCP bridge; uses `atomic_write_bytes` / `atomic_write_text` for crash-safe artifact writes. The only workspace member permitted to import `anthropic`. |

## Packages (`packages/*`)

Shared libraries imported by multiple services and MCP servers. No deployment artifact; each is a `py.typed` package consumed via uv-workspace sources.

| Member | LOC | Public exports | Purpose |
|---|---|---|---|
| **capabilities** | ~160 | `Tier`, `CallerContext`, `CapabilityOk`, `CapabilityDenied`, `check_tier`, `check_tier_with_approval` | Capability-tier classification + enforcement helpers (FR37/FR38). Single source for tier semantics across all MCP surfaces. |
| **events** | ~2,000 | `EventEnvelope`, `Actor`, `FrozenClock` + clock variants, `new_uuid7`, `FROZEN_EPOCH`, 32+ `*Payload` types, `REGISTRY`, `register`, canonical JSON serializers | The shared event envelope, schema registry, and canonical serializer. *Every* event flows through this envelope. The schema registry is the single source of truth for `(event_type, schema_version)` pairs. |
| **idempotency** | ~550 | `IdempotencyCacheStore`, `CacheHit`, `IdempotencyConflict` | UUIDv7 idempotency-key + `TTLCache` + SQLite-backed durability (FR28). 7-day retention. |
| **secret-hygiene** | ~2,000 | `AuditedSecret`, `AuditedBaseSettings`, `audited_secret_field`, `flush_pending_emissions` | Three-layer secret enforcement: `secret-hygiene-precommit` scanner, structlog sanitizer processor (wired *before* the renderer), `secret.accessed` audit event emission. Also owns the `commit-msg` hook. |

## MCP servers (`mcp-servers/*`)

Stdio-only MCP servers. Each is a workspace member with the canonical three-name structure (directory `<x>` → project `<x>-mcp` → import root `<x>_mcp`). Subprocess-spawned by the orchestrator; **not** in `docker-compose.yml`.

| Member | LOC | Registered tools | Purpose |
|---|---|---|---|
| **clawhip-bridge** | ~425 | `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` | Append-only event-emission surface (Story 2.8). **Sole mutation path to the event log.** All other workspace code reads events; only this surface writes them. |
| **session-registry** | ~420 | `session_register`, `session_heartbeat`, `session_close` | Session lifecycle — registration, heartbeat, close. Read-only session resource queries are exposed as MCP resources. |
| **task-registry** | ~470 | `task_add_note`, `task_attach_artifact`, `task_emit_event` | Read-only task / approval-queue / blocker queries (via resources) plus bounded write tools for notes and artifacts. |

## Workspace topology (dependency direction)

```
mcp-servers/*         services/*          (operator surfaces)
       │                  │
       └────────┬─────────┘
                ▼
            packages/*
       (capabilities, events,
        idempotency, secret-hygiene)
                │
                ▼
            stdlib + pinned 3p (pydantic, structlog, httpx, …)
```

- `services/*` may import `packages/*`. Never another service.
- `mcp-servers/*` may import `packages/*` and the public API of at most one service (declared in `pyproject [tool.bmad.mcp-binding]`).
- `packages/*` may NOT import `services/*` or `mcp-servers/*`.
- `upstream/*` is reachable only via its adapter shim (`upstream/<name>/adapter.py`).

Enforced by `scripts/checks/check_imports.py` on PR. See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 2 + Cat 4 for the full ruleset.

## Scaffold-replacement map (excerpt)

For the full table, see [exceptions.md](./exceptions.md).

| Service | Replacement story |
|---|---|
| `registry-api` | Story 2.9 (HTTP API + `/v1/health`) ✓ |
| `registry-state` | Stories 2.3–2.4 (SQLite schema + WAL writer) ✓ |
| `telegram-gateway` | Story 3.1 (aiogram webhook receiver) ✓ |
| `worker-wrapper` | Story 5.1 (worker lifecycle management) ✓ |
| `orchestrator-adapter` | Story 5.10 (OMC subprocess supervision) — pending |
| `clawhip-daemon` | Story 7.8 (clawhip-bridge MCP integration) — pending |
| `console-cli` | Story 4.6 (host shim) ✓ |

A scaffold `__main__.py` is correct code until its replacement story lands. Don't add business logic on top of `signal.pause()`.
