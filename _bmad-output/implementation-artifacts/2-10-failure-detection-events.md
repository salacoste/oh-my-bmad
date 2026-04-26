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

### Review Findings

Generated post-1.0 by `/bmad-code-review` against the scaffold commit. Three parallel adversarial reviewers (Acceptance Auditor, Blind Hunter, Edge Case Hunter — all opus). 0 CRITICAL, 18 MAJOR, 25 MINOR — all 43 actionable findings addressed below. Three findings are duplicates of others (F31→F2, F33→F23, F60→F36) and noted in line. 4 findings dismissed for design reasons.

- [x] **[Review][Patch] Spec deviation: `Actor.kind="service"` → `kind="system"`** [`failure_detection.py` emit_* funcs] — **MAJOR.** Spec example wrong; `ActorKind` Literal does not include `"service"`. Fix: keep `kind="system"` as default for `service.crashed`, `session.heartbeat_timeout`, `sink.delivery_failed`. For `task.stop_requested`, accept optional `actor_kind: ActorKind = "system"` parameter so operator-initiated stops can use `kind="operator"`. Spec amendment documents the divergence.

- [x] **[Review][Patch] Spec deviation: `request_id=None` → `new_request_id`** [`failure_detection.py` emit_* funcs] — **MAJOR.** `EventEnvelope.request_id` is required validated UUIDv7; passing `None` would fail validation. Fix: keep current generation; add optional `request_id: str | None = None` parameter to all emit functions so callers can correlate multiple emissions from the same polling tick. When `None`, synthesize via `new_request_id(clock=clock)`.

- [x] **[Review][Patch] `HeartbeatMonitor` blindly trusts `clock.now()`; tz-mixed crash possible** [`failure_detection.py:HeartbeatMonitor.record_heartbeat` / `overdue_sessions`] — **MAJOR.** Subtraction across naive/aware datetimes raises `TypeError` at runtime under pressure. Fix: enforce tz-aware datetime on store via new `_assert_aware(dt, *, field=...)` helper that raises `ValueError` if naive. Applied at every clock-now read AND at `record_heartbeat(at=...)` ingress.

- [x] **[Review][Patch] `HeartbeatMonitor.record_heartbeat` doesn't accept external `at`** [`failure_detection.py:HeartbeatMonitor.record_heartbeat`] — **MAJOR.** Fix: add `at: datetime | None = None` param. When `None`, use `clock.now()`. When provided, validate tz-awareness and store directly. Enables seeding from event-log replay.

- [x] **[Review][Patch] `SinkFailureTracker.record_success` drops `last_error` info** [`failure_detection.py:SinkFailureTracker.record_success`] — **MAJOR.** Original behavior reset to `(0, None)` losing operator signal. Fix: counter resets to `0` but **PRESERVES** `last_error` so operators can see "what was the failure cause when the streak ended". Documented in docstring. (This is the OPPOSITE of what the auditor flagged, but better operational signal.)

- [x] **[Review][Patch] `should_emit` non-edge-triggered → duplicate event spam** [`failure_detection.py:SinkFailureTracker.should_emit`] — **MAJOR.** A streak that stays past threshold across polling ticks would re-emit on every tick. Fix: add `_emitted_at_count: dict[str, int]` tracking the count at which we last emitted; `should_emit` returns `True` only when `current_count >= threshold` AND `current_count > emitted_at_count`. Add `mark_emitted(sink_name)` method that records the emit checkpoint, plus combined `should_emit_and_mark` for atomicity.

- [x] **[Review][Patch] `overdue_sessions` non-edge-triggered → duplicate heartbeat_timeout spam** [`failure_detection.py:HeartbeatMonitor.overdue_sessions`] — **MAJOR.** Fix: add `_emitted: set[str]` tracking already-notified sessions. `overdue_sessions()` returns only NEWLY-overdue (not in `_emitted`). Add `mark_emitted(session_id)` method; `record_heartbeat` (refresh) and `remove_session` clear from `_emitted`. Added `overdue_sessions_and_mark()` for atomicity.

- [x] **[Review][Patch] `Actor.kind="system"` for `task.stop_requested` is wrong for operator action** [`failure_detection.py:emit_task_stop_requested`] — **MAJOR.** Fix: add `actor_kind: ActorKind = "system"` parameter; docstring guides callers to pass `"operator"` when an operator issued the /stop.

- [x] **[Review][Patch] `actor_id="registry-state"` default for `emit_service_crashed` misattributes the actor** [`failure_detection.py:emit_service_crashed`] — **MAJOR.** The crashing service is the actor; registry-state is only the detector. Fix: change default to `actor_id=None`, and when `None` default to the value of the `service` parameter so `Actor.id == service` for `service.crashed`. Documented the detector-vs-actor distinction in docstring.

