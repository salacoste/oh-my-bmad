# Story 5.1: Worker-wrapper service scaffold + MCP client integration

Status: done

> **IMPLEMENTATION ORDER WARNING:** Per `epics.md` Epic 5 ordering note,
> Stories 5.8 (task-registry MCP) and 5.9 (session-registry MCP) must land
> BEFORE this story. Currently both are stubs. The connectivity tests in this
> story will need the real MCP servers from 5.8/5.9 to pass. If implementing
> out of order, stub the connectivity tests and document the gap.

## Story

As the platform,
I want `services/worker-wrapper/` scaffolded with MCP clients to `task-registry`, `session-registry`, and `clawhip-bridge`,
so that the worker has a wired-up surface to read task detail, emit events, and register sessions.

## Acceptance Criteria

1. **AC-1: MCP client connections** — When the worker-wrapper starts, it connects to all three MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) over stdio. Each `ClientSession.initialize()` succeeds.

2. **AC-2: Connectivity verification** — After startup, the worker calls at least one tool/resource on each MCP server and logs the result (verified by a connectivity test).

3. **AC-3: `mcp_clients.py` adapter** — A new `adapters/mcp_clients.py` module manages the three MCP client connections. It exposes a typed interface for each server's surface (tools/resources). Connections are established during startup and closed on shutdown.

4. **AC-4: Config via environment** — The MCP server commands/args are configurable via `WorkerSettings` (pydantic-settings). Defaults use the workspace-relative `python -m <server_module>` pattern.

5. **AC-5: Graceful shutdown** — SIGTERM/SIGINT closes all MCP sessions cleanly (no leaked subprocesses). `/tmp/ready` is removed on shutdown.

6. **AC-6: Structlog wiring** — Replace the `logging.basicConfig` hello-world with the canonical structlog pattern (same as console-cli and telegram-gateway: idempotent sentinel, same processor chain).

7. **AC-7: `pyproject.toml` updated** — Add `mcp>=1.0`, `structlog>=24.1`, `pydantic-settings>=2.5,<3.0`, workspace deps on `events`, `secret-hygiene`. Version bumped to `0.3.0`.

