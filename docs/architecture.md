# Architecture (operator-oriented overview)

This is the runtime / operator view of oh-my-bmad. For the original solution-design rationale (FR/NFR mapping, decision log, starter-template evaluation), read [`_bmad-output/planning-artifacts/architecture.md`](../_bmad-output/planning-artifacts/architecture.md). This file is the short version a new operator or contributor reads first.

## One-paragraph summary

A typed event spine connects three operator surfaces (Telegram bot, console CLI, future browser) to a Claude Code worker subprocess via an orchestrator adapter. All state lives in an append-only JSONL event log; `registry-state` is the **single writer** that materializes the log into SQLite for query, owns the UUIDv7 idempotency cache (FR28), and emits service-lifecycle events. Three MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) expose tool/resource contracts to the worker. Capability tiers gate every MCP tool call. Upstream forks (OMC, clawhip) integrate only via adapter shims under `upstream/`.

## Data-flow diagram (text)

```
                        ┌──────────────────────┐
                        │  Operator surfaces   │
                        ├──────────────────────┤
                        │  Telegram bot        │ ─┐
                        │  Console CLI         │  │  (Tier-tagged commands;
                        │  (Browser, Phase 4+) │  │   idempotency key = caller's UUIDv7)
                        └──────────────────────┘  │
                                                  ▼
                                       ┌──────────────────┐
                                       │  registry-api    │  FastAPI; POST /v1/tasks*,
                                       │  (HTTP surface)  │  GET /v1/tasks/*
                                       └──────────────────┘
                                                  │
                                                  │  emits typed *.requested events
                                                  ▼
                       ┌────────────────────────────────────────────┐
                       │   Append-only event log (JSONL on volume)  │
                       │   ─ envelopes immutable once emitted       │
                       │   ─ schema_version on every envelope        │
                       └────────────────────────────────────────────┘
                              │                                │
                              │ materialize (single writer)    │ subscribe (read-only)
                              ▼                                ▼
                ┌─────────────────────────┐         ┌─────────────────────────┐
                │     registry-state      │         │  Other subscribers:     │
                │  (single-writer SQLite  │         │  - telegram-gateway     │
                │   WAL + idempotency     │         │    (renders → operator) │
                │   cache + snapshots)    │         │  - console-cli          │
                └─────────────────────────┘         │  - clawhip-daemon       │
                                                    └─────────────────────────┘
                              ▲
                              │ HTTP read paths (FR4/FR5/FR6)
                              │
                       ┌──────────────────┐
                       │  registry-api    │
                       └──────────────────┘
                              │
                              │ tool calls (stdio; capability-tier-gated)
                              ▼
                ┌──────────────────────────────────────────────────┐
                │  MCP servers (stdio)                             │
                │  ─ task-registry        (read tasks + write)     │
                │  ─ session-registry     (session lifecycle)      │
                │  ─ clawhip-bridge       (event emission, sole    │
                │                          mutation path → log)    │
                └──────────────────────────────────────────────────┘
                              ▲
                              │
                       ┌────────────────────────┐
                       │  worker-wrapper        │
                       │  (Claude Code CLI      │
                       │   subprocess           │
                       │   supervisor)          │
                       └────────────────────────┘
                              ▲
                              │ task assignments, decisions
                              │
                       ┌────────────────────────┐
                       │  orchestrator-adapter  │  ← OMC fork, supervised
                       │  (subprocess shim)     │     via upstream/omc/adapter.py
                       └────────────────────────┘
```

Outbound rendering (event log → operator surface text) goes through `clawhip-daemon` once Story 7.8 lands; in Phase 1 the gateways render directly.

## Load-bearing invariants

