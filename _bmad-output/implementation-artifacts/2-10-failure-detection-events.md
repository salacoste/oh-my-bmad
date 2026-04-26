# Story 2.10: Failure-detection typed events

Status: review

## Story

As **the platform (registry-state)**,
I want `registry-state` to define and emit `service.crashed`, `session.heartbeat_timeout`, `sink.delivery_failed`, and `task.stop_requested` typed events when the corresponding failure conditions are detected,
so that recovery paths are driven by explicit, queryable signals rather than implicit timers or log-scraping.

## Acceptance Criteria

1. **AC-1: 4 new payload models** in `services/registry-state/src/registry_state/domain/event_types.py`:

   ```python
   class ServiceCrashedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       service: str          # e.g. "worker-wrapper", "registry-api"
       exit_code: int        # non-zero exit code reported by supervising process

   class SessionHeartbeatTimeoutPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       session_id: str       # s-<uuidv7>
       task_id: str          # t-<uuidv7> — owning task
       last_heartbeat_at: datetime  # UTC timestamp of last received heartbeat
       timeout_threshold_s: float   # configured 2× heartbeat_interval value

   class SinkDeliveryFailedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       sink_name: str        # e.g. "telegram"
       consecutive_failures: int  # always ≥ 3 at emission time
       last_error: str | None = None  # sanitized error description (no secrets)

   class TaskStopRequestedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       task_id: str
       actor_id: str         # who issued /stop — e.g. "telegram:12345678" or "console"
   ```

   All 4 models use `ConfigDict(frozen=True, strict=True, extra="forbid")` matching Story 2.1 discipline.

2. **AC-2: 4 schema-registry registrations** appended at the bottom of `event_types.py`:

   ```python
   register("service.crashed", "1.0.0", ServiceCrashedPayload)
   register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
   register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
   register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
   ```

   After this story, `EVENT_TYPES` contains 12 types (was 8 from Stories 2.5 + 2.8).

3. **AC-3: `services/registry-state/src/registry_state/domain/failure_detection.py`** — new module. Contains:

   **3a — Emission functions** (each takes `writer: EventLogWriter`, `clock: Clock`, and event-specific args; returns the emitted `EventEnvelope`):

   ```python
   async def emit_service_crashed(
       writer: EventLogWriter,
       *,
       clock: Clock,
       service: str,
       exit_code: int,
       actor_id: str = "registry-state",
   ) -> EventEnvelope: ...

   async def emit_session_heartbeat_timeout(
       writer: EventLogWriter,
       *,
       clock: Clock,
       session_id: str,
       task_id: str,
       last_heartbeat_at: datetime,
       timeout_threshold_s: float,
       actor_id: str = "registry-state",
   ) -> EventEnvelope: ...

   async def emit_sink_delivery_failed(
       writer: EventLogWriter,
       *,
       clock: Clock,
       sink_name: str,
       consecutive_failures: int,
       last_error: str | None = None,
       actor_id: str = "registry-state",
   ) -> EventEnvelope: ...

   async def emit_task_stop_requested(
       writer: EventLogWriter,
       *,
       clock: Clock,
       task_id: str,
       actor_id: str,
   ) -> EventEnvelope: ...
   ```

   All functions build an `EventEnvelope.create(...)` with `Actor(kind="service", id=actor_id)`, then call `await writer.append(envelope)`. No detection logic — these are pure emission primitives.

   **3b — `HeartbeatMonitor`** — tracks per-session last-heartbeat timestamps:

   ```python
   class HeartbeatMonitor:
       def __init__(self, *, heartbeat_interval_s: float, clock: Clock) -> None: ...
       def record_heartbeat(self, session_id: str) -> None: ...
       def overdue_sessions(self) -> list[tuple[str, datetime]]:
           # returns [(session_id, last_heartbeat_at)] for sessions where
           # now - last_heartbeat_at > 2 * heartbeat_interval_s
           ...
       def remove_session(self, session_id: str) -> None: ...
   ```

   Thread-safe: internal state is a plain `dict[str, datetime]` — called from async code only (single-threaded asyncio loop); no locking needed. `overdue_sessions()` uses `clock.now()` so it's deterministic in tests.

   **3c — `SinkFailureTracker`** — counts consecutive delivery failures per sink:

   ```python
   class SinkFailureTracker:
       def __init__(self, *, failure_threshold: int = 3) -> None: ...
       def record_failure(self, sink_name: str, error: str | None = None) -> int:
           # increments counter; returns current consecutive count
           ...
       def record_success(self, sink_name: str) -> None:
           # resets consecutive counter for sink to 0
           ...
       def should_emit(self, sink_name: str) -> bool:
           # True when consecutive_failures >= failure_threshold
           ...
       def get_state(self, sink_name: str) -> tuple[int, str | None]:
           # returns (consecutive_failures, last_error)
           ...
   ```

