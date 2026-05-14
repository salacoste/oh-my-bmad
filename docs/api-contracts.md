# API contracts

The platform exposes three categories of contract: HTTP (registry-api), MCP tools (three stdio servers), and Telegram bot commands. This file is an index, not a generated OpenAPI/MCP-schema dump — for exact shapes consult the source, which is `--strict`-typed Pydantic.

## HTTP API — `registry-api`

Versioned at `/v1`. Versions are additive; `/v1` semantics are frozen once shipped. All handlers are async; request/response models are Pydantic v2 with `extra="forbid"`. Errors flow through a single registered exception handler that maps `<svc>Error` → `{error_id, error_code, message, trace_id}`.

| Path | Method | Handler | Purpose |
|---|---|---|---|
| `/v1/tasks` | POST | `post_tasks` | Create a task; emits `task.created`; returns **201** with the new task ID. Idempotency key threaded from the caller. |
| `/v1/tasks/{task_id}` | GET | `get_task_by_id` | Fetch reconstituted task state (FR4). |
| `/v1/tasks/{task_id}/decisions` | POST | `post_decision` | Operator decisions: approve / reject / stop / retry (Story 6.4). |
| `/v1/tasks/{task_id}/logs/digest` | GET | `get_logs_digest` | LLM-summarized event digest for a task (Story 7.3, FR5). |
| `/v1/tasks/{task_id}/events` | GET | `get_task_events` | Raw event stream with pagination (Story 7.5, FR6). |

Health endpoints (`/healthz`, `/readyz`, `/v1/health` — FR17) emit **no** log lines under normal operation. A pytest assertion captures `structlog` output during the call to assert silence.

Trace context is pulled from inbound headers:
- `X-Trace-Id` → bound as `trace_id`; if absent, a new UUIDv7 is minted and logged at WARNING.
- `X-Parent-Event-Id` → bound as `parent_event_id`; if absent, stays None (never fabricated).

Both are bound to the structlog context at the middleware layer **before** any handler runs.

## MCP tool catalog

All three servers use stdio transport (`mcp.server.stdio.stdio_server()`). Imports of `mcp.server.sse` / `mcp.server.streamable_http` are rejected by static analysis. Tool handlers are pure async functions with pydantic-validated input and pydantic-modelled output; capability-tier middleware runs at every boundary.

Errors raise `ToolError(...)` for structured client-visible errors; never `raise ValueError(...)` (untyped). Tool error responses are mapped through the internal-vs-external error boundary — stack traces, file paths, and module names never reach the calling LLM context.

### `clawhip-bridge` MCP

**Append-only event-emission surface — sole mutation path to the event log.** Every mutating tool emits exactly one typed event onto the spine with `parent_event_id` set; read-only tools are exempt.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `emit_event` | Generic typed-event emission (worker-owned events) | the typed event itself |
| `emit_blocker` | Worker reports a blocker on a task | `task.blocker_raised` |
| `emit_summary` | Worker emits a task summary | `task.summary_emitted` |
| `emit_approval_request` | Worker requests operator approval | `task.approval_requested` |
| `emit_completion` | Worker reports task completion | `task.completed` |

### `session-registry` MCP

Read-only **resources** for session queries; **tools** for bounded writes.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `session_register` | Begin a session for a task | `session.started` |
| `session_heartbeat` | Liveness ping | `session.heartbeat` (also detects timeout) |
| `session_close` | End a session | `session.finished` |

### `task-registry` MCP

Read-only **resources** for task / approval-queue / blockers queries; **tools** for bounded writes.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `task_add_note` | Attach a typed note to a task | typed event (note kind) |
| `task_attach_artifact` | Attach an artifact pointer | typed event (artifact kind) |
| `task_emit_event` | Worker-routed typed event for the task | the typed event itself |

### Capability-tier enforcement

Every tool boundary above has **three mandatory tests** (per [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 4):

1. **Deny path** — a caller below the granted tier receives a structured deny envelope.
2. **Default-deny** — a caller with no capability claim is rejected, not silently routed to tier 0.
3. **Escalation** — a caller claiming a higher tier than provisioned is rejected.

Each boundary's allowed and rejected request shapes are pinned as contract fixtures.

## Telegram bot commands — `telegram-gateway`

Inbound idempotency key = `f"tg:{update_id}"`, threaded into the command envelope. `AllowlistMiddleware` is the single auth gate (ADR-0001); unauthorized chat IDs are dropped with a single `secret.access_denied` audit event — never echoed back to the user.

Outbound messages are rendered through the template registry documented in [message-design.md](./message-design.md). Templates are validated against the Telegram character budget by tests; inlining Telegram-specific Markdown in handler code is banned.

For the complete command list and FSM/state details, read `services/telegram-gateway/src/telegram_gateway/handlers/`. The set is intentionally kept in code (not duplicated here) because parity with the console CLI is enforced by the integration tests, not by documentation.

## Console-CLI commands

`console-cli` mirrors the Telegram surface for full parity (FR12). For the catalog, run:

```sh
uv run python -m console_cli --help
```

The CLI is published as a GHCR image (`ghcr.io/<owner>/oh-my-bmad-console-cli`) but is NOT in `docker compose up` by design — it's invoked ad-hoc on the host. See [README](../README.md) §"Upgrading" and [exceptions.md](./exceptions.md).

## Cross-references

- [data-models.md](./data-models.md) — event types catalog + registry-state DB schema.
- [message-design.md](./message-design.md) — Telegram template catalog + character budgets.
- [schema-evolution.md](./schema-evolution.md) — how to add an event type + ship a migrator.
- [adr/0001-allowlist-middleware-auth.md](./adr/0001-allowlist-middleware-auth.md) — single-auth-gate decision.
- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 3 — FastAPI / aiogram / MCP framework rules and trace-context binding.