These are non-bypassable. Most are enforced by CI gates (see [testing-guide.md](./testing-guide.md) and `scripts/checks/check_imports.py`); the rest are discipline rules captured in [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 7.

1. **Single-writer (FR26).** Only `registry-state` opens the DB for writes; only `EventLogWriter` opens the JSONL log for write.
2. **Service-to-service imports banned.** `services.<A>` never imports `services.<B>.*`. Communication is via the event spine or registry HTTP API.
3. **Event envelopes are immutable.** Once emitted, `event_id`, `schema_version`, `type`, `emitted_at`, `emitted_at_monotonic_ns`, `actor`, `payload`, `parent_event_id?` are never mutated.
4. **Additive-only schema within a major.** `DROP COLUMN`, `DROP TABLE`, `ALTER COLUMN (type change)`, `RENAME`, `ADD COLUMN NOT NULL` w/o `DEFAULT` are rejected by the migrator linter.
5. **MCP stdio-only in Phase 1.** Imports of `mcp.server.sse` / `mcp.server.streamable_http` are rejected.
6. **Upstream-fork boundary.** Vendored code accessed only through `upstream/<fork>/adapter.py`.
7. **No `anthropic` SDK in platform code.** Only `worker-wrapper` may import `anthropic`; everyone else routes via Claude Code worker through the event spine.
8. **Capability-tier enforcement at every MCP tool boundary.** Deny-path / default-deny / escalation tests are mandatory per boundary.
9. **Idempotency by UUIDv7.** Every command handler dedupes by the triggering event's UUIDv7 (7-day retention).

## Cross-cutting concerns

- **Event schema governance** — versioned, additive-only. New `(event_type, schema_version)` pairs register in `packages/events/src/events/`. Breaking changes ship via the one-shot Docker migrator (see [schema-evolution.md](./schema-evolution.md)).
- **Secret hygiene** — three-layer enforcement: pre-commit scanner, structlog sanitizer in the processor chain *before* the renderer, and `secret.accessed` audit events on every secret read. The `secret-hygiene` package owns all three.
- **Capability tiers** — applied identically at every MCP surface (`task-registry`, `session-registry`, `clawhip-bridge`). See `packages/capabilities` for the type contracts and [adr/0001-allowlist-middleware-auth.md](./adr/0001-allowlist-middleware-auth.md) for the authentication surface decision.
- **Idempotency** — UUIDv7 client-generated keys flow from bot/console through the application API to `registry-state`. 7-day dedup cache (FR28). See `packages/idempotency`.
- **Shutdown / recovery** — every long-running service handles SIGTERM cleanly: `registry-state` runs `PRAGMA wal_checkpoint(FULL)` + `await engine.dispose()`; workers release locks on SIGTERM; all services emit a terminal lifecycle event. Recovery replays the event log from the most recent snapshot.
- **Structured logs vs typed events** — separate streams with different persistence semantics. **Typed events on the spine are the primary observability stream**; structured logs are secondary. See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 2/3 for binding rules.
- **Upstream-fork pinning** — `VENDORED.md` carries the pinned commit SHA per fork. `just sync-upstream <name>` is the only sanctioned path. Contract tests under `tests/contract/fixtures/<adapter>/` gate semantic drift.
- **Metrics + distributed tracing — Phase 2 gap (explicit ban in Phase 1).** Do NOT add OpenTelemetry, Prometheus exporters, or trace instrumentation now. Placeholder spans are also banned — they create false coverage signals. The `trace_id` field is reserved on the envelope for the Phase 2 wiring story.

## What runs where

| Process | Workspace member | Role | Volume access | Stateful? |
|---|---|---|---|---|
| `registry-api` | `services/registry-api/` | HTTP application surface | RO (DB via state RPC) | No |
| `registry-state` | `services/registry-state/` | Materializer + writer + recovery | RW DB + RW event log | Yes — single writer |
| `telegram-gateway` | `services/telegram-gateway/` | Telegram ingress + outbound rendering | None (RO API) | No |
| `console-cli` | `services/console-cli/` | Local Typer CLI (not in Compose) | None | No |
| `worker-wrapper` | `services/worker-wrapper/` | Claude Code CLI subprocess supervisor | RW artifact tree | Per-task |
| `orchestrator-adapter` | `services/orchestrator-adapter/` | OMC subprocess supervisor (scaffold) | None | No |
| `clawhip-daemon` | `services/clawhip-daemon/` | clawhip supervisor + outbound sink rendering (scaffold) | None | No |
| `task-registry` MCP | `mcp-servers/task-registry/` | Read tasks + bounded writes | None (RPC) | No |
| `session-registry` MCP | `mcp-servers/session-registry/` | Session lifecycle | None (RPC) | No |
| `clawhip-bridge` MCP | `mcp-servers/clawhip-bridge/` | Event emission — **sole mutation surface** | None (event RPC) | No |

The Docker Compose stack runs 6 containers (registry-api, registry-state, telegram-gateway, worker-wrapper, orchestrator-adapter, clawhip-daemon). MCP servers are subprocess-spawned by the orchestrator — they do NOT appear in `docker-compose.yml`. `console-cli` is published as an image but is intentionally not in Compose (see [README](../README.md) and [exceptions.md](./exceptions.md)).

## Phase-2 hooks (deferred, do not pre-implement)

- Metrics collector (Prometheus-style gauges/counters) — clean insertion point exists in the structlog config + `/healthz` endpoints.
- Distributed tracing (OpenTelemetry spans across services) — `trace_id?` field already reserved on the envelope.
- Browser-automation plane — would add a 4th operator surface; integrates through the same event spine.
- Additional CLI agents (Codex, Gemini, GLM) — swappable behind the orchestrator-adapter shim contract.
- Remote-MCP transports (HTTP/SSE) — explicit Phase 2 decision, gated by ADR. Phase 1 transport is stdio-only.
- Digest-pinning + signed-image verification (cosign + SLSA + SBOM) — replaces tag-based versioning currently in `release.yml`.

See `_bmad-output/planning-artifacts/architecture.md` for the full decision rationale per item.

## Cross-references

- [project-overview.md](./project-overview.md) — top-level summary.
- [source-tree-analysis.md](./source-tree-analysis.md) — annotated directory layout.
- [component-inventory.md](./component-inventory.md) — per-workspace-member catalog.
- [api-contracts.md](./api-contracts.md) — HTTP routes + MCP tool catalog.
- [data-models.md](./data-models.md) — event types + DB schema.
- [operator-runbook.md](./operator-runbook.md) — paging conditions + recovery playbooks.
- [schema-evolution.md](./schema-evolution.md) — event-log migrator workflow.
- [exceptions.md](./exceptions.md) — naming/convention exceptions (incl. scaffold-replacement map).