4. **AC-4: `app/main.py` import wiring** — add import for new payload classes so the `register()` side-effects fire on startup:

   ```python
   from registry_state.domain.event_types import (  # noqa: F401
       ...
       ServiceCrashedPayload,
       SessionHeartbeatTimeoutPayload,
       SinkDeliveryFailedPayload,
       TaskStopRequestedPayload,
   )
   ```

   After this, `check_event_registry.py` scans emission sites for `type="service.crashed"` etc. and finds them registered.

5. **AC-5: No materializer handlers in 2.10**. These 4 events are observability/signalling events. Their SQLite state transitions (e.g. marking a task `stopped` when `task.stop_requested` fires) are wired in later stories (Epic 3 for stop, Epic 5 for worker lifecycle). The materializer's `_extract_ids` already handles unknown event types gracefully (returns `(None, None)`); the events are persisted in the `events` table with `task_id = NULL` (except `task.stop_requested` which has a `task_id` in payload — the materializer will store `NULL` in the FK column until a handler is registered). **Document this explicitly in the module docstring.**

6. **AC-6: `__all__` and `__init__.py` re-exports** in `registry_state/domain/__init__.py` — export the 4 new payload classes + 3 new failure_detection exports (`HeartbeatMonitor`, `SinkFailureTracker`, and each `emit_*` function). Consistent with the pattern from prior stories.

7. **AC-7: Co-located tests** in `services/registry-state/src/registry_state/domain/test_failure_detection.py` — target 18–22 tests:

   **TestPayloadModels** (~4):
   - `test_service_crashed_payload_validates_correctly`
   - `test_session_heartbeat_timeout_payload_validates_correctly`
   - `test_sink_delivery_failed_payload_validates_correctly`
   - `test_task_stop_requested_payload_validates_correctly`

   **TestEmissionFunctions** (~6): (use `FrozenClock(0)` + in-memory `EventLogWriter` with `tmp_path`)
   - `test_emit_service_crashed_writes_envelope_to_log`
   - `test_emit_service_crashed_envelope_has_correct_type_and_payload`
   - `test_emit_session_heartbeat_timeout_writes_envelope`
   - `test_emit_sink_delivery_failed_writes_envelope`
   - `test_emit_task_stop_requested_writes_envelope`
   - `test_emit_task_stop_requested_actor_id_preserved`

   **TestHeartbeatMonitor** (~6):
   - `test_heartbeat_monitor_no_overdue_when_fresh`
   - `test_heartbeat_monitor_session_overdue_after_2x_interval`
   - `test_heartbeat_monitor_refreshed_session_not_overdue`
   - `test_heartbeat_monitor_remove_session_clears_tracking`
   - `test_heartbeat_monitor_multiple_sessions_independent`
   - `test_heartbeat_monitor_boundary_exactly_2x_not_overdue` (at exactly 2×, not yet overdue; > 2× triggers)

   **TestSinkFailureTracker** (~5):
   - `test_sink_failure_tracker_no_emit_before_threshold`
   - `test_sink_failure_tracker_emits_at_threshold`
   - `test_sink_failure_tracker_success_resets_counter`
   - `test_sink_failure_tracker_last_error_preserved`
   - `test_sink_failure_tracker_independent_sinks`

8. **AC-8: `check_event_registry` green** — `scripts/check_event_registry.py` finds `type="service.crashed"`, `"session.heartbeat_timeout"`, `"sink.delivery_failed"`, `"task.stop_requested"` literals in `failure_detection.py`'s emission functions and confirms they are all registered. No `# noqa: EV001` needed.

9. **AC-9: mypy --strict clean** — `failure_detection.py` + `test_failure_detection.py` pass `mypy --strict`. `datetime` fields typed as `datetime` (not `str`). `Actor` model from `events.envelope` used correctly.

10. **AC-10: `check_single_writer` green** — `failure_detection.py` uses `EventLogWriter.append()` only; no SQLAlchemy writes. Scanner exits 0.

