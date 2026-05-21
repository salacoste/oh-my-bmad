# Story 12.1 — Budget supervisor module in worker-wrapper

Status: **ready-for-dev**

## Story

**As** the platform operator
**I want** worker-wrapper to react to a `task.budget_exceeded` event for the currently-running task by promptly terminating the `claude` subprocess (SIGTERM → wait ≤5s → SIGKILL if still alive)
**so that** a task that blows past its token-ceiling stops burning operator budget within seconds rather than running unbounded until natural completion (FR66, NFR-R8: p99 ≤ 5s from event-emit to subprocess-exit).

Story 12.1 ships the **enforcement leg** of Epic 12's per-task budget loop. Three moving parts:

1. **`budget_supervisor.py`** — new `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py`. Subscribes to the JSONL event log via the established `read_log_lines` + `current_day_path` polling pattern (already in use by `services/worker-wrapper/src/worker_wrapper/adapters/approval_waiter.py:5`). Filters for `task.budget_exceeded` events matching the active `task_id`. On match: invokes a caller-supplied termination hook (`async def terminate(*, deadline_s: float) -> None`) that wraps `ClaudeCodeRunner._process.terminate()` → `await asyncio.wait_for(_process.wait(), timeout=5.0)` → `_process.kill()` on TimeoutError.
2. **Lifespan integration** — `services/worker-wrapper/src/worker_wrapper/app/main.py` spawns the supervisor as a background asyncio task at task-start, cancels it cleanly when `ClaudeCodeRunner` exits normally OR when the supervisor itself fires the termination.
3. **Integration test** — `tests/integration/test_budget_enforcement_latency.py` emits a `task.budget_exceeded` envelope to a tmp JSONL log, runs a long-sleep subprocess as the "claude" target, asserts subprocess exit code + total elapsed wall-time < 5s (NFR-R8 p99 ceiling).

Per Epic 11 retro L1: cross-cutting story (new subprocess control + new event-log subscriber + new lifespan task) → **mandatory 3-lane review at `/bmad-code-review 12.1` regardless of complexity estimate**. Spec D1-D5 pre-resolved here.

## Acceptance criteria

### AC1 — `budget_supervisor.py` module + public API

New module: `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py`. Mirror placement convention of `approval_gate.py` / `lifecycle.py` in the same directory.

Public API:

```python
async def watch_for_budget_exceeded(
    *,
    task_id: str,
    event_log_dir: Path,
    terminate_callback: Callable[[], Awaitable[None]],
    poll_interval_s: float = 0.5,
    clock: Clock,
    cancel_event: asyncio.Event,
) -> _BudgetSupervisorResult:
    """Tail the JSONL event log for `task.budget_exceeded` events matching task_id.

    On the FIRST matching event:
      1. Invoke `terminate_callback()` and `await` it (caller wraps subprocess
         SIGTERM → wait ≤5s → SIGKILL escalation).
      2. Return `_BudgetSupervisorResult(triggered=True, ...)` describing
         the event payload + termination wall-clock duration.

    If `cancel_event` is set before any matching event arrives, return
    `_BudgetSupervisorResult(triggered=False)` cleanly (normal task
    completion path).

    Polling interval is `poll_interval_s` (default 0.5s — gives ~10 retries
    inside the 5s NFR-R8 budget). Caller's `terminate_callback` MUST
    enforce its own 5s deadline; the supervisor only owns the
    detect-and-dispatch step.
    """
```

`_BudgetSupervisorResult` is a frozen dataclass with fields:
- `triggered: bool` — true if a `task.budget_exceeded` was observed; false on cancel-clean path
- `event_id: str | None` — the `event_id` of the matching envelope
- `tokens_used: int | None` — copied from `TaskBudgetExceededPayload.tokens_used`
- `token_limit: int | None` — copied from `TaskBudgetExceededPayload.token_limit`
- `detection_latency_s: float | None` — time from event `emitted_at` to supervisor noticing (clock-based; bounded by `poll_interval_s + JSONL fdatasync delay`)
- `termination_latency_s: float | None` — wall-clock duration of `terminate_callback()` (separately measured; covers the 5s NFR-R8 budget end-to-end)

