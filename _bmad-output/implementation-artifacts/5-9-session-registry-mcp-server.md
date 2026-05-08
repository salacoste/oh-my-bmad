# Story 5.9: session-registry MCP server (read + bounded-write)

Status: done

## Story

As the worker,
I want `mcp-servers/session-registry/` exposing `active sessions`, `worker metadata`, `heartbeats` as resources and `session.heartbeat`, `session.register`, `session.close` as tools,
So that the worker lifecycle has a structured surface.

## Acceptance Criteria

1. **AC-1: Read-only resources** — 3 MCP resources registered via `@mcp.resource()`:
   - `session/active` — sessions with `status="active"`, ordered by `started_at` desc
   - `session/detail/{session_id}` — single session by ID, returns materialized state from SQLite
   - `session/heartbeats` — sessions with `last_heartbeat_at` within configurable window (stale-heartbeat surface)
   Each resource returns JSON text. Missing session returns `""` (not an error).

2. **AC-2: Bounded-write tools** — 3 MCP tools registered via `@mcp.tool()`:
   - `session.register(task_id, worker_kind, worktree_path)` — registers a new session; Tier-1 minimum
   - `session.heartbeat(session_id)` — updates session heartbeat timestamp; Tier-1 minimum
   - `session.close(session_id)` — marks session as finished; Tier-1 minimum
   Each tool calls `_check_tier()` at entry and raises `PermissionError` on deny. Tools return `{"ok": true}` or structured error.

3. **AC-3: Capability-tier enforcement** — `_check_tier()` is a NO-OP placeholder returning `True` (same pattern as task-registry post-review). Defined once in `handlers/tools.py`. Full Tier 0/1/2/3 enforcement lands in Story 6.1-6.3.

4. **AC-4: Read-only SQLite connection** — Server uses `create_engine(db_url, read_only=True)` from `registry_state.adapters.sqlite_store`. OS-level write protection via SQLite URI `mode=ro`.

5. **AC-5: Factory pattern** — `build_server(*, db_path, actor_kind, actor_id, _session_maker=None) -> FastMCP` synchronous factory (same as task-registry). Engine lifecycle via `atexit.register(engine.sync_engine.dispose)`.

6. **AC-6: Entrypoint** — `__main__.py` reads env vars (`SESSION_REGISTRY_DB_PATH`, `SESSION_REGISTRY_ACTOR_KIND`, `SESSION_REGISTRY_ACTOR_ID`), validates required vars, builds server, calls `mcp.run()`. Exit code 2 on missing/invalid vars. Literal type narrowing for `actor_kind`.

7. **AC-7: Import discipline** — `mcp-servers/session-registry` imports from `packages/*` and `services/registry-state` only (same `# noqa: IMP001` exception per architecture line 339). No cross-mcp-server imports. `scripts/check_imports.py` exits 0.

8. **AC-8: Directory structure** — Files at `mcp-servers/session-registry/src/session_registry_mcp/`:
   - `app/main.py` — `build_server()` factory
   - `handlers/resources.py` — 3 resource handler functions
   - `handlers/tools.py` — 3 tool handler functions + `_check_tier()` placeholder
   - `__main__.py` — entrypoint
   - `__init__.py` — update to export `build_server`

9. **AC-9: Tests** — At least 15 tests in `test_server.py` (co-located): server construction (verifies 3 resources + 3 tools registered), resource reads (active sessions, session detail, heartbeats, missing session returns empty), tool execution (register, heartbeat, close), tier-check placeholder, entrypoint env-var validation. Tests use in-memory SQLite with pre-seeded data.

10. **AC-10: Dependencies** — `pyproject.toml` updated with: `mcp>=1.0`, `events>=0.3.0`, `registry-state>=0.5.0`, `pydantic>=2.8`. `uv sync` succeeds.

11. **AC-11: `just lint` 9/9 green** — All lint gates pass including `mypy --strict`.

12. **AC-12: `just test` no regressions** — Existing test count unchanged. New tests increase count.

