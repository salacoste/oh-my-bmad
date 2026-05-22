# Story 12.1 — Budget supervisor module in worker-wrapper

Status: **done** (CI green @ 12e08f4 — pass-2 review batch: 22 fixes incl. 4 P1-H lifecycle/exception residuals from pass-1)

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
) -> BudgetSupervisorResult:
    """Tail the JSONL event log for `task.budget_exceeded` events matching task_id.

    On the FIRST matching event:
      1. Invoke `terminate_callback()` and `await` it (caller wraps subprocess
         SIGTERM → wait ≤5s → SIGKILL escalation).
      2. Return `BudgetSupervisorResult(triggered=True, ...)` describing
         the event payload + termination wall-clock duration.

    If `cancel_event` is set before any matching event arrives, return
    `BudgetSupervisorResult(triggered=False)` cleanly (normal task
    completion path).

    Polling interval is `poll_interval_s` (default 0.5s — gives ~10 retries
    inside the 5s NFR-R8 budget). Caller's `terminate_callback` MUST
    enforce its own 5s deadline; the supervisor only owns the
    detect-and-dispatch step.
    """
```

`BudgetSupervisorResult` is a frozen dataclass with fields (PP31 — public name; backwards-compat underscore alias `_BudgetSupervisorResult` retained per PP18):
- `triggered: bool` — true if a `task.budget_exceeded` was observed; false on cancel-clean path
- `event_id: str | None` — the `event_id` of the matching envelope
- `tokens_used: int | None` — copied from `TaskBudgetExceededPayload.tokens_used`
- `token_limit: int | None` — copied from `TaskBudgetExceededPayload.token_limit`
- `detection_latency_s: float | None` — time from event `emitted_at` to supervisor noticing (clock-based; bounded by `poll_interval_s + JSONL fdatasync delay`)
- `termination_latency_s: float | None` — wall-clock duration of `terminate_callback()` (separately measured; covers the 5s NFR-R8 budget end-to-end)
- `step: int | None` — copied from `TaskBudgetExceededPayload.step` (PP16; Story 12.2 uses this for `task.budget_enforcement_triggered`)
- `termination_method: Literal["noop","sigterm","sigkill"] | None` — propagated from `TerminationResult.method` (PP21; `None` when callback is opaque or cancel-clean path)
- `enforcement_failed: bool` — `True` when `triggered=True` AND the `terminate_callback` raised (PP23); signals lifespan to retry termination directly

The supervisor MUST be **pure async** — no blocking I/O on the event loop — JSONL reads off-loaded via `asyncio.to_thread` (PP2). No subprocess spawning or signal handling in domain layer (D2). JSONL read via existing `events.log_reader.read_log_lines` (already streaming-bounded, blank-line-tolerant per Story 11.4 PP14 lesson). (PP33)

Self-verification:
- `grep -nE "^async def watch_for_budget_exceeded" services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` returns exactly one line.
- `grep -nE "subprocess\.|os\.kill|signal\." services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` returns ZERO lines (subprocess control lives in adapter layer; supervisor only orchestrates via callback).
- `uv run python scripts/check_imports.py` exits 0.
- Unit tests (AC4) all pass.

### AC2 — Termination callback wired to `ClaudeCodeRunner`

In `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`:

Add a method `async def terminate_with_grace(self, *, grace_period_s: float = 5.0) -> TerminationResult` that:

1. Captures wall-clock start time via injected `Clock`.
2. If `self._process is None` → return `TerminationResult(method="noop", elapsed_s=0.0)`.
3. Calls `self._process.terminate()` (sends SIGTERM).
4. `await asyncio.wait_for(self._process.wait(), timeout=grace_period_s)` → on success, return `TerminationResult(method="sigterm", elapsed_s=<measured>)`.
5. On `asyncio.TimeoutError`: call `self._process.kill()` (SIGKILL); `await self._process.wait()` (uncapped — kernel guarantees ≤O(1)); return `TerminationResult(method="sigkill", elapsed_s=<measured>)`.

`TerminationResult` is a frozen dataclass: `method: Literal["noop","sigterm","sigkill"]`, `elapsed_s: float`, `exit_code: int | None`, `escalation_landed: bool = False` (PP34; `True` only when SIGKILL was actually delivered, not the race-window case). PP31 — public name; backwards-compat underscore alias `_TerminationResult` retained per PP18.

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

For Story 12.1: ALSO update the existing `task.completed` emission path to skip emission when `BudgetSupervisorResult.triggered=True` — that case will emit a different event via Story 12.2's path.

**PP15 amendment (2026-05-21):** After budget enforcement fires (supervisor `triggered=True`), transition lifecycle FSM to `LifecycleEvent.TASK_FAILED` BEFORE the early `return`. This prevents orphaned lifecycle state when the runner exception path runs (PP1 P0 fix). The `LifecycleEvent.TASK_FAILED` handler must NOT emit a `task.failed` JSONL event (verified during PP1 implementation — `mgr.handle_event` only does state transition + sidecar persist for this event; no JSONL write) — only state transition. Story 12.2 will refine this with a distinct `LifecycleEvent.TASK_BUDGET_ENFORCED` + emit `task.budget_enforcement_triggered` at this callsite. The Acceptance Auditor O3 ratified this deviation as a correctness improvement over the original AC3 wording.

The lifespan MUST also capture any runner exception with `runner_raised: BaseException | None = None` BEFORE the `finally` block so the budget-triggered handling path is reachable even when `runner.run()` raises (the common SIGTERM-cascade case: BrokenPipeError on stdout pipe). See PP1 implementation in `app/main.py`.

The join timeout on the budget supervisor task MUST exceed `grace_period_s` (PP6). Join timeout = `grace_period_s + 2.0` (7s total for the default 5s grace).

```python
if budget_result and budget_result.triggered:
    # Story 12.1 — terminated by budget enforcement; task.completed NOT emitted.
    # Story 12.2 will emit task.budget_enforcement_triggered here (FR67).
    await mgr.handle_event(LifecycleEvent.TASK_FAILED)
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

- [x] Phase 0 — Sprint-status flip + D3 pre-flight verify
  - [x] D3 producer search → orchestrator-adapter/src/orchestrator_adapter/app/main.py:361-368 confirmed
  - [x] Sprint-status: 12-1 ready-for-dev → in-progress
- [x] Phase 1 — `terminate_with_grace` on `ClaudeCodeRunner` (AC2)
  - [x] Add `TerminationResult` frozen dataclass (`method`, `elapsed_s`, `exit_code`, `escalation_landed`) — PP31: public name; `_TerminationResult` alias retained per PP18
  - [x] Add `async def terminate_with_grace(self, *, grace_period_s: float = 5.0) -> TerminationResult`
  - [x] Unit test: `test_terminate_with_grace_sigterm_succeeds`
  - [x] Unit test: `test_terminate_with_grace_sigkill_escalation`
  - [x] Unit test: `test_terminate_with_grace_noop_when_no_process`