The supervisor MUST be **pure async** — no thread spawn, no blocking I/O on the event loop. JSONL read via existing `events.log_reader.read_log_lines` (already streaming-bounded, blank-line-tolerant per Story 11.4 PP14 lesson).

Self-verification:
- `grep -nE "^async def watch_for_budget_exceeded" services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` returns exactly one line.
- `grep -nE "subprocess\.|os\.kill|signal\." services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` returns ZERO lines (subprocess control lives in adapter layer; supervisor only orchestrates via callback).
- `uv run python scripts/check_imports.py` exits 0.
- Unit tests (AC4) all pass.

### AC2 — Termination callback wired to `ClaudeCodeRunner`

In `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`:

Add a method `async def terminate_with_grace(self, *, grace_period_s: float = 5.0) -> _TerminationResult` that:

1. Captures wall-clock start time via injected `Clock`.
2. If `self._process is None` → return `_TerminationResult(method="noop", elapsed_s=0.0)`.
3. Calls `self._process.terminate()` (sends SIGTERM).
4. `await asyncio.wait_for(self._process.wait(), timeout=grace_period_s)` → on success, return `_TerminationResult(method="sigterm", elapsed_s=<measured>)`.
5. On `asyncio.TimeoutError`: call `self._process.kill()` (SIGKILL); `await self._process.wait()` (uncapped — kernel guarantees ≤O(1)); return `_TerminationResult(method="sigkill", elapsed_s=<measured>)`.

`_TerminationResult` is a frozen dataclass: `method: Literal["noop","sigterm","sigkill"]`, `elapsed_s: float`, `exit_code: int | None`.

This method is callable by `budget_supervisor`'s `terminate_callback`. Lifespan wires the wiring:

```python
async def _terminate_for_budget() -> None:
    result = await runner.terminate_with_grace(grace_period_s=5.0)
    _log.info("budget_termination", method=result.method, elapsed_s=result.elapsed_s, exit_code=result.exit_code)
```

Self-verification:
- `grep -nE "^    async def terminate_with_grace" services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` returns exactly one line.
- New unit test `test_terminate_with_grace_sigterm_succeeds` — mock subprocess that exits in 1s on SIGTERM; assert `method="sigterm"` + `elapsed_s < 2.0`.
- New unit test `test_terminate_with_grace_sigkill_escalation` — mock subprocess that ignores SIGTERM; assert `method="sigkill"` + `elapsed_s` between 5.0 and 5.5 (the timeout boundary).
- New unit test `test_terminate_with_grace_noop_when_no_process` — call without spawning; assert `method="noop"`.

### AC3 — Lifespan integration in `worker_wrapper/app/main.py`

In `services/worker-wrapper/src/worker_wrapper/app/main.py` `run_task` flow (or whichever orchestration function owns the per-task lifecycle — verify exact location during impl):

After `ClaudeCodeRunner` is constructed + subprocess spawned:

```python
budget_cancel = asyncio.Event()
budget_task = asyncio.create_task(
    watch_for_budget_exceeded(
        task_id=task_id,
        event_log_dir=event_log_dir,
        terminate_callback=lambda: runner.terminate_with_grace(grace_period_s=5.0),
        clock=clock,
        cancel_event=budget_cancel,
    ),
    name=f"budget-supervisor-{task_id}",
)
```

When `runner.run()` completes naturally (or raises), set `budget_cancel.set()` + `await budget_task` (with reasonable join timeout, e.g. 1s).

If the supervisor's task completes FIRST (because it triggered a termination), `runner.run()` will see the SIGTERM/SIGKILL via subprocess exit; capture its returncode + emit `task.budget_enforcement_triggered` is **Story 12.2 scope** — NOT this story. Story 12.1 just terminates; Story 12.2 emits the audit event.

For Story 12.1: ALSO update the existing `task.completed` emission path to skip emission when `_BudgetSupervisorResult.triggered=True` — that case will emit a different event via Story 12.2's path. Add a TODO marker if Story 12.2 hasn't landed:

