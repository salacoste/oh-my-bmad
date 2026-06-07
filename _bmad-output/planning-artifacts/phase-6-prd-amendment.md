# Phase 6 Scope Extension — Server Execution Pool

> **Status:** Phase-6 PRD amendment. Formalizes the server execution pool decision from the Phase 5 brainstorming convergence. FR/NFR numbering continues the canonical series (FR99 → FR106; NFR-O14 → NFR-O16; NFR-M11 → M12; NFR-S15 → S16; NFR-R11 → R12). Epic numbering continues from Phase 5 (Epic 30 = Phase 6 start).
>
> **Selected via:** Phase-5 retrospective readiness assessment + brainstorming convergence (D1–D5). Core decision: migrate to **Postgres** as the primary database backend (SQLite fallback retained for local dev), formalize the **task state machine** (resolves GATED-ARCH D4), and introduce **multi-task parallelism** via a Docker Compose worker pool. Remote MCP, mTLS, split deployment, GLM adapter, and web dashboard are deferred (Phase 7+).

**Theme:** the **server execution pool** — Postgres-backed persistence, formal task lifecycle, and concurrent execution. The operator can run multiple tasks simultaneously on isolated worker instances, each backed by a production-grade database. Built on the Phase-1–5 spine (event-only telemetry, `trace_id`, supply-chain pipeline, tier-enforced authz, multi-runtime adapters) with zero changes to the existing event spine or MCP fleet.

**Resolved scope (operator convergence, D1–D5):**

- **D1 (IN).** Postgres migration — SQLAlchemy ORM already in place; add Alembic migration + Postgres connection string. SQLite remains the default for local development. Both backends must pass the full test suite.
- **D2 (IN).** Task state machine — formalize the implicit FSM (CREATED → QUEUED → ASSIGNED → RUNNING → COMPLETED/FAILED/CANCELLED). Transitions are event-driven. Resolves GATED-ARCH D4 from Phase 1.
- **D3 (IN).** Multi-task parallelism — multiple worker-wrapper containers via Docker Compose `scale`. Orchestrator-adapter assigns tasks to available workers via the task registry. Per-task worktree isolation.
- **D4 (IN).** Gemini adapter — following ADR-0010 step-9 + ADR-0015 recipe (3rd runtime). Mirrors Codex adapter pattern.
- **D5 (OUT, deferred).** Remote MCP transport, mTLS, split deployment, GLM adapter, remote browsers, Codex session resume, web dashboard, scheduled jobs, recovery loops. All deferred to Phase 7+.

**Preserved invariants (carry from Phases 1–5 — non-negotiable):**

- **Single-writer (FR26) unchanged.** Postgres provides stronger transactional guarantees than SQLite WAL, but the application-layer single-writer discipline is preserved. Only one service writes to each table.
- **MCP transport remains stdio-only.** No HTTP/SSE/streamable transport. Remote-MCP stays deferred (Phase 7 D2).
- **Event-only telemetry (NFR-O1/O10) unchanged.** No per-worker instrumentation paths added to any service.
- **`trace_id` propagation (NFR-O7) unchanged.** Every event carries `trace_id`. Spans across parallel tasks and runtime adapters.
- **Tier-enforced authz (Epic 6) unchanged.** Approval gates are task-level, not worker-level. All workers enforce the same tier policy.
- **Supply-chain (Epic 8 + G-SEC-1/2) unchanged.** Postgres is a deployment dependency (Docker image), not a Python package change. SQLAlchemy and asyncpg/psycopg are already in the dependency tree.
- **Runtime adapter protocol (ADR-0015) unchanged.** The factory pattern extends to include Gemini. SUPPORTED_RUNTIMES grows from 2 to 3.

---

## Phase 6 Functional Requirements

### α — Postgres migration (Epic 30)

- **FR99.** Platform supports Postgres as an alternative database backend to SQLite. The `REGISTRY_DATABASE_URL` environment variable selects the backend: `sqlite:///path` (default) or `postgresql+asyncpg://user:pass@host/db`. Both backends pass the full test suite. Migration is an Alembic-managed schema evolution that works identically on both backends.

  **Acceptance criteria:**
  - `REGISTRY_DATABASE_URL` env var selects database backend.
  - Default (unconfigured) is SQLite (backward-compatible).
  - Both SQLite and Postgres pass the full registry-api + registry-state test suite.
  - Alembic migration runs cleanly on both backends.
  - Litestream WAL replication continues to work with SQLite (not affected by Postgres).
  - Separability test S-12 proves Postgres is optional (ABSENT state = SQLite fallback).

