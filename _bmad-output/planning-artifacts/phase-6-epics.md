---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: complete
finalStoryCount: 30
finalEpicCount: 5
inputDocuments:
  - _bmad-output/planning-artifacts/phase-6-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-6-architecture-amendment.md
  - docs/adr/0017-postgres-migration.md
  - docs/adr/0018-task-state-machine.md
  - docs/adr/0019-worker-pool-assignment.md
  - docs/adr/0020-phase-6-gate.md
workflowType: epics-and-stories
project_name: oh-my-bmad
user_name: R2d2
date: '2026-06-07'
---

# oh-my-bmad — Phase 6 Epic Breakdown: Server Execution Pool

## Overview

Phase 6 introduces the **server execution pool** — Postgres-backed persistence, a formal task state machine, and concurrent execution via a Docker Compose worker pool, plus a Gemini adapter as the third runtime. This document decomposes FR99–FR107 and associated NFRs into **5 epics (30–34) and 30 stories**, continuing the epic numbering from Phase 5 (Epics 26–29).

Source documents:
- PRD amendment: `_bmad-output/planning-artifacts/phase-6-prd-amendment.md` (FR99–FR107)
- Architecture amendment: `_bmad-output/planning-artifacts/phase-6-architecture-amendment.md` (P6-I1–P6-I5)
- ADR-0017 (Postgres migration), ADR-0018 (task state machine), ADR-0019 (worker pool assignment), ADR-0020 (Phase 6 gate)

## Requirements Inventory

### Functional Requirements

**FR99.** Platform supports Postgres as an alternative database backend to SQLite. The `REGISTRY_DATABASE_URL` environment variable selects the backend: `sqlite:///path` (default) or `postgresql+asyncpg://user:pass@host/db`. Both backends pass the full test suite. Migration is Alembic-managed and backend-agnostic.

**FR100.** Alembic migration framework integrated into registry-state and registry-api. Schema migrations run on startup (opt-in) or via explicit `just migrate`. Existing migrations 0001–0008 re-validated against Postgres.

**FR101.** CI pipeline adds Postgres service container for integration tests. A new `postgres` job in `ci.yml` runs the full test suite against Postgres. SQLite job continues as the primary gate.

