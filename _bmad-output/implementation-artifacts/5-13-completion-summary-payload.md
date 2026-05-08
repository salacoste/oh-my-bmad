# Story 5.13: Completion summary payload emission (FR9)

Status: review

## Story

As the operator,
I want the `task.completed` event's payload to include structured file count, line count, test count, CI state, blockers-encountered counters,
So that the telegram-sink's completion template has all fields it needs.

## Acceptance Criteria

1. **AC-1: Structured metrics extraction** — `build_completion_payload()` in `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` accepts an optional `CompletionMetrics` dataclass and populates the FR9 fields on `TaskCompletedPayload`: `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state`, `blockers_count`. When `CompletionMetrics` is `None`, these fields remain `None` (backward compat with v1.0.x events).

2. **AC-2: OMC output metrics parsing** — A new `parse_step_metrics(step_outputs: dict[int, str]) -> CompletionMetrics` function in `task_dispatch.py` extracts structured metrics from the raw OMC step stdout strings. It scans each step output for patterns like `N files changed`, `N insertions(+)`, `N deletions(-)`, `N passed`, `N failed`, `N tests`, and aggregates them across all steps. Unparseable output is treated as zero-count (defensive — no crash on unexpected OMC output).

3. **AC-3: CI state derivation** — `CompletionMetrics` includes a `ci_state: Literal["green", "red", "unknown"]` field derived from the parsed test results: `"green"` when tests pass, `"red"` when any test failure is detected, `"unknown"` when no test output is found in any step.

4. **AC-4: Blockers count** — `process_task()` in `app/main.py` tracks the number of `task.blocker_raised` events emitted during execution (currently 0 or 1 since first blocker returns early, but the counter is forward-compatible for future retry stories). The count is passed to `build_completion_payload()`.

5. **AC-5: Wire into process_task** — After all steps complete, `process_task()` calls `parse_step_metrics(step_outputs)` and passes the result (plus `blockers_count`) to `build_completion_payload()`. The resulting `TaskCompletedPayload` includes both the existing `summary` field and the new FR9 structured fields.

6. **AC-6: No new Telegram renderer** — The `_render_completed()` renderer in `telegram_sink.py` already handles all FR9 fields (shipped in Story 3.12). No renderer changes needed. Verify by manual inspection that the renderer works with the enriched payloads.

7. **AC-7: No new schema version** — `TaskCompletedPayload` was already registered with FR9 fields in v1.1.0 (Story 3.12). The payload model already has all the fields — this story just populates them. `scripts/check_event_registry.py` exits 0.

8. **AC-8: Import discipline** — No new cross-service imports. `orchestrator-adapter` imports from `events` (allowed). `scripts/check_imports.py` exits 0.

9. **AC-9: Tests** — At least 12 new tests in `test_task_dispatch.py`:
   - `parse_step_metrics` — git diff output parsing, pytest output parsing, combined metrics, empty outputs, malformed outputs, multi-step aggregation, ci_state derivation (green/red/unknown)
   - `build_completion_payload` — with metrics, without metrics (backward compat), partial metrics, blockers count
   - Integration: full `process_task` flow with metrics extraction (if feasible without major mocking)

10. **AC-10: `just lint` green** — All lint gates pass including `mypy --strict`.

11. **AC-11: `just test` no regressions** — Existing test count unchanged. New tests increase count.

12. **AC-12: Atomic commit** — title: `feat(orchestrator): enrich task.completed payload with FR9 structured metrics · E5`

## Tasks / Subtasks

