# Story 5.8: task-registry MCP server (read surfaces)

Status: review

## Story

As orchestrator and worker,
I want `mcp-servers/task-registry/` exposing `task list`, `task detail`, `approval queue`, `blockers` as read-only resources and `task.add_note`, `task.attach_artifact`, `task.emit_event` as bounded-write tools,
So that agents have a structured read-only view of task state plus a narrow write surface scoped by capability tier.

## Acceptance Criteria

1. **AC-1: Read-only resources** — 4 MCP resources registered via `@mcp.resource()`:
   - `task/list` — all tasks ordered by `updated_at` desc
   - `task/detail/{id}` — single task by ID, returns materialized state from SQLite
   - `task/approval-queue` — tasks with `task.approval_requested` events
   - `task/blockers` — tasks with `task.blocker_raised` events
   Each resource returns JSON text. Missing task returns `""` (not an error).

2. **AC-2: Bounded-write tools** — 3 MCP tools registered via `@mcp.tool()`:
   - `task.add_note(task_id, note)` — appends a note; Tier-1 minimum
   - `task.attach_artifact(task_id, artifact_url, artifact_type)` — attaches artifact; Tier-1 minimum
   - `task.emit_event(task_id, event_type, payload)` — bounded event emission; Tier-1 minimum
   Each tool calls `_check_tier()` at entry and raises `PermissionError` on deny. Tools return `{"ok": true}` or structured error.

3. **AC-3: Capability-tier enforcement** — `_check_tier()` is a NO-OP placeholder returning `True` (same pattern as clawhip-bridge). Full Tier 0/1/2/3 enforcement lands in Story 6.1-6.3. Every tool and resource handler calls it so the structure is correct for future replacement.

4. **AC-4: Read-only SQLite connection** — Server uses `create_engine(db_url, read_only=True)` from `registry_state.adapters.sqlite_store`. OS-level write protection via SQLite URI `mode=ro`. WAL mode allows concurrent reads alongside the single writer (subscriber).

5. **AC-5: Factory pattern** — `build_server(*, db_url, actor_kind, actor_id) -> FastMCP` synchronous factory (same as clawhip-bridge). Accepts injected `db_url` string and actor identity. Returns a `FastMCP` instance ready for `mcp.run()` on stdio.

6. **AC-6: Entrypoint** — `__main__.py` reads env vars (`TASK_REGISTRY_DB_PATH`, `TASK_REGISTRY_ACTOR_KIND`, `TASK_REGISTRY_ACTOR_ID`), validates required vars, builds server, calls `mcp.run()`. Exit code 2 on missing/invalid vars (same as clawhip-bridge pattern).

7. **AC-7: Import discipline** — `mcp-servers/task-registry` imports from `packages/*` and `services/registry-state` only (same `# noqa: IMP001` exception as clawhip-bridge per architecture line 272). No cross-mcp-server imports, no other service imports. `scripts/check_imports.py` exits 0.

8. **AC-8: Directory structure** — Files at `mcp-servers/task-registry/src/task_registry_mcp/`:
   - `app/main.py` — `build_server()` factory
   - `handlers/resources.py` — 4 resource handler functions
   - `handlers/tools.py` — 3 tool handler functions + `_check_tier()` placeholder
   - `__main__.py` — entrypoint
   - `__init__.py` — update to export `build_server`

9. **AC-9: Tests** — At least 15 tests in `test_server.py` (co-located): server construction (verifies 4 resources + 3 tools registered), resource reads (task list, task detail, approval queue, blockers, missing task returns empty), tool execution (add_note, attach_artifact, emit_event), tier-check placeholder (Tier-0 client rejected), entrypoint env-var validation. Tests use in-memory SQLite with pre-seeded data (no live registry).

10. **AC-10: Dependencies** — `pyproject.toml` updated with: `mcp>=1.0`, `events>=0.3.0`, `registry-state>=0.5.0`, `pydantic>=2.8`. `uv sync` succeeds.

11. **AC-11: `just lint` 9/9 green** — All lint gates pass including `mypy --strict`.

12. **AC-12: `just test` no regressions** — Existing test count unchanged. New tests increase count.

13. **AC-13: Atomic commit** — title: `feat(task-registry): add MCP server with read resources and bounded-write tools · E5`

## Tasks / Subtasks