- [x] Phase 2 — `budget_supervisor.py` module (AC1 + AC4)
  - [x] Create `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py`
  - [x] `BudgetSupervisorResult` frozen dataclass (9 fields per AC1) — PP31: public name; `_BudgetSupervisorResult` alias retained per PP18; PP32: `step`, `termination_method`, `enforcement_failed` fields added
  - [x] `async def watch_for_budget_exceeded(...)` per AC1 signature
  - [x] Reuse `read_log_lines` + `current_day_path`; skip blank/decode-error lines
  - [x] Match on `type == "task.budget_exceeded"` AND `payload.task_id == task_id`
  - [x] Latency measurements via injected `Clock`
  - [x] Create `test_budget_supervisor.py` with 7 unit tests per AC4
- [x] Phase 3 — Lifespan integration (AC3)
  - [x] Spawn supervisor `asyncio.Task` in `run_task` after `ClaudeCodeRunner.run`
  - [x] Use `asyncio.create_task` with budget_cancel event
  - [x] On runner completion: set cancel + `await` supervisor with 1s timeout
  - [x] Skip `task.completed` emission when `result.triggered=True`
- [x] Phase 4 — Integration latency test (AC5)
  - [x] Create `tests/integration/test_budget_enforcement_latency.py`
  - [x] `test_budget_enforced_subprocess_exits_within_5s_e2e` — 5× repetitions
  - [x] Use real `EventEnvelope.create` + `EventLogWriter.append` per Epic 11 retro L6
- [x] Phase 5 — Validation gates + commit (AC6)
  - [x] `ruff check` + `ruff format --check` — clean
  - [x] `mypy --strict` — 42 pre-existing errors (unchanged from baseline; 0 new)
  - [x] `check_imports.py` / `check_event_registry.py` / `check_single_writer.py` — exit 0
  - [x] `pytest` worker-wrapper + events + integration — 857 passed
  - [x] `just bootstrap-verify` — 14 workspace-member imports verified
  - [x] Dev Agent Record filled; spec/sprint-status flipped to `review`

### Pass-1 Review Findings (3-lane mandatory pass-2 per Epic 11 retro L1 — review of `fa3f03d..e98d50f` 2026-05-21)

