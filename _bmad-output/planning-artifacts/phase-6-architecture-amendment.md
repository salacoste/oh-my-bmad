## Phase 6 Architecture Amendment -- Worker Pool + Postgres + Gemini

> **Amendment added:** 2026-06-07.
>
> **Companion documents:**
> - PRD amendment: see [`prd.md`](./prd.md) S"Phase 6 Scope Extension" (worker pool + multi-database + Gemini plane).
> - Postgres migration: see [`docs/adr/0017-postgres-migration.md`](../../docs/adr/0017-postgres-migration.md) (accepted) -- resolves the dual-backend database topology.
> - Task state machine: see [`docs/adr/0018-task-state-machine.md`](../../docs/adr/0018-task-state-machine.md) (accepted) -- formal FSM for task lifecycle.
> - Worker pool assignment: see [`docs/adr/0019-worker-pool-assignment.md`](../../docs/adr/0019-worker-pool-assignment.md) (accepted) -- pull-based task claiming.
> - Authoring recipe: see [`docs/adr/0010-mcp-server-authoring.md`](../../docs/adr/0010-mcp-server-authoring.md) -- the Phase-3 canonical recipe extended for the 6th archetype.
> - Gate: see [`docs/adr/0020-phase-6-gate.md`](../../docs/adr/0020-phase-6-gate.md) (accepted) -- this section is the architecture amendment its acceptance criteria require.