- [x] **[Review][Patch] `SessionHeartbeatTimeoutPayload.last_heartbeat_at` lacks tz enforcement** [`event_types.py`] — **MAJOR.** Fix: switch to Pydantic `AwareDatetime` so naive datetimes are rejected at the payload boundary (defense-in-depth on top of the envelope-level enforcement).

- [x] **[Review][Patch] `ServiceCrashedPayload.exit_code` accepts 0** [`event_types.py`] — **MAJOR.** Docstring says non-zero. Fix: `@field_validator("exit_code")` rejects `exit_code == 0` with a clear message. Test added.

- [x] **[Review][Patch] `SinkDeliveryFailedPayload.consecutive_failures` allows 0/negative** [`event_types.py`] — **MAJOR.** Fix: `Field(ge=1)`. Test added.

- [x] **[Review][Patch] ID fields lack min_length / pattern enforcement** [`event_types.py`] — **MAJOR.** Fix: `service`, `sink_name`, `actor_id` get `Field(min_length=1, max_length=128)`; `session_id` and `task_id` get UUIDv7-prefix patterns. Tests added for malformed inputs.

- [x] **[Review][Patch] `SinkDeliveryFailedPayload.last_error` has no length cap** [`event_types.py`] — **MAJOR.** Fix: `Field(default=None, max_length=4096)`. Test added.

- [x] **[Review][Patch] Autouse fixture re-registers globally without teardown** [`test_failure_detection.py`] — **MAJOR.** Pollutes registry across test sessions. Fix: change to a snapshot/restore pattern — capture `dict(REGISTRY)` before the test, re-register the 4 Story 2.10 types idempotently, and on teardown call `unregister_all()` then replay the snapshot via `register()`.

- [x] **[Review][Patch] `_AdvancingClock` not declared as `Clock`; monotonic_ns from wall-clock** [`test_failure_detection.py`] — **MAJOR.** Fix: subclass `events.clock.Clock` (a `@runtime_checkable` Protocol). Track `_mono_ns` independently; `advance(seconds)` increments it by `int(seconds * 1e9)` so monotonic readings match the wall-time advance count without needing `time.time()` seeds.

- [x] **[Review][Patch] Concurrency: emit gating not atomic across `await`** [`failure_detection.py`] — **MAJOR.** Fix: documented module-level concurrency contract — tracker mutations are sync (no internal awaits), and callers MUST use the new combined `*_and_mark` helpers (`should_emit_and_mark`, `overdue_sessions_and_mark`) to atomically check + mark when concurrent invocation is possible. Avoids over-engineering with `asyncio.Lock` in the API.

- [x] **[Review][Patch] `last_error` sanitization offloaded to caller; no defense-in-depth** [`failure_detection.py:emit_sink_delivery_failed`] — **MAJOR.** Fix: add `_redact_last_error(s) -> str | None` helper that masks Telegram bot tokens, `Bearer ...` headers, and `password=` / `secret=` / `token=` / `api_key=` k/v patterns. Applied in `emit_sink_delivery_failed` before constructing the payload. Three unit tests cover the patterns plus a None passthrough.

- [x] **[Review][Patch] AC-7 dual-write semantic for `Actor.id`** [`test_failure_detection.py`] — **MINOR.** Added `test_emit_service_crashed_actor_id_defaults_to_service` (and dual-write assertion in existing `test_emit_task_stop_requested_actor_id_preserved`) to verify `Actor.id` reflects the resolved actor_id after defaults are applied.

- [x] **[Review][Patch] AC-5 docstring partial** [`failure_detection.py`] — **MINOR.** Fix: appended one sentence to module docstring noting that `task.stop_requested` populates `events.task_id` FK column with no handler-driven state mutation in 2.10.

- [x] **[Review][Patch] `HeartbeatMonitor.timeout_threshold_s` property undocumented in spec** [`failure_detection.py`] — **MINOR.** Spec amendment documents.

- [x] **[Review][Patch] `SinkFailureTracker.failure_threshold` property undocumented in spec** [`failure_detection.py`] — **MINOR.** Spec amendment documents.

- [x] **[Review][Patch] `HeartbeatMonitor` raises on non-positive interval undocumented** [`failure_detection.py`] — **MINOR.** Test `test_heartbeat_monitor_rejects_non_positive_interval` exercises the `ValueError` branch (and a separate test covers `NaN`/`inf`).