**FR102.** Formal task state machine replaces implicit status tracking. States: `CREATED` → `QUEUED` → `ASSIGNED` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED`. Transitions are event-driven. Invalid transitions raise `InvalidStateTransition`.

**FR103.** Registry-state materializer uses the state machine for state derivation. Events drive state transitions through the formal FSM. Resolves GATED-ARCH D4.

**FR104.** Platform supports multiple concurrent worker instances. `docker compose up --scale worker-wrapper=N` runs N workers, each processing one task at a time. Tasks assigned to available workers via registry (first-come, first-served with row-level locking).

**FR105.** Per-task worktree isolation. Each task gets its own worktree. With multi-task parallelism, multiple worktrees may be active simultaneously. Worktree paths include `task_id` to prevent collision.

**FR106.** Orchestrator-adapter assigns tasks to available workers. Assignment is registry-driven: workers poll for unassigned tasks and atomically claim them. Workers pull; orchestrator does not push.

**FR107.** Platform ships `gemini_runner.py` — a parallel adapter to `claude_code_runner.py` and `codex_runner.py` that spawns the Gemini CLI agent with structured output. Follows ADR-0010 step-9 + ADR-0015 RuntimeAdapter protocol.

### Non-Functional Requirements

**NFR-O14.** Postgres queries for single-task lookup must be <5ms p95. Connection pooling via SQLAlchemy async session factory.

**NFR-O15.** Per-worker metrics: `worker_tasks_completed_total`, `worker_tasks_failed_total`, labeled by `worker_id` and `runtime`. Cardinality bounded by `worker_count × runtime_count`.

**NFR-O16.** Every state transition emits an audit event on the spine. State transition history is queryable via the registry API.

**NFR-M11.** Postgres is conditionally available via `REGISTRY_DATABASE_URL`. Absent the env var, platform falls back to SQLite with zero code changes (S-12).

**NFR-M12.** Gemini is conditionally available via `WORKER_GEMINI_COMMAND`. Absent the env var, `GeminiRunner.health_check()` reports `installed=False` (S-13).

**NFR-S15.** Workers share the event spine and task registry but have independent subprocess trees. One worker crash does not affect other workers.

**NFR-S16.** Postgres credentials are never logged. Connection uses SSL when `REGISTRY_DATABASE_URL` specifies `?sslmode=require`.

**NFR-R11.** Worker crash mid-task is detected by the registry (heartbeat or task timeout). The task transitions to FAILED and is re-assignable.

**NFR-R12.** Migration is reversible (downgrade path). Backup before migration is recommended (litestream or `pg_dump`).

### Additional Requirements

**Architecture requirements (from phase-6-architecture-amendment.md):**

1. New archetype: Worker Pool Manager (6th archetype) with task-assignment loop
2. Database topology: dual-backend SQLite (default) + Postgres (opt-in)
3. State machine module: `domain/task_fsm.py` — sole authority for task state transitions
4. Gemini adapter: `adapters/gemini_runner.py` — third RuntimeAdapter implementation
5. New separability tests: S-12 (Postgres optional), S-13 (Gemini optional)

**Preserved invariants (Phase 1–5 carry forward):**
- FR26 single-writer (P2-I1)
- MCP transport stdio-only (P2-I4)
- Event-only telemetry (NFR-O1/O10)
- `trace_id` propagation (NFR-O7)
- Tier-enforced authz (Epic 6)
- Supply-chain (Epic 8 + G-SEC-1/2)
- Runtime adapter protocol (ADR-0015)
- Credential isolation (P5-I1)
- Budget supervision (P5-I3)

**New Phase 6 invariants:**

- **P6-I1:** Backward compatibility — SQLite remains default, Postgres is opt-in via `REGISTRY_DATABASE_URL`
- **P6-I2:** Single-task-per-worker — each worker instance processes one task at a time
- **P6-I3:** Event-driven state transitions — all state changes via the FSM, no direct DB mutations
- **P6-I4:** Worker identity — unique `worker_id` (hostname+PID) stamped on events and metrics
- **P6-I5:** Credential isolation extends to Gemini — `GEMINI_API_KEY` injected from settings only

**Gating ADRs (all accepted 2026-06-07):**
- ADR-0017: Postgres migration strategy — gates Epic 30
- ADR-0018: Task state machine — gates Epic 31
- ADR-0019: Worker pool assignment — gates Epic 32
- ADR-0020: Phase 6 gate — gates Phase 6

### FR Coverage Map

| FR | Epic | Story IDs | Notes |
|----|------|-----------|-------|
| FR99 | 30 | 30.2, 30.3, 30.6 | Dual-backend selection + Alembic + S-12 |
| FR100 | 30 | 30.3, 30.4 | Alembic framework + CI |
| FR101 | 30 | 30.5 | Postgres CI job |
| FR102 | 31 | 31.2, 31.3, 31.4 | FSM class + migration + materializer |
| FR103 | 31 | 31.4, 31.5 | Materializer FSM integration + audit |
| FR104 | 32 | 32.3, 32.4, 32.5 | Concurrent workers + claiming + scaling |
| FR105 | 32 | 32.6 | Per-task worktree isolation |
| FR106 | 32 | 32.3, 32.4 | Pull-based task assignment |
| FR107 | 33 | 33.2, 33.3, 33.4 | Gemini adapter + credentials + factory |

**100% FR coverage confirmed — 9 FRs mapped across 5 epics, zero orphans.**

### NFR Coverage Summary

- NFR-O14 (Postgres performance) → Epic 30 (Story 30.7)
- NFR-O15 (Per-worker metrics) → Epic 32 (Story 32.7)
- NFR-O16 (State machine audit) → Epic 31 (Story 31.5)
- NFR-M11 (Postgres separability) → Epic 30 (Story 30.6)
- NFR-M12 (Gemini separability) → Epic 33 (Story 33.6)
- NFR-S15 (Worker isolation) → Epic 32 (Story 32.5, 32.7)
- NFR-S16 (Postgres connection security) → Epic 30 (Story 30.7)
- NFR-R11 (Worker crash detection) → Epic 32 (Story 32.7)
- NFR-R12 (Migration reversibility) → Epic 30 (Story 30.4)

**Zero NFR orphans.**

## Epic List

### Dependency Graph

```
Epic 30 (Postgres) ──► Epic 31 (State Machine) ──► Epic 32 (Multi-task Parallelism)
                                                              │
                                            Epic 33 (Gemini) ─┤ (partial parallel with 32)
                                                              │
                                            Epic 34 (CI + Finalization) ← after all
