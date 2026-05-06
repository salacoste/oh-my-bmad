# Story 5.2: Session lifecycle emission (started / heartbeat / finished)

Status: done

> **IMPLEMENTATION ORDER NOTE:** Per `epics.md` Epic 5 ordering note, Stories 5.8 and 5.9 should land before this story. Currently both are stubs. The session lifecycle code can still be written — it calls MCP tools on the session-registry stub and emits events via clawhip-bridge (which IS real). Tests mock at the MCP transport level, same as Story 5.1.

## Story

As the platform,
I want the worker to register itself with the session registry on startup and emit `session.started`, periodic `session.heartbeat`, and `session.finished` typed events,
so that session state is observable and heartbeat timeouts trigger failure detection (FR24a).

## Acceptance Criteria

1. **AC-1: `session.started` event** — When the worker starts, it emits a `session.started` typed event via clawhip-bridge with payload `{session_id, worker_id, task_id?}`. The event type is registered in the schema registry.

2. **AC-2: Periodic heartbeat** — While the worker is running, a `session.heartbeat` event is emitted every 30 seconds (+/- 5 s tolerance). The heartbeat interval is configurable via `WorkerSettings`.

3. **AC-3: `session.finished` event** — On graceful shutdown (SIGTERM/SIGINT), `session.finished` is emitted before the process exits. The ready file is removed after the event is sent.

4. **AC-4: Session payload models** — Three new frozen Pydantic payload models are added to `packages/events/src/events/payloads.py`: `SessionStartedPayload`, `SessionHeartbeatPayload`, `SessionFinishedPayload`. Each is registered in `services/registry-state/src/registry_state/domain/event_types.py`.

5. **AC-5: `session_registry` MCP tool calls** — The worker calls `session.register` on the session-registry MCP server at startup, `session.heartbeat` periodically, and `session.close` on shutdown. These calls are best-effort (failure logs a warning but does not crash the worker).

6. **AC-6: `app/main.py` lifecycle module** — A new `app/main.py` module contains the session lifecycle logic (start/heartbeat/finish). The `__main__.py` `_run()` function delegates to this module instead of inline logic.

7. **AC-7: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

8. **AC-8: Tests** — At least 8 new tests covering: session started emission, heartbeat periodicity, session finished on shutdown, payload model validation, MCP tool call integration (mocked).

9. **AC-9: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

10. **AC-10: Atomic commit** — title: `feat(worker-wrapper): add session lifecycle event emission · E5`

## Tasks / Subtasks

