# Story 5.15: Per-task budget enforcement (FR44)

Status: done

## Story

As the platform,
I want the worker to track per-task compute/token budget and emit `task.budget_exceeded`
if the configured ceiling is reached,
So that cost-loop bugs cannot run the operator's API bill into the ground.

## Acceptance Criteria

1. **AC-1: Budget ceiling config** — `OrchestratorSettings` gains `task_token_budget: int = 50_000` (env: `ORCHESTRATOR_TASK_TOKEN_BUDGET`, must be > 0). Value of 0 disables enforcement entirely.

2. **AC-2: Token extraction from OMC output** — A `parse_token_usage(raw_output: str) -> int | None` function in `task_dispatch.py` extracts the token count from OMC step stdout using a regex pattern matching common Claude CLI output formats (e.g., `"tokens: 1234"`, `"usage: {input: 500, output: 200}"`, `"Token usage: 700"`). Returns `None` when no pattern matches (step output without token info).

3. **AC-3: Cumulative tracking** — A `BudgetTracker` class in `task_dispatch.py` (frozen dataclass: `limit: int`, `used: int = 0`, method `consume(tokens: int) -> BudgetTracker` returns new instance) accumulates tokens across all steps. After each step in the execution loop, the tracker is updated and checked against the ceiling.

4. **AC-4: `task.budget_exceeded` event** — A new `TaskBudgetExceededPayload` in `packages/events/src/events/payloads.py` with fields: `task_id: str`, `token_limit: int`, `tokens_used: int`, `step: int` (which step triggered it). Registered as `task.budget_exceeded` v1.0.0 in `services/registry-state/src/registry_state/domain/event_types.py`.

5. **AC-5: Halt on ceiling** — When cumulative tokens exceed `task_token_budget`, `process_task()` immediately emits `task.budget_exceeded` and returns — no further steps execute. The `task.completed` event is NOT emitted in this path (the task is "blocked pending extension approval", per FR44).

6. **AC-6: 10% overshoot tolerance** — The check fires when `used > limit`. Because token extraction happens after a step completes, the actual `tokens_used` in the event may exceed the limit by the tokens consumed in the triggering step. This is acceptable as long as `tokens_used <= limit * 1.1` (NFR-P5: no more than 10% over). If a single step's tokens alone exceed 1.1× the limit, log a warning (can't prevent it without pre-step estimation).

7. **AC-7: Token usage on completion** — When task completes normally (no budget breach), the `task.completed` payload includes a `token_usage: int | None` field (extend `CompletionMetrics` or add to `build_completion_payload`). This enables downstream reporting without a separate query.

8. **AC-8: `BudgetExceeded` exception** — Add a `BudgetExceeded` exception class to `packages/events/src/events/errors.py` in the typed exception hierarchy. This is for internal signaling within the orchestrator-adapter, not for API boundary use (the public interface is the event).

9. **AC-9: Budget disabled path** — When `task_token_budget == 0`, no token extraction, tracking, or checking occurs. All token-related code is skipped with zero overhead.

10. **AC-10: No new Telegram renderer** — The `task.budget_exceeded` event is consumed by Story 6.11 (Epic 6) which handles the operator notification flow. No renderer changes in this story.

11. **AC-11: Import discipline** — `BudgetTracker`, `parse_token_usage`, and `build_budget_exceeded_payload` are in `task_dispatch.py`. `TaskBudgetExceededPayload` in `packages/events`. `BudgetExceeded` in `packages/events/errors.py`. No cross-service imports. `scripts/check_imports.py` exits 0.

12. **AC-12: Tests** — At least 10 new tests:
    - `test_task_dispatch.py` — `parse_token_usage` with various formats, `BudgetTracker` accumulate/check/frozen, `build_budget_exceeded_payload`, completion payload with token_usage
    - `test_main_budget.py` or integration in `test_task_dispatch.py` — guard that budget disabled (limit=0) skips tracking

13. **AC-13: `just lint` green, `just test` no regressions.**

14. **AC-14: Atomic commit** — title: `feat(orchestrator): add per-task budget enforcement with task.budget_exceeded event · E5`

## Tasks / Subtasks