8. **AC-8: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict` on the new code.

9. **AC-9: Tests** — Tests for MCP client connection setup, config loading, and graceful shutdown. Connectivity tests may be stubbed if 5.8/5.9 haven't landed yet. At least 8 new tests.

10. **AC-10: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

11. **AC-11: Atomic commit** — title: `feat(worker-wrapper): scaffold MCP client connections to all three servers · E5`

## Tasks / Subtasks

- [x] **Task 1: Update `pyproject.toml`** (AC: #7)
  - [x] Add `mcp>=1.0` to dependencies
  - [x] Add `structlog>=24.1`, `pydantic-settings>=2.5,<3.0` to dependencies
  - [x] Add workspace deps: `events`, `secret-hygiene` with `[tool.uv.sources]` entries
  - [x] Bump version to `0.3.0`
  - [x] Run `uv sync --frozen --all-packages` to verify lock resolves

- [x] **Task 2: Create `app/config.py`** (AC: #4)
  - [x] `WorkerSettings(BaseSettings)` with `env_prefix = "WORKER_"`
  - [x] Fields: `task_registry_command` (default: `"python"`), `task_registry_args` (default: `["-m", "task_registry_mcp"]`)
  - [x] Fields: `session_registry_command`, `session_registry_args` (same pattern)
  - [x] Fields: `clawhip_bridge_command`, `clawhip_bridge_args` (same pattern)
  - [x] Optional: `registry_db_path` for future use

- [x] **Task 3: Create `adapters/mcp_clients.py`** (AC: #1, #3)
  - [x] Define `MCPClientGroup` dataclass that manages three `ClientSession` connections
  - [x] Use `AsyncExitStack` + `StdioServerParameters` + `stdio_client` from `mcp.client.stdio`
  - [x] Async context manager pattern: `__aenter__` connects all three, `__aexit__` closes all three
  - [x] Expose typed accessors: `task_registry`, `session_registry`, `clawhip_bridge` (each is a `ClientSession`)
  - [x] Log each successful connection via structlog

- [x] **Task 4: Rewrite `__main__.py`** (AC: #5, #6)
  - [x] Replace hello-world scaffold with structlog wiring (idempotent `_STRUCTLOG_CONFIGURED` sentinel)
  - [x] Same processor chain as console-cli
  - [x] Main function: configure structlog → create `WorkerSettings` → `MCPClientGroup` → connectivity check → ready
  - [x] SIGTERM/SIGINT via `loop.add_signal_handler` → set stop event → clean shutdown

- [x] **Task 5: Connectivity test function** (AC: #2)
  - [x] `verify_connectivity()` calls `list_tools()` on each server, returns `dict[str, bool]`

- [x] **Task 6: Write tests** (AC: #9)
  - [x] `test_mcp_clients.py` — 6 tests (connect all three, shutdown clears refs, double exit safe, connectivity all ok, one fails, null session)
  - [x] `test_config.py` — 7 tests (4 defaults + 3 env overrides)
  - [x] `test_main.py` — 3 tests (structlog idempotent, root handler, main lifecycle)
  - [x] Total: 16 new tests (AC requires >= 8)

- [x] **Task 7: Verification + commit** (AC: #8, #10, #11)
  - [x] `just lint` 9/9 green
  - [x] `just test` 1292 passed, 0 failed, no regressions
  - [x] Version bump verified (0.3.0 in both pyproject.toml and __init__.py)
  - [x] Atomic commit

## Dev Notes

### Implementation ordering

Per `epics.md` line 1426:
> Stories 5.8 and 5.9 remain at their original numbers for traceability; but the **implementation order** is 5.8 → 5.9 → 5.1 → 5.2 → 5.3 → 5.4 → ...

If implementing out of order:
- The MCP client code can still be written — it just connects to stub servers
- Connectivity tests should be written to work with both stubs and real servers
- Clawhip-bridge (Story 2.8) IS real and available for end-to-end testing

### What already exists

| File | Current state | What to change |
|---|---|---|
| `services/worker-wrapper/pyproject.toml` | v0.2.0, empty deps | Add mcp, structlog, pydantic-settings, workspace deps, bump to v0.3.0 |
| `services/worker-wrapper/src/worker_wrapper/__init__.py` | v0.2.0, re-exports atomic_edit | Bump version, keep atomic_edit re-exports |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | Hello-world scaffold | Replace with structlog + MCP client startup |
| `services/worker-wrapper/Dockerfile` | Multi-stage with Node.js + Python base | No changes needed |
| `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py` | Story 2.12 atomic writes | No changes — this is unrelated to MCP |
| `services/worker-wrapper/src/worker_wrapper/py.typed` | PEP 561 marker | No changes |

### MCP client pattern (canonical, from mcp SDK)

The `mcp` Python SDK (version 1.27.0 in our lock) provides client-side stdio transport:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command="python",
    args=["-m", "task_registry_mcp"],
    env=None,  # inherits process env
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("some_tool", arguments={...})
```

Key points:
- `stdio_client` spawns the server as a subprocess and returns `(read, write)` streams
- `ClientSession` wraps the streams and provides the MCP protocol layer
- `initialize()` is the MCP handshake — must succeed before any tool/resource calls
- The async context manager pattern ensures cleanup on exit

### Connecting to THREE MCP servers simultaneously

The worker needs three separate `stdio_client` connections. Pattern:

```python
class MCPClientGroup:
    def __init__(self, settings: WorkerSettings): ...

    async def __aenter__(self) -> MCPClientGroup:
        self._task_reg_ctx = stdio_client(StdioServerParameters(
            command=self._settings.task_registry_command,
            args=self._settings.task_registry_args,
        ))
        read, write = await self._task_reg_ctx.__aenter__()
        self._task_reg_session = ClientSession(read, write)
        await self._task_reg_session.__aenter__()
        await self._task_reg_session.initialize()
        # ... same for session_registry and clawhip_bridge
        return self

    async def __aexit__(self, *exc): ...
```

Alternative: use `contextlib.AsyncExitStack` for cleaner multi-resource management:

```python
async def __aenter__(self):
    async with AsyncExitStack() as stack:
        self._stack = stack
        self.task_registry = await self._connect(settings.task_registry_params)
        self.session_registry = await self._connect(settings.session_registry_params)
        self.clawhip_bridge = await self._connect(settings.clawhip_bridge_params)
        self._stack = None  # prevent double cleanup
        return self
```

### MCP server endpoints (from architecture.md)

**task-registry MCP server** (Story 5.8 — currently stub):
- Resources: task list, task detail, approval queue, blockers
- Tools: `task.add_note`, `task.attach_artifact`, `task.emit_event`

**session-registry MCP server** (Story 5.9 — currently stub):
- Resources: active sessions, worker metadata, heartbeats
- Tools: `session.heartbeat`, `session.register`, `session.close`

**clawhip-bridge MCP server** (Story 2.8 — REAL, available):
- Resources: recent event stream (read-only), route diagnostics
- Tools: `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion`

### Architecture directory structure target

From `architecture.md` lines 683-698, the worker-wrapper target structure:

```
├── worker-wrapper/                    # Component 6
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── src/worker_wrapper/
│       ├── __init__.py               # version + atomic_edit re-exports
│       ├── __main__.py               # structlog + MCP startup
│       ├── py.typed
│       ├── app/
│       │   ├── __init__.py           # NEW
│       │   ├── main.py               # NEW — worker lifecycle
│       │   └── config.py             # NEW — WorkerSettings
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── atomic_edit.py        # existing — no changes
│       │   ├── lifecycle.py          # Story 5.17a — NOT this story
│       │   ├── reasoning.py          # Story 5.5 — NOT this story
│       │   └── worktree_lock.py      # Story 5.3 — NOT this story
│       └── adapters/
│           ├── __init__.py           # NEW
│           ├── mcp_clients.py        # NEW — MCPClientGroup
│           ├── claude_code_runner.py # Story 5.4 — NOT this story
│           └── clawhip_client.py     # optional — may fold into mcp_clients
```

This story creates: `app/`, `adapters/`, `config.py`, `mcp_clients.py`. It does NOT create `lifecycle.py`, `reasoning.py`, `worktree_lock.py`, or `claude_code_runner.py`.

### Import-graph rules (CRITICAL)

Worker-wrapper MUST:
- Import from `packages/` (events, secret-hygiene, idempotency) — ALLOWED
- Import from `worker_wrapper` own modules — ALLOWED
- Import from `mcp` SDK — ALLOWED (it's a pip dependency, not a workspace service)
- Import from `services/*`, `mcp-servers/*` — **FORBIDDEN**
- Communication with MCP servers is stdio-only (subprocess), NOT via Python imports

The `scripts/check_imports.py` gate enforces this.

### Structlog wiring pattern

Follow the exact same pattern as `services/console-cli/src/console_cli/__main__.py`:
1. `_STRUCTLOG_CONFIGURED: bool = False` sentinel
2. Processor chain: `merge_contextvars → add_log_level → add_logger_name → ExtraAdder → TimeStamper → redact_secrets → JSONRenderer`
3. Bridge stdlib logging via `ProcessorFormatter`
4. Called from `main()` before any other work

### Testing strategy

Since task-registry and session-registry are stubs:
- Mock `stdio_client` and `ClientSession` at the transport level
- Test `MCPClientGroup` connection lifecycle (enter/exit)
- Test `verify_connectivity()` with mocked sessions returning tool lists
- For clawhip-bridge (which IS real): optionally include an integration test that actually spawns the server

### Key patterns from Epic 4 (console-cli scaffold)

1. **Version bump** — both `__init__.py` and `pyproject.toml` get the same version
2. **Commit message format** — `feat(worker-wrapper): <description> · E5`
3. **Lint gates** — `just lint` 9/9 is the gatekeeper (but note: `just lint` mypy scope includes `services/worker-wrapper`)
4. **`check_imports.py`** — must pass (no cross-service imports)
5. **Config pattern** — `BaseSettings` with env-var overrides (same as console-cli `ConsoleSettings`)

### File List

| File | Change |
|---|---|
| `services/worker-wrapper/pyproject.toml` | Modified — deps, version bump 0.3.0 |
| `services/worker-wrapper/src/worker_wrapper/__init__.py` | Modified — version bump |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | Rewritten — structlog + MCP startup |
| `services/worker-wrapper/src/worker_wrapper/app/__init__.py` | New |
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | New — WorkerSettings |
| `services/worker-wrapper/src/worker_wrapper/adapters/__init__.py` | New |
| `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` | New — MCPClientGroup |
| `services/worker-wrapper/src/worker_wrapper/test_mcp_clients.py` | New — MCP client tests |
| `services/worker-wrapper/src/worker_wrapper/test_config.py` | New — config tests |
| `_bmad-output/implementation-artifacts/5-1-worker-wrapper-scaffold.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1422-1440 — Story 5.1 definition + ordering note]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 683-698 — worker-wrapper directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 231 — inter-service communication matrix]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 712-732 — MCP server directory trees]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 781 — worker-wrapper ↔ MCP servers boundary]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-340 — import-graph rules]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` — existing MCP server pattern]
- [Source: MCP Python SDK — `mcp.client.stdio.stdio_client`, `ClientSession`, `StdioServerParameters`]
- [Source: `_bmad-output/implementation-artifacts/4-1-typer-binary-scaffold.md` — scaffold story patterns]
- [Source: `_bmad-output/implementation-artifacts/epic-4-retro-2026-05-06.md` — Epic 4 retrospective lessons]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