- [x] **Task 1: Add session payload models** (AC: #4)
  - [x] Add `SessionStartedPayload(session_id, worker_id, task_id?)` to `packages/events/src/events/payloads.py`
  - [x] Add `SessionHeartbeatPayload(session_id)` to same file
  - [x] Add `SessionFinishedPayload(session_id)` to same file
  - [x] Add all three to `__all__` in payloads.py
  - [x] Register all three in `services/registry-state/src/registry_state/domain/event_types.py` (versions 1.0.0, 1.0.1)
  - [x] Run `just lint` and `just test` to verify no regressions

- [x] **Task 2: Add `session` fields to `WorkerSettings`** (AC: #2)
  - [x] Add `session_id: str = ""` — auto-generated if empty (UUIDv7 from `events.ids`)
  - [x] Add `worker_id: str = ""` — auto-generated if empty (UUIDv7)
  - [x] Add `heartbeat_interval_s: float = 30.0`

- [x] **Task 3: Create `app/main.py` session lifecycle module** (AC: #1, #2, #3, #5, #6)
  - [x] `async def start_session(clients, settings)` — emit `session.started` via clawhip-bridge, call `session.register` MCP tool
  - [x] `async def heartbeat_loop(clients, settings, stop_event)` — periodic `session.heartbeat` event + MCP tool call, exits when `stop_event` is set
  - [x] `async def finish_session(clients, settings)` — emit `session.finished` via clawhip-bridge, call `session.close` MCP tool
  - [x] All MCP tool calls are best-effort: catch `Exception`, log warning, continue

- [x] **Task 4: Refactor `__main__.py` to use `app/main.py`** (AC: #6)
  - [x] Import `start_session`, `heartbeat_loop`, `finish_session` from `app.main`
  - [x] In `_run()`: after connectivity check → `start_session` → spawn `heartbeat_loop` as task → wait for stop → `finish_session` → cleanup
  - [x] Use `asyncio.create_task` for the heartbeat loop so it runs concurrently with the stop-event wait
  - [x] Cancel heartbeat task on stop before calling `finish_session`

- [x] **Task 5: Write tests** (AC: #8)
  - [x] `test_payloads.py` — test all 3 payload models for validation (valid, invalid session_id format, optional task_id)
  - [x] `test_session_lifecycle.py` — test `start_session`, `heartbeat_loop`, `finish_session` with mocked MCP clients
  - [x] Test heartbeat periodicity (mock `asyncio.sleep`, verify 3 heartbeats in 90 s)
  - [x] Test `finish_session` is called on stop
  - [x] Test MCP tool call failure is non-fatal (best-effort)

- [x] **Task 6: Verification + commit** (AC: #7, #9, #10)
  - [x] `just lint` 9/9 green
  - [x] `just test` no regressions
  - [x] Atomic commit

## Dev Notes

### Implementation ordering

Per `epics.md` ordering note: 5.8 → 5.9 → 5.1 → **5.2** → 5.3 → ...
If implementing out of order:
- session-registry MCP server is a stub — MCP tool calls (`session.register`, etc.) will fail but must be best-effort
- clawhip-bridge IS real — event emission will work end-to-end
- Tests mock at the MCP transport level, same as Story 5.1's `_patch_stdio_client` helper

### What already exists (Story 5.1)

| File | Current state | What to change |
|---|---|---|
| `__main__.py` | Structlog + MCP client startup, signal shutdown, `/tmp/ready` | Refactor to use `app/main.py` for session lifecycle |
| `adapters/mcp_clients.py` | `MCPClientGroup` with 3 connections, `verify_connectivity` | No changes — read-only usage |
| `app/config.py` | `WorkerSettings` with MCP server commands/args | Add `session_id`, `worker_id`, `heartbeat_interval_s` |
| `app/__init__.py` | Empty | No changes |
| `adapters/__init__.py` | Empty | No changes |

### Event payload design

The payloads follow the existing pattern in `packages/events/src/events/payloads.py`:

```python
class SessionStartedPayload(BaseModel):
    """Payload for the ``session.started`` event."""
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    worker_id: str = Field(min_length=1, pattern=_WORKER_ID_PATTERN)
    task_id: str | None = Field(default=None, min_length=1, pattern=_TASK_ID_PATTERN)
```

The `_SESSION_ID_PATTERN` regex is already defined in payloads.py as `^s-<uuidv7>$`. We need to add `_WORKER_ID_PATTERN` as `^w-<uuidv7>$` (consistent with the actor shape in the event envelope where `"kind": "worker", "id": "w-0192..."`).

Similarly for `SessionHeartbeatPayload(session_id)` and `SessionFinishedPayload(session_id)`.

### Event emission via clawhip-bridge

Use the existing `emit_event` tool on the clawhip-bridge MCP session:

```python
await clients.clawhip_bridge.call_tool("emit_event", arguments={
    "type": "session.started",
    "payload": {"session_id": "s-...", "worker_id": "w-...", "task_id": None},
})
```

### MCP tool calls on session-registry

The session-registry MCP server (Story 5.9 — stub) exposes:
- `session.register(session_id, worker_id, task_id?)` — at startup
- `session.heartbeat(session_id)` — periodic
- `session.close(session_id)` — at shutdown

These calls are best-effort. If the MCP server is a stub or unavailable, log a warning and continue.

### Heartbeat loop pattern

```python
async def heartbeat_loop(clients, settings, stop_event):
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.heartbeat_interval_s)
            return  # stop_event was set
        except asyncio.TimeoutError:
            pass  # interval elapsed, emit heartbeat
        await _emit_heartbeat(clients, settings)
```

### Import-graph rules (CRITICAL)

Worker-wrapper MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `worker_wrapper` own modules — ALLOWED
- Import from `mcp` SDK — ALLOWED
- Import from `services/*`, `mcp-servers/*` — **FORBIDDEN**

The new payload models go in `packages/events/` (shared). The registration goes in `services/registry-state/`. The lifecycle logic goes in `services/worker-wrapper/app/main.py`.

### Key patterns from Story 5.1 (review findings)

1. **Module-level structlog loggers** — use function-level `structlog.get_logger(__name__)` instead (from 5.1 code review M2 fix)
2. **`AsyncExitStack` pattern** — already in `MCPClientGroup`, use the same pattern
3. **Best-effort MCP calls** — catch `Exception`, log via structlog, continue
4. **`SettingsConfigDict`** — use this instead of plain dict for `model_config` (from 5.1 code review M1 fix)
5. **`asyncio.wait_for` timeout** — wrap MCP calls with timeouts (from 5.1 code review L2 fix)

### File List

| File | Change |
|---|---|
| `packages/events/src/events/payloads.py` | Modified — add 3 session payload models |
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — register 3 new event types |
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | Modified — add session/worker ID + heartbeat interval |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | New — session lifecycle module |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | Modified — delegate to app/main.py |
| `services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py` | New — lifecycle tests |
| `packages/events/src/events/test_payloads.py` | Modified — add session payload tests |
| `_bmad-output/implementation-artifacts/5-2-session-lifecycle-emission.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Story 5.2 definition + ordering note]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 683-698 — worker-wrapper directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 719-725 — session-registry MCP server tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 67 — shutdown ordering, workers emit terminal lifecycle event]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 328-331 — event naming convention]
- [Source: `packages/events/src/events/payloads.py` — existing payload pattern, ID regex patterns]
- [Source: `services/registry-state/src/registry_state/domain/event_types.py` — event type registration pattern]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:158` — `emit_event` tool signature]
- [Source: `_bmad-output/implementation-artifacts/5-1-worker-wrapper-scaffold.md` — previous story patterns + review findings]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

- ruff SIM105: `try/except/pass` on `CancelledError` → replaced with `contextlib.suppress`
- ruff I001: import sorting in config.py and test_session_lifecycle.py → auto-fixed by `ruff check --fix`
- ruff UP041: `asyncio.TimeoutError` → replaced with builtin `TimeoutError`
- mypy `unused-ignore`: removed three `# type: ignore[misc]` comments on frozen-model assignment tests
- mypy `arg-type` dict variance: `dict[str, str | None]` not assignable to `dict[str, object]` → changed `reg_args` annotation to `dict[str, object]`

### Completion Notes List

1. **Added `new_worker_id` to `events/ids.py`** with `w-` prefix, plus updated `parse_prefix` to recognize `w-` alongside `t-`, `s-`, `e-`.
2. **Three frozen Pydantic payload models** (`SessionStartedPayload`, `SessionHeartbeatPayload`, `SessionFinishedPayload`) with strict validation matching existing patterns. `_WORKER_ID_PATTERN` regex added alongside existing `_SESSION_ID_PATTERN` and `_TASK_ID_PATTERN`.
3. **`WorkerSettings` gains `session_id`, `worker_id`, `task_id`, `heartbeat_interval_s`** with `resolve_session_id()` / `resolve_worker_id()` helpers that auto-generate and cache UUIDv7 when empty. `heartbeat_interval_s` validated with `Field(gt=0)`.
4. **`app/main.py` lifecycle module** uses `_call_tool_best_effort` helper pattern for all MCP calls — catches any `Exception`, logs via structlog, continues. Each lifecycle function constructs Pydantic payload models for client-side validation before emission.
5. **Heartbeat loop** uses `asyncio.wait_for(stop_event.wait(), timeout=interval)` pattern from story dev notes — exits cleanly on stop without busy-waiting. MCP call timeout clamped to `min(10s, interval * 0.5)`.
6. **`__main__.py` refactored** — signal handlers registered BEFORE `start_session` to prevent dangling `session.started` events on SIGTERM during startup. Heartbeat task spawned as `asyncio.create_task`, cancelled on stop via `contextlib.suppress(CancelledError)`, then `finish_session`.
7. **30 new tests** (18 payload model + 9 lifecycle + 3 worker_id in test_ids), all passing. 1325 total tests, 0 failures.

### Review Findings

All 15 findings from three-layer review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) were batch-fixed:

- [x] [Review][Patch] Stale `parse_prefix` docstring — updated to include `w-` [ids.py:121]
- [x] [Review][Patch] `resolve_session_id()`/`resolve_worker_id()` non-idempotent — now cache generated UUIDv7 [config.py:44-54]
- [x] [Review][Patch] No client-side payload validation — `start_session`/`heartbeat_loop`/`finish_session` now construct Pydantic models and serialize via `.model_dump()` [app/main.py]
- [x] [Review][Patch] Signal handlers registered after `start_session` — moved before [__main__.py]
- [x] [Review][Patch] `_call_tool_best_effort` docstring said "any exception" but only catches `Exception` — clarified that `BaseException` subclasses propagate [app/main.py:34-44]
- [x] [Review][Patch] `_MCP_CALL_TIMEOUT` can exceed low `heartbeat_interval_s` — added `_clamp_timeout()` helper [app/main.py:28-30]
- [x] [Review][Patch] `heartbeat_interval_s` accepts zero/negative — added `Field(gt=0)` [config.py:42]
- [x] [Review][Patch] Tests don't verify MCP call arguments — added `call_args` assertions for event type and payload [test_session_lifecycle.py]
- [x] [Review][Patch] No test for `new_worker_id()` in `test_ids.py` — added `TestWorkerId` class with 3 tests + `test_recognizes_w_prefix` [test_ids.py]
- [x] [Review][Patch] No `extra_fields_forbidden` test for heartbeat/finished payloads — added to both classes [test_session_payloads.py]
- [x] [Review][Patch] No wrong-prefix test for heartbeat/finished payloads — added `test_invalid_session_id_wrong_prefix` using `_tid()` [test_session_payloads.py]
- [x] [Review][Patch] Test uses non-UUIDv7 IDs — replaced with `new_session_id()`/`new_worker_id()` [test_session_lifecycle.py]

### File List

| File | Change |
|---|---|
| `packages/events/src/events/ids.py` | Modified — add `new_worker_id`, update `parse_prefix` for `w-` |
| `packages/events/src/events/__init__.py` | Modified — re-export `new_worker_id` |
| `packages/events/src/events/payloads.py` | Modified — add `_WORKER_ID_PATTERN`, 3 session payload models |
| `packages/events/src/events/test_session_payloads.py` | New — 18 payload model tests |
| `packages/events/src/events/test_ids.py` | Modified — add 4 `new_worker_id` / `parse_prefix` tests |
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — register 3 new event types, update re-exports |
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | Modified — add session/worker ID + heartbeat interval fields |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | New — session lifecycle module (start/heartbeat/finish) |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | Modified — signal handlers before session start, delegate lifecycle to app/main.py |
| `services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py` | New — 9 lifecycle tests |
| `_bmad-output/implementation-artifacts/5-2-session-lifecycle-emission.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |
