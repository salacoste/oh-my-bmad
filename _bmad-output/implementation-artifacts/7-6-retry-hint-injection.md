# Story 7.6: `/retry` with hint-injection into orchestrator context

Status: done

## Story

As the operator,
I want `/retry t-0001 hint="..."` to resume a blocked task with my clarifying hint injected into the orchestrator's next planning pass,
So that I can course-correct without re-submitting a full task.

## Acceptance Criteria

1. **Given** a task is in `blocked` status
   **When** `POST /v1/tasks/t-0001/decisions {action:retry, hint:"rate limit must be per-user"}` is processed
   **Then** the materializer persists the hint onto the `Task` row, transitions the task status to `pending`, and the task-registry MCP resource surfaces the `hint` field.

2. **Given** a retried task with a persisted hint is picked up by the orchestrator-adapter
   **When** `build_omc_prompt()` is called
   **Then** the hint appears as `Hint: <text>` in the planning prompt.

3. **Given** `task.retry_requested` is emitted without a hint (`hint: null`)
   **When** the materializer processes the event
   **Then** the task row's `hint` column is set to `None` (cleared), and the task still transitions to `pending`.

*Cites: FR7, FR5, FR6 (reconnaissance coupling).*

## Tasks / Subtasks

- [x] Task 1 — Add `hint` column to Task ORM model (AC: #1)
  - [x] Add `hint: Mapped[str | None] = mapped_column(Text, nullable=True)` to `Task` in `services/registry-state/src/registry_state/schema.py`.
  - [x] Column is nullable — existing rows get `NULL`. No data migration needed (in-memory SQLite per test; production uses `create_all`).

- [x] Task 2 — Update materializer handler to persist hint and transition status (AC: #1, #3)
  - [x] In `services/registry-state/src/registry_state/domain/handlers.py`, update `handle_task_retry_requested`:
    - Extract `payload.hint` from the hydrated `TaskRetryRequestedPayload`.
    - Call `_touch_task(session, payload.task_id, envelope, extra_values={"status": "pending", "hint": payload.hint})`.
    - Update docstring to reflect the new behavior: persists hint and transitions to `pending`.
  - [x] **Why `pending` and not `planning`**: The orchestrator-adapter's `_tasks_needing_planning()` (line 97-100 of `orchestrator-adapter/app/main.py`) filters for `{"pending", "new", "ready"}`. Status `planning` is NOT in that set, so transitioning to `pending` ensures the orchestrator picks up the retried task.

- [x] Task 3 — Update task-registry MCP resource to surface hint (AC: #1)
  - [x] In `mcp-servers/task-registry/src/task_registry_mcp/handlers/resources.py` (lines 27-38), update `_task_to_dict()` to include `"hint": task.hint`.
  - [x] This makes `hint` available in the `task://list` and `task://detail/{task_id}` MCP resources that the orchestrator-adapter reads.

- [x] Task 4 — Update REST API `TaskResponse` to surface hint (AC: #1)
  - [x] In `services/registry-api/src/registry_api/routes/tasks.py`, add `hint: str | None = None` field to `TaskResponse` model (after `reply_to_message_id` at line ~217).
  - [x] Add `hint=task.hint` to the `TaskResponse(...)` constructor at line ~596.
  - [x] This follows the same pattern as `current_step`, `total_steps`, `last_agent_action` (Story 7.1), and `blocker_reason` (Story 6.11) — every Task ORM column is surfaced in both MCP resource and REST API.

- [x] Task 5 — Remove TODO in orchestrator-adapter (AC: #2)
  - [x] In `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` line 177, remove the `# TODO: not yet materialized by task-registry` comment. The hint is now materialized and surfaced.

- [x] Task 6 — Update tests (AC: #1, #2, #3)
  - [x] **registry-state handler tests** (`services/registry-state/src/registry_state/domain/test_handlers.py`):
    - Update `test_task_retry_requested_updates_last_event_id`: assert `task.hint == "focus on X"` and `task.status == "pending"`.
    - Add `test_task_retry_without_hint_clears_existing_hint`: seed a task with a previous hint, retry without hint, assert `task.hint is None` and `task.status == "pending"`.
    - Add `test_task_retry_requested_transitions_to_pending`: explicit test that status changes from `blocked`/`failed` to `pending`.
  - [x] **registry-api decisions tests** (`services/registry-api/src/registry_api/test_decisions.py`):
    - Update `test_retry_with_hint_on_blocked`: assert the emitted event payload contains `hint == "Focus on rate limiting"` (if the test inspects the event log).
  - [x] **registry-api tasks tests** (`services/registry-api/src/registry_api/test_app.py`):
    - Verify `GET /v1/tasks/{task_id}` response includes `hint` field (the existing `test_seeded_task_returns_all_fields` test will need `hint` added to its assertions).
  - [x] **task-registry MCP resource tests** (`mcp-servers/task-registry/src/task_registry_mcp/test_server.py` — this is the existing test file):
    - Add a unit test for `_task_to_dict` that asserts `"hint"` key is present in the returned dict.

- [x] Task 7 — Run full regression suite (AC: #1)
  - [x] `uv run pytest services/registry-state/ -x -q` passes.
  - [x] `uv run pytest services/registry-api/ -x -q` passes.
  - [x] `uv run pytest mcp-servers/task-registry/ -x -q` passes (if tests exist).
  - [x] `ruff check` clean on all modified files.

## Dev Notes

### Architecture: What This Story Does

This story closes the data-flow gap between the retry decision endpoint (which emits `task.retry_requested` with a hint payload) and the orchestrator-adapter (which reads task data to build its planning prompt). The pipe is 90% built — this story adds the missing 10%: materializing the hint onto the task row and surfacing it through the MCP resource.

**The full data flow after this story:**
```
Telegram /retry <id> hint  →  console-cli --hint
        ↓
POST /v1/tasks/{id}/decisions {action:retry, hint:"..."}
        ↓  (decisions.py — DONE)
EventEnvelope(type="task.retry_requested", payload.hint="...")
        ↓  (event log — DONE)
handle_task_retry_requested  →  _touch_task(extra_values={"status":"pending", "hint":...})
        ↓  (THIS STORY)
Task row: status="pending", hint="..."
        ↓
task-registry MCP resource _task_to_dict includes "hint"
        ↓  (THIS STORY)
orchestrator-adapter reads task.get("hint")  →  build_omc_prompt(hint=...)
        ↓  (READY — remove TODO)
OMC planning prompt: "Hint: rate limit must be per-user"
```

### Critical: What Is Already Done (DO NOT recreate)

| Layer | Status | File |
|---|---|---|
| Telegram `/retry <id> [hint]` | DONE | `telegram-gateway/handlers/retry_command.py` |
| Console CLI `--hint` | DONE | `console-cli/commands/retry.py` |
| HTTP API `DecisionRequest.hint` | DONE | `registry-api/routes/decisions.py` (line 69) |
| Event payload `TaskRetryRequestedPayload.hint` | DONE | `packages/events/src/events/payloads.py` (line 777) |
| Event emission in `_build_event()` | DONE | `registry-api/routes/decisions.py` (line 467) |
| Schema registry entry | DONE | `registry-state/domain/event_types.py` (line 181) |
| Materializer handler registration | DONE | `registry-state/domain/handlers.py` (line 531) |
| Orchestrator reads hint | DONE (blocked) | `orchestrator-adapter/app/main.py` (line 177) |
| `build_omc_prompt(hint=...)` | DONE | `orchestrator-adapter/domain/task_dispatch.py` (line 203) |

### Materializer Handler: Current vs Required

**Current** (`handlers.py` lines 316-326):
```python
async def handle_task_retry_requested(session, envelope):
    payload = _hydrate(envelope.payload, TaskRetryRequestedPayload)
    assert isinstance(payload, TaskRetryRequestedPayload)
    await _touch_task(session, payload.task_id, envelope)  # only updates last_event_id + updated_at
```

**Required** (after this story):
```python
async def handle_task_retry_requested(session, envelope):
    payload = _hydrate(envelope.payload, TaskRetryRequestedPayload)
    assert isinstance(payload, TaskRetryRequestedPayload)
    await _touch_task(
        session, payload.task_id, envelope,
        extra_values={"status": "pending", "hint": payload.hint},
    )
```

The `_touch_task` helper already accepts `extra_values: dict[str, object] | None = None` and merges them into the UPDATE statement. No changes needed to `_touch_task` itself.

### Task ORM: Column Addition

Add to `registry_state/schema.py` Task model:
```python
hint: Mapped[str | None] = mapped_column(Text, nullable=True)  # Story 7.6 / FR7
```

Place after `last_agent_action` (the most recently added column). The column is nullable so existing rows get `NULL` automatically.

### MCP Resource: `_task_to_dict` Update

In `mcp-servers/task-registry/src/task_registry_mcp/handlers/resources.py`:
```python
def _task_to_dict(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "actor_kind": task.actor_kind,
        "actor_id": task.actor_id,
        "title": task.title,
        "last_event_id": task.last_event_id,
        "hint": task.hint,          # ADD THIS
    }
```

### Orchestrator-Adapter: Remove TODO

In `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` line 177:
```python
hint = task.get("hint")  # TODO: not yet materialized by task-registry
```
Change to:
```python
hint = task.get("hint")
```

### Why `pending` Status (Not `planning`)

The AC text says "transitions back to `planning`" but the orchestrator-adapter's `_tasks_needing_planning()` (lines 97-100) filters for `{"pending", "new", "ready"}`. The status `planning` is NOT in that set. The correct transition target is `pending`, which:
1. Is the initial state for new tasks (consistent semantics)
2. Is picked up by the orchestrator's filter
3. Indicates the task needs a fresh planning pass

If `planning` status is needed for other reasons (e.g., worker lifecycle tracking), that transition happens when the orchestrator dispatches the task to the worker, not at the materializer level.

### REST API: `TaskResponse` Update

In `services/registry-api/src/registry_api/routes/tasks.py`:
1. Add field to `TaskResponse` (line ~217, after `reply_to_message_id`):
   ```python
   hint: str | None = None  # Story 7.6 / FR7
   ```
2. Add to constructor (line ~596, after `reply_to_message_id=task.reply_to_message_id`):
   ```python
   hint=task.hint,
   ```

This follows the established pattern: every column added to the Task ORM is surfaced in both the MCP resource (`_task_to_dict`) and the REST API (`TaskResponse`).

### Scope Boundary

**DO modify:**
- `services/registry-state/src/registry_state/schema.py` — add `hint` column to Task ORM
- `services/registry-state/src/registry_state/domain/handlers.py` — update `handle_task_retry_requested`
- `mcp-servers/task-registry/src/task_registry_mcp/handlers/resources.py` — update `_task_to_dict`
- `services/registry-api/src/registry_api/routes/tasks.py` — add `hint` to `TaskResponse` model and constructor
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` — remove TODO comment
- Test files as specified in Task 6

**DO NOT modify:**
- `packages/events/src/events/payloads.py` — `TaskRetryRequestedPayload` already has `hint` field
- `packages/events/src/events/schema_registry.py` — `task.retry_requested` already registered
- `services/registry-api/src/registry_api/routes/decisions.py` — already emits events with hint
- `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` — already sends hint
- `services/console-cli/src/console_cli/commands/retry.py` — already sends `--hint`
- `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py` — `build_omc_prompt` already handles hint
- `services/worker-wrapper/` — worker lifecycle is out of scope for this story

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated (same as prior stories):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### Relationship to Other Stories

- **Story 6.4** (decisions handler): Created `POST /v1/tasks/{id}/decisions` with retry action and hint support. This story closes the materializer gap.
- **Story 7.5** (events raw tail): Created `GET /v1/tasks/{id}/events`. The retry hint can be observed in raw events via this endpoint (reconnaissance coupling).
- **Story 7.7** (worktree-lock blocker persistence): Downstream. Cites FR27 — worktree lock held through blocker windows. The retry transition in this story does NOT release the lock; the worker-wrapper handles that when it picks up the retried task.
- **Story 7.10** (journey-6 integration test): End-to-end test that exercises the full `/retry hint="..."` flow and verifies the hint is honored in the final plan.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.6]
- [Source: _bmad-output/planning-artifacts/prd.md#FR7]
- [Source: _bmad-output/planning-artifacts/architecture.md#registry-api-service]
- [Source: services/registry-state/src/registry_state/schema.py — Task ORM model]
- [Source: services/registry-state/src/registry_state/domain/handlers.py — handle_task_retry_requested, _touch_task]
- [Source: mcp-servers/task-registry/src/task_registry_mcp/handlers/resources.py — _task_to_dict, task://list resource]
- [Source: services/orchestrator-adapter/src/orchestrator_adapter/app/main.py — process_task, _tasks_needing_planning]
- [Source: services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py — build_omc_prompt]
- [Source: packages/events/src/events/payloads.py — TaskRetryRequestedPayload]
- [Source: services/registry-state/src/registry_state/domain/event_types.py — task.retry_requested registration]
- [Source: services/registry-api/src/registry_api/routes/decisions.py — retry action handling]
- [Source: services/registry-state/src/registry_state/domain/test_handlers.py — existing retry tests]
- [Source: services/registry-api/src/registry_api/test_decisions.py — existing retry route tests]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- Added `hint` column (Text, nullable) to Task ORM model in `schema.py`.
- Updated `handle_task_retry_requested` to persist hint and transition status to `pending` via `_touch_task(extra_values={"status": "pending", "hint": payload.hint})`.
- Added `hint` field to `_task_to_dict` in task-registry MCP resource — surfaces hint in `task://list` and `task://detail/{task_id}`.
- Added `hint: str | None = None` to `TaskResponse` model and constructor in `tasks.py` — REST API parity with MCP resource.
- Removed `# TODO: not yet materialized by task-registry` comment from orchestrator-adapter `main.py`.
- Updated existing test `test_task_retry_requested_updates_last_event_id` to assert `task.hint == "focus on X"`.
- Added `test_task_retry_without_hint_clears_existing_hint` — verifies hint is cleared on retry without hint.
- Added `test_task_retry_requested_transitions_to_pending` — verifies status transition from `blocked` to `pending`.
- Added `test_get_task_includes_hint_field` — verifies REST API returns `hint` in GET response.
- Added `test_task_to_dict_includes_hint_field` and `test_task_to_dict_includes_hint_when_set` — verifies MCP resource serialization.
- All 281 registry-state tests pass, all 114 registry-api tests pass, all 37 task-registry tests pass. Ruff clean.

### File List

- services/registry-state/src/registry_state/schema.py — added `hint` column to Task ORM
- services/registry-state/src/registry_state/domain/handlers.py — updated `handle_task_retry_requested` to persist hint and transition to pending
- mcp-servers/task-registry/src/task_registry_mcp/handlers/resources.py — added `hint` to `_task_to_dict`
- services/registry-api/src/registry_api/routes/tasks.py — added `hint` to `TaskResponse` model and constructor
- services/orchestrator-adapter/src/orchestrator_adapter/app/main.py — removed TODO comment
- services/registry-state/src/registry_state/domain/test_handlers.py — updated existing test + added 2 new tests
- services/registry-api/src/registry_api/test_app.py — added `test_get_task_includes_hint_field`
- mcp-servers/task-registry/src/task_registry_mcp/test_server.py — added 2 MCP resource tests