11. **AC-11: `scan-secrets` clean** — `last_error` field in `SinkDeliveryFailedPayload` has no raw secrets (sanitization is the caller's responsibility; documented in the emission function docstring).

12. **AC-12: Regression green** — `just test` count bumps from **397 passed, 6 skipped** by ≥18 (target: **415+**). `just lint` 7/7 green. mypy strict on ≥64 source files (was 62; +`failure_detection.py` + `test_failure_detection.py`).

13. **AC-13: Atomic commit** titled `feat(registry-state): story 2.10 — failure-detection typed events (service.crashed, heartbeat_timeout, sink.delivery_failed, stop_requested) · FR24a NFR-R5`.

## Tasks / Subtasks

- [x] **Task 1: 4 payload models + registrations** (AC: #1, #2)
  - [x] Add `ServiceCrashedPayload`, `SessionHeartbeatTimeoutPayload`, `SinkDeliveryFailedPayload`, `TaskStopRequestedPayload` to `domain/event_types.py`.
  - [x] Append 4 `register(...)` calls after existing Story 2.8 registrations.
  - [x] Confirm `EVENT_TYPES` grows from 8 → 12 types.

- [x] **Task 2: `failure_detection.py` module** (AC: #3)
  - [x] Module docstring explaining: emission primitives + detection helpers; materializer handlers deferred to later stories; NFR-R5 60s SLA is enforced by the polling loop (future wiring), not by this module.
  - [x] 4 `async emit_*` functions using `EventLogWriter` + `EventEnvelope.create()` + `Actor`.
  - [x] `HeartbeatMonitor` class with `record_heartbeat`, `overdue_sessions`, `remove_session`.
  - [x] `SinkFailureTracker` class with `record_failure`, `record_success`, `should_emit`, `get_state`.

- [x] **Task 3: Import wiring in `app/main.py`** (AC: #4)
  - [x] Add 4 new payload classes to the `noqa: F401` import block.

- [x] **Task 4: `__all__` + `__init__.py` re-exports** (AC: #6)
  - [x] Export new payload models from `domain/__init__.py`.
  - [x] Export `HeartbeatMonitor`, `SinkFailureTracker`, and all 4 `emit_*` functions.

- [x] **Task 5: Tests** (AC: #7)
  - [x] Create `domain/test_failure_detection.py` with 4 test classes and ≥18 tests (delivered: 21).
  - [x] Use `FrozenClock` + `tmp_path` pattern (matching prior stories e.g. 2.4, 2.9).
  - [x] Use `pytest.mark.asyncio` for emission tests (they call `await writer.append`).
  - [x] `HeartbeatMonitor` tests use a custom `_AdvancingClock` whose `now()` is advanced manually.

- [x] **Task 6: Regression + CI gates + atomic commit** (AC: #8–#13)
  - [x] `just test` → 418 passed, 6 skipped (was 397+6; +21 new tests).
  - [x] `just lint` → 7/7 green, mypy strict on 64 source files (was 62; +2 new files).
  - [x] `check_event_registry` → green (4 new types registered; emission sites use `EventEnvelope.create(...)` which the AST scanner does not target — vacuous green, types still in REGISTRY).
  - [x] `check_single_writer` → 0 violations on registry-state.
  - [x] `scan-secrets` → clean.
  - [x] Single atomic commit per AC-13.

## Dev Notes

### Architecture context

- **`failure_detection.py` location**: `services/registry-state/src/registry_state/domain/failure_detection.py` — explicitly named in Architecture line 633 as the NFR-R5 health-probe emitter module.
- **registry-state owns this file** per Arch line 42: "emits `service.crashed` / recovery events (FR24a)".
- **NFR-R5** (Arch line, PRD line 916): events must be emitted within **60 s** of the underlying condition. The 60-second SLA is a polling-loop contract: the detection probes (which CALL these emission functions) must be scheduled to run at least every 60 s. This module ships the **callable primitives**; their wiring into background tasks is explicitly deferred to Epic 3 (Telegram sink failures) and Epic 5 (worker heartbeats).
- **FR24a** (PRD line 848): 4 signals → 4 typed events. Story 2.10 ships the event-type infrastructure so recovery paths in later stories can reference known, schema-validated event types.

### Event type name validation

`schema_registry.register()` validates type name against `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. All 4 new types pass:
- `service.crashed` ✅
- `session.heartbeat_timeout` ✅
- `sink.delivery_failed` ✅
- `task.stop_requested` ✅

### `_extract_ids` in materializer.py

Current logic (line ~64 in `materializer.py`):
```python
task_id_raw = data.get("task_id") if env.type.startswith("task.") else None
session_id_raw = data.get("session_id") if env.type == "task.execution.started" else None
```

For the 4 new types:
- `service.crashed` — no `task_id` prefix: `task_id=None`, `session_id=None`. ✅ already handled.
- `session.heartbeat_timeout` — `session_id` in payload but extracted via `env.type == "task.execution.started"` check: `session_id=None`. This is fine for 2.10 — full session FK wiring is Epic 5 territory. Do NOT modify `_extract_ids` in this story.
- `sink.delivery_failed` — no `task_id` prefix: `(None, None)`. ✅
- `task.stop_requested` — `task_id` IS in payload AND type starts with `task.` → materializer will extract `task_id` but **no handler is registered**. The event row will be inserted with the task_id column populated. This is fine — the task row already exists; inserting an event row referencing it satisfies the FK. **No materializer handler is needed in 2.10; document this explicitly.**

### EventEnvelope.create() call pattern

From Story 2.1 (established in Story 2.4/2.5/2.8/2.9 — all prior stories use this exact signature):
```python
from events import EventEnvelope
from events.clock import Clock
from events.envelope import Actor

envelope = EventEnvelope.create(
    event_id=new_event_id(clock=clock),
    type="service.crashed",
    schema_version="1.0.0",
    emitted_at=clock.now(),
    emitted_at_monotonic_ns=clock.monotonic_ns(),
    actor=Actor(kind="service", id=actor_id),
    payload=ServiceCrashedPayload(service=service, exit_code=exit_code),
    request_id=None,       # failure events are internally-triggered; no HTTP request_id
    parent_event_id=None,
)
await writer.append(envelope)
```

### HeartbeatMonitor timing contract

The `overdue_sessions()` uses `> 2 × heartbeat_interval_s` (strictly greater than), matching the epics AC: "overdue by MORE THAN 2× its configured interval". At exactly 2×, the session is NOT yet overdue. Test `test_heartbeat_monitor_boundary_exactly_2x_not_overdue` must verify this boundary.

### FrozenClock usage for HeartbeatMonitor tests

`HeartbeatMonitor.__init__` takes a `Clock` instance. Tests use `FrozenClock` from `events.clock` (established in Story 2.2). To simulate time passing in tests without real `asyncio.sleep`, use a `FrozenClock` subclass or mock whose `now()` returns an increasing `datetime`. Pattern from existing tests in `test_recovery.py` and `test_snapshots.py`:

```python
from events.clock import FrozenClock
from datetime import datetime, timezone, timedelta

class AdvancingClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def monotonic_ns(self) -> int:
        return int(self._now.timestamp() * 1_000_000_000)
```

### What this story does NOT do

- **No materializer handlers** for the 4 new events — state transitions (e.g. `task.stop_requested` → `tasks.status = "stopped"`) are Epic 3/5 territory.
- **No process-monitoring loop** — `failure_detection.py` is emission primitives only. The asyncio background tasks that poll container health / session heartbeat timestamps / Telegram delivery counters are wired in Epic 3 (Telegram sink, Story 3.x) and Epic 5 (worker heartbeats, Story 5.x).
- **No `/v1/tasks/{id}/decisions` endpoint integration** — `task.stop_requested` will be emitted from that endpoint (Story 6.4); Story 2.10 just provides the emission primitive.
- **No `/v1/health` endpoint** — Story 2.9 deferred this; 2.10 does NOT pick it up (outside scope for clean AC boundary).
- **No session tracking in SQLite** — sessions table updates tied to heartbeat events land in Epic 5.

### File List (predicted)

**New (2):**
- `services/registry-state/src/registry_state/domain/failure_detection.py`
- `services/registry-state/src/registry_state/domain/test_failure_detection.py`

**Modified (3):**
- `services/registry-state/src/registry_state/domain/event_types.py` — 4 new payload models + 4 `register()` calls.
- `services/registry-state/src/registry_state/domain/__init__.py` — new `__all__` exports.
- `services/registry-state/src/registry_state/app/main.py` — 4 new payload imports in `noqa: F401` block.

### Previous Story Intelligence

- **Story 2.9** established the RFC 7807 error pattern and FastAPI skeleton; not directly relevant here.
- **Story 2.8** established the pattern for adding new event types to `event_types.py` — follow the same block structure (payload class → `register()` call at bottom). The new registrations go AFTER the 8 existing ones.
- **Story 2.5** established `handlers.py` + `materializer.py` dispatch. For 2.10: no new handlers needed; the `_extract_ids` function already handles unknown-type events gracefully with `(None, None)` → `task.stop_requested` is the only one that extracts a `task_id` but no handler is registered, which is the explicitly-documented intent.
- **Story 2.4** established `EventLogWriter` + `new_event_id`/`new_task_id` helper pattern. `failure_detection.py` uses `new_event_id(clock=clock)` for every emitted envelope.
- **Story 2.2** established `FrozenClock`, `Clock` protocol, `new_event_id()`. All used in tests.
- **Story 2.1** established `EventEnvelope.create()` + `Actor`. Import from `events.envelope`.

### References

- `epics.md` Story 2.10 (lines 847–865) — full BDD acceptance criteria.
- `architecture.md` line 633 — `failure_detection.py` filename mandate.
- `architecture.md` line 42 — registry-state owns `service.crashed` emission.
- `prd.md` FR24a — 4 explicit failure signals + typed events.
- `prd.md` NFR-R5 — 60s detection-to-emission SLA.
- `services/registry-state/src/registry_state/domain/event_types.py` — existing payload model pattern.
- `packages/events/src/events/schema_registry.py` — `register()` contract + `_EVENT_TYPE_RE` validation.
- `2-4-event-log-append-writer.md` — `EventLogWriter.append()` pattern.
- `2-8-clawhip-bridge-mcp-server.md` — prior "add 4 event types" story as template.
- Story 2.9 Dev Notes line "No `/v1/health`" — confirms 2.10 does not add health endpoint.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (executor subagent)

### Debug Log References

- `just test` → `418 passed, 6 skipped, 2 warnings` (baseline 397+6, +21 new tests).
- `just lint` → `Success: no issues found in 64 source files` (mypy strict, was 62).
- `just check-gates-self-test` → 3/3 green (`check_imports`, `check_event_registry`, `check_single_writer`).
- `just bootstrap-verify` → 13/13 workspace-member imports OK (registry_state stays at 0.2.0 — no version bump needed for this story; spec did not mandate one and bootstrap-verify accepts the existing version).

### Completion Notes List

- **Spec deviation — `Actor.kind="service"` → `kind="system"`**: the story spec (AC-3a Dev Notes example) specifies `Actor(kind="service", id=actor_id)`, but the canonical `ActorKind` Literal in `packages/events/src/events/envelope.py` does not include `"service"` (allowed: `operator | orchestrator | worker | system | clawhip`). Pydantic rejects `kind="service"` with `literal_error`. Used `"system"` instead, matching the convention used by every existing internal/test envelope in `services/registry-state` (see `test_handlers.py`, `test_main.py`, `test_recovery.py`). Widening `ActorKind` would touch the shared envelope contract — out of scope for Story 2.10. Documented inline in `failure_detection.py`.
- **Spec deviation — `request_id=None`**: Dev Notes example shows `request_id=None`, but `EventEnvelope.request_id` is a required `str` field (validated against the bare-UUIDv7 regex). Generated a fresh `new_request_id(clock=clock)` per emitted envelope — internally-triggered events get their own request id, mirroring how `middleware.py` synthesizes one when an HTTP client omits it.
- **AC-7 test count**: 21 tests delivered (target ≥18, range 18–22).
- **AC-8 nuance**: `check_event_registry.py`'s AST scanner targets `EventEnvelope(...)` and `<known>.emit(...)` calls only — it does NOT inspect `EventEnvelope.create(...)` (Attribute call, not in `_EMIT_NAMES`). So the gate is vacuously green for the 4 emission functions in `failure_detection.py`. The 4 `register()` calls in `event_types.py` still populate `EVENT_TYPES`, so any future direct `EventEnvelope(type="service.crashed", ...)` use would be greenlit by the scanner. Documented in Task 6 checkbox.
- **AC-5 confirmed**: `handlers.py` and `materializer.py` were not touched. `_extract_ids` already handles unknown event types as `(None, None)`, and `task.stop_requested` (which DOES extract a `task_id`) is dispatched through `apply_one` but no handler is registered — the event row is inserted with the FK populated but no state mutation, exactly as spec requires.

### File List

**New (2):**
- `services/registry-state/src/registry_state/domain/failure_detection.py`
- `services/registry-state/src/registry_state/domain/test_failure_detection.py`

**Modified (3):**
- `services/registry-state/src/registry_state/domain/event_types.py` — 4 new payload models + 4 `register()` calls + `datetime` import + `__all__` extended.
- `services/registry-state/src/registry_state/domain/__init__.py` — re-exports for the 4 new payloads + 2 helper classes + 4 `emit_*` functions.
- `services/registry-state/src/registry_state/app/main.py` — 4 new payload imports added to the `noqa: F401` block so `register()` side-effects fire on startup.

## Change Log

| Date       | Version | Description                                                                                                                          | Author              |
|------------|---------|--------------------------------------------------------------------------------------------------------------------------------------|---------------------|
| 2026-04-26 | 1.0     | Story 2.10 implemented: 4 failure-detection event types + emission primitives + HeartbeatMonitor + SinkFailureTracker. Final test count 418 passed / 6 skipped (+21 new). mypy strict on 64 source files. Single atomic commit per AC-13 (SHA filled below). | executor (Opus 4.7) |