```

### Standalone Value

- **Epic 30** delivers: Production-grade Postgres persistence. Operators can deploy with Postgres for production scale while keeping SQLite for local dev. Zero existing deployments break.
- **Epic 31** delivers: Formal task lifecycle. Every state transition is guarded, audited, and testable. Resolves Phase-1 GATED-ARCH D4 technical debt.
- **Epic 32** delivers: Horizontal worker scaling. `docker compose up --scale worker-wrapper=3` gives the operator 3 concurrent task executors with atomic claiming and isolated worktrees.
- **Epic 33** delivers: Third runtime (Gemini). Operators can dispatch tasks to Google's Gemini CLI following the same adapter pattern as Claude and Codex.
- **Epic 34** delivers: Ship-ready Phase 6. All CI gates enforced, retrospectives produced, ship-blocker checklist verified green.

### Sequencing Rationale

Epic 30 (Postgres) lands first because the worker pool's atomic claim semantics require Postgres `SKIP LOCKED` under concurrent load. Epic 31 (state machine) lands second because the worker pool depends on the FSM for `QUEUED → ASSIGNED` transitions. Epic 32 (multi-task parallelism) lands third, combining Postgres + FSM into the worker pool. Epic 33 (Gemini) can partially parallelize with Epic 32 since it only depends on the adapter protocol (ADR-0015), not on Epics 30–32. Epic 34 (CI/finalization) lands last as the ship-gate verification.

## Epic 30: Postgres Migration (backlog)

**Goal.** Migrate the registry to support Postgres as an alternative database backend while preserving SQLite as the zero-config default. The `REGISTRY_DATABASE_URL` environment variable selects the backend. Both backends pass the full test suite via Alembic-managed schema migrations. This is the foundation for the worker pool — Postgres MVCC provides the concurrent-reader + single-writer-per-table semantics that multi-worker claiming requires.

**FRs covered:** FR99, FR100, FR101
**NFRs:** NFR-O14, NFR-M11, NFR-S16, NFR-R12

### Story 30.1: ATDD Red-Phase — Dual-Backend Contract Tests

As the developer, I want xfail(strict) contract tests that assert dual-backend behavior, so that I have a test-first safety net before writing any production code.

**Given** the existing registry-state and registry-api test suites
**When** I write xfail(strict) tests for dual-backend support
**Then** the following contracts are asserted (all initially failing):

1. `REGISTRY_DATABASE_URL` unset → engine factory creates aiosqlite engine
2. `REGISTRY_DATABASE_URL=postgresql+asyncpg://...` → engine factory creates asyncpg engine with connection pool
3. `just migrate` runs all pending Alembic migrations on SQLite
4. `just migrate` runs all pending Alembic migrations on Postgres (mocked)
5. Both backends produce identical schema after migration
6. Backend-conditional pragmas: SQLite gets WAL/busy_timeout, Postgres does not
7. S-12 lifecycle (create → queue → assign → run → complete) passes on SQLite without `REGISTRY_DATABASE_URL`

**And Given** the existing Alembic migration chain (0001–0008)
**When** each migration is inspected for backend-specific SQL
**Then** tests assert all migrations use SQLAlchemy Core operations (no raw SQL)

### Story 30.2: Database Backend Configuration + Dual Engine Factory

As the operator, I want `REGISTRY_DATABASE_URL` to select my database backend, so that I can use Postgres for production without changing application code.

**Given** the existing aiosqlite session factory in registry-state
**When** I add `REGISTRY_DATABASE_URL` to WorkerSettings/config
**Then** unset or `sqlite+aiosqlite:///...` creates the existing aiosqlite engine (P6-I1)
**And** `postgresql+asyncpg://...` creates an asyncpg engine with connection pool (pool size = `5 + 2 * worker_count`)

**And Given** SQLite-specific pragmas in the current codebase
**When** Postgres backend is selected
**Then** SQLite pragmas (`PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout`, `PRAGMA synchronous=NORMAL`) are NOT applied to Postgres connections
**And** the engine factory returns the correct engine type based on URL scheme

**And Given** any deployment without `REGISTRY_DATABASE_URL`
**When** the service starts
**Then** it behaves identically to Phase 5 (backward compatible, P6-I1)

### Story 30.3: Backend-Conditional Repository + Alembic Re-Validation

As the developer, I want a repository abstraction that hides backend differences, so that service code never needs to know whether it's talking to SQLite or Postgres.

**Given** existing direct SQLAlchemy queries in registry-state and registry-api
**When** I create a `TaskRepository` protocol with concrete implementations
**Then** `SqliteTaskRepository` and `PostgresTaskRepository` hide dialect differences
**And** both implementations pass the existing test suite

**And Given** the existing Alembic migrations 0001–0008
**When** I run `alembic upgrade head` against Postgres
**Then** all 8 migrations execute without error
**And** the resulting schema is identical to the SQLite schema
**And** `just migrate` works on both backends

### Story 30.4: Alembic Migration Downgrade Path (NFR-R12)

As the operator, I want a reversible migration path, so that I can roll back if a migration causes issues in production.