- [x] **[Review][Patch] `SinkFailureTracker` raises on threshold < 1 undocumented** [`failure_detection.py`] — **MINOR.** Test `test_sink_failure_tracker_rejects_threshold_below_one` exercises the `ValueError` branch.

- [x] **[Review][Patch] AC-8 scanner gap** — **MINOR.** Closed via explicit `test_all_2_10_event_types_in_registry` that asserts the 4 new types are present in `EVENT_TYPES`. Module-attribute access (`_schema_registry.EVENT_TYPES`) used to honour the live binding (PEP 562) since `register()` rebuilds the cache on every call.

- [x] **[Review][Patch] `timeout_threshold_s` int promotion** [`failure_detection.py`] — **MINOR.** Fix: `float(2 * self._interval_s)` to avoid `int` passing through to a strict-float field.

- [x] **[Review][Patch] `overdue_sessions` O(N)** [`failure_detection.py`] — **MINOR.** Deferred per spec scope; documented in class docstring with "TODO Phase 2 if N grows large".

- [x] **[Review][Patch] `record_heartbeat` no clock regression check** [`failure_detection.py`] — **MINOR.** Fix: when incoming `at` is older than the prior stored timestamp, log a warning and IGNORE the regression (keep the newer timestamp). Test `test_heartbeat_monitor_clock_regression_ignored` covers it.

- [x] **[Review][Patch] `__all__` ordering** [`failure_detection.py`, `__init__.py`, `event_types.py`] — **MINOR.** Fix: alphabetized all `__all__` lists; ruff RUF022 stays green.

- [x] **[Review][Patch] `from datetime import datetime` runtime import** [`event_types.py`] — **MINOR.** Fix: with `from __future__ import annotations` already present, `datetime` is no longer imported at runtime — `AwareDatetime` (which is itself an annotated alias) is the only datetime reference. `TYPE_CHECKING` block left in place for symmetry.

- [x] **[Review][Patch] (covered by F2)** — `request_id` parameter for correlation. Same fix as F2.

- [x] **[Review][Patch] `parent_event_id` should chain task.stop_requested to operator command** [`failure_detection.py`] — **MINOR.** Fix: add `parent_event_id: str | None = None` parameter to ALL four emit functions (not just stop_requested — the others can chain to a polling-tick parent in future). Pass through to `EventEnvelope.create(parent_event_id=...)`.

- [x] **[Review][Patch] (covered by F23)** — `record_heartbeat` `ValueError` branch test.

- [x] **[Review][Patch] No structured logging in trackers** [`failure_detection.py`] — **MINOR.** Fix: added `log = logging.getLogger(__name__)`. Logs `debug` on heartbeat record + sink failure record, `info` on overdue detection, `warning` on heartbeat regression.

- [x] **[Review][Patch] No test for `register()` idempotency for new types** [`test_failure_detection.py`] — **MINOR.** Added `test_register_new_event_types_is_idempotent`.

- [x] **[Review][Patch] No test asserts request_id is fresh per emission** [`test_failure_detection.py`] — **MINOR.** Combined into `test_emit_generates_distinct_event_and_request_ids` which asserts both event_id AND request_id differ across two emissions even under FrozenClock (entropy comes from rand bits, confirmed by reading `events.ids.new_uuid7`).

- [x] **[Review][Patch] No test for emission failure path** [`test_failure_detection.py`] — **MINOR.** Added `test_emit_propagates_writer_errors` that closes the writer and asserts `RuntimeError` on the next emit.

- [x] **[Review][Patch] Test `test_heartbeat_monitor_session_overdue_after_2x_interval` asserts last_at fragility** [`test_failure_detection.py`] — **MINOR.** Fix: added `HeartbeatMonitor.last_heartbeat_at(session_id)` accessor; test reads via the accessor instead of capturing from outside.

- [x] **[Review][Patch] `last_error` typed contract** — **MINOR.** Covered by F14.

- [x] **[Review][Patch] `_state` heterogeneous tuple** [`failure_detection.py:SinkFailureTracker`] — **MINOR.** Fix: replaced `tuple[int, str | None]` with `@dataclass(frozen=True) SinkFailureState(count, last_error)`. `get_state` return type updated; existing tests rewritten to assert against the dataclass.

- [x] **[Review][Patch] `overdue_sessions` returns list[tuple[str, datetime]]** — **MINOR.** Kept tuples; small enough; deferred dataclass conversion.

- [x] **[Review][Patch] `# type: ignore[call-arg]`** — **MINOR.** Kept; necessary for testing `extra="forbid"` rejection.

- [x] **[Review][Patch] dismissed.** F43–F44 cosmetic.