```python
if budget_result and budget_result.triggered:
    # Story 12.1 — terminated by budget enforcement; task.completed NOT emitted.
    # Story 12.2 will emit task.budget_enforcement_triggered here (FR67).
    _log.info("budget_enforced_task_terminated", task_id=task_id, ...)
    return
```

Self-verification:
- `grep -nE "watch_for_budget_exceeded|budget-supervisor-" services/worker-wrapper/src/worker_wrapper/app/main.py` returns at least 2 lines.
- Integration test `test_budget_supervisor_integrates_with_runner_lifespan` (in AC5) exercises full flow end-to-end.

### AC4 — Unit tests for `budget_supervisor.py`

Add to `services/worker-wrapper/src/worker_wrapper/domain/test_budget_supervisor.py`:

- `test_watch_returns_clean_when_cancel_event_set_first` — start supervisor; immediately set cancel_event; assert `triggered=False`, terminate_callback NOT called.
- `test_watch_fires_callback_on_matching_event` — pre-write a `task.budget_exceeded` envelope for `task_id="t-1"` to a tmp JSONL; start supervisor with same task_id; assert `triggered=True`, callback called exactly once.
- `test_watch_ignores_other_task_ids` — pre-write a `task.budget_exceeded` for `task_id="t-OTHER"`; start supervisor watching `task_id="t-1"`; set cancel_event after 1s; assert `triggered=False`, callback NOT called.
- `test_watch_ignores_non_budget_event_types` — pre-write a `task.completed` envelope; assert `triggered=False`, callback NOT called.
- `test_watch_handles_corrupted_jsonl_line_gracefully` — pre-write a malformed `{"oops}` line followed by a valid budget event; assert `triggered=True` (recovery per Story 11.4 PP14 / AC3 reader pattern).
- `test_watch_records_detection_latency` — emit event with `emitted_at = clock_start`; supervisor's clock advances `poll_interval_s + small_delta`; assert `result.detection_latency_s` is within `[0.5, 1.5]` seconds.
- `test_watch_records_termination_latency_from_callback` — callback sleeps 2.5s before returning; assert `result.termination_latency_s` is `~2.5s` (clock-injected; not wall-clock).

### AC5 — Integration test for NFR-R8 latency (5s p99)

New file: `tests/integration/test_budget_enforcement_latency.py`.

Test `test_budget_enforced_subprocess_exits_within_5s_e2e`:

1. **Setup:** Create tmp `event_log_dir`. Spawn a real-but-trivial subprocess via `asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(300)")` — a 5-minute sleep that ONLY SIGTERM can stop.
2. **Arrange supervisor:** Construct a `ClaudeCodeRunner`-like wrapper holding `self._process = the_subprocess`, hand it to `watch_for_budget_exceeded(...)` with `terminate_callback=lambda: wrapper.terminate_with_grace(grace_period_s=5.0)`.
3. **Trigger:** Write a `task.budget_exceeded` envelope to the JSONL (via `EventLogWriter.append` to match production wire format).
4. **Measure:** Capture `t0 = clock.now()` IMMEDIATELY before the write. After `await budget_task`, capture `t1 = clock.now()` (or use a real `SystemClock` if FrozenClock is too synthetic for this test).
5. **Assert:** 
   - `(t1 - t0).total_seconds() < 5.0` — NFR-R8 p99 ceiling
   - `the_subprocess.returncode is not None` — subprocess actually exited
   - `result.triggered is True`
   - `result.detection_latency_s + result.termination_latency_s ≈ (t1 - t0)` (sanity)
6. **Repeat 5×** with different random `task_id`s + budget payloads; assert ALL 5 runs satisfy the 5s ceiling. Mark `@pytest.mark.slow` if individual runs approach 1-2s (5× = 5-10s test).

Per Epic 11 retro L6 (test fixture realism): use real `EventEnvelope.create(...)` + `EventLogWriter.append(...)` for the budget event — NOT a hand-rolled dict.

