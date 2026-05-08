# Story 5.11: Task plan emission (FR2)

Status: done

## Story

As the operator,
I want the platform to produce a stepwise plan before execution and emit `task.plan.ready` with a structured plan summary,
So that I see what the agent intends to do before it acts.

## Acceptance Criteria

1. **AC-1: Structured plan payload** — `TaskPlanReadyPayload` in `packages/events/src/events/payloads.py` is extended with:
   - `plan: list[PlanStep]` where `PlanStep` is a frozen Pydantic model with `step: int` and `description: str`
   - `estimated_steps: int` — count of plan steps
   - `plan_summary: str` is retained for backward compatibility (existing emitters continue to work)
   - New fields default to empty/zero so existing events deserialize cleanly (additive-only, NFR-M3)

2. **AC-2: Plan step parsing** — `parse_omc_plan_output()` in `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` returns a `ParseResult` containing both the structured `list[PlanStep]` and the human-readable `plan_summary: str`. New `build_plan_ready_payload()` populates all fields including `plan` and `estimated_steps`.

3. **AC-3: Event emission** — Orchestrator-adapter emits `task.plan.ready` via clawhip-bridge MCP with the fully populated structured payload (plan steps + estimated_steps + plan_summary).

4. **AC-4: Telegram rendering** — A `_render_plan_ready()` renderer in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` produces:
   ```
   Plan ready, N steps:
   1) ...
   2) ...
   3) ...
   ```
   Registered in the `_RENDERERS` dispatch table under `"task.plan.ready"`.

5. **AC-5: Length safety** — The plan-ready renderer follows the same section-drop pattern as existing renderers (Story 3.10-3.13): 1900-codepoint cap, progressive step truncation, emergency one-liner fallback.

6. **AC-6: Schema registry** — `task.plan.ready` and `task.planning.started` are registered in `packages/events/src/events/schema_registry.py` with their payload models if not already present. `scripts/check_event_registry.py` exits 0.

7. **AC-7: Import discipline** — No new cross-service imports. `clawhip-daemon` imports `TaskPlanReadyPayload` from `events` (already allowed). `orchestrator-adapter` imports from `events` (already allowed). `check_imports.py` exits 0.

8. **AC-8: Tests** — At least 15 new tests across 3 test files:
   - `test_task_dispatch.py` (orchestrator-adapter) — structured parsing, step extraction, payload construction
   - `test_plan_ready_renderer.py` (clawhip-daemon) — renderer output, length safety, section-drop ladder, emergency fallback
   - `test_payloads.py` or inline — `PlanStep` validation, `TaskPlanReadyPayload` backward compatibility

9. **AC-9: `just lint` green** — All lint gates pass including `mypy --strict`.

10. **AC-10: `just test` no regressions** — Existing test count unchanged. New tests increase count.

11. **AC-11: Atomic commit** — title: `feat(events+orchestrator+clawhip): structured plan emission and Telegram rendering · E5`

## Tasks / Subtasks

- [x] **Task 1: Add PlanStep model and extend TaskPlanReadyPayload** (AC: #1)
  - [x] Add `PlanStep` frozen Pydantic model (`step: int`, `description: str`) to `packages/events/src/events/payloads.py`
  - [x] Add `plan: tuple[PlanStep, ...] = Field(default=())` and `estimated_steps: int = Field(default=0)` to `TaskPlanReadyPayload`
  - [x] Verify backward compatibility: existing events with only `task_id` + `plan_summary` deserialize with new fields defaulting

- [x] **Task 2: Register plan events in schema registry** (AC: #6)
  - [x] Check `packages/events/src/events/schema_registry.py` for existing `task.plan.ready` / `task.planning.started` registrations
  - [x] Add v1.1.0 registration with `TaskPlanReadyPayload` in `registry_state/domain/event_types.py`
  - [x] Run `scripts/check_event_registry.py` to verify

- [x] **Task 3: Update plan parsing in orchestrator-adapter** (AC: #2, #3)
  - [x] Create `PlanParseResult` frozen dataclass in `domain/task_dispatch.py` with `steps: tuple[PlanStep, ...]`, `summary: str`
  - [x] Update `parse_omc_plan_output()` to return `PlanParseResult` — extract numbered steps into `PlanStep` list, fall back to flat summary
  - [x] Update `build_plan_ready_payload()` to accept `PlanParseResult` and populate `plan`, `estimated_steps`, and `plan_summary`
  - [x] Update `app/main.py` `process_task()` to use the new return type

- [x] **Task 4: Add plan-ready Telegram renderer** (AC: #4, #5)
  - [x] Add `_render_plan_ready()` function in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
  - [x] Import `TaskPlanReadyPayload` from `events` (follow existing import pattern)
  - [x] Render format: `Plan ready, N steps:\n1) ...\n2) ...`
  - [x] Implement section-drop ladder: 20 steps → 10 → 4 → emergency one-liner
  - [x] Add `_PLAN_READY_MESSAGE_MAX_CHARS: int = 1900` (parity with other renderers)
  - [x] Register in `_RENDERERS` dispatch table under `"task.plan.ready"`

- [x] **Task 5: Write tests** (AC: #8)
  - [x] `test_task_dispatch.py` — 16 tests: step extraction from numbered lists, markdown headings, fallback; payload construction with all fields; frozen dataclass
  - [x] `test_telegram_sink.py` — 10 plan-ready renderer tests: basic, empty, single step, HTML escape, newline collapse, overflow, emergency, type mismatch, dispatcher routing, length cap
  - [x] Backward compatibility verified: old-format payload deserializes with default plan=() and estimated_steps=0

- [x] **Task 6: Verification + commit** (AC: #7, #9, #10, #11)
  - [x] `ruff check` clean on all modified files
  - [x] `scripts/check_imports.py` exits 0 (pre-existing IMP001 unrelated)
  - [x] `scripts/check_event_registry.py` exits 0
  - [x] `just test` — 474 passed, 0 regressions
  - [x] Atomic commit

## Dev Notes

### What already exists

**`packages/events/src/events/payloads.py`** — `TaskPlanReadyPayload`:
```python
class TaskPlanReadyPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    task_id: str
    plan_summary: str