- [x] **[Review][Patch] Inconsistent Actor import path** [`failure_detection.py`] — **MINOR.** Fix: `from events import Actor, EventEnvelope` (Actor is exported from `events/__init__.py`). `ActorKind` still imported from `events.envelope` (not re-exported at the top level).

- [x] **[Review][Patch] emit_* don't validate writer not closed** [`failure_detection.py`] — **MINOR.** Deferred — closed-writer error from `EventLogWriter.append` is informative enough; the new `test_emit_propagates_writer_errors` exercises the path.

- [x] **[Review][Patch] Test imports internal adapter functions** [`test_failure_detection.py`] — **MINOR.** Kept using `current_day_path` / `read_log_lines` (already public from `registry_state.adapters.event_log`).

- [x] **[Review][Patch] No test for future `last_heartbeat_at`** — **MINOR.** Added `test_heartbeat_monitor_accepts_future_last_at` (clock skew). Stored value is preserved; `overdue_sessions` returns nothing while now < at + 2× interval.

- [x] **[Review][Patch] `domain/__init__.py` asymmetric public surface** [`domain/__init__.py`] — **MINOR.** Fix: re-exported all 8 prior payload classes (TaskCreated/PlanningStarted/PlanReady/ExecutionStarted/BlockerRaised/SummaryEmitted/ApprovalRequested/Completed) plus the new `SinkFailureState` dataclass. `__all__` reorganized alphabetically.

- [x] **[Review][Patch] No `__repr__` on trackers** [`failure_detection.py`] — **MINOR.** Fix: added `__repr__` to both `HeartbeatMonitor` and `SinkFailureTracker` that includes session/sink count + threshold. Smoke tests added.

- [x] **[Review][Patch] Inconsistent API: `actor_id` required vs default** [`failure_detection.py:emit_task_stop_requested`] — **MINOR.** Kept required (operator action MUST identify actor); other 3 (system-initiated) get sensible defaults. Documented in docstrings.

- [x] **[Review][Patch] No test asserting types in EVENT_TYPES via public API** — **MINOR.** Covered by F25.

- [x] **[Review][Patch] Parameter ordering** [`failure_detection.py:emit_sink_delivery_failed`] — **MINOR.** Kept current (all kw-only via `*`). Documented in docstring that order doesn't matter.

- [x] **[Review][Patch] `timeout_threshold_s` permits NaN/inf** [`event_types.py`] — **MINOR.** Fix: `Field(gt=0, allow_inf_nan=False)` on `timeout_threshold_s`. Test `test_session_heartbeat_timeout_rejects_inf_threshold` covers it.

- [x] **[Review][Patch] `HeartbeatMonitor` doesn't reject NaN/inf interval** [`failure_detection.py`] — **MINOR.** Fix: in `__init__`, reject `not math.isfinite(heartbeat_interval_s)` with `ValueError`. Test `test_heartbeat_monitor_rejects_nan_interval` covers it.

- [x] **[Review][Patch] `SinkFailureTracker` counter unbounded** [`failure_detection.py`] — **MINOR.** Fix: cap `count` at 9999 (per-sink saturation) to prevent payload bloat. Test `test_sink_failure_tracker_count_caps_at_9999` covers it.

- [x] **[Review][Patch] dismissed/cosmetic.** F57–F58.

- [x] **[Review][Patch] UUIDv7 collision under FrozenClock** — **MINOR.** Verified by reading `events.ids.new_uuid7`: `os.urandom(10)` provides 74 bits of randomness independent of timestamp; under FrozenClock the timestamp bits are fixed but the rand bits ensure distinct UUIDs. NOT a real bug. Test `test_emit_generates_distinct_event_and_request_ids` makes the guarantee explicit.

- [x] **[Review][Patch] No test for distinct event_id across emissions** — **MINOR.** Covered by F36.

- [x] **[Review][Patch] exit_code bool→int** — **MINOR.** Pydantic strict mode rejects bool→int coercion. No fix needed.

Dismissed (documented for auditability):

- F27 `overdue_sessions` O(N) optimization — N << 1000 in Phase 1; documented as "TODO Phase 2 if N grows large".
- F46 emit_* explicit closed-writer guard — duplicates `EventLogWriter.append` poison check; no benefit.
- F47 internal adapter import in tests — `current_day_path` / `read_log_lines` are public.
- F61 strict-mode bool→int — Pydantic strict mode already rejects bool inputs.

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

### Spec Amendments (from code review)

The post-1.0 review pass introduced the following deviations from the original AC text. These are intentional improvements; the AC numbers stay as written and the behavior described here governs.