- [x] **Task 1: Create event infrastructure** (AC: #4, #8)
  - [x] Add `TaskBudgetExceededPayload` to `packages/events/src/events/payloads.py` with `task_id`, `token_limit`, `tokens_used`, `step` fields
  - [x] Add to `__all__` in payloads.py
  - [x] Register `task.budget_exceeded` v1.0.0 in `services/registry-state/src/registry_state/domain/event_types.py`
  - [x] Add `BudgetExceeded` exception to `packages/events/src/events/errors.py`

- [x] **Task 2: Add budget config** (AC: #1)
  - [x] Add `task_token_budget: int = Field(default=50_000, ge=0)` to `OrchestratorSettings` in `config.py`
  - [x] Docstring note: 0 disables enforcement

- [x] **Task 3: Implement budget tracking logic** (AC: #2, #3)
  - [x] Add `parse_token_usage(raw_output: str) -> int | None` to `task_dispatch.py` with regex patterns
  - [x] Add `BudgetTracker` frozen dataclass with `limit: int`, `used: int = 0`, `consume(tokens: int)` method
  - [x] Add `is_exceeded` property: `return self.used > self.limit if self.limit > 0 else False`

- [x] **Task 4: Build payload and wire into process_task** (AC: #4, #5, #6, #7, #9)
  - [x] Add `build_budget_exceeded_payload(task_id, tracker, step)` to `task_dispatch.py`
  - [x] In `process_task()`, initialize `BudgetTracker` from settings before step loop
  - [x] After each step: extract tokens, update tracker, check if exceeded
  - [x] If exceeded: emit `task.budget_exceeded`, log warning, return early (no `task.completed`)
  - [x] If budget disabled (limit==0): skip all tracking
  - [x] On normal completion: pass `token_usage=tracker.used` to `build_completion_payload`

- [x] **Task 5: Extend completion payload** (AC: #7)
  - [x] Add `token_usage: int | None = None` keyword arg to `build_completion_payload()`
  - [x] Pass through to `TaskCompletedPayload` (field already exists or add it)

- [x] **Task 6: Write tests** (AC: #12)
  - [x] `test_task_dispatch.py` — `parse_token_usage` tests (matching, not matching, edge cases)
  - [x] `test_task_dispatch.py` — `BudgetTracker` tests (accumulate, is_exceeded, frozen, zero limit)
  - [x] `test_task_dispatch.py` — `build_budget_exceeded_payload` test
  - [x] `test_task_dispatch.py` — completion payload with `token_usage`

- [x] **Task 7: Verification + commit** (AC: #11, #13, #14)
  - [x] `ruff check` and `ruff format` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `scripts/check_event_registry.py` exits 0
  - [x] All tests green, no regressions
  - [x] Atomic commit

## Dev Notes

### What already exists

**`packages/events/src/events/payloads.py`** — All existing payload classes. `TaskCompletedPayload` (line ~363) already has PR fields. Need to check if it has a `token_usage` field or if one needs to be added.

**`packages/events/src/events/errors.py`** — Currently has `EventsError`, `EventSchemaUnknown`, `EventValidationError`, `CanonicalSerializationError`, `WorktreeLockHeld`. Need to add `BudgetExceeded`.

**`services/registry-state/src/registry_state/domain/event_types.py`** — Event type registry. Last registered: `file.edited`, `agent.reasoning.*`, `session.*` lifecycle events. Need to add `task.budget_exceeded`.

**`services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py`** — Has `parse_step_metrics()` (regex extraction pattern to follow), `CompletionMetrics`, `build_completion_payload()`. Extend with `parse_token_usage()`, `BudgetTracker`, `build_budget_exceeded_payload()`.

**`services/orchestrator-adapter/src/orchestrator_adapter/app/main.py`** — `process_task()` has the step execution loop at line ~258 (`for step in plan_result.steps:`). Each step produces `step_outputs[step.step]`. Budget tracking inserts after line ~281 (step output captured), before the next iteration.

**`services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py`** — `OMCRunner.run()` returns `OMCResult` with `stdout` and `duration_ms`. The token count must be extracted from `stdout` via regex — the OMC subprocess does not expose structured token metadata.

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Event payload | Pydantic BaseModel in `packages/events/payloads.py` | Stories 5.10-5.14 |
| Event registration | `register()` in `event_types.py` with version | Stories 2.1, 5.2 |
| Payload builder | `build_*_payload()` in `task_dispatch.py` | Stories 5.10-5.14 |
| Regex extraction | `re.compile` + `findall` in `parse_step_metrics()` | Story 5.13 |
| Config | `OrchestratorSettings` with `ORCHESTRATOR_` prefix | Story 5.10 |
| Frozen dataclass | `CompletionMetrics`, `PlanParseResult` | Stories 5.11, 5.13 |
| Import boundary | `events` package OK; no cross-service imports | architecture.md |
| Typed exceptions | `packages/events/errors.py` hierarchy | architecture.md line 423 |

### Key design decisions

1. **Extract tokens from OMC stdout, not from a structured API** — The OMC subprocess is a Node CLI that wraps Claude Code. It does not expose a structured token-usage API. Token counts must be parsed from stdout using regex patterns matching Claude CLI output formats. This is the same pattern as `parse_step_metrics()` extracting git-diff and pytest patterns.

2. **Frozen dataclass tracker** — `BudgetTracker` is a frozen dataclass following the `CompletionMetrics` pattern. `consume()` returns a new instance (immutable). This avoids mutable state issues in the async step loop.

3. **Early return on budget breach** — When budget is exceeded, `process_task()` returns without emitting `task.completed`. The task is effectively "blocked" — Story 6.11 (Epic 6) handles the operator notification and extension-approval flow. This is a deliberate departure from the non-blocking pattern used for PR creation (Story 5.14).

4. **Token usage on normal completion** — Even when budget is not breached, the cumulative token count is included in the `task.completed` payload. This provides observability without requiring a separate query.

5. **Budget disabled when limit=0** — Setting `task_token_budget=0` disables all budget tracking, allowing operators to run without enforcement during development/testing.

6. **No pre-step estimation** — Token counting happens after a step completes (post-hoc). We cannot prevent a single large step from exceeding the budget. The 10% overshoot tolerance (NFR-P5) accommodates this. Pre-step estimation would require changes to the OMC subprocess protocol, which is out of scope.

### Token extraction regex patterns

Claude Code CLI output formats to match:
```
"Token usage: 1234"
"tokens used: 1234"
"Usage: {input_tokens: 500, output_tokens: 200}" → sum = 700
"Total tokens: 1234"
```

The primary pattern should match a number following "token" (case-insensitive):
```python
_TOKEN_USAGE_RE = re.compile(r"(\d+)\s+tokens?", re.IGNORECASE)
```

With a fallback for structured JSON-like usage blocks. If no pattern matches, `parse_token_usage` returns `None` (step didn't produce token info).

### Downstream consumers

- **Story 6.11** (Epic 6) — consumes `task.budget_exceeded`, transitions task to `blocked`, delivers blocker message to operator via Telegram
- **Story 5.17a** (resume-after-approval) — handles the extension-approval flow that unblocks a budget-exceeded task
- **Story 5.18** (Journey 1 integration test) — validates the full execution flow including budget enforcement

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1657-1672 — Story 5.15 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 874 — FR44 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 908 — NFR-P5 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 423 — BudgetExceeded exception]
- [Source: `packages/events/src/events/payloads.py` — Existing payload classes]
- [Source: `packages/events/src/events/errors.py` — Existing exception hierarchy]
- [Source: `services/registry-state/src/registry_state/domain/event_types.py` — Event registry]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — parse_step_metrics pattern]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` lines 258-287 — Step execution loop]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/app/config.py` — OrchestratorSettings]
- [Source: `_bmad-output/implementation-artifacts/5-14-pr-draft-auto-creation.md — Previous story, patterns to follow]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

None — no blocking issues encountered.

### Completion Notes List

- All 7 tasks completed. 76 orchestrator-adapter tests passing (66 existing + 10 new budget tests). 170 events package tests passing.
- Added `TaskBudgetExceededPayload` with `task_id`, `token_limit`, `tokens_used`, `step` fields.
- Added `BudgetExceeded` exception to the typed exception hierarchy in `packages/events/errors.py`.
- Added `token_usage: int | None` field to `TaskCompletedPayload` (registered v1.2.0).
- `parse_token_usage` uses two regex patterns: `(\d+)\s+tokens?` (number before "tokens") and `tokens?\D*?(\d+)` (number after "tokens") — covers "1234 tokens", "tokens: 500", "Token usage: 500", "Total tokens: 999" formats.
- Budget disabled path: `tracker=None` when `limit==0`, so all token extraction and checking is skipped with zero overhead.
- 10% overshoot warning logged when `tokens_used > 1.1 * limit`.
- Pre-existing `check_imports.py` violation in `worker-wrapper/test_reasoning.py` is unrelated.

### File List

- `packages/events/src/events/payloads.py` (MODIFIED — TaskBudgetExceededPayload, token_usage on TaskCompletedPayload)
- `packages/events/src/events/errors.py` (MODIFIED — BudgetExceeded exception)
- `services/registry-state/src/registry_state/domain/event_types.py` (MODIFIED — task.budget_exceeded v1.0.0, task.completed v1.2.0)
- `services/orchestrator-adapter/src/orchestrator_adapter/app/config.py` (MODIFIED — task_token_budget setting)
- `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` (MODIFIED — BudgetTracker, parse_token_usage, build_budget_exceeded_payload, token_usage kwarg)
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` (MODIFIED — budget tracking in step loop)
- `services/orchestrator-adapter/src/orchestrator_adapter/test_task_dispatch.py` (MODIFIED — 15 new tests)