- [x] **Task 1: Update dependencies** (AC: #10)
  - [x] Add `mcp>=1.0`, `events>=0.3.0`, `registry-state>=0.5.0`, `pydantic>=2.8` to `mcp-servers/task-registry/pyproject.toml`
  - [x] Run `uv sync` to verify resolution

- [x] **Task 2: Create directory structure** (AC: #8)
  - [x] Create `src/task_registry_mcp/app/` directory
  - [x] Create `src/task_registry_mcp/app/__init__.py` (empty or with import)
  - [x] Create `src/task_registry_mcp/handlers/` directory
  - [x] Create `src/task_registry_mcp/handlers/__init__.py` (empty)
  - [x] Create `src/task_registry_mcp/handlers/resources.py`
  - [x] Create `src/task_registry_mcp/handlers/tools.py`

- [x] **Task 3: Implement `build_server()` factory** (AC: #1, #3, #5)
  - [x] In `app/main.py`, create `build_server(*, db_url: str, actor_kind: str, actor_id: str) -> FastMCP`
  - [x] Create read-only engine: `create_engine(db_url, read_only=True)`
  - [x] Create session maker: `get_session(engine)`
  - [x] Register 4 resource handlers from `handlers/resources.py`
  - [x] Register 3 tool handlers from `handlers/tools.py`
  - [x] Add `_check_tier()` NO-OP placeholder (same as clawhip-bridge)

- [x] **Task 4: Implement resource handlers** (AC: #1, #4)
  - [x] `task_list(session_maker) -> str` — `select(Task).order_by(Task.updated_at.desc())`, return JSON
  - [x] `task_detail(session_maker, task_id) -> str` — `select(Task).where(Task.id == task_id)`, return JSON or `""`
  - [x] `approval_queue(session_maker) -> str` — join `Task` + `Event`, filter `type == "task.approval_requested"`, return JSON
  - [x] `blockers(session_maker) -> str` — join `Task` + `Event`, filter `type == "task.blocker_raised"`, return JSON
  - [x] Each handler: open async session, execute query, serialize to JSON string, close session

- [x] **Task 5: Implement tool handlers** (AC: #2, #3)
  - [x] `task_add_note(session_maker, task_id, note)` — validate task exists, emit `task.note_added` via event log or inline storage
  - [x] `task_attach_artifact(session_maker, task_id, artifact_url, artifact_type)` — validate task exists, store artifact reference
  - [x] `task_emit_event(session_maker, task_id, event_type, payload)` — bounded event emission
  - [x] Each tool calls `_check_tier(actor_kind, tool_name)` at entry
  - [x] Return `{"ok": true, ...}` or raise on failure

- [x] **Task 6: Implement `__main__.py` entrypoint** (AC: #6)
  - [x] Read `TASK_REGISTRY_DB_PATH` (required, construct `sqlite+aiosqlite:///` URL)
  - [x] Read `TASK_REGISTRY_ACTOR_KIND` (required, validate against allowed set)
  - [x] Read `TASK_REGISTRY_ACTOR_ID` (required, non-empty)
  - [x] Build server via `build_server()`, call `mcp.run()`
  - [x] Exit code 2 on missing/invalid vars

- [x] **Task 7: Update `__init__.py`** (AC: #8)
  - [x] Export `build_server` from `task_registry_mcp`
  - [x] Update version to `"0.2.0"`

- [x] **Task 8: Write tests** (AC: #9)
  - [x] Create `test_server.py` in `src/task_registry_mcp/`
  - [x] In-memory SQLite fixture with seed data (tasks, events)
  - [x] Test: server construction — 4 resources + 3 tools registered
  - [x] Test: no mutation keywords in resource names
  - [x] Test: task_list returns seeded tasks
  - [x] Test: task_detail returns specific task
  - [x] Test: task_detail returns empty for missing ID
  - [x] Test: approval_queue filters by `task.approval_requested` event type
  - [x] Test: blockers filters by `task.blocker_raised` event type
  - [x] Test: task_add_note succeeds for valid task
  - [x] Test: task_attach_artifact succeeds for valid task
  - [x] Test: task_emit_event succeeds for valid task
  - [x] Test: Tier-0 client rejected on bounded-write tool
  - [x] Test: entrypoint exits 2 on missing env vars

- [x] **Task 9: Verification + commit** (AC: #7, #11, #12, #13)
  - [x] `mypy --strict` clean on all modified files
  - [x] `ruff check` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` — no regressions
  - [ ] Atomic commit

## Dev Notes

### What already exists

The `mcp-servers/task-registry/` directory has a scaffold only:
- `pyproject.toml` — name `task-registry-mcp` v0.1.0, zero dependencies
- `src/task_registry_mcp/__init__.py` — stub with `__version__ = "0.1.0"`
- No `app/`, `handlers/`, `__main__.py`, or test files

The reference implementation is **clawhip-bridge** at `mcp-servers/clawhip-bridge/`:
- `server.py` — `build_server()` factory with `FastMCP`, `@mcp.tool()`, `@mcp.resource()` decorators
- `__main__.py` — env-var validation + `mcp.run()` on stdio
- `test_server.py` — comprehensive tests with inlined fixtures

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Server factory | `build_server(*, ...) -> FastMCP` | clawhip-bridge `server.py` |
| Transport | stdio (Phase 1) | architecture.md line 55 |
| Import rule | `mcp-servers/*` → `packages/*` + `registry-state` | architecture.md line 339 |
| Tier enforcement | `_check_tier()` NO-OP placeholder | clawhip-bridge `server.py` line 62 |
| Read path | SQLAlchemy `select()` against ORM models | registry-api pattern |
| DB connection | `create_engine(url, read_only=True)` | registry-state `sqlite_store.py` |

### Import-graph rules

| Import | Allowed? | Notes |
|---|---|---|
| `mcp.server.fastmcp` | ALLOWED | FastMCP SDK for server |
| `events` (workspace) | ALLOWED | `packages/events` — payloads, IDs, clock |
| `registry_state` | ALLOWED | `# noqa: IMP001` — same exception as clawhip-bridge per AC-7 |
| `sqlalchemy` | ALLOWED | async ORM queries — transitive from registry-state |
| `pydantic` | ALLOWED | model validation |
| `stdlib` (os, sys, json, logging) | ALLOWED | env vars, serialization, logging |
| Other `mcp-servers/*` | **FORBIDDEN** | cross-mcp-server import ban |
| Other `services/*` | **FORBIDDEN** | except `registry-state` per exception |
| Domain modules in worker-wrapper | **FORBIDDEN** | cross-service import ban |

### SQLite read-only query patterns

The task-registry MCP server reads from the materialized SQLite state. There are NO pre-built query helpers — build queries using SQLAlchemy `select()` against ORM models from `registry_state.schema`.

**ORM models (from `registry_state.schema`):**
- `Task` — columns: `id`, `status`, `created_at`, `updated_at`, `actor_kind`, `actor_id`, `title`, `last_event_id`, `chat_id`, `reply_to_message_id`
- `Event` — columns: `id`, `type`, `schema_version`, `emitted_at`, `emitted_at_monotonic_ns`, `actor_kind`, `actor_id`, `task_id`, `session_id`, `parent_event_id`, `request_id`, `payload_json`
- `Session` — columns: `id`, `task_id`, `worker_kind`, `worktree_path`, `status`, `started_at`, `ended_at`, `last_heartbeat_at`

**Task status values in schema:** `"pending"`, `"planning"`, `"plan_ready"`, `"executing"`, `"completed"`. Note: FR8 lists additional lifecycle states (`awaiting_approval`, `verifying`, `blocked`, `failed`, `stopped`) but the current materialized schema uses a subset. Blockers and approvals are identified via `Event.type` joins, not via task status.

**Key indexes (pre-built, no new indexes needed):**
- `ix_tasks_status_updated_at` on `(status, updated_at)` — task listing
- `ix_events_task_id_emitted_at` on `(task_id, emitted_at)` — task event history
- `ix_events_type_emitted_at` on `(type, emitted_at)` — event type filtering
- `ix_sessions_task_id` on `(task_id)` — sessions per task

**Resource query implementations:**

```python
# task/list — all tasks, newest first
select(Task).order_by(Task.updated_at.desc())

# task/detail/{id} — single task
select(Task).where(Task.id == task_id)

# task/approval-queue — tasks with approval_requested events
select(Task).join(Event, Event.task_id == Task.id)
    .where(Event.type == "task.approval_requested")
    .order_by(Task.updated_at.asc())

# task/blockers — tasks with blocker_raised events
select(Task).join(Event, Event.task_id == Task.id)
    .where(Event.type == "task.blocker_raised")
    .order_by(Task.updated_at.desc())
```

**Read-only connection setup:**

```python
from registry_state.adapters.sqlite_store import create_engine, get_session

engine = create_engine(f"sqlite+aiosqlite:///{db_path}", read_only=True)
session_maker = get_session(engine)

# Per-query:
async with session_maker() as session:
    result = await session.execute(select(Task).where(...))
```

### Bounded-write tools — implementation strategy

The 3 bounded-write tools (`task.add_note`, `task.attach_artifact`, `task.emit_event`) are scoped writes. They do NOT go through the event spine (clawhip-bridge is the sole mutation path per FR26). Instead, they write directly to a bounded auxiliary table or emit through clawhip-bridge.

**Recommended approach for Phase 1:** These bounded-write tools should forward their writes through the clawhip-bridge MCP server's `emit_event` tool. However, since task-registry cannot import from clawhip-bridge (import ban), the tools should either:
- (a) Write directly to the SQLite DB via a separate write-capable connection (bypassing FR26 single-writer), or
- (b) Emit structured events that get processed by the subscriber — this is the architecturally correct approach but requires the tool to call clawhip-bridge as a client, or
- (c) For Phase 1, implement the tools as stubs that validate input and return success but log a warning that full write-through is pending Story 5.12 integration.

**Recommended: option (c)** — implement bounded-write tools as validated stubs. They check tier, validate task_id exists, validate parameters, and return `{"ok": true}`. Actual persistence defers to when the orchestrator wires them through clawhip-bridge in Story 5.12. This keeps the architecture clean and avoids breaking FR26.

### Resource URI templates

Follow clawhip-bridge pattern for resource URIs:

| Resource | URI Template |
|---|---|
| Task list | `task://list` |
| Task detail | `task://detail/{task_id}` |
| Approval queue | `task://approval-queue` |
| Blockers | `task://blockers` |

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TASK_REGISTRY_DB_PATH` | Yes | — | Absolute path to the SQLite database file |
| `TASK_REGISTRY_ACTOR_KIND` | Yes | — | One of `operator`, `orchestrator`, `worker`, `system` |
| `TASK_REGISTRY_ACTOR_ID` | Yes | — | Non-empty string identifying the actor instance |

### Downstream consumers

- **Story 5.1** (worker-wrapper scaffold) — connects to task-registry MCP server via stdio. The `MCPClientGroup` in `mcp_clients.py` already has `task_registry: ClientSession` wired.
- **Story 5.12** (task execution driver) — reads task detail, writes notes/artifacts via bounded-write tools.
- **Story 5.14** (PR draft auto-creation) — reads task detail for PR metadata.
- **Story 6.1-6.3** (capability tier enforcement) — replaces `_check_tier()` NO-OP with real enforcement.
- **Story 7.2** (telegram status business logic) — reads task list/detail for status command.

### Key patterns from clawhip-bridge (reference implementation)

1. **`build_server()` factory**: Synchronous function returning `FastMCP`. Injects dependencies (db_url, actor_kind, actor_id) into closures. Same pattern for task-registry.

2. **`_check_tier()` NO-OP**: Returns `True`, logs at debug level. Every tool/resource calls it and raises `PermissionError` on `False`. Structure is correct for Story 6.1 replacement.

3. **`@mcp.tool()` and `@mcp.resource()` decorators**: Register handlers on the FastMCP instance. Tools are `async def` functions. Resources use URI templates like `"recent-events://current-day/{limit}"`.

4. **`__main__.py` pattern**: Read env vars, validate, build server, `mcp.run()`. Typed dispatch for actor_kind (if/elif chain for mypy strict).

5. **Test pattern**: `mcp._tool_manager._tools["name"].fn` for direct tool invocation. `mcp._resource_manager._templates["uri"].create_resource()` for resource reads. `await mcp.call_tool()` for end-to-end integration.

6. **stdlib `logging`**: clawhip-bridge uses `logging.getLogger(__name__)`, NOT structlog. MCP servers use stdlib logging (they are lightweight processes, not service-internal adapters).

### Testing strategy

- **In-memory SQLite**: Use `sqlite+aiosqlite://` (in-memory) for tests. Seed with `Task` and `Event` rows via synchronous setup.
- **No live registry**: Tests create a fresh in-memory DB per test class/module.
- **Test structure** (following clawhip-bridge):
  - `TestServerConstruction` — verify 4 resources + 3 tools, no mutation keywords
  - `TestResourceHandlers` — task_list, task_detail, approval_queue, blockers, missing task
  - `TestToolHandlers` — add_note, attach_artifact, emit_event
  - `TestTierEnforcement` — Tier-0 rejected on bounded-write
  - `TestEntryPoint` — env-var validation (subprocess)

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1550-1565 — Story 5.8 definition]
- [Source: `_bmad-output/planning-artifacts/epics.md` line 1426 — implementation ordering note]
- [Source: `_bmad-output/planning-artifacts/epics.md` line 1436 — Story 5.1 dependency on 5.8]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 712-718 — task-registry directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 339 — import rules for mcp-servers]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 589 — MCP resource/tool definitions table]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 822-823 — capability-tier enforcement]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 792 — read-only SQLite connection pattern]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 800 — read vs write path separation]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 819 — FR8 task lifecycle states]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 850 — FR26 registry sole writer]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 860 — FR33 worker reads task detail]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 867 — FR37 capability tiers]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` — reference server factory]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/__main__.py` — reference entrypoint]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py` — reference test pattern]
- [Source: `services/registry-state/src/registry_state/schema.py` — Task, Event, Session ORM models]
- [Source: `services/registry-state/src/registry_state/adapters/sqlite_store.py` — read-only engine factory]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — MCPClientGroup consumer]