- **FR100.** Alembic migration framework integrated into registry-state and registry-api. Schema migrations run on startup (opt-in via `REGISTRY_STATE_AUTO_CREATE_SCHEMA`) or via explicit `just migrate` command. Existing Alembic migrations (0001–0008) are re-validated against Postgres.

  **Acceptance criteria:**
  - `just migrate` runs all pending migrations on both SQLite and Postgres.
  - Migration history is identical on both backends.
  - CI tests run against both backends (parameterized).

- **FR101.** CI pipeline adds Postgres service container for integration tests. A new `postgres` job in `ci.yml` runs the full test suite against Postgres. SQLite job continues as the primary gate.

### β — Task state machine (Epic 31)

- **FR102.** Formal task state machine replaces the implicit status tracking. States: `CREATED` → `QUEUED` → `ASSIGNED` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED`. Transitions are event-driven: `task.created` → QUEUED, `task.assigned` → ASSIGNED, `task.started` → RUNNING, `task.completed` → COMPLETED, etc.

  **Acceptance criteria:**
  - `TaskStateMachine` module with explicit states, transitions, and guard conditions.
  - Invalid transitions raise `InvalidStateTransition` (not silently ignored).
  - All existing event-driven state changes map to the formal state machine.
  - State machine is testable in isolation (unit tests for every transition).
  - Backward-compatible: existing events without explicit state transitions continue to work.

- **FR103.** Registry-state materializer uses the state machine for state derivation. Events drive state transitions through the formal FSM. This resolves GATED-ARCH D4 (state machine design from Phase 1).

### γ — Multi-task parallelism (Epic 32)

- **FR104.** Platform supports multiple concurrent worker instances. Docker Compose `docker compose up --scale worker-wrapper=N` runs N worker instances, each processing one task at a time. Tasks are assigned to available workers via the task registry (first-come, first-served with row-level locking on Postgres or exclusive transaction on SQLite).

  **Acceptance criteria:**
  - `docker compose up --scale worker-wrapper=3` starts 3 worker instances.
  - Each worker claims one task at a time via the registry.
  - No two workers claim the same task (exclusive assignment).
  - Worker identity (`worker_id`) is visible in task events and metrics.
  - `docker compose up --scale worker-wrapper=1` (default) is backward-compatible.

- **FR105.** Per-task worktree isolation. Each task gets its own worktree (already the case in Phase 1). With multi-task parallelism, multiple worktrees may be active simultaneously on the same host. Worktree paths include `task_id` to prevent collision.

  **Acceptance criteria:**
  - Multiple tasks can run simultaneously without worktree collision.
  - Worktree-lock per task is independent (no cross-task locking).
  - `OMB_WORKTREE_ROOT` shared across all workers.

- **FR106.** Orchestrator-adapter assigns tasks to available workers. The assignment is registry-driven: workers poll for unassigned tasks and atomically claim them. The orchestrator does NOT push tasks — it signals availability, and workers pull.

### δ — Gemini adapter (Epic 33)

- **FR107.** Platform ships `gemini_runner.py` (package `worker_wrapper.adapters`) — a parallel adapter to `claude_code_runner.py` and `codex_runner.py` that spawns the Gemini CLI agent with structured output. Follows ADR-0010 step-9 recipe and ADR-0015 RuntimeAdapter protocol.

  **Acceptance criteria:**
  - `SUPPORTED_RUNTIMES` grows from `{"claude-code", "codex"}` to `{"claude-code", "codex", "gemini"}`.
  - `WorkerSettings.runtime` accepts `"gemini"`.
  - `get_runtime_adapter(settings, runtime="gemini")` returns a `GeminiRunner`.
  - Credential isolation: `GEMINI_API_KEY` injected from settings, NOT from parent env.
  - `health_check()` probes Gemini CLI binary availability.
  - Separability test S-13 proves Gemini is optional (ABSENT state).

## Phase 6 Non-Functional Requirements

- **NFR-O14 (Postgres performance).** Postgres queries for single-task lookup must be <5ms p95 (matching SQLite baseline). Connection pooling via SQLAlchemy async session factory.
- **NFR-O15 (Parallelism observability).** Per-worker metrics: `worker_tasks_completed_total`, `worker_tasks_failed_total`, labeled by `worker_id` and `runtime`. Cardinality bounded by `worker_count × runtime_count`.
- **NFR-O16 (State machine audit).** Every state transition emits an audit event on the spine. State transition history is queryable via the registry API.
- **NFR-M11 (Postgres separability).** Postgres is conditionally available via `REGISTRY_DATABASE_URL`. Absent the env var, the platform falls back to SQLite with zero code changes (S-12).
- **NFR-M12 (Gemini separability).** Gemini is conditionally available via `WORKER_GEMINI_COMMAND`. Absent the env var, `GeminiRunner.health_check()` reports `installed=False` (S-13, mirrors S-11).
- **NFR-S15 (Worker isolation).** Workers share the event spine and task registry but have independent subprocess trees. One worker crash does not affect other workers.
- **NFR-S16 (Postgres connection security).** Postgres credentials are never logged. Connection uses SSL when `REGISTRY_DATABASE_URL` specifies `?sslmode=require`.
- **NFR-R11 (Parallelism reliability).** Worker crash mid-task is detected by the registry (heartbeat or task timeout). The task transitions to FAILED and is re-assignable.
- **NFR-R12 (Postgres migration reliability).** Migration is reversible (downgrade path). Backup before migration is recommended (litestream or `pg_dump`).

## Phase 6 Invariants

- **P6-I1: Backward compatibility.** SQLite remains the default. Existing deployments upgrade with zero config changes. Postgres is opt-in.
- **P6-I2: Single-task-per-worker invariant preserved.** Each worker instance processes one task at a time. Parallelism comes from multiple workers, not multi-threading within a worker.
- **P6-I3: Event-driven state transitions.** All state changes are driven by events on the spine. No direct database state mutations bypass the FSM.
- **P6-I4: Worker identity.** Each worker has a unique `worker_id` (default: hostname + PID). Events and metrics carry `worker_id` for observability.
- **P6-I5: Credential isolation extends to Gemini.** `GEMINI_API_KEY` is injected from settings, never from parent env. Mirrors P5-I1 for Codex.

## Phase 6 Architecture Decisions Required

- **ADR-0017: Postgres migration strategy** — Alembic + dual-backend + connection pooling
- **ADR-0018: Task state machine** — states, transitions, guards, event mapping
- **ADR-0019: Worker pool assignment** — pull-based (workers poll) vs push-based (orchestrator assigns)
- **ADR-0020: Phase 6 gate** — acceptance criteria for Phase 6 readiness

## Phase 6 Ship-Blocker Checklist

1. [ ] All Phase 1–5 invariants regression-free
2. [ ] Postgres integration tests pass on CI (Linux)
3. [ ] SQLite integration tests pass (backward-compatibility)
4. [ ] State machine unit tests cover all transitions
5. [ ] Multi-worker smoke test (2+ workers, concurrent tasks)
6. [ ] Gemini adapter contract tests pass
7. [ ] Separability S-12 (Postgres optional) + S-13 (Gemini optional) green
8. [ ] `just lint` EXIT 0
9. [ ] All discipline scripts exit 0
10. [ ] No new third-party Python dependencies without ADR
11. [ ] ADR-0017, ADR-0018, ADR-0019, ADR-0020 accepted
12. [ ] Mutation gate ≥82 (ratchet from Phase 3)
13. [ ] Tier declarations gate green for all MCP servers (including Gemini-era)
14. [ ] Event cardinality ratchet updated for new event types

## Estimated Effort

**5 epics, ~30 stories, ~6-8 weeks solo-operator work.**

| Epic | Stories | Estimate |
|------|---------|----------|
| 30 — Postgres migration | 7 | ~1.5 weeks |
| 31 — Task state machine | 5 | ~1 week |
| 32 — Multi-task parallelism | 7 | ~2 weeks |
| 33 — Gemini adapter | 6 | ~1.5 weeks |
| 34 — CI hardening + finalization | 5 | ~1 week |