**Given** the Alembic migration chain
**When** I run `alembic downgrade -1` on Postgres
**Then** the most recent migration is cleanly reversed
**And** the database returns to the prior schema state

**And Given** the same downgrade command on SQLite
**When** executed
**Then** the downgrade also works correctly
**And** the downgrade path is documented in the operator runbook

### Story 30.5: Postgres CI Service Container Job (FR101)

As the developer, I want a CI job that runs the full test suite against Postgres, so that both backends are continuously validated.

**Given** `.github/workflows/ci.yml` with the existing SQLite job
**When** I add a `postgres` job
**Then** it runs a Postgres service container alongside the test runner
**And** sets `REGISTRY_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test`
**And** runs the full registry-state + registry-api test suite
**And** both SQLite (primary gate) and Postgres jobs must pass before merge

**And Given** parameterized test configuration
**When** a new migration is added
**Then** both CI jobs exercise it automatically

### Story 30.6: Separability Test S-12 — Postgres Optional (NFR-M11)

As the operator, I want Postgres to be completely optional, so that my existing SQLite deployment continues to work without any changes.

**Given** a clean environment without Postgres installed
**And** `REGISTRY_DATABASE_URL` is NOT set
**When** I run the full task lifecycle (create → queue → assign → run → complete)
**Then** the system uses SQLite exclusively and all operations succeed
**And** no Postgres-related imports or connections are attempted

**And Given** the S-12 separability test
**When** `REGISTRY_DATABASE_URL` IS set to a Postgres URL
**Then** the system uses Postgres and the same lifecycle succeeds
**And** the test proves the ABSENT state (no env var) = SQLite fallback

### Story 30.7: NFR-O14 Performance Validation + NFR-S16 Connection Security

As the operator, I want Postgres queries to be fast and credentials to be secure, so that production performance and security meet the NFR thresholds.

**Given** a Postgres backend with connection pooling
**When** I benchmark single-task lookup queries
**Then** p95 latency is <5ms (matching SQLite baseline)
**And** connection pool size is configurable via settings

**And Given** a `REGISTRY_DATABASE_URL` with `?sslmode=require`
**When** the connection is established
**Then** SSL is used for the connection
**And** Postgres credentials are never logged (NFR-S16)
**And** a negative test asserts no credential strings appear in log output

---

## Epic 31: Task State Machine (backlog)

**Goal.** Replace the implicit task status tracking with a formal finite state machine. The FSM is the sole authority for state transitions — no service may mutate `Task.status` directly. Invalid transitions raise `InvalidStateTransition`. This resolves GATED-ARCH D4 (deferred since Phase 1) and is a prerequisite for the multi-worker pool, where concurrent claims require guarded transitions.

**FRs covered:** FR102, FR103
**NFRs:** NFR-O16

### Story 31.1: ATDD Red-Phase — FSM Contract Tests

As the developer, I want xfail(strict) contract tests for the state machine, so that I have comprehensive test coverage before implementing the FSM.

**Given** the planned state machine design (7 states, 10 transitions)
**When** I write xfail(strict) tests
**Then** the following contracts are asserted:

1. Every valid transition (10 paths) succeeds and returns the target state
2. Every invalid transition raises `InvalidStateTransition` with descriptive message
3. Terminal states (`COMPLETED`, `FAILED`, `CANCELLED`) reject all transitions
4. Unknown states raise `InvalidStateTransition`
5. FSM is pure — no database dependency, no side effects
6. `task.assigned` event triggers `QUEUED → ASSIGNED` transition
7. `task.started` event triggers `ASSIGNED → RUNNING` transition
8. `task.completed` event triggers `RUNNING → COMPLETED` transition
9. `task.failed` event triggers `RUNNING → FAILED` or `ASSIGNED → FAILED`
10. `task.cancelled` event triggers transition from any non-terminal state

### Story 31.2: TaskStateMachine + InvalidStateTransition (FR102)

As the developer, I want a `TaskStateMachine` class in `domain/task_fsm.py`, so that task lifecycle transitions are formally guarded.