1. **`Actor.kind="system"` is the canonical default**; `task.stop_requested` accepts an `actor_kind: ActorKind = "system"` parameter so operator-initiated stops can pass `actor_kind="operator"`. The original spec's `kind="service"` example was unrepresentable (the `ActorKind` Literal allows: `operator | orchestrator | worker | system | clawhip`).
2. **`request_id` is generated, not `None`**. `EventEnvelope.request_id` is a required validated UUIDv7 field; the original spec sketch's `request_id=None` would never validate. All four `emit_*` functions accept an optional `request_id: str | None = None` parameter; when `None`, `new_request_id(clock=clock)` is synthesized.
3. **New `emit_*` parameters**: every emit function gains optional `request_id` (correlation across one polling tick) and `parent_event_id` (causality chaining). Callers MAY pass them; defaults stay backwards-compatible.
4. **New tracker methods**: `HeartbeatMonitor.last_heartbeat_at(session_id)`, `mark_emitted(session_id)`, `overdue_sessions_and_mark()`, plus `record_heartbeat(at=...)` for replay-time seeding. `SinkFailureTracker.mark_emitted(sink_name)`, `should_emit_and_mark(sink_name)`. The `*_and_mark` helpers eliminate the await-window race for concurrent callers.
5. **Validation enforcement** at the payload boundary: `ServiceCrashedPayload.exit_code != 0`, `SinkDeliveryFailedPayload.consecutive_failures >= 1`, ID-shaped fields carry `min_length`/`pattern` (UUIDv7 prefix where applicable), `last_error` capped at 4096 chars, `last_heartbeat_at` is `AwareDatetime` (naive rejected at the payload level), `timeout_threshold_s` rejects `NaN`/`inf`.
6. **Defense-in-depth `_redact_last_error`** for sink failures. `emit_sink_delivery_failed` runs the redactor over `last_error` before constructing the payload, masking common Telegram bot tokens, `Bearer ...` headers, and `password=`/`secret=`/`token=`/`api_key=` k/v patterns. The caller's sanitization contract is unchanged; this is a safety net.
7. **AC-8 scanner gap acknowledged** with explicit `EVENT_TYPES` membership test (`test_all_2_10_event_types_in_registry`). The AST scanner targets `EventEnvelope(...)` literal-type calls only; `EventEnvelope.create(...)` is invisible to it but the test makes registry presence visible to the suite.
8. **`SinkFailureState` frozen dataclass** replaces the heterogeneous `tuple[int, str | None]` from the original spec. `SinkFailureTracker.get_state` returns the dataclass; the field names (`count`, `last_error`) are stable.
9. **`record_success` preserves `last_error`** (counter resets to 0). The operational signal — "what was the failure cause when the streak ended?" — is more valuable than the cleared state.

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
- `services/registry-state/src/registry_state/domain/event_types.py` — 4 new payload models + 4 `register()` calls + `datetime` import + `__all__` extended. v1.1: payload-level validators (`exit_code != 0`, `consecutive_failures >= 1`, ID patterns, `AwareDatetime`, `last_error` length cap, `timeout_threshold_s` finite check).
- `services/registry-state/src/registry_state/domain/__init__.py` — re-exports for the 4 new payloads + 2 helper classes + 4 `emit_*` functions. v1.1: symmetric re-export of all 8 prior payload classes + `SinkFailureState` dataclass.
- `services/registry-state/src/registry_state/app/main.py` — 4 new payload imports added to the `noqa: F401` block so `register()` side-effects fire on startup.

## Change Log

| Date       | Version | Description                                                                                                                          | Author              |
|------------|---------|--------------------------------------------------------------------------------------------------------------------------------------|---------------------|
| 2026-04-26 | 1.0     | Story 2.10 implemented: 4 failure-detection event types + emission primitives + HeartbeatMonitor + SinkFailureTracker. Final test count 418 passed / 6 skipped (+21 new). mypy strict on 64 source files. Atomic commit `0c3e841`. | executor (Opus 4.7) |
| 2026-04-26 | 1.1     | Code review — 43 adversarial findings (18 MAJOR, 25 MINOR) all addressed; 0 CRITICAL. New emit-fn params (`request_id`, `parent_event_id`, `actor_kind`); edge-triggered trackers with `*_and_mark` helpers; payload validators (`exit_code != 0`, `AwareDatetime`, ID patterns, length caps); `_redact_last_error` defense-in-depth; `SinkFailureState` frozen dataclass; `record_success` preserves `last_error`; structured logging; `__repr__`. Final test count 463 passed / 6 skipped (+45 new vs v1.0). Mypy strict on 64 source files. Fix commit: `<pending>`. | executor (Opus 4.7) |