- ruff F401 unused imports in test files (fixed by removing `pytest`, `AsyncMock`, `MagicMock`, `asyncio` imports)
- mypy strict errors: lambda inference (fixed with named `_handle_stop`), `func-returns-value` on `Event.set()`, `Handler.stream` attr (fixed by asserting on `.formatter` instead)

### Completion Notes List

1. **MCPClientGroup uses `AsyncExitStack`** for clean multi-resource management. The story dev notes suggested this pattern and it avoids manual `__aenter__`/`__aexit__` orchestration.
2. **Structlog wiring matches console-cli exactly** — same processor chain, same idempotent sentinel. This is the third service to use this pattern (after telegram-gateway and console-cli).
3. **Signal handling uses `loop.add_signal_handler`** instead of the old `signal.signal` + `signal.pause()` pattern. The async event loop is the correct shutdown coordination point when MCP sessions need clean async teardown.
4. **Connectivity tests are stub-safe** — `verify_connectivity` calls `list_tools()` which works with both stub MCP servers (empty tool list) and real ones. Tests mock at the transport level.

### Review Findings

All 15 findings from three-layer review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) were batch-fixed:

- [x] [Review][Patch] Dead `_stop()` function removed along with unused `_clients`/`_loop` globals and `FrameType`/`NoReturn` imports [__main__.py:68-75]
- [x] [Review][Patch] Ready-file leak on exception — wrapped in try/finally [__main__.py:85-105]
- [x] [Review][Patch] Partial connectivity documented as intentional graceful degradation during stub phase [__main__.py:88-89]
- [x] [Review][Patch] `_connect()` failure cleanup — `__aenter__` now calls `__aexit__` on failure before re-raising [mcp_clients.py:37-55]
- [x] [Review][Patch] Added test for `_connect()` failure during startup [test_mcp_clients.py]
- [x] [Review][Patch] `model_config` changed from plain dict to `SettingsConfigDict` matching console-cli pattern [config.py:17]
- [x] [Review][Patch] Module-level structlog logger moved to function-level in mcp_clients.py [mcp_clients.py]
- [x] [Review][Patch] `add_signal_handler` wrapped in try/except for Windows compatibility [__main__.py:101-106]
- [x] [Review][Patch] Hardcoded `/tmp/ready` replaced with configurable `ready_file_path` setting [config.py, __main__.py]
- [x] [Review][Patch] `registry_db_path` documented with TODO comment for future consumer [config.py:28]
- [x] [Review][Patch] `test_ready_file_touched_on_start` replaced with `test_main_invokes_run_without_error` + `test_ready_file_lifecycle` [test_main.py]
- [x] [Review][Patch] `tmp_path` fixture now used in tests instead of hardcoded paths [test_main.py]
- [x] [Review][Patch] `all({}.values())` guard added — `if not results or not all(results.values())` [__main__.py:87]
- [x] [Review][Patch] `verify_connectivity` now uses `asyncio.gather` for concurrent checks [mcp_clients.py]
- [x] [Review][Patch] `session.initialize()` wrapped with `asyncio.wait_for(timeout=30)` [mcp_clients.py:79]

### File List

| File | Change |
|---|---|
| `services/worker-wrapper/pyproject.toml` | Modified — deps, version bump 0.3.0 |
| `services/worker-wrapper/src/worker_wrapper/__init__.py` | Modified — version bump |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | Rewritten — structlog + MCP startup |
| `services/worker-wrapper/src/worker_wrapper/app/__init__.py` | New |
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | New — WorkerSettings |
| `services/worker-wrapper/src/worker_wrapper/adapters/__init__.py` | New |
| `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` | New — MCPClientGroup + verify_connectivity |
| `services/worker-wrapper/src/worker_wrapper/test_config.py` | New — 8 config tests |
| `services/worker-wrapper/src/worker_wrapper/test_mcp_clients.py` | New — 7 MCP client tests |
| `services/worker-wrapper/src/worker_wrapper/test_main.py` | New — 4 main lifecycle tests |