**Given** the FSM design from ADR-0018
**When** I implement `TaskStateMachine`
**Then** `STATES` contains all 7 states: `CREATED`, `QUEUED`, `ASSIGNED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
**And** `TRANSITIONS` maps each state to its permitted targets:

| From | Permitted targets |
|------|-------------------|
| CREATED | QUEUED, CANCELLED |
| QUEUED | ASSIGNED, CANCELLED |
| ASSIGNED | RUNNING, FAILED, CANCELLED, QUEUED |
| RUNNING | COMPLETED, FAILED, CANCELLED |
| COMPLETED | *(terminal)* |
| FAILED | *(terminal)* |
| CANCELLED | *(terminal)* |

**And** `transition(current, target)` returns `target` on success
**And** `transition(current, target)` raises `InvalidStateTransition` on failure
**And** `InvalidStateTransition` is a domain exception in `domain/exceptions.py`
**And** the FSM is a pure function (no DB, no I/O, no side effects)

### Story 31.3: Migrate All Task.status Mutations to FSM (FR102, P6-I3)

As the developer, I want all existing `Task.status = ...` sites to go through the FSM, so that no direct database mutations bypass the event spine (P6-I3).

**Given** the codebase has sites that mutate `Task.status` directly
**When** I audit and migrate each site
**Then** every `Task.status` mutation site calls `fsm.transition(current, target)` instead
**And** the caller persists the result and emits the corresponding event
**And** no `UPDATE tasks SET status` or ORM status mutations exist outside `task_fsm.py`
**And** a CI gate (AST check or grep) enforces this invariant going forward

**And Given** existing event-driven state changes
**When** mapped to the formal FSM
**Then** backward compatibility is preserved — events without explicit transitions continue to work
**And** the FSM validates (not creates) transitions — events are still the source of truth

### Story 31.4: Materializer FSM Integration + Event Mapping (FR103)

As the developer, I want the registry-state materializer to use the FSM for state derivation, so that events drive transitions through the formal state machine.

**Given** the materializer processes `task.*` events to derive task state
**When** I integrate the FSM
**Then** each `task.*` event maps to a FSM transition:

| Event | FSM Transition |
|-------|---------------|
| `task.created` | (none — initial state CREATED) |
| `task.queued` | CREATED → QUEUED |
| `task.assigned` | QUEUED → ASSIGNED |
| `task.execution.started` | ASSIGNED → RUNNING |
| `task.execution.completed` | RUNNING → COMPLETED |
| `task.execution.failed` | RUNNING → FAILED |
| `task.cancelled` | any non-terminal → CANCELLED |

**And** the materializer calls `fsm.transition(current_state, target_state)` before persisting
**And** `InvalidStateTransition` from the FSM is caught and logged as a data integrity warning
**And** existing materializer tests pass without modification (backward compat)

### Story 31.5: State Machine Audit Trail (NFR-O16)

As the operator, I want every state transition to emit an audit event, so that I can query the full history of task lifecycle changes.

**Given** the FSM integration from Story 31.4
**When** a state transition occurs
**Then** an audit event is emitted on the event spine containing: `task_id`, `from_state`, `to_state`, `trigger_event`, `worker_id` (if assigned), `timestamp`
**And** `task.state_transition` event is registered in the schema registry

**And Given** the registry API
**When** I query `GET /v1/tasks/{id}/transitions`
**Then** the response contains the full ordered history of state transitions for that task
**And** each transition includes the audit metadata (trigger, worker_id, timestamp)

---

## Epic 32: Multi-task Parallelism (backlog)

**Goal.** Enable multiple concurrent worker instances via Docker Compose scaling. Each worker polls for `QUEUED` tasks and atomically claims them. Postgres uses `SELECT ... FOR UPDATE SKIP LOCKED` for concurrent claiming; SQLite uses `BEGIN EXCLUSIVE`. Workers have unique identities stamped on events and metrics. One worker crash does not affect other workers. This is the main value delivery of Phase 6 — horizontal task execution scaling.

**FRs covered:** FR104, FR105, FR106
**NFRs:** NFR-O15, NFR-S15, NFR-R11

### Story 32.1: ATDD Red-Phase — Worker Pool Contract Tests

As the developer, I want xfail(strict) contract tests for the worker pool, so that concurrent claiming, worker identity, and scaling behavior are test-verified before implementation.

**Given** the planned worker pool design from ADR-0019
**When** I write xfail(strict) tests
**Then** the following contracts are asserted:

1. Two workers cannot claim the same task (exclusive assignment)
2. Worker claims a task by transitioning `QUEUED → ASSIGNED` via FSM
3. `worker_id` is stamped on the claimed task row
4. `worker_id` is present in `task.assigned` event payload
5. `worker_id` is present in metrics labels
6. Worker polls at configurable interval (`WORKER_POLL_INTERVAL_SECONDS`)
7. `docker compose up --scale worker-wrapper=3` starts 3 independent workers
8. `--scale worker-wrapper=1` (default) is backward-compatible
9. Worker crash mid-task does not affect other workers
10. Crashed worker's task transitions to FAILED and is re-assignable

### Story 32.2: Worker Identity System (P6-I4)

As the operator, I want each worker to have a unique identity, so that I can trace which worker processed which task.

**Given** a worker-wrapper process starting up
**When** it generates its `worker_id`
**Then** `worker_id` = `f"{hostname}-{pid}"` (or configurable via `WORKER_ID` env var)
**And** `worker_id` is included in: `task.assigned` event, `task.execution.started` event, `task.execution.completed` event, `task.execution.failed` event
**And** `worker_id` is included in per-worker metrics labels
**And** `worker_id` is logged at startup for operational visibility

**And Given** a negative test
**When** a task-lifecycle event is emitted
**Then** the event has a non-empty `worker_id` field (P6-I4 CI gate)

### Story 32.3: Atomic Task Claiming — SKIP LOCKED / BEGIN EXCLUSIVE (FR104, FR106)

As the worker, I want to atomically claim a task from the queue, so that no two workers claim the same task even under concurrent polling.

**Given** a Postgres backend
**When** a worker claims a `QUEUED` task
**Then** the claim uses `SELECT ... FOR UPDATE SKIP LOCKED` to atomically select and lock an unclaimed task
**And** the task transitions `QUEUED → ASSIGNED` via FSM (Story 31.2)
**And** `task.worker_id` is set to the claiming worker's identity

**And Given** a SQLite backend
**When** a worker claims a `QUEUED` task
**Then** the claim uses `BEGIN EXCLUSIVE` transaction for atomic selection
**And** the same FSM transition and `worker_id` assignment applies

**And Given** two workers polling concurrently (Postgres)
**When** both attempt to claim the same task simultaneously
**Then** exactly one succeeds; the other gets no task or a different task
**And** no deadlock or data corruption occurs

### Story 32.4: Worker Polling Loop + Poll Interval Config (FR106)

As the worker, I want to poll the registry for available tasks at a configurable interval, so that I can continuously process tasks without orchestrator push.

**Given** a worker-wrapper process
**When** it enters the task-assignment loop
**Then** it polls for `QUEUED` tasks at `WORKER_POLL_INTERVAL_SECONDS` (default 2.0s)
**And** when a `QUEUED` task is found, it atomically claims it (Story 32.3)
**And** after claiming, it spawns the runtime adapter and runs the task
**And** after task completion, it polls for the next task

**And Given** no `QUEUED` tasks available
**When** the poll returns empty
**Then** the worker waits `WORKER_POLL_INTERVAL_SECONDS` before polling again
**And** the loop is cancellable (graceful shutdown)

### Story 32.5: Docker Compose Multi-Worker Scaling (FR104)

As the operator, I want to scale workers via Docker Compose, so that I can run multiple concurrent task executors.

**Given** the existing `docker-compose.yml` with a single worker-wrapper service
**When** I run `docker compose up --scale worker-wrapper=3`
**Then** 3 independent worker-wrapper containers start
**And** each has its own `worker_id` (unique hostname+PID)
**And** each runs the task-assignment loop independently
**And** no shared state exists between workers beyond the database

**And Given** `--scale worker-wrapper=1` (default)
**When** the stack starts
**Then** behavior is identical to Phase 5 (backward compatible)

### Story 32.6: Per-Task Worktree Isolation Under Multi-Worker (FR105)

As the worker, I want each task to have its own isolated worktree, so that multiple concurrent tasks don't collide on the filesystem.

**Given** the existing worktree management in worker-wrapper
**When** multiple workers run tasks simultaneously
**Then** each task gets its own worktree (already the Phase-1 behavior)
**And** worktree paths include `task_id` to prevent collision
**And** `OMB_WORKTREE_ROOT` is shared across all workers (configurable)
**And** worktree lock per task is independent — no cross-task locking
**And** cleanup of completed task worktrees is unchanged

### Story 32.7: Per-Worker Metrics + Worker Crash Detection (NFR-O15, NFR-S15, NFR-R11)

As the operator, I want per-worker metrics and crash detection, so that I can monitor the health and throughput of each worker.

**Given** the metrics-subscriber service
**When** workers process tasks
**Then** `worker_tasks_completed_total` counter is labeled by `worker_id` and `runtime`
**And** `worker_tasks_failed_total` counter is labeled by `worker_id` and `runtime`
**And** cardinality is bounded by `worker_count × runtime_count`

**And Given** a worker crash mid-task (NFR-R11)
**When** the crash is detected (heartbeat timeout or task timeout)
**Then** the task transitions to FAILED via FSM
**And** the task is re-assignable (returns to QUEUED or stays FAILED per policy)
**And** other workers are unaffected (NFR-S15 — independent subprocess trees)

---

## Epic 33: Gemini Adapter (backlog)

**Goal.** Introduce a Gemini runtime adapter as the third concrete `RuntimeAdapter` implementation. Following ADR-0010 step-9 + ADR-0015 recipe exactly. `SUPPORTED_RUNTIMES` grows from 2 to 3. Credential isolation (P6-I5) ensures `GEMINI_API_KEY` is injected from settings only — never from parent env, never into Claude/Codex subprocesses.

**FRs covered:** FR107
**NFRs:** NFR-M12

### Story 33.1: ATDD Red-Phase — Gemini Adapter Contract Tests

As the developer, I want xfail(strict) contract tests for the Gemini adapter, so that adapter protocol compliance and credential isolation are verified before implementation.

**Given** the RuntimeAdapter protocol from ADR-0015
**When** I write xfail(strict) tests
**Then** the following contracts are asserted:

1. `get_runtime_adapter(settings, runtime="gemini")` returns a `GeminiRunner`
2. `GeminiRunner.runtime_name` returns `"gemini"`
3. `GeminiRunner.spawn()` spawns `gemini` CLI subprocess with `--json` flag
4. `GeminiRunner.parse_output()` deserializes JSONL structured output
5. `GeminiRunner.kill()` follows SIGTERM → 5s grace → SIGKILL escalation
6. `GeminiRunner.is_healthy()` checks subprocess liveness
7. `GEMINI_API_KEY` is in Gemini child env but NOT in Claude or Codex child env (P6-I5)
8. `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are NOT in Gemini child env
9. `WORKER_GEMINI_COMMAND` blank → `health_check()` returns `installed=False`

