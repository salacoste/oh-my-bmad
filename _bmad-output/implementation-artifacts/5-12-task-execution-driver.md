# Story 5.12: Task execution driver (FR3 + FR31)

Status: ready-for-dev

## Story

As the platform,
I want the orchestrator-adapter → worker-wrapper handoff to drive execution from `task.execution.requested` through per-step events to `task.completed`,
So that a planned task actually runs end-to-end.

## Acceptance Criteria

1. **AC-1: Execution request emission** — After emitting `task.plan.ready`, `process_task()` in `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` emits `task.execution.started` via clawhip-bridge MCP with `TaskExecutionStartedPayload(task_id, session_id)`. The `session_id` is a placeholder `"s-placeholder"` (worker-wrapper session ID not yet wired — deferred to 5.17).

2. **AC-2: Step-by-step OMC driving** — After plan emission, the orchestrator-adapter iterates over `plan_result.steps` from `PlanParseResult`. For each step, it drives OMC with a step-specific prompt (step number + description) and emits a `task.step.completed` event via clawhip-bridge. A new `TaskStepCompletedPayload` frozen Pydantic model is added to `packages/events/src/events/payloads.py` with fields: `task_id: str`, `step: int`, `description: str`, `output_summary: str` (max 2000 chars).

3. **AC-3: Task completion emission** — After all steps complete, the orchestrator-adapter emits `task.completed` via clawhip-bridge with the existing `TaskCompletedPayload` (minimal fields: `task_id`, `summary`). The summary is synthesized from the step outputs.

4. **AC-4: Schema registry** — `task.execution.started` (v1.0.1 already registered) and `task.step.completed` (new v1.0.0) are registered in `packages/events/src/events/schema_registry.py` via `services/registry-state/src/registry_state/domain/event_types.py`. `scripts/check_event_registry.py` exits 0.

5. **AC-5: Telegram rendering** — A `_render_step_completed()` renderer in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` produces `Step N/N done: <truncated description>`. Registered in `_RENDERERS` under `"task.step.completed"`. Follows established section-drop pattern: 1900-codepoint cap, HTML escape, emergency one-liner.

6. **AC-6: Import discipline** — No new cross-service imports. `orchestrator-adapter` imports from `events` (allowed). `clawhip-daemon` imports from `events` (allowed). `scripts/check_imports.py` exits 0.

7. **AC-7: Tests** — At least 12 new tests across 3 test files:
   - `test_task_dispatch.py` (orchestrator-adapter) — step iteration, completion payload construction, empty plan handling
   - `test_telegram_sink.py` (clawhip-daemon) — step-completed renderer output, length safety, HTML escape, dispatcher routing
   - `test_payloads.py` or inline — `TaskStepCompletedPayload` validation, backward compat

8. **AC-8: `just lint` green** — All lint gates pass including `mypy --strict`.

9. **AC-9: `just test` no regressions** — Existing test count unchanged. New tests increase count.

10. **AC-10: Atomic commit** — title: `feat(events+orchestrator+clawhip): task execution driver with step-by-step OMC driving · E5`

## Tasks / Subtasks

- [ ] **Task 1: Add TaskStepCompletedPayload model** (AC: #2, #4)
  - [ ] Add `TaskStepCompletedPayload` frozen Pydantic model to `packages/events/src/events/payloads.py` with `task_id: str`, `step: int` (ge=1), `description: str` (min_length=1, max_length=500), `output_summary: str` (max_length=2000)
  - [ ] Add to `__all__`
  - [ ] Register `task.step.completed` v1.0.0 in `services/registry-state/src/registry_state/domain/event_types.py`

- [ ] **Task 2: Add execution-driving functions to task_dispatch.py** (AC: #1, #2, #3)
  - [ ] Add `build_execution_started_payload(task_id: str, session_id: str) -> dict[str, object]`
  - [ ] Add `build_step_completed_payload(task_id: str, step: PlanStep, output_summary: str) -> dict[str, object]`
  - [ ] Add `build_completion_payload(task_id: str, plan_result: PlanParseResult, step_outputs: dict[int, str]) -> dict[str, object]` — synthesizes summary from step outputs, populates `TaskCompletedPayload` minimal fields

- [ ] **Task 3: Wire execution loop into process_task** (AC: #1, #2, #3)
  - [ ] After `task.plan.ready` emission in `app/main.py`, emit `task.execution.started`
  - [ ] Iterate over `plan_result.steps`, drive OMC per step with `build_omc_prompt(task_id, hint=step.description)`, collect output
  - [ ] Emit `task.step.completed` after each step
  - [ ] After all steps, emit `task.completed` with synthesized summary
  - [ ] Handle per-step OMC failure: emit `task.blocker_raised` and return early

- [ ] **Task 4: Add step-completed Telegram renderer** (AC: #5)
  - [ ] Add `_render_step_completed()` in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
  - [ ] Import `TaskStepCompletedPayload` from `events`
  - [ ] Render format: `Step N/N done: <description>`
  - [ ] Implement length safety: 1900-char cap, description truncation, emergency one-liner
  - [ ] Register in `_RENDERERS` under `"task.step.completed"`

- [ ] **Task 5: Write tests** (AC: #7)
  - [ ] `test_task_dispatch.py` — payload builders, step iteration, completion summary synthesis, empty plan
  - [ ] `test_telegram_sink.py` — renderer output, HTML escape, length cap, dispatcher routing, type mismatch
  - [ ] Inline or `test_payloads.py` — `TaskStepCompletedPayload` validation

- [ ] **Task 6: Verification + commit** (AC: #6, #8, #9, #10)
  - [ ] `ruff check` clean
  - [ ] `scripts/check_imports.py` exits 0
  - [ ] `scripts/check_event_registry.py` exits 0
  - [ ] `just test` green
  - [ ] Atomic commit

## Dev Notes

### What already exists

**`services/orchestrator-adapter/src/orchestrator_adapter/app/main.py`** — `process_task()`:
- Already emits `task.planning.started`, drives OMC, emits `task.plan.ready`
- Has `PlanParseResult` with structured `steps: tuple[PlanStep, ...]`
- After plan emission, the function returns — Story 5.12 extends it to drive execution
- `_emit_event()` helper calls clawhip-bridge `emit_event` tool

**`services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py`**:
- `PlanParseResult` frozen dataclass with `summary`, `steps`, `estimated_steps`
- `build_omc_prompt(task_id, title=, hint=, repo=)` — can be reused for per-step prompts
- `build_plan_ready_payload(task_id, plan_result)` — existing pattern to follow

**`services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py`**:
- `OMCRunner.run(prompt)` returns `OMCResult(stdout, error, exit_code, duration_ms)`
- Already used in `process_task()` for planning

**`packages/events/src/events/payloads.py`** — `TaskExecutionStartedPayload`:
```python
class TaskExecutionStartedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    task_id: str
    session_id: str
