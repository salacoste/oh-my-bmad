# Story 6.5: Approval audit events (materializer handlers)

Status: review

## Story

As the operator,
I want every operator decision (`approve`, `reject`, `stop`, `retry`) to trigger a materializer state transition so the task's queryable status reflects the decision,
So that NFR-S3 (auditability) is enforced on the control plane and downstream consumers (GET /v1/tasks, Telegram status) see correct state.

## Acceptance Criteria

1. **AC-1: `approval.granted` handler** — Materializer handler registered for `approval.granted` that updates the task row: sets `updated_at` and `last_event_id`. Does NOT change `status` (the worker lifecycle FSM — Story 6.7 — controls the executing/planning transition; premature status change would break the FSM contract).

2. **AC-2: `approval.rejected` handler** — Materializer handler registered for `approval.rejected` that updates `updated_at` and `last_event_id`. Does NOT change `status` (the task stays in its current status; rejection is a decision, not a lifecycle transition).

3. **AC-3: `task.stop_requested` handler** — Materializer handler registered for `task.stop_requested` that updates `status="stopped"`, `updated_at`, and `last_event_id`. Stop is a terminal state — no FSM coupling needed.

4. **AC-4: `task.retry_requested` handler** — Materializer handler registered for `task.retry_requested` that updates `updated_at` and `last_event_id`. Does NOT change `status` (retry triggers re-planning via the worker lifecycle; the materializer does not own that transition).

5. **AC-5: Audit field verification** — Tests verify that the `EventEnvelope` for each decision event carries the NFR-S3 audit fields: `actor` (kind + id), `emitted_at` (timestamp), `request_id`, and `payload` containing `task_id` and `decision_id` (or `actor_id` for stop). These are already guaranteed by the `EventEnvelope` structure but the tests assert the contract explicitly.

6. **AC-6: No regression** — All existing tests pass. `check_imports.py` exits 0 (only changes are in `registry-state` which is a service). `ruff check` clean.

7. **AC-7: Atomic commit** — Single commit with title `feat(registry-state): materializer handlers for decision audit events (Story 6.5)`.

## Tasks

- [x] Task 1 — Add `handle_approval_granted` handler in `handlers.py` (AC-1)
  - [x] Query task by `payload.task_id`; skip if not found (same pattern as `handle_task_blocker_raised`)
  - [x] UPDATE tasks SET `updated_at=envelope.emitted_at`, `last_event_id=envelope.event_id` WHERE id=task_id
  - [x] Do NOT change `status`
- [x] Task 2 — Add `handle_approval_rejected` handler in `handlers.py` (AC-2)
  - [x] Same pattern as `handle_approval_granted` — update `updated_at` + `last_event_id` only
- [x] Task 3 — Add `handle_task_stop_requested` handler in `handlers.py` (AC-3)
  - [x] UPDATE tasks SET `status="stopped"`, `updated_at=envelope.emitted_at`, `last_event_id=envelope.event_id`
- [x] Task 4 — Add `handle_task_retry_requested` handler in `handlers.py` (AC-4)
  - [x] Same pattern as `handle_approval_granted` — update `updated_at` + `last_event_id` only
- [x] Task 5 — Register all 4 handlers in `register_default_handlers` (AC-1–4)
  - [x] Add 4 `materializer.register_handler()` calls
  - [x] Update docstring (currently says "4 task-event handlers" — update count)
  - [x] Update `__all__` with new handler names
- [x] Task 6 — Write tests for the 4 handlers (AC-5)
  - [x] Seed a task in appropriate status for each handler
  - [x] Emit the corresponding event envelope
  - [x] Assert task row reflects the handler's state changes (or non-changes)
  - [x] Assert `last_event_id` and `updated_at` are updated
  - [x] Assert `events` table contains the event row
- [x] Task 7 — Verification + commit (AC-6, AC-7)

## Dev Notes

### Key Insight: Why Some Handlers Don't Change Status

The decision events are emitted by the HTTP API (`POST /v1/tasks/{id}/decisions`) which is a **command** endpoint. The actual lifecycle state machine runs in the **worker-wrapper**. Prematurely changing task status in the materializer would:

- Break the FSM's state guard assertions (Story 6.7's worker checks status before resuming)
- Race with the worker's own `task.execution.started` emission
- Violate the principle that the materializer reflects, not drives, state changes

The exception is `task.stop_requested` → `stopped`: this IS a terminal transition that no FSM manages. The operator's stop decision is the final word.