Self-verification:
- `grep -nE "test_budget_enforced_subprocess_exits_within_5s_e2e" tests/integration/test_budget_enforcement_latency.py` returns the test.
- Test passes locally (`uv run pytest -q tests/integration/test_budget_enforcement_latency.py`).
- Test passes under CI (`-m "not slow"` excludes — confirm marker placement matches separability test convention).

### AC6 — Validation gates

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict services/worker-wrapper packages/events
uv run python scripts/check_imports.py
uv run python scripts/check_event_registry.py
uv run python scripts/check_single_writer.py  # CRITICAL — supervisor reads JSONL, does NOT emit
uv run pytest -x -q services/worker-wrapper packages/events tests/integration/test_budget_enforcement_latency.py
uv run pytest -x -q -m "not slow"
just bootstrap-verify
```

All exit 0. Expected baseline shift: 3068 → ~3080 tests (~12 new). Mypy unchanged.

## Decisions (resolve BEFORE implementation per AI-3 cadence rule)

### D1 — Subscriber poll interval default

**Problem:** `watch_for_budget_exceeded` polls the JSONL log. Too tight (≤100ms) wastes CPU + creates fdatasync race risk; too loose (≥1s) eats into the 5s NFR-R8 budget.

**Options:**
- **(a) `poll_interval_s: float = 0.5`** (RECOMMENDED) — 10 retries inside the 5s budget; leaves ≥4.5s for subprocess wait + escalation; aligns with `approval_waiter.py` precedent (verify in impl — if approval_waiter uses different default, reconcile or document divergence).
- (b) `poll_interval_s: float = 0.2` — tighter, more responsive; up to 25 polls inside 5s budget. May saturate disk read cache on slow runners.
- (c) `poll_interval_s: float = 1.0` — looser; only 5 polls inside budget; risky if any single poll iteration takes >100ms.

**Resolved: (a) 0.5s default.** Operator can tune via env var `OMB_BUDGET_SUPERVISOR_POLL_INTERVAL_S` (Story 12.4 scope — defer to that story OR add now). For Story 12.1: hardcode default; env-var override deferred.

### D2 — Termination callback ownership: supervisor OR runner?

**Problem:** Who owns the `terminate() → wait → kill()` escalation logic?

**Options:**
- **(a) `ClaudeCodeRunner.terminate_with_grace(...)`** (RECOMMENDED, per AC2). Supervisor calls back via injected callback. Keeps subprocess-control logic in the adapter layer (where the process handle lives); supervisor stays pure orchestration.
- (b) Supervisor owns SIGTERM/SIGKILL directly. Requires injecting `_process` handle. Couples supervisor to subprocess details.

**Resolved: (a) Runner owns escalation.** Architectural: supervisor is in `domain/` (pure orchestration); subprocess control is `adapters/` (I/O boundary). Mirror Story 5.4's existing separation.

### D3 — `task.budget_exceeded` event source — who emits it?

**Problem:** Story 12.1 CONSUMES `task.budget_exceeded`; doesn't emit it. Who's the producer?

**Options:**
- **(a) Out of scope for Story 12.1** (RECOMMENDED) — Phase 1 FR44 already established the event type. Production emission is likely from `ClaudeCodeRunner`'s token-counter when it observes a token-usage event exceeding `task_token_budget`. That logic ALREADY EXISTS or is in Phase 1 scope.
- (b) Implement emission as part of 12.1.

**Resolved: (a) Out of scope.** Verify during impl that the event IS emitted in production via `grep -rnE 'task\.budget_exceeded.*append\|"task.budget_exceeded"' services/worker-wrapper services/registry-api`. If NO producer found, file Story 12.0 (pre-12.1 emission backfill) and HALT Story 12.1.

### D4 — Cancel cleanup ordering: runner-first or supervisor-first?

**Problem:** When `runner.run()` completes naturally, who gets cancelled first?

**Options:**
- **(a) Runner completes → set `budget_cancel.set()` → `await budget_task` with timeout**. Supervisor sees cancel_event, returns clean.
- (b) `asyncio.gather(runner_task, budget_task, return_exceptions=True)` race-pattern. Whichever completes first wins; other is cancelled.

**Resolved: (a) Runner-first.** Simpler reasoning model: budget supervisor is a SHADOW task that exists only while runner is running. When runner exits, shadow goes away cleanly. The race-pattern (b) is harder to reason about for the natural-completion path.

### D5 — Detection latency vs termination latency separation in `_BudgetSupervisorResult`

**Problem:** NFR-R8 says "event-emit to subprocess-exit < 5s p99". This is the SUM of (detection_latency + termination_latency). Should we measure them separately?

**Options:**
- **(a) Separate fields** (RECOMMENDED, per AC1 schema) — `detection_latency_s` (event emit → supervisor notice) + `termination_latency_s` (callback start → subprocess wait return). Operator can tune separately.
- (b) Single field `enforcement_latency_s` — sum only. Simpler API.

**Resolved: (a) Separate.** Story 12.2 will emit `task.budget_enforcement_triggered` with these fields; metrics-subscriber will surface them as separate histograms. Composability with Epic 10.

## Constraints

- **FR26 single-writer rule** — supervisor READS JSONL, NEVER writes. Subprocess SIGTERM/SIGKILL is process-control, not state mutation. `scripts/check_single_writer.py` exit 0.
- **NFR-R8 budget** — event-emit to subprocess-exit < 5s p99. Enforced by AC5 integration test (5× repetitions).
- **Pure async** — no thread spawn, no blocking I/O. Use `asyncio.create_subprocess_exec`, `asyncio.wait_for`, `asyncio.sleep`. NO `subprocess.run` or `time.sleep` in supervisor.
- **structlog discipline** (Story 11.1 P1-H5) — keyword-arg form throughout; NO `%s` stdlib logging in supervisor (worker-wrapper convention may differ from registry-api — verify in impl).
- **Test-fixture realism** (Epic 11 retro L6) — integration test uses real `EventEnvelope.create(...)` + `EventLogWriter.append(...)`; NO hand-rolled `01HZX...` event_id shapes.
- **D5 fail-loud isolation** (Epic 11 retro L1) — supervisor does NOT halt task on its own errors. If `read_log_lines` raises on a corrupted file, log + retry (not crash). The supervisor's role is shadow-monitor; it must not regress task reliability for its own bugs.

## Frontmatter

```yaml
---
story_id: 12.1
story_key: 12-1-budget-supervisor-worker-wrapper
parent_epic: 12
phase: 2
fr_refs: [FR66, NFR-R8]
nfr_refs: [NFR-R8, FR26]
arch_refs:
  - "Story 5.4 ClaudeCodeRunner subprocess supervision pattern"
  - "Story 5.15 task.budget_exceeded event (Phase 1 FR44 source)"
  - "Story 11.4 PP14 — JSONL reader streaming + blank-line/decode-error tolerance"
  - "Story 11.4 verify_approval.py — log-reader convention reused"
  - "approval_waiter.py — existing read_log_lines + current_day_path subscriber precedent"
  - "Epic 11 retro L1 — cross-cutting stories require pass-2 review regardless of complexity estimate"
