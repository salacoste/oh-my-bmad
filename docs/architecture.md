# Architecture (operator-oriented overview)

This is the runtime / operator view of oh-my-bmad. For the original solution-design rationale (FR/NFR mapping, decision log, starter-template evaluation), read [`_bmad-output/planning-artifacts/architecture.md`](../_bmad-output/planning-artifacts/architecture.md). This file is the short version a new operator or contributor reads first.

## One-paragraph summary

A typed event spine connects three operator surfaces (Telegram bot, console CLI, future browser) to a Claude Code worker subprocess via an orchestrator adapter. All state lives in an append-only JSONL event log; `registry-state` is the **single writer** that materializes the log into SQLite for query, owns the UUIDv7 idempotency cache (FR28), and emits service-lifecycle events. Three always-on MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) expose tool/resource contracts to the worker. Six optional fleet MCP servers (`git`, `github`, `verification`, `memory`, `artifact`, `browser`) are conditionally spawned based on `WORKER_*_COMMAND` env vars. Capability tiers gate every MCP tool call. `trace_id` correlation (schema_version 1.1.0) and a derived metrics-subscriber projection ship in Phase 2. Upstream forks (OMC, clawhip) integrate only via adapter shims under `upstream/`.

## Data-flow diagram (text)

```
                        ┌──────────────────────┐
                        │  Operator surfaces   │
                        ├──────────────────────┤
                        │  Telegram bot        │ ─┐
                        │  Console CLI         │  │  (Tier-tagged commands;
                        │  (Browser — Phase 4) │  │   idempotency key = caller's UUIDv7)
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
                │  ─ git *                (bounded git ops)        │
                │  ─ github *             (GitHub API ops)         │
                │  ─ verification *       (validation tooling)     │
                │  ─ memory *             (knowledge store/FTS5)   │
                │  ─ artifact *           (content-addressed store)│
                │  ─ browser *            (Playwright automation)  │
                │     * = conditional; spawned only when           │
                │       WORKER_*_COMMAND env var is set            │
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

Outbound rendering (event log -> operator surface text) goes through `clawhip-daemon` once Story 7.8 lands; in Phase 1--2 the gateways render directly.

## Load-bearing invariants

These are non-bypassable. Most are enforced by CI gates (see [testing-guide.md](./testing-guide.md) and `scripts/checks/check_imports.py`); the rest are discipline rules captured in [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 7.

1. **Single-writer (FR26).** Only `registry-state` opens the DB for writes; only `EventLogWriter` opens the JSONL log for write.
2. **Service-to-service imports banned.** `services.<A>` never imports `services.<B>.*`. Communication is via the event spine or registry HTTP API.
3. **Event envelopes are immutable.** Once emitted, `event_id`, `schema_version`, `type`, `emitted_at`, `emitted_at_monotonic_ns`, `actor`, `payload`, `parent_event_id?` are never mutated.
4. **Additive-only schema within a major.** `DROP COLUMN`, `DROP TABLE`, `ALTER COLUMN (type change)`, `RENAME`, `ADD COLUMN NOT NULL` w/o `DEFAULT` are rejected by the migrator linter.
5. **MCP stdio-by-default, streamable-http opt-in (P2-I4, amended by ADR-0022).** SSE transport is permanently forbidden (`mcp.server.sse` imports rejected). Streamable HTTP is permitted when a server explicitly opts in via `MCP_TRANSPORT=streamable-http` (gated by `check_mcp_transport.py`). Stdio remains the default. Remote transport requires mandatory bearer-token auth (Invariant 15).
6. **Upstream-fork boundary.** Vendored code accessed only through `upstream/<fork>/adapter.py`.
7. **No `anthropic` SDK in platform code.** Only `worker-wrapper` may import `anthropic`; everyone else routes via Claude Code worker through the event spine.
8. **Capability-tier enforcement at every MCP tool boundary.** Deny-path / default-deny / escalation tests are mandatory per boundary.
9. **Idempotency by UUIDv7.** Every command handler dedupes by the triggering event's UUIDv7 (7-day retention).
10. **Additive-only API within a major (ADR-0021).** Within `/v1/`, no field removal, rename, or type change. Breaking changes require a new ADR + `/v2/` prefix.
11. **Per-server env isolation (G-SEC-2).** Each MCP server child receives only its own allowlisted env vars. No cross-server secret leakage.
12. **Runtime credential isolation (P5-I1).** Each runtime adapter's API key is injected into its own subprocess env only. `ANTHROPIC_API_KEY` absent from Codex/Gemini child envs; `OPENAI_API_KEY` absent from Claude/Gemini child envs; `GOOGLE_API_KEY` absent from Claude/Codex child envs.
13. **Event-driven state transitions (P6-I3).** All task state changes emit events; no direct DB mutations bypassing the event spine. Invalid transitions raise `InvalidStateTransition`.
14. **Browser session ephemerality (P4-I1).** Playwright subprocess runs with `--isolated`; no cookie/localStorage/sessionStorage state leaks between tasks.
15. **Remote MCP auth required (P10-I1, ADR-0022).** Any MCP server running on Streamable HTTP transport MUST validate bearer tokens. Unauthenticated Streamable HTTP is forbidden. Docker network isolation (no external ports) is the transport-layer control; bearer token is the application-layer control. Defense-in-depth.
16. **mTLS all-or-nothing within profile (P11-I1, ADR-0023).** When `MTLS_ENABLED=true`, ALL network-facing services MUST present valid client certificates. Partial TLS config is a startup error, never silent fallback.
17. **No committed cert/key material (P11-I2).** `.pem`, `.key`, `.crt`, `.p12` files are forbidden in the source tree. CI gate enforces. Certs generated at deploy time or test time only.
18. **Short-lived certificates only (P11-I3).** Maximum certificate validity: 72 hours. Rotation interval: 24 hours.

## Cross-cutting concerns

- **Event schema governance** — versioned, additive-only. New `(event_type, schema_version)` pairs register in `packages/events/src/events/`. Breaking changes ship via the one-shot Docker migrator (see [schema-evolution.md](./schema-evolution.md)).
- **Secret hygiene** — three-layer enforcement: pre-commit scanner, structlog sanitizer in the processor chain *before* the renderer, and `secret.accessed` audit events on every secret read. The `secret-hygiene` package owns all three.
- **Capability tiers** -- applied identically at every MCP surface (`task-registry`, `session-registry`, `clawhip-bridge`, plus the six fleet servers including `browser`). See `packages/capabilities` for the type contracts and [adr/0001-allowlist-middleware-auth.md](./adr/0001-allowlist-middleware-auth.md) for the authentication surface decision. The `check_tier_declarations.py` AST gate ensures every `@mcp.tool()` has a `TIER_MAP` entry at build time.
- **Idempotency** — UUIDv7 client-generated keys flow from bot/console through the application API to `registry-state`. 7-day dedup cache (FR28). See `packages/idempotency`.
- **Shutdown / recovery** — every long-running service handles SIGTERM cleanly: `registry-state` runs `PRAGMA wal_checkpoint(FULL)` + `await engine.dispose()`; workers release locks on SIGTERM; all services emit a terminal lifecycle event. Recovery replays the event log from the most recent snapshot.
- **Structured logs vs typed events** — separate streams with different persistence semantics. **Typed events on the spine are the primary observability stream**; structured logs are secondary. See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 2/3 for binding rules.
- **Upstream-fork pinning** — `VENDORED.md` carries the pinned commit SHA per fork. `just sync-upstream <name>` is the only sanctioned path. Contract tests under `tests/contract/fixtures/<adapter>/` gate semantic drift.
- **Metrics + distributed tracing — shipped in Phase 2.** `metrics-subscriber` (ADR-0005) derives Prometheus-style counters/gauges from the event log. `trace_id` propagation (ADR-0004) provides distributed correlation across services. Do NOT add OpenTelemetry spans or Prometheus exporters inside `services/*` -- instrumentation lives in `metrics-subscriber` only (P2-I2). The `trace_id` field on the envelope is mandatory since schema_version 1.1.0.

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
| `metrics-subscriber` | `services/metrics-subscriber/` | Derived metric projection from event log | RO event log (cursor file) | Own cursor file |
| **Fleet MCP servers** (conditional stdio -- spawned only when `WORKER_*_COMMAND` is set; 6 servers in Phase 4+) | | | | |
| `git` MCP | `mcp-servers/git/` | Bounded git operations on sandboxed worktree | RW worktree tree | No |
| `github` MCP | `mcp-servers/github/` | GitHub API operations (scoped credential) | None (RPC) | No |
| `verification` MCP | `mcp-servers/verification/` | Verification / validation tooling | None (RPC) | No |
| `memory` MCP | `mcp-servers/memory/` | Cross-task knowledge store (SQLite FTS5) | RW own SQLite DB | Own DB file |
| `artifact` MCP | `mcp-servers/artifact/` | Content-addressed build/run-output store | RW own FS subtree | Own FS store |
| `browser` MCP | `mcp-servers/browser/` | Browser automation via Playwright MCP (Tier-0--3) | None (subprocess RPC) | No |

The Docker Compose stack runs 7 containers (registry-api, registry-state, telegram-gateway, worker-wrapper, orchestrator-adapter, clawhip-daemon, metrics-subscriber). The three always-on MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) are subprocess-spawned by the worker-wrapper or orchestrator-adapter -- they do NOT appear in `docker-compose.yml`. The six fleet MCP servers are also stdio subprocesses but are **conditionally spawned**: the worker/orchestrator checks for `WORKER_GIT_COMMAND`, `WORKER_GITHUB_COMMAND`, `WORKER_BROWSER_COMMAND`, etc. in the child env and only starts the server when the var is present. Fleet servers are optional -- the worker and all always-on servers function correctly when any fleet server is absent (NFR-M8). `console-cli` is published as an image but is intentionally not in Compose (see [README](../README.md) and [exceptions.md](./exceptions.md)).

## Phase-2 features (shipped as v0.3.0)

Phase 2 shipped 2026-06-03 as **v0.3.0** (Epics 8--13 `done`; 6 ADRs accepted: ADR-0003 through ADR-0008). Key shipped capabilities:

- **`trace_id` propagation** (Epic 9 / ADR-0004) -- explicit, shape-validated `caller_trace_id` input on every MCP tool; `trace_id` field on `EventEnvelope` (schema_version bumped 1.0.0 -> 1.1.0); AST gate `check_trace_id_required.py` enforces the contract at build time.
- **`metrics-subscriber` derived projection** (Epic 10 / ADR-0005) -- service subscribes read-only to the event log, derives bounded-cardinality counters/gauges, owns a private cursor file on the named volume. Not a second JSONL writer.
- **HMAC approval signing + key rotation** (Epic 11 / ADR-0006) -- operator HMAC non-repudiation for Tier-3 approval flows; offline-verify test gate.
- **Per-task budget enforcement** (Epic 12) -- token budget ceilings with override cap (FR68); enforcement latency test gate.
- **Litestream WAL replication** (Epic 13 / ADR-0007) -- read-only sidecar replicates the registry DB WAL to S3/B2/R2 for disaster recovery (not HA). Replication target is the registry DB only; fleet-server own-stores (artifact, memory) are deliberately outside litestream scope.
- **Supply-chain triumvirate** (Epic 8 / ADR-0008) -- cosign keyless + SLSA L2 + CycloneDX SBOM; fail-closed license gate.

## Phase 3 -- MCP Tooling Fleet (shipped 2026-06-04, gate ADR-0009)

Phase 3 shipped 2026-06-04 as five optional stdio MCP fleet servers (Epics 14--19 `done`; FR72--FR77). Each epic shipped independently; Phase 3 released incrementally.

### ADR-0010 authoring recipe

Every fleet server follows an eight-step recipe ([ADR-0010](./adr/0010-mcp-server-authoring.md)), established by Epic 15 (`git`) and reused verbatim by Epics 16--19. The canonical story sequence per server:

1. **ATDD contracts** -- red-phase `xfail` test stubs per tool: one per tier, one denial path per Tier-3 tool, one default-deny.
2. **Server scaffold** -- `build_server` factory, `__main__.py`, `TIER_MAP` with placeholder entries.
3. **Tools + event emission** -- read tools (Tier-1) first, then write tools (Tier-2/3). Each handler calls `check_tier` or `check_tier_with_approval` before side effects. Tier-3 handlers wrapped by `emit_capability_denied_on_deny`.
4. **Event registration** -- two-location registration: spine JSONL (FR26 writer via clawhip-bridge) + service-local event type in `domain/event_types.py`. New events born at `schema_version 1.1.0`.
5. **Separability + supply chain** -- S-5...S-9 entry proving the member is optional; license gate + SBOM inherited from the base image (NFR-S12).

### Fleet server lifecycle

Fleet servers are **conditional stdio subprocesses**, not compose services (P3-I3). The worker-wrapper and orchestrator-adapter each maintain a byte-identical `_ENV_ALLOWLIST` frozenset. A fleet server is spawned only when its `WORKER_<NAME>_COMMAND` env var is present and non-blank in the child env. With the var absent, the worker and all always-on servers function correctly (NFR-M8). Ships in the base image via the existing `COPY mcp-servers/` + `uv sync --all-packages` -- no per-server Dockerfile, no compose entry, no `release.yml` matrix row.

### Tier enforcement

Every fleet tool declares its tier in a module-level `TIER_MAP: dict[str, Tier]`. The build-time AST gate `scripts/check_tier_declarations.py` (Epic 15 / Story 15.2a) asserts every `@mcp.tool()` in `mcp-servers/**/handlers/tools.py` has a `TIER_MAP` entry. Destructive tools are `Tier.THREE` and must ship a negative test proving `CapabilityDenied` without a matching `approval.granted` event. `caller_trace_id` is a required keyword-only input on every tool, validated by the byte-identical `validate_caller_trace_id` helper guarded by contract tests.

### Content-addressed stores (P3-I2)

Two fleet servers own isolated backing stores on the existing `oh-my-bmad-data` named volume -- single-writer by exactly that server process, never the registry DB:

- **artifact** (ADR-0011) -- content-addressed local-FS store under `<volume>/artifact-mcp/`. `put` writes to `objects/<hash[:2]>/<hash>` (sha256); `get`/`list` read from it. Metadata-only `artifact.stored`/`artifact.deleted` spine events route through the FR26 writer. Deliberately outside litestream scope (regenerable build output, not authoritative state).
- **memory** (ADR-0012) -- SQLite FTS5 store at `<volume>/memory-mcp/store.db`. `write` upserts + indexes; `search` runs FTS5 `MATCH`; `read` fetches by key. Metadata-only `memory.written` spine events for observability. Also outside litestream scope.

### Event emission pattern

Mutating fleet-server events use the **two-location** pattern: the spine event routes through the FR26 writer (clawhip-bridge `EventLogWriter.append`) for observability, while the event type is also registered in `domain/event_types.py` (additive, never mutating an existing version). All fleet-server events are born at `schema_version 1.1.0` (the version that introduced `trace_id`). Payloads are metadata-only -- never logs, secrets, or artifact content/bytes.

### Mutation testing gate (NFR-O11)

Epic 14 established the cosmic-ray mutation gate (`scripts/mutation_score.py`). The `just mutation-gate` recipe passes `--threshold 82` (the NFR-O11 floor). The gate targets the capability-tier kernel (`packages/capabilities`) and the event-envelope core -- a surviving mutant in `tiers.py` `check_tier` is a fleet-wide authz hole. Newer kernels may be tracked through a separate non-gating expanded baseline until they have a reviewed threshold; the nightly gating job enforces the original three-kernel score. The current expanded baseline, including `task_fsm.py` and `gemini_runner.py`, has an aggregate score of 252/387 = 65.1% and remains non-gating; future ratchets require per-module review rather than relying on the aggregate alone.

## Phase 4 -- Browser Automation Plane (shipped 2026-06-05)

Phase 4 shipped 2026-06-05 as the browser-automation plane (Epics 20--25 `done`; FR78--FR88). Key shipped capabilities:

- **`browser` MCP server** (Epic 20 / ADR-0013) -- stdio MCP fleet server wrapping Microsoft Playwright MCP (`@playwright/mcp`) as a Docker-subprocess transport. Dual tier enforcement: Playwright's `--caps` flag + oh-my-bmad's `TIER_MAP`. Container sandboxing (seccomp, user-namespace isolation); `--isolated` mode for ephemeral sessions (P4-I1).
- **Navigation, interaction, and tab-management tools** (Epics 21--22) -- Tier-1/2 browser tools with structured accessibility-tree output. `browser_evaluate` is Tier-3 with approval gating (P4-I2). Six browser event types (`browser.*`) on the spine.
- **Container sandboxing** (Epic 25 / ADR-0014) -- Playwright subprocess runs inside a Docker container, never bare-metal (P4-I3). Image pinned by digest; `--no-sandbox` never passed.

Phase 4 invariants (P4-I1 through P4-I3) documented in ADR-0013 and the Phase-4 architecture amendment.

## Phase 5 -- Multi-Runtime Adapters (shipped 2026-06-07)

Phase 5 shipped 2026-06-07 as the multi-runtime plane (Epics 26--29 `done`; FR89--FR98). Key shipped capabilities:

- **RuntimeAdapter protocol** (Epic 26 / ADR-0015) -- `typing.Protocol` defining the adapter interface (`spawn`, `is_healthy`, `parse_output`, `kill`). `ClaudeCodeRunner` satisfies the protocol via structural subtyping with zero behavioral change. Factory function `get_runtime_adapter()` dispatches by runtime name.
- **Codex adapter** (Epic 26) -- `CodexRunner` spawns `codex exec --json`, parses JSONL, maps tool names to `ExtractedEvent` types. Credential isolation: `OPENAI_API_KEY` appears only in Codex's allowlist; `ANTHROPIC_API_KEY` appears only in Claude Code's (P5-I1).
- **Per-task runtime selection + handoff** (Epics 27--28) -- `TaskCreatedPayload.preferred_runtime` selects the runner per-task. Runtime handoff preserves `trace_id` continuity (P5-I2). Per-runtime budget accounting (P5-I3).
- **Fleet smoke test** (Epic 29) -- end-to-end integration test exercising Codex + git-mcp + verification-mcp + event spine.

Phase 5 invariants (P5-I1 through P5-I3) documented in ADR-0015/ADR-0016.

## Phase 6 -- Server Execution Pool (shipped 2026-06-07)

Phase 6 shipped 2026-06-07 as the server-execution-pool plane (Epics 30--34 `done`; FR99--FR107). Key shipped capabilities:

- **Postgres migration** (Epic 30 / ADR-0017) -- dual-backend registry: SQLite default (zero-change), Postgres opt-in via `REGISTRY_DATABASE_URL`. Alembic migrations run on both backends. CI matrix runs full suite against both. Connection pooling: `5 + 2 * num_workers` for Postgres.
- **Task state machine** (Epic 31 / ADR-0018) -- formal FSM replaces implicit status tracking. States: `CREATED -> QUEUED -> ASSIGNED -> RUNNING -> COMPLETED|FAILED|CANCELLED`. Invalid transitions raise `InvalidStateTransition`. Event-driven: transitions triggered by `task.*` events on the spine. Resolves GATED-ARCH D4 (deferred since Phase 1).
- **Worker pool assignment** (Epic 32 / ADR-0019) -- pull-based task claiming: workers poll for `QUEUED` tasks and atomically claim them (`SELECT FOR UPDATE SKIP LOCKED` on Postgres, `BEGIN EXCLUSIVE` on SQLite). `worker_id = <hostname>-<pid>`. Scaling: `docker compose up --scale worker-wrapper=N`.
- **Gemini adapter** (Epic 33) -- third runtime following ADR-0010 step-9 recipe. `GOOGLE_API_KEY` isolated to Gemini's allowlist (P6-I5). FC-P6-2: structured output schema enforcement for Gemini runner.

Phase 6 invariants (P6-I1 through P6-I5) documented in ADR-0017 through ADR-0020.

## Phase 7 -- Reliability & Operator Tooling (shipped 2026-06-08)

Phase 7 shipped 2026-06-08 as the reliability and operator-tooling plane (Epics 35--40 `done`; 24 stories). Key shipped capabilities:

- **Recovery loops** (Epic 38) -- `RecoveryExecutor` + `RecoveryPolicy` drive automatic retry/auto-stop for failed tasks. `task.auto_retry` and `task.auto_stop` events on the spine. Retry count persisted in Task schema.
- **Dead-session detection + stale-task alerting** (Epics 36--37) -- per-worker heartbeat, `StaleTaskDetector` emitting `task.stale_warning` / `task.stale_critical` events. Metrics subscriber registers stale events.
- **Task priority queue** (Epic 39 / FC-P6-3) -- `Task.priority` column (default 0 = normal). Workers claim highest-priority `QUEUED` tasks first.
- **Audit trail completion** (Epic 35) -- automated audit trail verification; all capability-denied and approval events tracked.

## Phase 8 -- Platform Hardening & Debt Resolution (shipped 2026-06-08)

Phase 8 shipped 2026-06-08 as the closure phase (Epics 41--45 `done`; FR108--FR111). **Zero open GATED items** in `deferred-work.md`. Key shipped capabilities:

- **API versioning** (Epic 41 / ADR-0021) -- additive-only within `/v1/`; breaking changes require a new ADR + `/v2/` prefix. `response_model` opt-in for existing endpoints where schema matches wire contract.
- **Per-server env scoping** (Epic 43 / G-SEC-2) -- each MCP server child receives only its own allowlisted env vars; no cross-server secret leakage. Defense-in-depth on top of the existing `CHILD_ENV_ALLOWLIST` discipline.
- **Events composite index** (Epic 41) -- Alembic migration adds `ix_events_task_id_mono_ns` on `(task_id, emitted_at_monotonic_ns)` for faster event-log queries.
- **Deferred-work backlog closure** (Epic 45) -- all 20 GATED items resolved (12 GATED-ARCH, 6 GATED-OPS, 1 GATED-P0, plus WONTDO documentation). State-machine GATED items closed with Phase 6/7 evidence citations.

## Phase 9 -- Operational Excellence & Feature Completion (shipped 2026-06-09)

Phase 9 shipped 2026-06-09 as the final operational-excellence phase (Stories 46--48). Key shipped capabilities:

- **PR draft creation** (FR10 / Story 46.1) -- worker can create GitHub pull-request drafts via the `github` MCP server. `IdempotencyCacheStore` + `diff_summary` wired into the approval flow.
- **Operator runbook updates** (Story 48.1) -- five missing operational playbooks added covering recovery, priority queue, per-server env scoping, Postgres backend, and API versioning.
- **Dead code + stale TODO resolution** (Stories 46.2, 46.3) -- resolved stale production TODOs (health endpoint verification, dead-code documentation), closed Phase-2-era scaffold TODOs. Three remaining stale TODOs closed across the codebase.

## Phase 10: Remote MCP Transport (Streamable HTTP)

Phase 10 opened 2026-06-09 (ADR-0022 accepted). Scope: Streamable HTTP transport for MCP servers with JWT bearer token auth, unlocking split deployment and remote workers. Six epics (50-55).

New FRs: FR122 (Streamable HTTP transport mode), FR123 (Bearer token auth), FR124 (Client-side dual transport), FR125 (CI gate update), FR126 (Extended separability tests). New NFRs: NFR-S13 (no external ports), NFR-S14 (token validation <5ms), NFR-M10 (zero-change backward compatibility), NFR-O19 (transport mode observable), NFR-R15 (transport fallback).

Invariants amended: Invariant 5 (stdio-by-default, streamable-http opt-in), Invariant 15 added (remote auth required).

## Phase 11: mTLS for Internal Docker Network

Phase 11 shipped 2026-06-09 (ADR-0023 accepted). Scope: Transport-layer mutual authentication for all internal Docker-network service-to-service communication. Four epics (56-59), 10 stories.

New FRs: FR127 (mTLS context factory), FR128 (omb-ca CLI tool), FR129 (server TLS for HTTP services), FR130 (server TLS for MCP services), FR131 (client TLS), FR132 (CI gates), FR133 (compose profile). New NFRs: NFR-S15 (profile-gated, default off), NFR-S16 (handshake <10ms), NFR-M11 (zero-change compat), NFR-R16 (clear failure), NFR-O20 (TLS observable).

New invariants: P11-I1 (all-or-nothing within profile), P11-I2 (no committed cert material), P11-I3 (short-lived certs, 72h max).

New packages: `packages/mtls/` (TLS context factory). New tools: `scripts/omb-ca/` (CA init/issue/rotate/check). New CI gates: `check_no_secrets.py` (P11-I2), extended `check_mcp_transport.py` (MTLS001). 78 new tests. 16 services configured for mTLS.

## Phase 12: Historical Event Replay

Phase 12 shipped 2026-06-10 (ADR-0024 accepted). Scope: read-only point-in-time reconstruction from the authoritative JSONL event log for auditing/debugging.

New FRs: FR134 (replay engine), FR135 (replay HTTP endpoint), FR136 (task history endpoint), FR137 (replay validation), FR138 (snapshot management). New NFRs: NFR-O21 (observable replay), NFR-M12 (additive compatibility), NFR-R17 (bounded replay failure behavior), NFR-S17 (audit logging).

New package: `packages/replay/` with `replay_events()`, validation helpers, and snapshot helpers. New registry-api endpoints: `GET /v1/events/replay`, `GET /v1/tasks/{task_id}/history`, `GET /v1/events/replay/validate`, and `POST/GET /v1/events/replay/snapshots`.

## Phase 13: Event Log Lifecycle Management

Phase 13 shipped 2026-06-10 as Event Log Lifecycle Management (P13-ELLM). Scope: make Phase 12 replay safe as logs grow without introducing destructive pruning.

New FRs: FR139 (archive manifest inclusion), FR140 (archive manifest env resolution), FR141 (`HOT_ONLY_REPLAY` for snapshots), FR142 (route-local archive ProblemDetails), FR143 (package-only streaming replay progress).

Key lifecycle decisions:

- **Archive manifest, not hot deletion.** Archived segments are referenced by `lifecycle-manifest.json`; Phase 13 does not delete or prune hot logs.
- **Validated hot+archive replay.** Archive segments are checksum-validated and rejected on missing files, malformed manifests, duplicate keys, or sequence overlap.
- **Explicit archive config precedence.** Direct `archive_manifest_path` wins; otherwise `REPLAY_ARCHIVE_MANIFEST` is preferred, with `EVENT_LOG_ARCHIVE_MANIFEST` kept as a legacy alias. Conflicting env vars fail closed.
- **Hot-only surfaces stayed hot-only in Phase 13.** Snapshot creation uses the `HOT_ONLY_REPLAY` sentinel. Task history stayed hot-log-only until the separate Phase 16 archive-aware history contract.
- **No public streaming endpoint yet.** `replay_events_stream()` is package-only and yields frozen `ReplayProgress` updates plus a terminal `ReplayResult` equivalent to `replay_events()`.
- **Error mapping is route-local.** Replay/archive failures on replay and validate endpoints return route-local ProblemDetails; global `/errors/internal` behavior is unchanged.


## Phase 14: Event Log Lifecycle Operations

Phase 14 shipped 2026-06-11 as Event Log Lifecycle Operations (P14-ELLO). Scope: define the operator-safe lifecycle boundary after Phase 13 archive-aware replay, without authorizing destructive mutation.

New FRs: FR144 (sprint-status hygiene), FR145 (ADR/operator gate), FR146 (non-destructive lifecycle dry-run), FR147 (archived task-history boundary), FR148 (operator docs safe sequence).

Key lifecycle-operation decisions:

- **ADR-0025 gates destructive apply.** Planning, validation, and non-destructive dry-run behavior are authorized; deletion/truncation/archive mutation requires a future story and operator gate.
- **Plan identity must be stable.** Future apply authorization must bind to the exact dry-run plan hash and re-compute that hash immediately before mutation.
- **Task history stayed hot-log-only in Phase 14.** Archive-aware task history changes an operator-facing query contract, so it was split into the separate Phase 16 contract and tests.
- **Object storage and scheduled retention stay future.** Automatic lifecycle jobs wait until dry-run/apply safety is proven.

## Phase 15: Lifecycle Documentation Reconciliation and Backlog Triage

Phase 15 shipped 2026-06-12 as a docs/status-only reconciliation slice. Scope: update deeper API, operator, data-model, and architecture docs so they consistently reflect Phase 14 boundaries, and surface future lifecycle candidates without reopening implementation scope.

No runtime, API, service, package, MCP, dependency, or deployment behavior changed.

## Phase 16: Archive-Aware Task History

Phase 16 shipped 2026-06-12 as Archive-Aware Task History (P16-AATH), activating the first Phase 15 future candidate as a small read-only query extension. `GET /v1/tasks/{task_id}/history` now preserves its hot-log-only default when no archive manifest is configured, and can include validated archive segments when `REPLAY_ARCHIVE_MANIFEST` or the legacy alias is present. Invalid archive configuration fails closed with the same route-local ProblemDetails family used by replay/validate.

New FRs: FR152 (archive-aware task history), FR153 (hot-log default compatibility), FR154 (fail-closed archive errors), FR155 (read-only guarantee).

Key decisions:

- **Existing archive validation is reused.** Task history delegates to the Phase 13 `collect_replay_envelopes` hot+archive merge path instead of introducing a second parser.
- **No lifecycle mutation is authorized.** Destructive apply/delete/truncate/move/rewrite/chmod, object-storage lifecycle jobs, and scheduled retention remain future work gated by ADR-0025.
- **Snapshots stay hot-only.** `HOT_ONLY_REPLAY` remains the snapshot boundary.

## Phase 17: Destructive Lifecycle Apply Readiness

Phase 17 shipped 2026-06-13 as Destructive Lifecycle Apply Readiness (P17-DLAR). It is a planning/readiness phase, not an apply implementation. The phase formalizes the future destructive-apply safety contract from ADR-0025 and Phase 15 carry-forward notes.

New FRs: FR156 (readiness scope), FR157 (exact dry-run plan-hash binding), FR158 (replay validation precondition), FR159 (rollback/restore evidence), FR160 (distinct apply surface), FR161 (no destructive behavior in Phase 17).

Key decisions:

- **Apply remains unimplemented.** Phase 17 does not add delete/truncate/move/rewrite/chmod/prune/apply behavior, archive mutation, object-storage lifecycle jobs, scheduled retention workers, or credentialed production operations.
- **Future apply binds to exact plan identity.** A later apply design must authorize the exact `LifecycleDryRunPlan.plan_hash` and re-compute it immediately before mutation.
- **Replay and rollback proof are mandatory.** Archive manifest validation, replay validation against the retained hot+archive set, and backup/restore evidence are future preconditions before any mutation.
- **Dry-run and apply stay separate.** A future apply command/API must be distinct from dry-run; no `dry_run=false` toggle can authorize mutation.

## Future work beyond Phase 17

The following items remain unshipped or intentionally out of scope after Phase 17:

- **Event-log prune/apply implementation** -- destructive lifecycle operations still require a later implementation phase after the Phase 17 readiness contract, exact dry-run plan-hash authorization, replay validation, rollback evidence, and explicit operator gate.
- **Object-storage lifecycle jobs** -- archive manifests currently reference validated local/archive paths; automatic S3/B2/R2 lifecycle management is future work.
- **Scheduled jobs** -- time-based task scheduling and lifecycle automation.
- **Web dashboard** -- browser-based operator surface.
- **GLM adapter** -- fourth runtime following the ADR-0015 pattern.
- **Split deployment** -- Postgres accessible from multiple hosts for horizontal scaling of the registry layer.
- **Postgres connection mTLS** -- extends mTLS to database connections.

See `_bmad-output/planning-artifacts/architecture.md` and the phase-specific amendments for full decision rationale.

## Cross-references

- [project-overview.md](./project-overview.md) — top-level summary.
- [source-tree-analysis.md](./source-tree-analysis.md) — annotated directory layout.
- [component-inventory.md](./component-inventory.md) — per-workspace-member catalog.
- [api-contracts.md](./api-contracts.md) — HTTP routes + MCP tool catalog.
- [data-models.md](./data-models.md) — event types + DB schema.
- [operator-runbook.md](./operator-runbook.md) — paging conditions + recovery playbooks.
- [schema-evolution.md](./schema-evolution.md) — event-log migrator workflow.
- [exceptions.md](./exceptions.md) — naming/convention exceptions (incl. scaffold-replacement map).