### Story 33.2: GeminiRunner Scaffold — spawn, runtime_name, parse_output (FR107)

As the developer, I want a `GeminiRunner` class that satisfies the `RuntimeAdapter` protocol, so that the worker can spawn Gemini CLI tasks.

**Given** the ADR-0015 RuntimeAdapter protocol
**When** I implement `adapters/gemini_runner.py`
**Then** `spawn()` runs `gemini run --json "<prompt>"` with `cwd` set to the worktree
**And** `runtime_name` returns `"gemini"`
**And** `parse_output()` deserializes each JSONL line, extracting events from structured response objects
**And** token usage is extracted from `usageMetadata` fields
**And** `is_healthy()` checks `self._process.returncode is None`

### Story 33.3: Gemini Credential Isolation (P6-I5)

As the operator, I want Gemini credentials isolated from other runtime subprocesses, so that Google credentials never leak into Anthropic or OpenAI child processes.

**Given** the existing per-runtime `_ENV_ALLOWLIST` pattern from Phase 5
**When** I define the Gemini allowlist
**Then** `_GEMINI_ENV_ALLOWLIST` contains: `PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TMP`, `TEMP`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`
**And** `_GEMINI_ENV_PREFIXES` = `("OMB_", "GEMINI_")`
**And** `GEMINI_API_KEY` is injected separately in `_spawn()`, NOT in the allowlist
**And** a negative test confirms `GEMINI_API_KEY` is absent from Claude and Codex child envs
**And** a negative test confirms `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are absent from Gemini child env