```
Already registered as v1.0.0 and v1.0.1 in `event_types.py`.

**`packages/events/src/events/payloads.py`** — `TaskCompletedPayload`:
Already has full structured fields (files_changed, lines_added, etc.) — Story 5.13 enriches these. For 5.12, only `task_id` and `summary` are populated (minimal).

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Event emission | `_emit_event()` helper → clawhip-bridge `emit_event` tool | `main.py` line 93 |
| Payload construction | `build_*_payload()` in `task_dispatch.py` → returns dict | Story 5.10/5.11 |
| Step iteration | `plan_result.steps` from `PlanParseResult` | Story 5.11 |
| OMC driving | `OMCRunner.run(prompt)` → `OMCResult` | Story 5.10 |
| Telegram renderer | Section-drop ladder, 1900-char cap, HTML escape | Stories 3.10-3.13 |
| Schema registration | `register()` in `event_types.py` | All stories |
| Import boundary | `events` package OK; no cross-service imports | architecture.md |

### Execution flow design

```
process_task():
  1. [EXISTS] Emit task.planning.started
  2. [EXISTS] Drive OMC for planning → parse plan
  3. [EXISTS] Emit task.plan.ready
  4. [NEW] Emit task.execution.started(task_id, "s-placeholder")
  5. [NEW] For each step in plan_result.steps:
     a. Drive OMC with step-specific prompt (hint=step.description)
     b. On failure: emit task.blocker_raised, return
     c. Emit task.step.completed(task_id, step, output_summary)
  6. [NEW] Emit task.completed(task_id, summary)
```

### Key design decisions

1. **Orchestrator drives execution, not worker-wrapper** — The orchestrator-adapter already drives OMC for planning. Story 5.12 extends it to also drive per-step execution. The worker-wrapper (which drives the actual Claude Code CLI) will be wired in a future story (5.17a resume-after-approval).

2. **Sequential step execution** — Steps are driven one at a time. If any step fails, execution stops and a blocker is raised.

3. **Session ID placeholder** — The `task.execution.started` payload requires a `session_id`. Since the worker-wrapper session is not yet wired to orchestrator-adapter, use `"s-placeholder"`. This is explicitly called out and will be replaced in Story 5.17a.

4. **Minimal task.completed payload** — Only `task_id` and `summary` are populated. Structured fields (files_changed, lines_added, etc.) are added by Story 5.13 (completion summary payload).

### Telegram renderer pattern

The step-completed renderer should follow the established pattern (Stories 3.10-3.13, 5.11):

1. **Payload type guard**: `isinstance(payload, TaskStepCompletedPayload)` — log WARN on mismatch
2. **HTML-escape** all user-controlled fields
3. **Format**: `Step N: <description>` (single line per step)
4. **Length safety**: 1900-char cap, description truncation to 200 chars, emergency one-liner

### Downstream consumers

- **Story 5.13** (completion summary) — enriches the `task.completed` payload with structured fields
- **Story 5.14** (PR draft) — creates PR after task.completed
- **Story 5.17a** (resume-after-approval) — replaces `"s-placeholder"` with real session ID
- **Story 5.18** (Journey 1 integration test) — validates the full execution flow end-to-end

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1615-1627 — Story 5.12 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 840-849 — data flow steps 7-9]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 862-906 — Journey 1 data flow]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 327-330 — event naming convention]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 384-401 — event envelope schema]
- [Source: `packages/events/src/events/payloads.py` lines 107-114 — TaskExecutionStartedPayload]
- [Source: `packages/events/src/events/payloads.py` lines 81-87 — PlanStep model]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — PlanParseResult]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` — current process_task flow]
- [Source: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` — renderer pattern]
- [Source: `_bmad-output/implementation-artifacts/5-11-task-plan-emission.md` — previous story]

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