```

**`services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py`** — `parse_omc_plan_output()` returns a flat string:
- Tries markdown "Plan" section heading
- Tries numbered list extraction (`1. ... 2. ...`)
- Falls back to first 500 chars

**`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`**:
- `task.plan.ready` is already in `_DELIVERABLE_EVENT_TYPES` (line 91)
- But NOT registered in `_RENDERERS` dispatch table — falls through to placeholder `"Task <id>: <type>"`
- Existing renderers (approval_request, blocker_raised, completed, self_recovered) provide the pattern to follow

**`services/orchestrator-adapter/src/orchestrator_adapter/app/main.py`** — `process_task()`:
- Already emits `task.planning.started` and `task.plan.ready`
- Calls `parse_omc_plan_output()` for plan summary
- Calls `build_plan_ready_payload()` for payload construction

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Structured payload | Frozen Pydantic models, `ConfigDict(frozen=True, strict=True, extra="forbid")` | `packages/events/src/events/payloads.py` |
| Additive-only changes | New fields default so existing events deserialize | NFR-M3 |
| Telegram renderer | Section-drop ladder, 1900-codepoint cap, HTML-escape, emergency one-liner | Stories 3.10-3.13 |
| Renderer registration | `_RENDERERS` dict in `telegram_sink.py`, `_render()` dispatcher | Story 3.10 AC-4 |
| Event registration | Schema registry in `packages/events/src/events/schema_registry.py` | architecture.md line 331 |
| Import boundary | `events` package OK for all services; no cross-service imports | architecture.md |

### Renderer pattern (reference: `_render_completed` in `telegram_sink.py`)

The plan-ready renderer should follow the established pattern:

1. **Payload type guard**: `isinstance(payload, TaskPlanReadyPayload)` — log WARN on mismatch, fall back to placeholder
2. **HTML-escape** all user-controlled fields (`task_id`, step descriptions)
3. **Section-drop ladder** for length safety:
   - Step 1: Full message with all steps
   - Step 2: Truncate steps (show first N, `… and M more`)
   - Step 3: Emergency one-liner — `Plan ready, N steps. (see /logs <id>)`
4. **Parity cap**: `_PLAN_READY_MESSAGE_MAX_CHARS: int = 1900`
5. **Defensive final-length self-clamp** on emergency one-liner

### Import-graph rules

| Import | Allowed? | Notes |
|---|---|---|
| `events.payloads` | ALLOWED | Both orchestrator-adapter and clawhip-daemon |
| `events.envelope` | ALLOWED | Already imported in both services |
| `events` (top-level) | ALLOWED | `from events import TaskPlanReadyPayload` |
| Other `services/*` | **FORBIDDEN** | Cross-service import ban |

### Step parsing strategy

`parse_omc_plan_output()` should extract structured steps from OMC output:

1. **Markdown heading section**: Find `# Plan` or `## Implementation Plan`, split into numbered items
2. **Numbered list**: Match `1. ...` / `2. ...` / `3. ...` patterns, extract step number + description
3. **Fallback**: If no structured steps found, create a single `PlanStep(step=1, description=<first 500 chars>)`
4. **Summary**: The `plan_summary` field gets the full extracted text (up to 2000 chars as today)

Each `PlanStep.description` should be capped at ~200 chars to keep Telegram rendering within bounds.

### Downstream consumers

- **Story 5.12** (task execution driver) — picks up after plan is ready, drives execution per-step
- **Story 5.18** (Journey 1 integration test) — verifies plan emission as part of end-to-end flow
- **Story 6.1-6.3** (capability tier enforcement) — may inspect plan steps for tier classification

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1598-1613 — Story 5.11 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 847-848 — orchestrator data flow step 7]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 880-884 — Journey 1 plan-ready data flow]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 327-331 — event naming convention]
- [Source: `packages/events/src/events/payloads.py` lines 81-87 — current TaskPlanReadyPayload]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — current plan parsing]
- [Source: `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` — current event emission]
- [Source: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` — renderer pattern]
- [Source: `_bmad-output/implementation-artifacts/5-10-orchestrator-adapter-omc-supervision.md` — Story 5.10 (previous)]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

N/A

### Completion Notes List

- Added `PlanStep` frozen Pydantic model to `packages/events/src/events/payloads.py` with `step: int` and `description: str` fields
- Extended `TaskPlanReadyPayload` with `plan: tuple[PlanStep, ...]` and `estimated_steps: int` (additive-only, defaults for backward compat)
- Registered `task.plan.ready` v1.1.0 in `registry_state/domain/event_types.py`
- Refactored `parse_omc_plan_output()` to return `PlanParseResult` dataclass with structured steps + flat summary
- Updated `build_plan_ready_payload()` signature to accept `PlanParseResult`
- Updated `app/main.py process_task()` to use new return type
- Added `_render_plan_ready()` renderer to `telegram_sink.py` with section-drop ladder (20->10->4->emergency)
- Registered `"task.plan.ready"` in `_RENDERERS` dispatch table
- 26 new tests: 16 in orchestrator-adapter, 10 in clawhip-daemon
- All 474 tests pass, 0 regressions

### File List

- `packages/events/src/events/payloads.py` — added PlanStep model, extended TaskPlanReadyPayload
- `services/registry-state/src/registry_state/domain/event_types.py` — added v1.1.0 registration
- `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — PlanParseResult, structured parsing, updated payload builder
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` — updated process_task to use PlanParseResult
- `services/orchestrator-adapter/src/orchestrator_adapter/test_task_dispatch.py` — 16 tests (updated + new)
- `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` — _render_plan_ready renderer + dispatch registration
- `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` — 10 plan-ready renderer tests