### Handler Pattern

Follow the established pattern from `handle_task_blocker_raised` (Story 2.8):

```python
async def handle_approval_granted(session: AsyncSession, envelope: EventEnvelope) -> None:
    payload = ApprovalGrantedPayload.model_validate(envelope.payload)
    result = await session.execute(
        update(Task)
        .where(Task.id == payload.task_id)
        .values(updated_at=envelope.emitted_at, last_event_id=envelope.event_id)
    )
    if result.rowcount == 0:
        log.warning("approval.granted for unknown task", extra={"task_id": payload.task_id})
```

The `handle_task_stop_requested` handler adds `status="stopped"` to the `.values()` call.

### Existing Handler Conventions

From `handlers.py`:
- Each handler is an `async def` taking `(session: AsyncSession, envelope: EventEnvelope)`
- Payload is validated via `PayloadModel.model_validate(envelope.payload)`
- Unknown-task case raises MaterializerError (consistent with Story 2.8 handlers; rowcount guard catches out-of-order replay)
- All handlers are registered in `register_default_handlers()`
- All handler names are exported in `__all__`

### structlog Gotcha

Never use `event=` as a kwarg with structlog loggers — clashes with positional `event` param. Use `extra={...}` for structured data.

### Files to Touch

| File | Change |
|------|--------|
| `services/registry-state/src/registry_state/domain/handlers.py` | Add 4 handler functions + register them + update `__all__` |
| `services/registry-state/src/registry_state/domain/event_types.py` | No changes needed — event types already registered |
| `services/registry-state/src/registry_state/domain/materializer.py` | No changes needed — dispatch logic already generic |
| `services/registry-state/tests/test_handlers.py` | Add tests for the 4 new handlers |

### Relationship to Previous Stories

- **Story 6.4** emitted the events via `EventLogWriter.append()` in registry-api. Those events land in the JSONL log. This story adds the materializer side — consuming those events to update SQLite state.
- **Story 2.5** built the materializer dispatch core (`_handlers.get(envelope.type)`).
- **Story 2.8** added 4 handlers (`blocker_raised`, `summary_emitted`, `approval_requested`, `completed`) — follow the same pattern.
- **Story 6.7** (worker approval-wait state) will CONSUME the `approval.granted` event at the worker-wrapper level to resume execution. The materializer handler in this story only records the audit event; it does not trigger execution.
- **Story 5.17** (resume-after-approval) tests the end-to-end approval pipeline including the materializer's state update.

### Scope Boundary

- Do NOT add worker lifecycle FSM changes (Story 6.7)
- Do NOT add `tier3.action_attempted` / `tier3.action_performed` handlers (Story 6.6)
- Do NOT change the HTTP decisions endpoint (Story 6.4)
- DO register materializer handlers that make decision events queryable via the tasks table
- DO write tests that verify the full event → handler → state change pipeline

### References

- [Source: epics.md — Epic 6 Story 6.5]
- [Source: prd.md — FR7, NFR-S3]
- [Source: architecture.md — line 220 audit logging (LOCKED)]
- [Source: architecture.md — line 327-331 event naming conventions]
- [Source: architecture.md — line 800 read vs write path separation]
- [Source: handlers.py — existing 8 handler registrations (lines 331-339)]
- [Source: materializer.py — dispatch logic (lines 168-170)]
- [Source: decisions.py — event emission (lines 159-173)]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

None.

### Completion Notes List

- Implemented 4 materializer handlers: `handle_approval_granted`, `handle_approval_rejected`, `handle_task_stop_requested`, `handle_task_retry_requested`
- All handlers follow the established `_hydrate` + `update(Task)` + `rowcount != 1` pattern from Story 2.8 handlers
- Only `handle_task_stop_requested` changes status to `"stopped"` (terminal state); other 3 handlers update `updated_at` + `last_event_id` only
- Registered all 4 in `register_default_handlers()`, updated docstring count from 4 to 12, updated `__all__`
- Added 6 tests: 4 positive handler tests (AC-1 through AC-4), 1 missing-task error test, 1 audit field verification test (AC-5)
- All 259 registry-state tests pass (0 regressions), ruff check clean, check_imports.py shows only pre-existing IMP001 violations from Story 6.4

### File List

- `services/registry-state/src/registry_state/domain/handlers.py` — Added 4 handler functions, registered them, updated imports/docstring/__all__
- `services/registry-state/src/registry_state/domain/test_handlers.py` — Added 6 tests for Story 6.5 handlers