13. **AC-13: Atomic commit** — title: `feat(session-registry): add MCP server with session resources and lifecycle tools · E5`

## Tasks / Subtasks

- [x] **Task 1: Update dependencies** (AC: #10)
  - [x] Add `mcp>=1.0`, `events>=0.3.0`, `registry-state>=0.5.0`, `pydantic>=2.8` to `mcp-servers/session-registry/pyproject.toml`
  - [x] Run `uv sync` to verify resolution

- [x] **Task 2: Create directory structure** (AC: #8)
  - [x] Create `src/session_registry_mcp/app/` directory
  - [x] Create `src/session_registry_mcp/app/__init__.py` (empty)
  - [x] Create `src/session_registry_mcp/handlers/` directory
  - [x] Create `src/session_registry_mcp/handlers/__init__.py` (empty)
  - [x] Create `src/session_registry_mcp/handlers/resources.py`
  - [x] Create `src/session_registry_mcp/handlers/tools.py`

- [x] **Task 3: Implement `build_server()` factory** (AC: #3, #4, #5)
  - [x] In `app/main.py`, create `build_server(*, db_path: str, actor_kind: str, actor_id: str, _session_maker=None) -> FastMCP`
  - [x] Create read-only engine: `create_engine(db_url, read_only=True)` + `atexit.register(engine.sync_engine.dispose)`
  - [x] Guard empty `db_path` with `ValueError`
  - [x] Create session maker: `get_session(engine)`
  - [x] Register 3 resource handlers from `handlers/resources.py`
  - [x] Register 3 tool handlers from `handlers/tools.py`

- [x] **Task 4: Implement resource handlers** (AC: #1, #4)
  - [x] `session_active(session_maker) -> str` — `select(Session).where(Session.status == "active").order_by(desc(Session.started_at))`, return JSON
  - [x] `session_detail(session_maker, session_id) -> str` — `select(Session).where(Session.id == session_id)`, return JSON or `""`
  - [x] `session_heartbeats(session_maker) -> str` — sessions where `last_heartbeat_at` is not None, ordered by `last_heartbeat_at` desc, return JSON

- [x] **Task 5: Implement tool handlers** (AC: #2, #3)
  - [x] `session_register(session_maker, task_id, worker_kind, worktree_path)` — validate task exists, return `{"ok": True}`
  - [x] `session_heartbeat(session_maker, session_id)` — validate session exists, return `{"ok": True}`
  - [x] `session_close(session_maker, session_id)` — validate session exists, return `{"ok": True}`
  - [x] Define `_check_tier()` in `handlers/tools.py` only (single location)
  - [x] Each tool calls `_check_tier(actor_kind, tool_name)` at entry
  - [x] Return `{"ok": true}` or raise/return error

- [x] **Task 6: Implement `__main__.py` entrypoint** (AC: #6)
  - [x] Read `SESSION_REGISTRY_DB_PATH` (required)
  - [x] Read `SESSION_REGISTRY_ACTOR_KIND` (required, validate against allowed set with Literal narrowing)
  - [x] Read `SESSION_REGISTRY_ACTOR_ID` (required, non-empty)
  - [x] Build server via `build_server()`, call `mcp.run()`
  - [x] Exit code 2 on missing/invalid vars

- [x] **Task 7: Update `__init__.py`** (AC: #8)
  - [x] Export `build_server` from `session_registry_mcp`
  - [x] Update version to `"0.2.0"`

- [x] **Task 8: Write tests** (AC: #9)
  - [x] Create `test_server.py` in `src/session_registry_mcp/`
  - [x] In-memory SQLite fixture with seed data (sessions, tasks for FK)
  - [x] Test: server construction — 3 resources + 3 tools registered
  - [x] Test: no mutation keywords in tool names
  - [x] Test: session_active returns active sessions only
  - [x] Test: session_detail returns specific session
  - [x] Test: session_detail returns empty for missing ID
  - [x] Test: session_heartbeats returns sessions with heartbeat timestamps
  - [x] Test: session_active empty when no active sessions
  - [x] Test: session_register succeeds for valid task
  - [x] Test: session_register rejects missing task
  - [x] Test: session_register rejects empty params
  - [x] Test: session_heartbeat succeeds for valid session
  - [x] Test: session_heartbeat rejects missing session
  - [x] Test: session_close succeeds for valid session
  - [x] Test: session_close rejects missing session
  - [x] Test: Tier placeholder returns True + patched deny raises PermissionError
  - [x] Test: entrypoint exits 2 on missing/invalid env vars

- [x] **Task 9: Verification + commit** (AC: #7, #11, #12, #13)
  - [x] `ruff check` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` — no regressions
  - [ ] Atomic commit

## Dev Notes

### What already exists

The `mcp-servers/session-registry/` directory has a scaffold only:
- `pyproject.toml` — name `session-registry-mcp` v0.1.0, empty dependencies, description already mentions lifecycle tools
- `src/session_registry_mcp/__init__.py` — stub with `__version__ = "0.1.0"`, docstring references Story 5.9
- No `app/`, `handlers/`, `__main__.py`, or test files

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Server factory | `build_server(*, ...) -> FastMCP` | task-registry `app/main.py` (Story 5.8 post-review) |
| Transport | stdio (Phase 1) | architecture.md line 55 |
| Import rule | `mcp-servers/*` → `packages/*` + `registry-state` | architecture.md line 339 |
| Tier enforcement | `_check_tier()` NO-OP in `handlers/tools.py` only | task-registry post-review fix |
| Read path | SQLAlchemy `select()` against ORM models | registry-api pattern |
| DB connection | `create_engine(url, read_only=True)` + `atexit.dispose` | task-registry post-review fix |
| Engine guard | `ValueError` on empty `db_path` | task-registry post-review fix |
| Bounded-write tools | Phase 1 validated stubs | task-registry pattern (option c) |

### Import-graph rules

| Import | Allowed? | Notes |
|---|---|---|
| `mcp.server.fastmcp` | ALLOWED | FastMCP SDK for server |
| `events` (workspace) | ALLOWED | `packages/events` — payloads, IDs, clock |
| `registry_state` | ALLOWED | `# noqa: IMP001` — same exception per AC-7 |
| `sqlalchemy` | ALLOWED | async ORM queries — transitive from registry-state |
| `pydantic` | ALLOWED | model validation |
| `stdlib` (os, sys, json, logging, atexit) | ALLOWED | env vars, serialization, logging |
| Other `mcp-servers/*` | **FORBIDDEN** | cross-mcp-server import ban |
| Other `services/*` | **FORBIDDEN** | except `registry-state` per exception |

### Session ORM model (from `registry_state.schema`)

```python
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str]              # String(38), PK, format: s-<uuidv7>
    task_id: Mapped[str]         # String(38), FK -> tasks.id (RESTRICT)
    worker_kind: Mapped[str]     # String(32)
    worktree_path: Mapped[str | None]  # Text, nullable
    status: Mapped[str]          # String(32) — "active" or "finished" (application-enforced)
    started_at: Mapped[datetime]       # UTCDateTime
    ended_at: Mapped[datetime | None]  # UTCDateTime, nullable
    last_heartbeat_at: Mapped[datetime | None]  # UTCDateTime, nullable
```

Index: `ix_sessions_task_id` on `(task_id)`.

### Session status values

- `"active"` — set on initial session creation (materializer handler sets this)
- `"finished"` — set when session is closed
- Statuses are application-enforced, not CHECK constraints

### Registered session event types (from `events.schema_registry`)

| Event Type | Payload Model | Notes |
|---|---|---|
| `session.started` | `SessionStartedPayload(session_id, worker_id, task_id?)` | v1.0.0, v1.0.1 |
| `session.heartbeat` | `SessionHeartbeatPayload(session_id)` | v1.0.0, v1.0.1 |
| `session.finished` | `SessionFinishedPayload(session_id)` | v1.0.0, v1.0.1 |
| `session.heartbeat_timeout` | `SessionHeartbeatTimeoutPayload(session_id, task_id, last_heartbeat_at, timeout_threshold_s)` | v1.0.0, v1.0.1 |

### SQLite read-only query patterns

```python
# session/active — active sessions, newest first
select(Session).where(Session.status == "active").order_by(desc(Session.started_at))

# session/detail/{session_id} — single session
select(Session).where(Session.id == session_id)

# session/heartbeats — sessions with heartbeat timestamps
select(Session).where(Session.last_heartbeat_at.isnot(None)).order_by(desc(Session.last_heartbeat_at))
```

### Resource URI templates

| Resource | URI Template | Type |
|---|---|---|
| Active sessions | `session://active` | Static |
| Session detail | `session://detail/{session_id}` | Template |
| Heartbeats | `session://heartbeats` | Static |

### Bounded-write tools — implementation strategy

Phase 1 validated stubs (same as task-registry option c):
- Check tier via `_check_tier()` (single definition in `handlers/tools.py`)
- Validate parameters (non-empty check)
- Validate task/session existence via `_validate_task_exists()` / `_validate_session_exists()`
- Return `{"ok": True}` on success
- Log at `info` level with `(stub)` suffix
- Actual persistence routes through clawhip-bridge event spine — deferred to Story 5.12

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `SESSION_REGISTRY_DB_PATH` | Yes | Absolute path to the SQLite database file |
| `SESSION_REGISTRY_ACTOR_KIND` | Yes | One of `operator`, `orchestrator`, `worker`, `system` |
| `SESSION_REGISTRY_ACTOR_ID` | Yes | Non-empty string identifying the actor instance |

### Downstream consumers

- **Story 5.1** (worker-wrapper scaffold) — connects to session-registry MCP server via stdio
- **Story 5.2** (session lifecycle emission) — emits session.started, heartbeat, finished via tools
- **Story 5.12** (task execution driver) — reads active sessions, manages heartbeat cadence
- **Story 6.1-6.3** (capability tier enforcement) — replaces `_check_tier()` NO-OP with real enforcement

### Key patterns from task-registry (reference — Story 5.8 + code review fixes)

1. **`build_server()` factory**: Synchronous, keyword-only args. `_session_maker` override for testing. `atexit.register(engine.sync_engine.dispose)` for cleanup. `ValueError` guard on empty `db_path`.

2. **`_check_tier()`**: Single definition in `handlers/tools.py` (NOT duplicated in main.py). Returns `True`, logs at debug level. Test uses `unittest.mock.patch` (not manual monkeypatch).

3. **Resource queries**: Use `select()` + subqueries (not JOINs). Add `.where(Event.task_id.isnot(None))` for nullable FK columns. No `.correlate()` on non-correlated subqueries.

4. **`__main__.py`**: if/elif chain for `actor_kind` (needed for `mypy --strict` Literal narrowing, NOT a set lookup). `from typing import Literal`. Exit code 2 on all validation failures.

5. **Tests**: `@pytest_asyncio.fixture`, `StaticPool` for in-memory SQLite, `expire_on_commit=False`. FK pragmas via `event.listens_for`. Seed parent entities first (Tasks before Sessions for FK). Test internal APIs via `mcp._resource_manager._resources[uri]` and `mcp._tool_manager._tools[name].fn`.

6. **`__init__.py`**: Exports `build_server`, bumps version to `"0.2.0"`.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1567-1579 — Story 5.9 definition]
- [Source: `_bmad-output/planning-artifacts/epics.md` line 1426 — implementation ordering note]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 719-725 — session-registry directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 339 — import rules for mcp-servers]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 780, 792 — read-only SQLite connection]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 800 — read vs write path separation]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 266 — MCP servers as read-only surfaces]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 65 — capability-tier enforcement]
- [Source: `services/registry-state/src/registry_state/schema.py` lines 120-135 — Session ORM model]
- [Source: `services/registry-state/src/registry_state/domain/event_types.py` — session event types]
- [Source: `packages/events/src/events/payloads.py` — session payload models]
- [Source: `mcp-servers/task-registry/` — reference implementation (Story 5.8 + code review fixes)]