**Theme.** The worker-pool + multi-database + Gemini plane -- generalize the worker-wrapper from a single-process worker into a horizontally-scalable pool backed by an optional Postgres database, then introduce a **Gemini adapter** (Google's `gemini` CLI) as the third concrete runtime. Phase 6 adds a **6th archetype: Worker Pool Manager** and a **task-assignment loop** with atomic claim semantics. Every Phase-1 through Phase-5 invariant stands.

### Preserved invariants (Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 carry forward)

All prior invariants stand unchanged. As they apply to the new surface:

- **FR26 single-writer (P2-I1).** Workers do not write persisted state directly. They emit typed events through the FR26 writer path. State transitions are driven exclusively by the FSM module, never by raw DB mutations (P6-I3).
- **MCP transport stdio-only (P2-I4).** Gemini adapter communicates with its CLI subprocess via stdio pipes, the same pattern as `ClaudeCodeRunner` and `CodexRunner`. No new network surfaces.
- **Credential isolation (P5-I1).** `GEMINI_API_KEY` appears ONLY in `GeminiRunner`'s allowlist. `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` remain excluded from the Gemini child env. The per-runtime allowlist discipline is preserved exactly.
- **Budget supervision (P5-I3).** `BudgetSupervisor` continues to invoke the active adapter's `kill()` method. The Gemini adapter implements the same SIGTERM -> grace -> SIGKILL escalation.
- **Supply-chain (Epic 8 + G-SEC-1/2).** The `gemini` binary is a pinned dependency in the base image. The child-env allowlist is expanded for `GEMINI_*` vars, following the explicit-allowlist discipline.

### New invariants (delta from P5-I1..I3)

Phase 6 introduces **five** new discipline rules on top of the preserved set.

| # | Invariant | Why |
|---|---|---|
| **P6-I1** | **Backward compatibility -- SQLite remains default, Postgres is opt-in via `REGISTRY_DATABASE_URL`.** Absent URL = SQLite identically to Phase 5. CI-gate: full suite passes on SQLite without `REGISTRY_DATABASE_URL`. | Breaking the SQLite path would break every existing deployment. Postgres is a production scaling option, not a requirement. |
| **P6-I2** | **Single-task-per-worker -- each worker instance processes one task at a time.** Parallelism via `docker compose up --scale worker-wrapper=N`. Worker claims a task, runs to completion, then polls for next. CI-gate: no worker holds >1 active task handle. | Simplifies execution model. No intra-worker locking. Debugging one-task-per-worker is tractable; N-concurrent-tasks-per-worker is not. |
| **P6-I3** | **Event-driven state transitions -- all state changes via the FSM, no direct DB mutations.** `TaskStateMachine` in `domain/task_fsm.py` is the sole authority. CI-gate: no `UPDATE tasks SET status` or ORM status mutations outside `task_fsm.py`. | Direct DB mutations bypass event logging, audit trails, and transition validation. FSM enforces permitted transitions; bypassing it creates phantom states invisible to metrics. |
| **P6-I4** | **Worker identity -- unique `worker_id` (hostname+PID) stamped on events and metrics.** Generated at startup via `f"{socket.gethostname()}-{os.getpid()}"`. Included in `task.execution.started`, `task.assigned`, `task.completed`. CI-gate: every task-lifecycle event has non-empty `worker_id`. | Multi-worker pools require attribution. Without `worker_id`, debugging a hung worker needs log-correlation heuristics. |
| **P6-I5** | **Credential isolation extends to Gemini -- `GEMINI_API_KEY` injected from settings only.** Appears ONLY in `GeminiRunner`'s allowlist. Absent from Claude and Codex child envs. CI-gate: negative test for all three runtime pairs. | Extends P5-I1 per-runtime-allowlist discipline to a third provider. Google credentials must not leak into Anthropic/OpenAI subprocesses. |

### New archetype: Worker Pool Manager (6th archetype)

The existing five archetypes describe MCP server interaction patterns. Phase 6 adds a sixth:

**Worker Pool Manager archetype:**
- **The orchestrator-adapter gains a task-assignment loop** that polls for `QUEUED` tasks. The loop runs on a configurable interval (`poll_interval_s`, default 2.0). When a `QUEUED` task is found, the worker claims it atomically.
- **Atomic claim uses database-appropriate locking.** Postgres: `SELECT ... FOR UPDATE SKIP LOCKED`. SQLite: `BEGIN EXCLUSIVE` transaction. Both guarantee that exactly one worker claims a given task, even under concurrent polling.
- **Docker Compose scaling.** `docker compose up --scale worker-wrapper=N` launches N independent worker processes. Each runs the same task-assignment loop. No leader election, no shared state between workers beyond the database.
- **Task lifecycle per worker.** Claim -> transition to ASSIGNED -> spawn runtime adapter -> transition to RUNNING -> stream events -> transition to COMPLETED/FAILED -> poll for next task.

### Database topology change

Phase 6 introduces a dual-database topology while preserving SQLite as the zero-config default.

**SQLite (default, local dev):**
- Single file, single-writer via `BEGIN EXCLUSIVE`.
- No connection pool needed (file-level locking).
- Full test suite must pass on SQLite without `REGISTRY_DATABASE_URL`.

**Postgres (opt-in, production scaling):**
- Container-based, configured via `REGISTRY_DATABASE_URL`.
- Connection pooling via `psycopg_pool.AsyncConnectionPool` (max 10 connections per worker).
- Concurrent readers + single-writer-per-table (SKIP LOCKED for task claims).
- Both must pass the full test suite.

**Migration tooling:**
- Alembic for schema migrations, backend-agnostic.
- Migrations are written once, run against both SQLite and Postgres in CI.
- `alembic upgrade head` is a pre-start step in the worker entrypoint.

**Configuration:**

```python
# services/worker-wrapper/src/worker_wrapper/app/config.py

# Phase 6 -- database topology. Absent = SQLite (backward compat, P6-I1).
database_url: str = ""  # REGISTRY_DATABASE_URL
pool_max: int = 10      # Postgres connection pool size (ignored for SQLite)
```

### State machine module

The FSM is the single authority for task state transitions (P6-I3).

**Module location:** `services/worker-wrapper/src/worker_wrapper/domain/task_fsm.py`

```python
# domain/task_fsm.py

class TaskStateMachine:
    """Finite state machine for task lifecycle (P6-I3).

    Sole authority for task state transitions. No service may mutate
    task.status directly -- all transitions go through this class.
    """

    STATES: frozenset[str] = frozenset({
        "CREATED", "QUEUED", "ASSIGNED", "RUNNING",
        "COMPLETED", "FAILED", "CANCELLED",
    })

    TRANSITIONS: dict[str, frozenset[str]] = {
        "CREATED":   frozenset({"QUEUED", "CANCELLED"}),
        "QUEUED":    frozenset({"ASSIGNED", "CANCELLED"}),
        "ASSIGNED":  frozenset({"RUNNING", "FAILED", "CANCELLED"}),
        "RUNNING":   frozenset({"COMPLETED", "FAILED", "CANCELLED"}),
        "COMPLETED": frozenset(),  # terminal
        "FAILED":    frozenset(),  # terminal
        "CANCELLED": frozenset(),  # terminal
    }

    def transition(self, current: str, target: str) -> str:
        if target not in self.STATES:
            raise InvalidStateTransition(f"Unknown state: {target!r}")
        if target not in self.TRANSITIONS.get(current, frozenset()):
            raise InvalidStateTransition(
                f"Cannot transition from {current!r} to {target!r}"
            )
        return target  # caller persists + emits event
```

**8 permitted transitions:** CREATED->QUEUED, CREATED->CANCELLED, QUEUED->ASSIGNED, QUEUED->CANCELLED, ASSIGNED->RUNNING, ASSIGNED->FAILED, ASSIGNED->CANCELLED, RUNNING->COMPLETED, RUNNING->FAILED, RUNNING->CANCELLED. Three terminal states: COMPLETED, FAILED, CANCELLED.

**InvalidStateTransition exception:**

```python
class InvalidStateTransition(Exception):
    """Raised when a task state transition violates the FSM rules."""
```

### Gemini adapter

Following ADR-0010 step-9 + ADR-0015 recipe (the Phase-5 authoring recipe extended for runtime adapters).

**Module:** `services/worker-wrapper/src/worker_wrapper/adapters/gemini_runner.py`

**Gemini CLI specifics mapped to the adapter:**

| Adapter method | Gemini CLI invocation |
|---|---|
| `spawn()` | `gemini run --json "<prompt>"` with `cwd` set to the worktree. `--json` produces structured JSONL output (P5-I2). |
| `runtime_name` | Returns `"gemini"`. |
| `is_healthy()` | Checks `self._process.returncode is None`. |
| `parse_output()` | Deserializes each JSONL line. Extracts events from structured response objects. Token usage extracted from `usageMetadata` fields. |
| `kill()` | SIGTERM -> 5s grace -> SIGKILL. Mirrors existing adapter semantics exactly (P5-I3). |

**Gemini allowlist (P6-I5):**

```python
_GEMINI_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER",
    "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TMP", "TEMP",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})
_GEMINI_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "GEMINI_")
# GEMINI_API_KEY injected separately in _spawn(), NOT in the allowlist.
```

**SUPPORTED_RUNTIMES expansion:**

```python
# Phase 5: {"claude-code", "codex"}
# Phase 6: {"claude-code", "codex", "gemini"}
```

**Factory update:** `get_runtime_adapter()` gains a `gemini` branch returning `GeminiRunner(settings)`.

### New separability tests

| Test | What it asserts |
|---|---|
| **S-12** | Postgres optional: `REGISTRY_DATABASE_URL` absent -> system uses SQLite. Full lifecycle (create, queue, assign, run, complete) passes on SQLite without Postgres installed. |
| **S-13** | Gemini optional: `WORKER_GEMINI_COMMAND` blank -> `GeminiRunner.installed` returns `False`. Worker falls back to the configured default runtime (claude-code). No import error, no crash. |

### Per-epic wiring decisions

**Epic 30 -- Task FSM.** `domain/task_fsm.py` (FSM class + `InvalidStateTransition`). Migrate all `task.status = ...` to `fsm.transition()`. FSM is pure (no DB dependency) -- caller persists and emits events. Terminal states (COMPLETED/FAILED/CANCELLED) have empty transition sets.

**Epic 31 -- Worker pool manager.** Task-assignment loop in orchestrator-adapter. Poll-based (no message broker). Atomic claim: Postgres uses `SKIP LOCKED`, SQLite uses `BEGIN EXCLUSIVE`, both behind `claim_task(task_id, worker_id)`.

**Epic 32 -- Postgres support.** Backend-agnostic `TaskRepository` protocol with `SqliteTaskRepository` and `PostgresTaskRepository` concrete classes. Alembic migrations, single chain, tested against both backends.

**Epic 33 -- Gemini adapter.** `adapters/gemini_runner.py` following ADR-0010 step-9 + ADR-0015 recipe. Third `RuntimeAdapter` implementation. P6-I5 credential isolation enforced.

### Forward-referenced ADRs (proposed; each gates its epic)

Each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges.

- **ADR-0017** -- Postgres migration strategy (dual-backend, Alembic, connection pooling). **Gates Epic 30.** `docs/adr/0017-postgres-migration.md`.
- **ADR-0018** -- Task state machine (FSM, transitions, InvalidStateTransition). **Gates Epic 31.** `docs/adr/0018-task-state-machine.md`.
- **ADR-0019** -- Worker pool assignment (pull-based claiming, SKIP LOCKED). **Gates Epic 32.** `docs/adr/0019-worker-pool-assignment.md`.
- **ADR-0020** -- Phase 6 gate (opens Phase 6, lists acceptance criteria). **Gates Phase 6.** `docs/adr/0020-phase-6-gate.md`.

### Phase 6 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 30:** `TaskStateMachine` class in `domain/task_fsm.py`; all 8 permitted transitions validated; `InvalidStateTransition` raised for invalid transitions; no `task.status = ...` outside `task_fsm.py`; terminal states reject all transitions.
- **Epic 31:** Task-assignment loop polls for QUEUED tasks; atomic claim with `worker_id`; `docker compose up --scale worker-wrapper=3` smoke test passes; `worker_id` stamped on task-lifecycle events (P6-I4).
- **Epic 32:** `REGISTRY_DATABASE_URL` absent -> SQLite path passes full suite (P6-I1); `REGISTRY_DATABASE_URL` present -> Postgres path passes full suite; Alembic migrations run against both backends; connection pooling configured for Postgres; repository abstraction hides backend differences.
- **Epic 33:** `GeminiRunner` satisfies `RuntimeAdapter` (contract test); P6-I5 credential isolation negative test (no cross-runtime key leakage among all three runtimes); `parse_output()` structured JSON only (P5-I2); `kill()` semantics match existing adapters (P5-I3); S-13 separability test: `WORKER_GEMINI_COMMAND` blank -> graceful fallback.

### Acceptance checklist (for ADR-0018 gate)

- [ ] Architecture amendment (this section) accepted; P6-I1 through P6-I5 invariants explicitly stated.
- [ ] ADR-0017 (`docs/adr/0017-postgres-migration.md`) accepted -- Postgres migration strategy.
- [ ] ADR-0018 (`docs/adr/0018-task-state-machine.md`) accepted -- Task state machine.
- [ ] ADR-0019 (`docs/adr/0019-worker-pool-assignment.md`) accepted -- Worker pool assignment.
- [ ] ADR-0020 (`docs/adr/0020-phase-6-gate.md`) accepted -- Phase 6 gate.
- [ ] `bmad-create-epics-and-stories` has decomposed the scope into Epics 30-34 stories.
- [ ] Each Phase 6 epic has its `phase: 6` label set in `sprint-status.yaml`.
- [ ] `deferred-work.md` reviewed; items superseded by Phase 6 marked accordingly.
- [ ] `gemini` binary pinned in `Dockerfile.base` with verified checksum.

-- *Amendment by R2d2, 2026-06-07, via the BMad `bmad-create-architecture` workflow (amendment mode).*