### Story 33.4: SUPPORTED_RUNTIMES Expansion + Factory Update (FR107)

As the developer, I want the runtime factory to support Gemini, so that task dispatch routes to the correct adapter.

**Given** `SUPPORTED_RUNTIMES = {"claude-code", "codex"}` from Phase 5
**When** I add Gemini support
**Then** `SUPPORTED_RUNTIMES` becomes `{"claude-code", "codex", "gemini"}`
**And** `WorkerSettings.runtime` accepts `"gemini"`
**And** `get_runtime_adapter(settings, runtime="gemini")` returns `GeminiRunner(settings)`
**And** the existing Claude and Codex paths are unchanged

### Story 33.5: Gemini Health Check + Binary Detection

As the operator, I want the Gemini adapter to detect whether the CLI binary is available, so that the system gracefully handles environments without Gemini installed.

**Given** `GeminiRunner` with `WORKER_GEMINI_COMMAND` config
**When** `health_check()` is called
**Then** it probes for the Gemini CLI binary at the configured path
**And** returns `installed=True` with version info if found
**And** returns `installed=False` if not found or `WORKER_GEMINI_COMMAND` is blank

**And Given** `GeminiRunner.kill()` (budget supervision)
**When** the budget supervisor invokes `kill()`
**Then** SIGTERM → 5s grace → SIGKILL escalation mirrors existing adapter semantics (P5-I3)
**And** the kill is testable via the existing budget enforcement test harness

### Story 33.6: Separability Test S-13 — Gemini Optional (NFR-M12)

As the operator, I want Gemini to be completely optional, so that my deployment works without the Gemini binary.