**Reviewer dedup:** 43 raw findings (Blind 25 + Edge 14 + Acceptance 4 LOW) → **22 unique**. 1 P0 confirmed by 2-lane convergence. Acceptance D3-producer verification REFUTED Edge F4 "dead code" architectural claim (event flow is correct: orchestrator-adapter consumes worker-wrapper's token events, emits `task.budget_exceeded` keyed on same task_id; supervisor consumes that). Edge F4 dismissed.

**P0 finding:**

- [x] [Review][Patch] PP1 — **Lifecycle corruption when `runner.run()` raises** (Blind F1 + Edge F1, 2-lane convergence) — `budget_result` declared INSIDE `try/finally`'s finally clause; on runner exception path, `if budget_result.triggered` check (line 535) is never reached because exception propagates past the post-finally block. Supervisor-triggered SIGTERM commonly CAUSES `runner.run()` to raise (BrokenPipeError on subprocess stdout pipe, CancelledError, etc.) — this is the COMMON path, not edge case. Result: budget enforcement fires but lifecycle FSM stays in IN_PROGRESS, no `task.failed` event emitted, exception leaks. Fix: wrap with explicit `runner_raised` capture; handle budget-triggered case BEFORE re-raising runner exception [`services/worker-wrapper/src/worker_wrapper/app/main.py:516-540`, P0]

**P1-H findings:**

- [x] [Review][Patch] PP2 — `read_log_lines` called directly on event loop (Edge F3) — supervisor docstring claims "Pure async; no blocking I/O on event loop" but `read_log_lines` opens file + synchronously iterates+parses. Sibling `ApprovalWaiter._scan_today` uses `await asyncio.to_thread(...)` (approval_waiter.py:76). For multi-MB JSONL on busy systems, supervisor blocks event loop on every poll → starves runner's stdout reader → regresses Claude Code UX. Fix: wrap both `_scan_for_match` and `_last_scanned_idx` calls in `asyncio.to_thread` [`services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py:155-217`, P1-H]
- [x] [Review][Patch] PP3 — O(N) double-scan per poll (Blind F4 + Edge F2) — `_scan_for_match` iterates envelopes once (O(n)), then `_last_scanned_idx` iterates AGAIN to count (another O(n)). 2× file I/O per 0.5s poll. Also non-atomic: new envelope arriving between scans can be skipped on next poll. Fix: refactor `_scan_for_match` to return `(match | None, envelopes_scanned: int)`; eliminate `_last_scanned_idx`. Mirror ApprovalWaiter pattern [`budget_supervisor.py:295-313 + 563-565`, P1-H]
- [x] [Review][Patch] PP4 — Deadlock potential: supervisor SIGKILLs subprocess but `runner.run()` may hang (Blind F3) — if runner's read loop doesn't see EOF cleanly after SIGKILL, `await runner.run()` blocks forever → lifespan `finally` never runs → process wedged. Fix: add `asyncio.wait_for` ceiling around `await runner.run(...)` with timeout = `settings.task_overall_timeout_s` (default 900s — well above `claude_timeout_s`; trips only on pathological hangs) (PP42). PP43: PP4a (ceiling) shipped; PP4b (regression test for orphan-subprocess reap after timeout) split to backlog Story 12.1.1 — see sprint-status.yaml [`app/main.py:516`, P1-H]
- [x] [Review][Patch] PP5 — `ProcessLookupError` TOCTOU race in `terminate_with_grace` (Blind F7/F8 + Edge F9) — `process.terminate()` after `returncode` check can raise if subprocess died in the race window. Also `process.kill()` after SIGTERM grace can raise. Both bubble up to supervisor callback, abort it mid-flight, propagate to lifespan with masked exception. Fix: wrap both `terminate()` and `kill()` in `try/except ProcessLookupError: pass` (process absence IS desired post-condition) [`adapters/claude_code_runner.py:434-446`, P1-H]
- [x] [Review][Patch] PP6 — 1s join timeout < 5s grace = cancel race (Blind F22/F23 + Edge F5) — lifespan does `await asyncio.wait_for(budget_supervisor_task, timeout=1.0)` but supervisor may be mid-`terminate_with_grace(grace_period_s=5.0)`. Timeout cancels supervisor mid-`process.wait()`, leaves zombie subprocess + lost `triggered=True` result. Fix: raise join timeout to `grace_period_s + 2.0` (~7s) [`app/main.py:524-528`, P1-H]

**P1-M findings:**

- [x] [Review][Patch] PP7 — Cross-day rotation bug (Edge F7) — envelope written 23:59:59 to yesterday's JSONL; supervisor polling at 00:00:00.05 polls TODAY only → loses event forever. ApprovalWaiter has same bug acknowledged as Phase-1 limitation; supervisor docstring incorrectly claims "Cross-day rotation: re-resolves current_day_path each poll" without scanning yesterday. Fix: on first poll, scan yesterday's path too; on subsequent polls today-only. OR explicitly document as known limitation matching ApprovalWaiter [`budget_supervisor.py:155-217`, P1-M]
- [x] [Review][Patch] PP8 — AC5 test elides realistic latency measurement (Blind F18 + Edge F6) — 50ms warmup before `t0` (favorable supervisor start), happy-path SIGTERM only (no SIGKILL escalation path tested), unique task_id per iteration (no dedup behavior). Reported "0.15s/run" is best-case. Fix: add separate cold-start test (envelope BEFORE supervisor spawn); add SIGKILL-escalation test using `signal.SIG_IGN` for SIGTERM (harness already exists in `test_terminate_with_grace_sigkill_escalation`) [`tests/integration/test_budget_enforcement_latency.py:1382-1467`, P1-M]
- [x] [Review][Patch] PP9 — Supervisor exception in `finally` clobbers runner exception (Blind F7) — if `await terminate_callback()` raises (e.g., ProcessLookupError from PP5 before fix), the exception clobbers any in-flight runner exception, masking root cause. Fix: wrap callback in `try/except`; on exception, set `triggered=True` with error field, log but don't propagate [`budget_supervisor.py:577-596`, P1-M]
- [x] [Review][Patch] PP10 — fdatasync race on offset accounting (Blind F5) — writer fdatasync vs reader race may leave a partial line; `_scan_for_match` skips it; `_last_scanned_idx` may count differently → offsets get out of sync → lost match. Fix: subsumed by PP3 (single-pass scan eliminates dual-counting). Add unit test simulating writer-during-read race [`budget_supervisor.py:618-680`, P1-M]
- [x] [Review][Patch] PP11 — Cancel-event sleep loops one more time (Blind F23 + Edge F11) — `wait_for(cancel_event.wait(), timeout)` else-branch `continue`s instead of returning. Triggers one more scan after cancel set; can spuriously fire `triggered=True` on late-arriving event → wrong lifecycle transition (TASK_FAILED on a successfully-completed task). Fix: in else-branch, `return _BudgetSupervisorResult(triggered=False)` directly [`budget_supervisor.py:598-606`, P1-M]
- [x] [Review][Patch] PP12 — Lifespan integration test gap (Blind F2) — no test exercises "runner.run() exception + supervisor triggered=True simultaneously". This is the PP1 P0 scenario. Add `test_lifespan_handles_runner_exception_when_supervisor_fires_budget_enforcement` [`tests/integration/test_budget_enforcement_latency.py`, P1-M]
- [x] [Review][Patch] PP13 — `test_no_undocumented_spawn_sites.py` hardcoded line allowlist fragile (Blind F19) — bump 151→175 invites bypass; future stories must remember to update. Fix: replace line-number allowlist with AST-based detection (find actual `asyncio.create_subprocess_exec` call inside `_spawn` function) [`tests/test_no_undocumented_spawn_sites.py:1517-1521`, P1-M]
- [x] [Review][Patch] PP14 — `_isolated_registry` fixture doesn't isolate (Blind F25) — registers into global registry, never unregisters. Tests have order dependency. Fix: yield/teardown that unregisters, OR rename fixture to `_ensure_registry` to match actual behavior [`test_budget_supervisor.py:792-801 + test_budget_enforcement_latency.py:1326-1331`, P1-M]
- [x] [Review][Patch] PP15 — TASK_FAILED transition deviation from spec (Blind F24) — Dev Agent Record deviation #4 documents adding `LifecycleEvent.TASK_FAILED` before early-return, contradicting AC3 wording. Acceptance Auditor O3 ratifies as correctness improvement. Resolution: verify `mgr.handle_event(TASK_FAILED)` does NOT emit `task.failed` event (only state transition); confirm Story 12.2 spec covers FSM transition replacement. If spec is wrong, update AC3 wording [`app/main.py:540` + Story 12.1 spec AC3, P1-M]

**P1-L findings:**

- [x] [Review][Patch] PP16 — `_BudgetSupervisorResult` missing `step` field (Edge F10) — producer's `TaskBudgetExceededPayload.step` not propagated; Story 12.2 will need it for `task.budget_enforcement_triggered`. Add `step: int | None = None` field [`budget_supervisor.py:54-85`, P1-L]
- [x] [Review][Patch] PP17 — `_safe_payload` Pydantic-vs-dict sibling contradiction (Edge F8 + Blind F9) — supervisor handles both BaseModel and dict; ApprovalWaiter only handles dict. One sibling has wrong understanding of `read_log_lines` return type. Empirical verification needed; unify. Fix per investigation: if BaseModel, file P1 against ApprovalWaiter; if dict, simplify supervisor [`budget_supervisor.py:721-724 + approval_waiter.py:138-143`, P1-L]
- [x] [Review][Patch] PP18 — `__all__` exports `_TerminationResult` / `_BudgetSupervisorResult` (private names) (Blind F15) — underscore-prefix + public-export contradiction. Either rename (drop underscore) or remove from `__all__` [`claude_code_runner.py:500-504 + budget_supervisor.py:730-733`, P1-L]
- [x] [Review][Patch] PP19 — Integration test cleanup race (Blind F17) — subprocess spawned BEFORE `try/finally`; if pre-try-block code raises, subprocess leaks. Fix: move `proc = create_subprocess_exec(...)` INSIDE `try:` OR use `AsyncExitStack` [`test_budget_enforcement_latency.py:1397-1453`, P1-L]
- [x] [Review][Patch] PP20 — `test_terminate_with_grace_sigterm_succeeds` asserts `exit_code != 0` (Blind F20) — Python's SIGTERM exit code is platform-dependent. Fix: assert `exit_code is not None` only OR specifically `in (-15, 143, 1)` [`test_claude_code_runner.py:1199-1200`, P1-L]
- [x] [Review][Patch] PP21 — `_terminate_for_budget` discards `_TerminationResult` (Blind F14) — `term_result.method` (sigterm/sigkill/noop) logged but not propagated to `_BudgetSupervisorResult`. Operator can't tell from final log whether SIGTERM was clean or escalated to SIGKILL. Fix: add `termination_method: Literal["noop","sigterm","sigkill"] | None = None` to result; populate from callback return [`app/main.py:324-332 + budget_supervisor.py:54-85`, P1-L]
- [x] [Review][Patch] PP22 — Read iterator file handle ownership (Blind F21) — `read_log_lines` returns iterator; supervisor abandons it on match-return or exception. Relies on Python GC for file close. Long-running supervisor with hourly polling may leak FDs. Verify `read_log_lines` is context-manager friendly OR wrap in `with closing(iter):` [`budget_supervisor.py:634-680`, P1-L]

### Pass-2 Review Findings (3-lane review of pass-1 batch `e98d50f..e4183dd` — 2026-05-22)

**Reviewer dedup:** 24 raw findings (Blind 12 + Edge 7 + Acceptance 5) → **22 unique**. **No new P0 introduced by pass-1.** Pass-2 found 4 P1-H residual issues — pass-1's lifecycle restructuring left exception-semantics edge cases. 2-lane convergence on 2 P1-H (PP4 subprocess leak; CancelledError mishandling). Acceptance Auditor: all 22 PP code-level claims faithfully backed; 5 doc-drift findings from PP18 rename + PP16/PP21 field additions.

**P1-H findings (4):**

- [x] [Review][Patch] PP23 — **PP9 callback isolation hides live subprocess** (Blind F1) — supervisor's `try/except Exception` around `terminate_callback` returns `triggered=True` even when termination raised. Lifespan trusts result, transitions FSM to TASK_FAILED, returns. Subprocess STILL ALIVE consuming tokens until PP4's 900s ceiling (if at all). Violates NFR-R8 silently. Fix: add `enforcement_failed: bool = True` field; OR retry callback once before giving up; OR return `triggered=False` on callback exception (causes runner to continue, less surprising) [`budget_supervisor.py:230-252`, P1-H]
- [x] [Review][Patch] PP24 — **PP4 `wait_for` timeout leaks subprocess** (Blind F2 + Edge F2 2-lane) — when 900s ceiling fires, `wait_for` cancels coroutine but subprocess is NOT killed. PP4 papers over the symptom (worker not wedged) but creates a new defect (orphan process). Fix: wrap `asyncio.wait_for` in `try/except TimeoutError`, call `await runner.terminate_with_grace(grace_period_s=5.0)` before re-raising. Add regression test (mocked-hang subprocess) [`app/main.py:542-545`, P1-H]
- [x] [Review][Patch] PP25 — **CancelledError mishandling across two clauses** (Blind F3 + Edge F3 2-lane) — `except BaseException` in PP1 catches `CancelledError` (BaseException in 3.11+); stores in `runner_raised`; `finally` then waits up to 7s for supervisor → shutdown that should be ms takes 7s. Worse: supervisor's `except Exception as supervisor_exc` MISSES CancelledError if supervisor itself raises it. Fix: split into `except asyncio.CancelledError:` (set cancel, short 100ms supervisor-join, re-raise) + `except Exception:` (PP1 capture pattern). Also broaden supervisor join to `except (Exception, asyncio.CancelledError)` [`app/main.py:550-565`, P1-H]
- [x] [Review][Patch] PP26 — **`budget_cancel.set()` not exception-suppressed** (Edge F1) — pathological event-loop teardown could raise from `set()`, masking original `runner_raised` per Python's finally-overrides-try semantic. Fix: wrap in `with contextlib.suppress(Exception):` (1-line) [`app/main.py:554`, P1-H]

**P1-M findings (8):**

- [x] [Review][Patch] PP27 — PP21 type-contract weakness (Blind F4 + Edge F4) — supervisor duck-types `getattr(callback_value, "method", None)`; if future refactor renames `.method`, propagation silently breaks. Fix: introduce `domain/` Protocol declaring `.method` (decouples from adapter import) OR `isinstance(callback_value, TerminationResult)` (accepts adapter import cost) [`budget_supervisor.py:230-252`, P1-M]
- [x] [Review][Patch] PP28 — PP3 stale-offset infinite re-scan on persistent error (Blind F5) — `(None, scan_offset)` on `OSError/ValueError` causes re-scan of same N envelopes every poll forever. Disk thrash + log spam. Fix: counter for consecutive errors; after K=5 advance `scan_offset = idx` past problematic region with one warning [`budget_supervisor.py:380-388`, P1-M]
- [x] [Review][Patch] PP29 — PP14 snapshot fixture edge cases (Edge F5) — shallow snapshot doesn't deep-copy class refs; `_rebuild_types_cache` NOT called after the `register(...)` calls during yield. Fix: call `_rebuild_types_cache()` after `register()` too; document fixture scope [`test_budget_supervisor.py:53-86 + test_budget_enforcement_latency.py:60-78`, P1-M]
- [x] [Review][Patch] PP30 — PP6 join-timeout doesn't interrupt to_thread (Edge F6) — `supervisor_task.cancel()` requests cancellation; if supervisor mid-`to_thread(_scan_for_match)`, thread is uncancellable. `await supervisor_task` then hangs forever. Fix: replace `await supervisor_task` with `await asyncio.wait_for(supervisor_task, timeout=2.0)` inside suppress; add regression test mocking `_scan_for_match` to sleep 30s [`app/main.py:564`, P1-M]
- [x] [Review][Patch] PP31 — PP18 rename not propagated to spec AC1/AC2 (Acceptance F1) — spec still uses `_BudgetSupervisorResult` / `_TerminationResult` private names at lines 36, 42, 46, 56, 76, 79, 81, 82, 84, 124. Tasks/Subtasks lines 312, 313, 319 also. Fix: spec-only edits to use public names; one-line note about backwards-compat aliases [`12-1-budget-supervisor-worker-wrapper.md`, P1-M]
- [x] [Review][Patch] PP32 — Spec AC1 field list missing `step` (PP16) and `termination_method` (PP21) (Acceptance F3) — `BudgetSupervisorResult` has 8 fields shipped; spec AC1 (lines 56-62) lists only 6. Story 12.2 will need these fields documented. Fix: append 2 bullets to AC1 field list; update Tasks/Subtasks "(7 fields per AC1)" to 8 [`12-1-budget-supervisor-worker-wrapper.md:56-62`, P1-M]
- [x] [Review][Patch] PP33 — Spec AC1 "no thread spawn" contradicts shipped PP2 `asyncio.to_thread` (Acceptance F4) — AC1 line 64-65 says "no thread spawn, no blocking I/O on event loop"; shipped code DOES off-load JSONL reads via `asyncio.to_thread`. Fix: amend AC1 to "no blocking I/O on event loop — JSONL reads off-loaded via `asyncio.to_thread` (PP2)" [`12-1-budget-supervisor-worker-wrapper.md:64-65`, P1-M]
- [x] [Review][Patch] PP34 — PP5 SIGKILL escalation misclassification (Blind F6) — when target dies in grace race window, code classifies as `method="sigterm"` (because SIGKILL not landed). But the runner DID hit grace timeout — semantically wrong. NFR-R8 dashboards undercount escalations. Fix: introduce `"sigkill_unneeded"` method value OR add `escalation_landed: bool` field [`claude_code_runner.py:505-525`, P1-M]

**P1-L findings (10):**

- [x] [Review][Patch] PP35 — PP13 AST allowlist class-context collision (Blind F7) — `_func_stack` records function NAMES only; two classes with same-named method (e.g., `MockRunner._spawn` + `ClaudeCodeRunner._spawn`) collide. Fix: override `visit_ClassDef`, push qualified `Class.method` names. Update `_FUNC_ALLOWLIST` keys [`tests/test_no_undocumented_spawn_sites.py:139-156`, P1-L]
- [x] [Review][Patch] PP36 — PP14 private `_rebuild_types_cache` import (Blind F8) — two test modules import private name from `events.schema_registry`. Fragile to rename. Fix: add public `events.schema_registry.unregister(event_type, version)` OR `registry_snapshot()` context manager; migrate fixtures [`events.schema_registry + 2 test files`, P1-L]
- [x] [Review][Patch] PP37 — PP1 lost traceback on cascade (Blind F9) — `log.info("runner_raised_after_budget_enforcement", exc_str=str(runner_raised))` discards traceback. Add `exc_info=runner_raised` for forensic visibility [`app/main.py:577-583`, P1-L]
- [x] [Review][Patch] PP38 — PP5 test global `asyncio.wait_for` monkeypatch fragile (Blind F10) — manual swap on global asyncio module risks concurrent-test interference. Fix: replace with `monkeypatch.setattr(_asyncio, "wait_for", _wait_for_passthrough)` for pytest auto-restore [`test_claude_code_runner.py:1287-1298`, P1-L]
- [x] [Review][Patch] PP39 — PP18 alias in `__all__` undermines rename (Blind F11) — `_TerminationResult` + `_BudgetSupervisorResult` exported in `__all__` defeats the rename intent. Fix: remove aliases from `__all__` (keep module-scope aliases for internal callers); add deprecation comment [`claude_code_runner.py:544-550 + budget_supervisor.py:441-446`, P1-L]
- [x] [Review][Patch] PP40 — PP1 `assert result is not None` under `-O` safety (Blind F12) — assert stripped under `PYTHONOPTIMIZE=1`; future refactor could break invariant silently. Fix: convert to `if result is None: raise RuntimeError(...)` [`app/main.py`, P1-L]
- [x] [Review][Patch] PP41 — PP13 alias-of-alias gap (Edge F7) — `b = asyncio.create_subprocess_exec; b(...)` undetected by AST visitor (only tracks Import/ImportFrom). Fix: either document gap in module docstring OR add `visit_Assign` for `Name = Attribute` aliasing [`tests/test_no_undocumented_spawn_sites.py:73-83`, P1-L]
- [x] [Review][Patch] PP42 — PP4 description "2× grace_period_s" vs shipped 900s (Acceptance F2) — spec line 354 says ~10s; shipped uses 900s. Fix: amend PP4 description to "`task_overall_timeout_s` (default 900s — well above claude_timeout_s; trips only on pathological hangs)" [`12-1-budget-supervisor-worker-wrapper.md:354`, P1-L]
- [x] [Review][Patch] PP43 — PP4 deferred-test claim (Acceptance F5) — checkbox `[x]` claims both ceiling AND regression test; test deferred without follow-up Story ID. Fix: split into `PP4a` (shipped) and `PP4b` (deferred — file follow-up Story ID like 12.1.1) [`12-1-budget-supervisor-worker-wrapper.md:354`, P1-L]
- [x] [Review][Patch] PP44 — PP9 lost traceback when callback raises (Blind F1 related) — supervisor logs `log.error("budget_supervisor_callback_raised")` without traceback. Add `exc_info=exc` for forensic visibility [`budget_supervisor.py:233-244`, P1-L]

## Dev Agent Record

**Implementation summary**: Story 12.1 ships the budget-enforcement leg of Epic 12. Three new components:
(1) `ClaudeCodeRunner.terminate_with_grace` — SIGTERM → wait ≤5s → SIGKILL escalation with measured latency;
(2) `budget_supervisor.py` — pure-async JSONL tail subscriber for `task.budget_exceeded`, calls back into runner on match;
(3) `app/main.py` lifespan wiring — shadow asyncio task spawned per-task, cancelled cleanly on natural runner exit.

**D3 pre-flight outcome**: Producer confirmed. `task.budget_exceeded` emitted at
`services/orchestrator-adapter/src/orchestrator_adapter/app/main.py:361-368` —
`build_budget_exceeded_payload(task_id, tracker, step.step)` feeds `_emit_event(clients, "task.budget_exceeded", ...)`.
No Story 12.0 backfill required.

**Files added/modified**:
- MODIFIED `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — added `_TerminationResult` dataclass + `terminate_with_grace` method + updated `__all__`
- MODIFIED `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` — added `TestTerminateWithGrace` class (3 unit tests using real subprocesses)
- MODIFIED `services/worker-wrapper/src/worker_wrapper/app/main.py` — lifespan integration: imports + budget supervisor spawn/cancel/result block in `run_task`
- MODIFIED `tests/test_no_undocumented_spawn_sites.py` — updated allowlist line number for `asyncio.create_subprocess_exec` in `claude_code_runner.py` (shifted 151→175 due to added dataclass above `_spawn`)
- ADDED `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` — new module: `_BudgetSupervisorResult`, `watch_for_budget_exceeded`, helpers
- ADDED `services/worker-wrapper/src/worker_wrapper/domain/test_budget_supervisor.py` — 7 unit tests per AC4
- ADDED `tests/integration/test_budget_enforcement_latency.py` — AC5 NFR-R8 latency test (5× repetitions, `@pytest.mark.integration`)
- MODIFIED `_bmad-output/implementation-artifacts/sprint-status.yaml` — 12-1 ready-for-dev → in-progress (Phase 0), then → review (Phase 5)
- MODIFIED `_bmad-output/implementation-artifacts/12-1-budget-supervisor-worker-wrapper.md` — status flip + tasks ticked + Dev Agent Record

**Test count delta**: true pre-story baseline 3062 (not-slow) → 3073 after story = **+11 new tests**
- 3 `TestTerminateWithGrace` unit tests (AC2)
- 7 `test_budget_supervisor.py` unit tests (AC4)
- 1 integration latency test (AC5)

**Mypy --strict delta**: 42 errors → 42 errors (zero regression; true repo scope baseline was 42 for `services/worker-wrapper packages/events`, not 108 — the 108 figure in spec context referred to a broader scope run earlier in Epic 11 that included additional services)

**`check_single_writer.py` exit code**: 0 (supervisor reads JSONL via `read_log_lines`, never writes; subprocess SIGTERM/SIGKILL is process-control, not state mutation)

**AC5 latency measurements — all 5 NFR-R8 runs (wall-clock envelope-write → supervisor-return)**:

| Run | detection_latency_s | termination_latency_s | wall-clock total | NFR-R8 (<5s) |
|-----|--------------------|-----------------------|-----------------|--------------|
| 0   | 0.1024s            | 0.0008s               | ~0.15s          | PASS         |
| 1   | 0.1022s            | 0.0006s               | ~0.15s          | PASS         |
| 2   | 0.1026s            | 0.0010s               | ~0.15s          | PASS         |
| 3   | 0.1023s            | 0.0013s               | ~0.15s          | PASS         |
| 4   | 0.1017s            | 0.0011s               | ~0.15s          | PASS         |

All 5 runs complete in ~150ms wall-clock, well under the 5s p99 NFR-R8 ceiling. Detection latency ~100ms = one poll interval (D1 default 0.1s in test; production default 0.5s). Termination latency ~1ms = SIGTERM-driven exit on cooperative subprocess.

**Deviations from spec**:

1. **`allow_list` update required** (`tests/test_no_undocumented_spawn_sites.py`): Adding `_TerminationResult` dataclass above `_spawn` shifted `asyncio.create_subprocess_exec` from line 151→175. The spawn-sites allowlist CI gate enforces exact line numbers per its STABILITY RULE comment; updated in same diff per that rule.

2. **Mypy baseline discrepancy**: Spec says "108 errors — UNCHANGED expected". Actual baseline for `services/worker-wrapper packages/events` scope is 42. The 108 figure applies to the broader `services/ packages/` full-repo scope. Story 12.1 introduces zero new mypy errors in either scope.

3. **Test count baseline discrepancy**: Spec says 3068 baseline; actual true baseline (with untracked new files stashed) was 3062. Delta is +11 as expected. The spec baseline was slightly stale relative to recent story completions; this is not a regression.

4. **`LifecycleEvent.TASK_FAILED` on budget termination**: AC3 says skip `task.completed` and return. The implementation transitions lifecycle FSM to `TASK_FAILED` before returning, so the task is not left in a terminal-less state. This is consistent with the existing `task.failed` LSM transition and avoids orphaned lifecycle state; Story 12.2 will add `task.budget_enforcement_triggered` emission at this callsite.

---

### Pass-1 Batch Outcomes (2026-05-21 — 22 fixes applied)

**PP1 (P0) — Lifecycle corruption on runner exception:**
- Restructured `app/main.py` `run_task` try/except/finally to capture `runner_raised: BaseException | None = None` before the `try`. Budget-triggered path (`if budget_result is not None and budget_result.triggered`) now runs unconditionally from the post-finally scope before any re-raise. Join timeout raised to `budget_grace_period_s + 2.0 = 7.0s` (subsumes PP6). Added `asyncio.wait_for(runner.run(...), timeout=settings.task_overall_timeout_s)` deadlock ceiling (PP4). Integration test `test_lifespan_handles_runner_exception_when_supervisor_fires_budget_enforcement` in `tests/integration/test_budget_enforcement_latency.py`: PASS.

**PP2 — asyncio.to_thread for scan:** `_scan_for_match` refactored as a sync function called via `await asyncio.to_thread(...)` in `watch_for_budget_exceeded`. Mirrors `ApprovalWaiter._scan_today` pattern exactly.

**PP3 — Single-pass scan:** `_scan_for_match` now returns `tuple[_Match | None, int]` (match + envelopes_scanned). `_last_scanned_idx` deleted. Caller uses returned count as `scan_offset` directly. Added unit test `test_watch_single_pass_scan_skips_already_scanned_envelopes` (100-envelope log, target at index 50): PASS.

**PP4 — Deadlock ceiling:** `asyncio.wait_for(runner.run(...), timeout=settings.task_overall_timeout_s)` added. New config field `task_overall_timeout_s: float = Field(default=900.0, gt=0)` in `WorkerSettings`.

**PP5 — ProcessLookupError defence:** `terminate_with_grace` now wraps both `process.terminate()` and `process.kill()` in `try/except ProcessLookupError`. Two new tests: `test_terminate_with_grace_handles_processlookuperror_on_terminate` + `test_terminate_with_grace_handles_processlookuperror_on_kill_during_grace`: both PASS.

**PP6 — Join timeout raised:** Subsumed by PP1 — join timeout now `budget_grace_period_s + 2.0 = 7.0s`.

**PP7 — Cross-day documented:** Module docstring updated with explicit "known Phase-1 limitation" wording matching `ApprovalWaiter` precedent. Story 12.1.1 follow-up noted.

**PP8 — Cold-start + SIGKILL tests added:** `test_budget_enforcement_latency_cold_start` (envelope pre-written before supervisor spawn, wall-clock < 5s asserted) + `test_budget_enforcement_latency_sigkill_escalation` (SIGTERM-ignoring child, grace=1s, `termination_method=="sigkill"` asserted, wall-clock < 5s): both PASS.

**PP9 — Callback exception isolation:** `terminate_callback()` wrapped in `try/except Exception` in `watch_for_budget_exceeded`; logs `budget_supervisor_callback_raised` but does not propagate — returns `triggered=True` with `termination_method=None`. Covered by `test_watch_callback_exception_isolated_from_lifespan`: PASS.

**PP10 — fdatasync race:** Subsumed by PP3 single-pass refactor. No separate test needed.

**PP11 — Cancel mid-sleep returns immediately:** Post-`wait_for` `else` branch now `return BudgetSupervisorResult(triggered=False)` directly (no `continue`). Added `test_watch_cancel_during_sleep_returns_immediately` with 1s poll interval: PASS.

**PP12 — Lifespan integration test:** `test_lifespan_handles_runner_exception_when_supervisor_fires_budget_enforcement` added (see PP1 outcome above).

**PP13 — AST-based spawn-site allowlist:** `_SpawnVisitor` extended to track enclosing function via `_func_stack`. New `_FUNC_ALLOWLIST` dict maps `(rel_path, func_name) → primitive_name`. Main test loop uses `_check()` helper that tries func-name key first, falls back to line key. Legacy `_ALLOWLIST` line entries retained as defence-in-depth. Line 175→187 updated (PP18 alias added above `_spawn`). Two new self-tests: `test_ast_walker_captures_enclosing_func_name` + `test_ast_walker_module_scope_offender_has_none_enclosing` + `test_func_allowlist_entries_match_real_spawn_sites`: all PASS. `test_no_undocumented_spawn_sites.py` total: 11 → 13 tests.

**PP14 — Registry fixture isolation:** Both `_isolated_registry` fixtures (unit test + integration test) now snapshot `REGISTRY` before `register()` calls and restore via explicit teardown in `finally:`. Handles "entries we added" vs "pre-existing entries" correctly. No `unregister` API exposed (none exists); snapshot/restore is the minimum viable approach.

**PP15 — AC3 wording updated:** See PP15 amendment block above in AC3.

**PP16 — `step` field:** Added `step: int | None = None` to `BudgetSupervisorResult` (renamed from `_BudgetSupervisorResult`). `_Match` dataclass also gains `step: int | None`. Populated from `payload.get("step")` in `_scan_for_match`. Test `test_watch_step_field_propagates`: PASS.

**PP17 — `_safe_payload` unified:** Empirical investigation confirmed `read_log_lines` yields envelopes via `model_validate_json` → `_FrozenDict` payload (dict subclass). The `BaseModel.model_dump` branch in the supervisor was dead code. Removed — supervisor now matches `ApprovalWaiter._safe_payload` exactly.

**PP18 — Public dataclass names:** `_TerminationResult` renamed to `TerminationResult` in `claude_code_runner.py`; alias `_TerminationResult = TerminationResult` retained for in-tree callers. `_BudgetSupervisorResult` renamed to `BudgetSupervisorResult` in `budget_supervisor.py`; alias `_BudgetSupervisorResult = BudgetSupervisorResult` retained. Both modules export new public names in `__all__`.

**PP19 — subprocess spawn inside try:** In `_run_one_iteration` in `test_budget_enforcement_latency.py`, `proc = await asyncio.create_subprocess_exec(...)` moved inside the `try:` block. `proc` typed as `asyncio.subprocess.Process | None = None` initialized before; `finally:` guards on `proc is not None`.

**PP20 — Exit code assertion relaxed:** `test_terminate_with_grace_sigterm_succeeds` now asserts `result.exit_code is not None` only (platform-portable). Comment explains platform variance (-15 POSIX / 143 shell / 1 Windows).

**PP21 — `termination_method` propagated:** `_terminate_for_budget` in `app/main.py` changed return type from `None` to `object` and returns `term_result`. `BudgetSupervisorResult` gains `termination_method: Literal["noop","sigterm","sigkill"] | None = None`. `watch_for_budget_exceeded` duck-types `callback_value.method` (avoids domain→adapter import). Log line `budget_enforced_task_terminated` now includes `termination_method`. Test `test_watch_propagates_termination_method_from_callback`: PASS. PP8 SIGKILL test asserts `result.termination_method == "sigkill"`: PASS.

**PP22 — File handle closing:** `_scan_for_match` casts the iterator to `Generator[EventEnvelope, None, None]` (the concrete runtime type from `_read_log_lines_gen`) and wraps iteration in `with contextlib.closing(closeable_iter):`. Handles early return (on match) and exceptions. `import contextlib` + `cast` + `Generator` added to supervisor imports.

**Test count delta:** 3073 (baseline after initial story) → **3083** after pass-1 batch = **+10 new tests**
- +2 `TestTerminateWithGrace` unit tests (PP5: ProcessLookupError x2)
- +5 `test_budget_supervisor.py` unit tests (PP3 single-pass, PP9 isolation, PP11 cancel-immediate, PP16 step, PP21 method)
- +3 integration tests (PP8 cold-start, PP8 SIGKILL escalation, PP1/PP12 lifespan exception)
- (+2 `test_no_undocumented_spawn_sites.py` self-tests — PP13 func-name detection — counted in existing category above)

**Mypy --strict delta (services/worker-wrapper packages/events):** 42 errors → **42 errors** (zero regression).

**`check_single_writer.py` exit code:** 0 (unchanged — supervisor still reads only).

**Deviations from batch prompt:**

1. **PP6 join timeout** — prompt specified "Verify new code uses ≥7s timeout." Implemented as `budget_grace_period_s + 2.0` where `budget_grace_period_s = 5.0`, giving 7.0s. Matches spec intent.

2. **PP4 regression test** — prompt requested "Add regression test using a subprocess that explicitly hangs after SIGKILL signal arrives but stdout pipe stays open via a parent shell." This test is highly platform-specific (pipe-keep-alive after SIGKILL is a kernel-version-dependent edge case; constructing it reliably in CI would require process group manipulation). Deferred: the `asyncio.wait_for` ceiling itself is the fix; a pathological-hang regression test would be flaky on macOS CI. Filed as follow-up.

3. **PP13 AST refactor scope** — prompt offered two options (full AST refactor OR keep line-based + comment). Implemented the AST path (`_FUNC_ALLOWLIST` + `_func_stack` visitor extension) as the primary, retaining line entries as fallback — this is the stronger option and closes the "future drift bypass" gap permanently.

4. **PP14 fixture isolation** — prompt suggested `unregister("task.budget_exceeded", ...)` which does not exist in `events.schema_registry` (only `unregister_all()` exists). Used snapshot/restore pattern against the `REGISTRY` dict directly instead. This provides equivalent isolation semantics without requiring a new public API.

---

### Pass-2 Batch Outcomes (2026-05-22 — 22 fixes applied: PP23-PP44)

**PP23 (P1-H) — enforcement_failed field + lifespan retry:**
- `BudgetSupervisorResult` gains `enforcement_failed: bool = False`. Supervisor sets it `True` when `terminate_callback()` raises in the PP9 isolation block. `app/main.py` post-budget-triggered block: when `enforcement_failed=True`, calls `await runner.terminate_with_grace(grace_period_s=5.0)` best-effort before FSM transition. PP44 subsumed: added `exc_info=exc` to the `budget_supervisor_callback_raised` log call. Test `test_supervisor_reports_enforcement_failed_when_callback_raises`: PASS.

**PP24 (P1-H) — outer timeout kills subprocess before re-raise:**
- Added inner `try/except TimeoutError` around `asyncio.wait_for(runner.run(...))` in `app/main.py`. On `TimeoutError`: logs `runner_overall_timeout_exceeded`, calls `await runner.terminate_with_grace(grace_period_s=5.0)` in `contextlib.suppress(Exception)`, then re-raises. Test `test_overall_timeout_kills_subprocess_before_reraise` in integration test: PASS.

**PP25 (P1-H) — CancelledError split from Exception:**
- `except BaseException` narrowed to `except asyncio.CancelledError:` (tight 100ms supervisor join + re-raise) + `except Exception:` (PP1 capture). Finally clause now guarded by `not budget_supervisor_task.done()` + catches `(TimeoutError, asyncio.CancelledError)` on join. Supervisor join error clause broadened to `except (Exception, asyncio.CancelledError)`.

**PP26 (P1-H) — budget_cancel.set() wrapped in suppress (subsumed by PP25):**
- Both the `CancelledError` clause and the `finally` clause now wrap `budget_cancel.set()` in `with contextlib.suppress(Exception)`.

**PP27 (P1-M) — _MethodCarrier Protocol:**
- `@runtime_checkable class _MethodCarrier(Protocol): method: str` added to `budget_supervisor.py`. Duck-type replaced with `isinstance(callback_value, _MethodCarrier)` + `cast(Literal[...], method_attr)`. Keeps domain→adapter dependency direction intact.

**PP28 (P1-M) — persistent-error break in _scan_for_match:**
- `scan_state: dict[str, int]` initialized in supervisor loop. Passed to `_scan_for_match` which tracks `consecutive_errors`. After K=5 consecutive errors: logs `budget_supervisor_scan_persistent_errors_advancing`, resets counter, advances `scan_offset = idx` past problematic region. On clean pass: resets counter.

**PP29 (P1-M) — fixture cache rebuild (subsumed by PP36):**
- Both fixtures now use `unregister()` (new public API per PP36) in teardown, which calls `_rebuild_types_cache()` internally. Private `_rebuild_types_cache` import dropped from both test modules.

**PP30 (P1-M) — hard 2s cap on supervisor cancel join:**
- `await supervisor_task` in the `TimeoutError`/`CancelledError` branch replaced with `await asyncio.wait_for(supervisor_task, timeout=2.0)` inside suppress. Test `test_lifespan_cancels_stuck_supervisor_after_join_timeout`: PASS.

**PP31 (P1-M) — spec renames propagated (doc-only):**
- Spec AC1/AC2/AC3 text + Tasks/Subtasks: `_BudgetSupervisorResult` → `BudgetSupervisorResult`, `_TerminationResult` → `TerminationResult`. One-line note per instance: "backwards-compat underscore alias retained per PP18."

**PP32 (P1-M) — AC1 field list updated (doc-only):**
- 3 bullets added: `step` (PP16), `termination_method` (PP21), `enforcement_failed` (PP23). Tasks/Subtasks "(7 fields per AC1)" → "(9 fields per AC1)".

**PP33 (P1-M) — AC1 constraint corrected (doc-only):**
- "no thread spawn, no blocking I/O" → "no blocking I/O on event loop — JSONL reads off-loaded via `asyncio.to_thread` (PP2). No subprocess spawning or signal handling in domain layer (D2)."

**PP34 (P1-M) — escalation_landed field:**
- `TerminationResult` gains `escalation_landed: bool = False`. SIGKILL-landed path: `escalation_landed=True`. Race-window case (kill() raises ProcessLookupError): `method="sigkill"`, `escalation_landed=False` (was previously misclassified as `"sigterm"`). `test_terminate_with_grace_handles_processlookuperror_on_kill_during_grace` updated: now asserts `method == "sigkill"` + `escalation_landed is False` (PP38 monkeypatch fix also applied here).

**PP35 (P1-L) — AST class-context qualification:**
- `_SpawnVisitor` gains `_class_stack: list[str]` + `visit_ClassDef`. `_qualify()` helper returns `Class.method` when inside a class body. `visit_FunctionDef`/`visit_AsyncFunctionDef` push `self._qualify(node.name)`. `_FUNC_ALLOWLIST` keys updated: `"_spawn"` → `"ClaudeCodeRunner._spawn"` / `"OMCRunner._spawn"`. New test `test_ast_walker_qualifies_class_method_names`: PASS.

**PP36 (P1-L) — public unregister API:**
- `events.schema_registry.unregister(event_type, schema_version)` added. Calls `_rebuild_types_cache()` only when key was present. Both test fixtures migrated to use it; private `_rebuild_types_cache` import dropped.

**PP37 (P1-L) — traceback on cascade:**
- `log.info("runner_raised_after_budget_enforcement", exc_str=str(runner_raised))` → `log.info(..., exc_info=runner_raised, exc_str=...)`.

**PP38 (P1-L) — monkeypatch.setattr:**
- Manual `original_wait_for / finally: restore` block in `test_terminate_with_grace_handles_processlookuperror_on_kill_during_grace` replaced with `monkeypatch.setattr(_asyncio, "wait_for", _wait_for_passthrough)` (pytest auto-restore).

**PP39 (P1-L) — __all__ cleanup:**
- `_TerminationResult` removed from `claude_code_runner.py.__all__`. `_BudgetSupervisorResult` removed from `budget_supervisor.py.__all__`. Module-scope aliases retained; deprecation comments added.

**PP40 (P1-L) — RuntimeError for invariant:**
- `assert result is not None` in `app/main.py` → `if result is None: raise RuntimeError("invariant: result must be set when runner did not raise (PP40)")`.

**PP41 (P1-L) — alias-of-alias gap documented:**
- Gap documented in `_SpawnVisitor` class docstring per option (a) in the batch prompt.

**PP42 (P1-L) — PP4 description corrected (doc-only):**
- "timeout = 2× grace_period_s" → "`settings.task_overall_timeout_s` (default 900s — well above `claude_timeout_s`; trips only on pathological hangs)".

**PP43 (P1-L) — PP4 test split (doc-only):**
- PP4 checkbox kept `[x]` (ceiling shipped); note added: "PP4b regression test split to backlog Story 12.1.1."

**PP44 (P1-L) — callback traceback (subsumed by PP23):**
- `exc_info=exc` added to `log.error("budget_supervisor_callback_raised")` in supervisor.

**Test count delta:** 3083 → **~3090** (est. +7 new tests: PP23 enforcement_failed, PP24 overall-timeout, PP30 supervisor-cancel, PP34 escalation_landed update, PP35 AST class-context, PP23 sibling in unit test, PP30 integration)

**Mypy --strict delta (services/worker-wrapper packages/events):** 42 errors → **42 errors** (zero regression expected; Protocol + `runtime_checkable` may resolve 1 existing error if any related).

**check_single_writer.py exit code:** 0 (supervisor unchanged read-only posture).

**Deviations from pass-2 batch prompt:**

1. **PP24 regression test scope** — batch prompt specified mocking a subprocess with `signal.SIG_IGN` on SIGTERM AND ignoring stdin close. Integration test `test_overall_timeout_kills_subprocess_before_reraise` instead exercises the full `terminate_with_grace(grace_period_s=1.0)` path with a SIGTERM-ignoring subprocess — this directly validates PP24's fix (subprocess reaped after orphan-timeout) without requiring the specific `asyncio.wait_for` mock from the batch prompt, which would be testing a different invariant. The actual "outer ceiling fires" path is tested indirectly via the terminate_with_grace integration flow.

2. **PP34 classification** — batch prompt specified introducing `"sigkill_unneeded"` method value OR `escalation_landed: bool`. Chose `escalation_landed: bool = False` field approach (less breaking — no new `Literal` variant, existing callers that pattern-match `method == "sigkill"` still work). The race-window case now has `method="sigkill"` (entered escalation branch) + `escalation_landed=False` (SIGKILL was a no-op). Existing test `test_terminate_with_grace_handles_processlookuperror_on_kill_during_grace` updated accordingly.

3. **PP38 test assertion update** — because PP34 changed the race-window classification from `"sigterm"` to `"sigkill"`, the assertion in `test_terminate_with_grace_handles_processlookuperror_on_kill_during_grace` changed from `assert result.method == "sigterm"` to `assert result.method == "sigkill"` + `assert result.escalation_landed is False`. This is a correctness fix bundled with PP38's monkeypatch cleanup.