estimated_complexity: MEDIUM
priority: high (Epic 12 entry; FR66 is the most user-facing budget-control invariant)
blocks:
  - 12-2-budget-enforcement-triggered-event (12.2 emits the audit event AFTER 12.1's termination)
  - 12-3-approve-override-budget-event (12.3 needs supervisor's grace window to override)
  - epic-12-retrospective
review_cadence: MANDATORY_3_LANE_PASS_2  # Epic 11 retro L1 — cross-cutting subprocess control + event subscriber + lifespan task
---
```

## Context

- **Phase:** 2
- **FR refs:** FR66 (budget enforcement), NFR-R8 (5s latency p99), FR26 (single-writer preserved)
- **Direct deps (must be `done`):** Story 5.4 (ClaudeCodeRunner exists), Story 5.15 (TaskBudgetExceededPayload registered — VERIFIED at `event_types.py:236-238` schema_versions 1.0.0 + 1.1.0), Story 2.4 (EventLogWriter), Story 11.4 (log-reader streaming pattern).
- **Test count baseline:** 3068 non-slow (Story 11.5 pass-1 close + 11.3.2 status flip)
- **Mypy --strict baseline:** 108 errors / 191 source files — UNCHANGED expected
- **Estimated +tests:** ~12 (7 unit tests for supervisor + 3 for `terminate_with_grace` + 1 integration latency + 1 supervisor-runner integration)
- **Estimated complexity:** MEDIUM. Cross-cutting (domain + adapter + app/main + new test file) but well-scoped. **Per Epic 11 retro L1: mandatory 3-lane review at `/bmad-code-review 12.1` regardless of executor's "1-pass review predicted" claim at completion.**

## Definition of Done

- All 6 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `12-1-budget-supervisor-worker-wrapper: backlog → done` (after CI green + pass-1 review batch).
- Spec Status `**done** (CI green @ <sha>)`.
- NFR-R8 latency test (AC5) passes 5/5 runs under 5s wall-clock.
- D3 verification — `task.budget_exceeded` emission producer confirmed present in production code (NOT a Story 12.0 backfill blocker).
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy delta, latency measurements from AC5 runs).
- No regressions in: existing worker-wrapper tests (especially `test_lifecycle.py`, `test_run_task.py`, `test_claude_code_runner.py`).
- 3-lane review batch (pass-1 minimum; pass-2 if findings warrant) applied per Epic 11 retro L1.

## Tasks / Subtasks

- [ ] Phase 0 — Sprint-status flip + D3 pre-flight verify
  - [ ] D3 producer search → orchestrator-adapter/src/orchestrator_adapter/app/main.py:362-368 confirmed
  - [ ] Sprint-status: 12-1 ready-for-dev → in-progress
- [ ] Phase 1 — `terminate_with_grace` on `ClaudeCodeRunner` (AC2)
  - [ ] Add `_TerminationResult` frozen dataclass (`method`, `elapsed_s`, `exit_code`)
  - [ ] Add `async def terminate_with_grace(self, *, grace_period_s: float = 5.0) -> _TerminationResult`
  - [ ] Unit test: `test_terminate_with_grace_sigterm_succeeds`
  - [ ] Unit test: `test_terminate_with_grace_sigkill_escalation`
  - [ ] Unit test: `test_terminate_with_grace_noop_when_no_process`
- [ ] Phase 2 — `budget_supervisor.py` module (AC1 + AC4)
  - [ ] Create `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py`
  - [ ] `_BudgetSupervisorResult` frozen dataclass (7 fields per AC1)
  - [ ] `async def watch_for_budget_exceeded(...)` per AC1 signature
  - [ ] Reuse `read_log_lines` + `current_day_path`; skip blank/decode-error lines
  - [ ] Match on `type == "task.budget_exceeded"` AND `payload.task_id == task_id`
  - [ ] Latency measurements via injected `Clock`
  - [ ] Create `test_budget_supervisor.py` with 7 unit tests per AC4
- [ ] Phase 3 — Lifespan integration (AC3)
  - [ ] Spawn supervisor `asyncio.Task` in `run_task` after `ClaudeCodeRunner.run`
  - [ ] Use `asyncio.create_task` with budget_cancel event
  - [ ] On runner completion: set cancel + `await` supervisor with 1s timeout
  - [ ] Skip `task.completed` emission when `result.triggered=True`
- [ ] Phase 4 — Integration latency test (AC5)
  - [ ] Create `tests/integration/test_budget_enforcement_latency.py`
  - [ ] `test_budget_enforced_subprocess_exits_within_5s_e2e` — 5× repetitions
  - [ ] Use real `EventEnvelope.create` + `EventLogWriter.append` per Epic 11 retro L6
- [ ] Phase 5 — Validation gates + commit (AC6)
  - [ ] `ruff check` + `ruff format --check`
  - [ ] `mypy --strict` (108 baseline unchanged)
  - [ ] `check_imports.py` / `check_event_registry.py` / `check_single_writer.py` (exit 0)
  - [ ] `pytest` worker-wrapper + events + integration
  - [ ] `just bootstrap-verify`
  - [ ] Update Dev Agent Record + flip spec/sprint-status to `review`