- [x] **Task 1: Add CompletionMetrics dataclass** (AC: #1, #3)
  - [x] Add `CompletionMetrics` frozen dataclass to `task_dispatch.py` with fields: `files_changed: int`, `lines_added: int`, `lines_removed: int`, `tests_added: int`, `ci_state: Literal["green", "red", "unknown"]`, `blockers_count: int` (all default 0 / "unknown")

- [x] **Task 2: Implement parse_step_metrics** (AC: #2, #3)
  - [x] Add `parse_step_metrics(step_outputs: dict[int, str]) -> CompletionMetrics` to `task_dispatch.py`
  - [x] Parse git-style diff patterns: `N file(s) changed`, `N insertions(+)`, `N deletions(-)`
  - [x] Parse pytest-style output: `N passed`, `N failed`, `N tests`
  - [x] Aggregate metrics across all step outputs (sum counters)
  - [x] Derive `ci_state`: green if tests pass, red if any failure, unknown if no test output found
  - [x] Gracefully handle malformed/empty/missing output (defensive zero-count)

- [x] **Task 3: Enrich build_completion_payload** (AC: #1, #4)
  - [x] Add `metrics: CompletionMetrics | None = None` parameter to `build_completion_payload()`
  - [x] When `metrics` is provided, populate `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state`, `blockers_count` on `TaskCompletedPayload`
  - [x] When `metrics` is `None`, all FR9 fields remain `None` (backward compat)

- [x] **Task 4: Wire into process_task** (AC: #4, #5)
  - [x] Track `blockers_count` during step iteration (increment on `task.blocker_raised` emission path)
  - [x] After all steps complete, call `parse_step_metrics(step_outputs)` to extract metrics
  - [x] Set `blockers_count` on the metrics and pass to `build_completion_payload()`
  - [x] Update the empty-plan early-return path to also use enriched payload (metrics=None for no-step case)

- [x] **Task 5: Write tests** (AC: #9)
  - [x] `test_task_dispatch.py` — `parse_step_metrics` tests: git diff parsing, pytest parsing, combined metrics, empty outputs, malformed output, multi-step aggregation, ci_state green/red/unknown
  - [x] `test_task_dispatch.py` — `build_completion_payload` with/without metrics, partial metrics, blockers_count
  - [x] `test_task_dispatch.py` — backward compat (no metrics → FR9 fields are None)

- [x] **Task 6: Verification + commit** (AC: #6, #7, #8, #10, #11, #12)
  - [x] `ruff check` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `scripts/check_event_registry.py` exits 0
  - [x] `just test` green
  - [x] Manual verification: inspect that `_render_completed` already handles enriched payloads (no code change needed)
  - [x] Atomic commit

## Dev Notes

### What already exists

**`packages/events/src/events/payloads.py`** — `TaskCompletedPayload` (lines 307-376):
Already has ALL FR9 fields with full Pydantic validation:
```python
class TaskCompletedPayload(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    pr_url: str | None = Field(default=None, ...)
    pr_number: int | None = Field(default=None, ge=1, le=10**9)
    pr_branch: str | None = Field(default=None, ...)
    files_changed: int | None = Field(default=None, ge=0, le=10**6)
    lines_added: int | None = Field(default=None, ge=0, le=10**9)
    lines_removed: int | None = Field(default=None, ge=0, le=10**9)
    tests_added: int | None = Field(default=None, ge=0, le=10**6)
    ci_state: Literal["green", "red", "unknown"] | None = None
    blockers_count: int | None = Field(default=None, ge=0, le=10**6)
```
Registered as v1.0.0, v1.0.1, v1.1.0 in `event_types.py` (line 107-109).

**`services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py`** — `build_completion_payload()` (lines 153-166):
Currently only populates `task_id` and `summary`. This is what Story 5.13 enriches.

**`services/orchestrator-adapter/src/orchestrator_adapter/app/main.py`** — `process_task()` (lines 207-250):
Step execution loop collects `step_outputs: dict[int, str]` mapping step numbers to OMC stdout. After all steps, calls `build_completion_payload(task_id, plan_result, step_outputs)`.

**`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`** — `_render_completed()` (lines 1227-1381):
Already renders ALL FR9 fields with a 7-step section-drop ladder. No changes needed.

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Payload construction | `build_*_payload()` in `task_dispatch.py` → returns dict | Stories 5.10-5.12 |
| Metrics extraction | New `parse_step_metrics()` → `CompletionMetrics` | Story 5.13 (this story) |
| Dataclass pattern | Frozen dataclass for domain aggregates | `PlanParseResult` in task_dispatch.py |
| Telegram renderer | Already handles all FR9 fields | Story 3.12 `_render_completed()` |
| Schema registration | Already registered v1.1.0 with FR9 fields | Story 3.12 event_types.py |
| Import boundary | `events` package OK; no cross-service imports | architecture.md |

### Key design decisions

1. **Extract metrics from OMC output, not from live git/pytest** — The orchestrator-adapter drives OMC (a subprocess). It doesn't have access to the actual git repo or test runner. It can only parse the structured text that OMC emits in its stdout. This is the only reliable data source available.

2. **Defensive regex parsing** — OMC output format is not guaranteed. The parser must handle missing patterns gracefully (zero-count defaults). No crash on unexpected output.

3. **No new Telegram renderer** — Story 3.12 already shipped the full FR9 renderer with section-drop ladder. This story only populates the fields that renderer already expects.

4. **No new payload model or schema version** — `TaskCompletedPayload` already has all FR9 fields. This story just changes `build_completion_payload()` to populate them instead of leaving them as `None`.

5. **`CompletionMetrics` as a separate dataclass** — Separates metrics extraction from payload construction. Makes the extraction testable in isolation without needing `PlanParseResult` or full event machinery.

6. **`blockers_count` is always 0 in current flow** — Since the first blocker returns early (Story 5.12 design), the count is either 0 (clean completion) or the flow doesn't reach `task.completed`. The field is forward-compatible for retry stories where multiple blockers may accumulate.

### OMC output patterns to parse

The parser should handle these common patterns from Claude Code / OMC output:

**Git diff style:**
```
3 files changed, 42 insertions(+), 15 deletions(-)
```

**Pytest style:**
```
12 passed, 2 failed
=== 14 tests in 3.45s ===
```

**Ruff/mypy style:**
```
All checks passed!
Found 0 errors
```

The parser should use simple regex patterns and be tolerant of variations (extra whitespace, different orderings, partial matches).

### Downstream consumers

- **Story 5.14** (PR draft) — creates PR after task.completed, enriches `pr_url` / `pr_number` / `pr_branch`
- **Story 5.17a** (resume-after-approval) — replaces `"s-placeholder"` with real session ID
- **Story 5.18** (Journey 1 integration test) — validates the full execution flow including enriched completion payload

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1629-1641 — Story 5.13 definition]
- [Source: `packages/events/src/events/payloads.py` lines 307-376 — TaskCompletedPayload with FR9 fields]
- [Source: `services/registry-state/src/registry_state/domain/event_types.py` lines 107-109 — task.completed v1.1.0 registration]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` lines 153-166 — current build_completion_payload]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` lines 207-250 — step execution loop and completion]
- [Source: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` lines 1227-1381 — _render_completed (no changes needed)]
- [Source: `_bmad-output/implementation-artifacts/5-12-task-execution-driver.md — previous story, explicitly names 5.13 as downstream consumer]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

N/A

### Completion Notes List

- Added `CompletionMetrics` frozen dataclass to `task_dispatch.py` with `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state`, `blockers_count` fields (all defaulting to 0/"unknown")
- Added `parse_step_metrics(step_outputs)` to `task_dispatch.py` — regex-based extraction of git-diff patterns (`N files changed, N insertions(+), N deletions(-)`), pytest patterns (`N passed`, `N failed`), and `N tests added` patterns from OMC step outputs; aggregates across all steps; derives ci_state from test results
- Updated `build_completion_payload()` to accept optional `metrics: CompletionMetrics | None` parameter; populates FR9 fields on `TaskCompletedPayload` when metrics provided; zero-count values mapped to `None` for clean Telegram rendering
- Wired metrics extraction into `process_task()`: added `blockers_count` tracking during step iteration, calls `parse_step_metrics(step_outputs)` after all steps complete, passes enriched metrics to `build_completion_payload()`
- No changes to Telegram renderer or payload model — both already had full FR9 support (shipped in Story 3.12)
- 16 new tests in `test_task_dispatch.py`: parse_step_metrics (git diff, pytest green/red, tests_added pattern, empty outputs, malformed output, multi-step aggregation, ci_state derivation, single-file grammar, passed+0-failed), CompletionMetrics frozen, build_completion_payload (with metrics, with blockers, backward compat, zero→None mapping, partial metrics)
- All 1199 tests pass (44 in target file); ruff clean; check_event_registry exits 0; check_imports 1 pre-existing IMP001 (unrelated)

### File List

- `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — added CompletionMetrics dataclass, parse_step_metrics, enriched build_completion_payload
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` — wired metrics extraction into process_task, added blockers_count tracking
- `services/orchestrator-adapter/src/orchestrator_adapter/test_task_dispatch.py` — 16 new tests
- `_bmad-output/implementation-artifacts/5-13-completion-summary-payload.md` — status → review
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 5-13 → review