**Given** a clean environment without the Gemini CLI
**And** `WORKER_GEMINI_COMMAND` is blank
**When** the worker-wrapper starts
**Then** `GeminiRunner.health_check()` returns `installed=False`
**And** the worker falls back to the configured default runtime (claude-code)
**And** no import error or crash occurs
**And** the system functions normally with only Claude and Codex runtimes

**And Given** the S-13 test
**When** `WORKER_GEMINI_COMMAND` IS set to a valid path
**Then** the system boots and Gemini tasks are dispatchable

---

## Epic 34: CI Hardening + Finalization (backlog)

**Goal.** Verify all Phase 6 ship-blocker criteria are met. Update CI gates, mutation ratchets, event cardinality, and produce retrospectives. This is the ship gate — Phase 6 is not complete until every item on the 14-point ship-blocker checklist is green.

**FRs covered:** *(cross-cutting — validates FR99–FR107)*
**NFRs:** *(cross-cutting — validates NFR-O14–O16, NFR-M11–M12, NFR-S15–S16, NFR-R11–R12)*

### Story 34.1: Phase 6 CI Gate Additions

As the developer, I want Phase 6 CI gates enforced on every PR, so that regressions are caught before merge.

**Given** the existing CI gate structure
**When** I add Phase 6 gates
**Then** the following are enforced as PR-required checks:

- **Epic 30 gate:** `TaskStateMachine` class exists; all 10 transitions validated; no `task.status = ...` outside `task_fsm.py`
- **Epic 31 gate:** Task-assignment loop polls for QUEUED tasks; atomic claim with `worker_id`; `worker_id` on events (P6-I4)
- **Epic 32 gate:** `REGISTRY_DATABASE_URL` absent → SQLite full suite (P6-I1); present → Postgres full suite; Alembic on both; repository abstraction hides backend
- **Epic 33 gate:** `GeminiRunner` satisfies `RuntimeAdapter`; P6-I5 negative test; `parse_output()` structured JSON; `kill()` semantics match; S-13 separability

### Story 34.2: Mutation Gate Ratchet Update

As the developer, I want the mutation testing baseline updated for Phase 6 kernels, so that test quality is enforced for the new modules.

**Given** the existing mutation gate at threshold 82 (from Epic 14)
**When** Phase 6 adds new kernels (`domain/task_fsm.py`, `adapters/gemini_runner.py`)
**Then** the mutation scope is expanded to include these kernels
**And** a new baseline is measured on the nightly
**And** the threshold is ratcheted (set at-or-below new baseline, never lowered)

### Story 34.3: Event Cardinality Ratchet Update

As the developer, I want the event cardinality ratchet updated, so that new Phase 6 events are tracked.

**Given** the existing cardinality ratchet in metrics-subscriber
**When** Phase 6 adds new event types (`task.assigned`, `task.state_transition`, `task.queued`)
**Then** the cardinality baseline is updated to include the new events
**And** the bounded-enum gate enforces the new baseline
**And** no unregistered event types bypass the gate

### Story 34.4: Phase 6 Ship-Blocker Checklist Verification

As the operator, I want every item on the Phase 6 ship-blocker checklist verified green, so that I can confidently ship Phase 6.

**Given** the 14-item Phase 6 ship-blocker checklist from the PRD amendment
**When** I run the full verification campaign
**Then** all 14 items are green with cited evidence:

1. All Phase 1–5 invariants regression-free
2. Postgres integration tests pass on CI (Linux)
3. SQLite integration tests pass (backward-compatibility)
4. State machine unit tests cover all transitions
5. Multi-worker smoke test (2+ workers, concurrent tasks)
6. Gemini adapter contract tests pass
7. Separability S-12 (Postgres optional) + S-13 (Gemini optional) green
8. `just lint` EXIT 0
9. All discipline scripts exit 0
10. No new third-party Python dependencies without ADR
11. ADR-0017, ADR-0018, ADR-0019, ADR-0020 accepted
12. Mutation gate ≥82 (ratchet from Phase 3)
13. Tier declarations gate green for all MCP servers
14. Event cardinality ratchet updated for new event types

### Story 34.5: Phase 6 Retrospective

As the team, I want a Phase 6 retrospective capturing lessons learned, so that Phase 7 benefits from our experience.

**Given** the completion of all Phase 6 epics (30–34)
**When** I run the retrospective
**Then** lessons are captured covering: Postgres migration pitfalls, FSM design decisions, worker pool concurrency issues, adapter protocol extensibility
**And** carry-forward items are documented for Phase 7
**And** the retrospective is saved to `_bmad-output/implementation-artifacts/phase-6-retrospective-2026-06-XX.md`
