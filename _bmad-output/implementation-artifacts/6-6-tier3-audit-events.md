# Story 6.6: Tier-3 audit events (materializer handlers)

Status: ready-for-dev

## Story

As the platform,
I want every Tier-3 action attempt and performance emitted as typed audit events that are consumed by the materializer to update queryable task state,
So that an audit reviewer can reconstruct every sensitive operation and the task row reflects the latest tier-3 event via `last_event_id` / `updated_at`.

## Acceptance Criteria

1. **AC-1: `tier3.action_attempted` handler** — Materializer handler registered for `tier3.action_attempted` that updates the task row: sets `updated_at` and `last_event_id`. Does NOT change `status` (the tier-3 attempt is an audit fact, not a lifecycle transition).

2. **AC-2: `tier3.action_performed` handler** — Materializer handler registered for `tier3.action_performed` that updates the task row: sets `updated_at` and `last_event_id`. Does NOT change `status` (the tier-3 performance is an audit fact; the worker lifecycle FSM owns downstream transitions).

3. **AC-3: `tier3.license_override` handler** — Materializer handler registered for `tier3.license_override` that updates the task row: sets `updated_at` and `last_event_id`. Does NOT change `status`.

4. **AC-4: `_extract_ids` fix** — The `_extract_ids` function in `materializer.py` is extended to extract `task_id` from payloads whose event type starts with `"tier3."` (currently only handles `"task."` and `"approval."`). This ensures the `events` table FK `task_id` column is populated correctly for tier-3 events.

5. **AC-5: `Tier3ActionPerformedPayload` model** — A new Pydantic payload model `Tier3ActionPerformedPayload` is added to `packages/events/src/events/payloads.py` with fields: `task_id` (str, 1..64), `action` (str, 1..2000), `accepted` (bool, always True), `approval_event_id` (str | None, 1..128), `reason` (str | None, max 4096). The model follows the same `ConfigDict(frozen=True, strict=True, extra="forbid")` discipline. It is re-exported from `event_types.py` and registered for `"tier3.action_performed"` schema `"1.0.0"`.

6. **AC-6: Audit field verification** — Tests verify that the `EventEnvelope` for each tier-3 event carries the NFR-S3 audit fields: `actor` (kind + id), `emitted_at`, `request_id`, and `payload` containing `task_id`.

7. **AC-7: No regression** — All existing tests pass. `ruff check` clean.

8. **AC-8: Atomic commit** — Single commit with title `feat(registry-state): materializer handlers for tier-3 audit events (Story 6.6)`.

## Tasks

- [ ] Task 1 — Add `Tier3ActionPerformedPayload` model in `payloads.py` (AC-5)
  - [ ] Add model with fields: `task_id`, `action`, `accepted` (bool), `approval_event_id` (str | None), `reason` (str | None)
  - [ ] Add to `__all__` in payloads.py
  - [ ] Add re-export in `event_types.py`
  - [ ] Register `("tier3.action_performed", "1.0.0", Tier3ActionPerformedPayload)` in `event_types.py`
  - [ ] Add to `__all__` in `event_types.py`
- [ ] Task 2 — Extend `_extract_ids` in `materializer.py` for `tier3.*` prefix (AC-4)
  - [ ] Change `env.type.startswith(("task.", "approval."))` to `env.type.startswith(("task.", "approval.", "tier3."))`
- [ ] Task 3 — Add `handle_tier3_action_attempted` handler in `handlers.py` (AC-1)
  - [ ] Import `Tier3ActionAttemptedPayload` from event_types
  - [ ] Use `_hydrate` + `_touch_task(session, payload.task_id, envelope)` pattern
  - [ ] Does NOT change status
- [ ] Task 4 — Add `handle_tier3_action_performed` handler in `handlers.py` (AC-2)
  - [ ] Import `Tier3ActionPerformedPayload` from event_types
  - [ ] Use `_hydrate` + `_touch_task(session, payload.task_id, envelope)` pattern
  - [ ] Does NOT change status
- [ ] Task 5 — Add `handle_tier3_license_override` handler in `handlers.py` (AC-3)
  - [ ] Import `LicenseOverridePayload` from event_types
  - [ ] Use `_hydrate` + `_touch_task(session, payload.task_id, envelope)` pattern
  - [ ] Does NOT change status
- [ ] Task 6 — Register all 3 handlers in `register_default_handlers` (AC-1–3)
  - [ ] Add 3 `materializer.register_handler()` calls
  - [ ] Update `__all__` with new handler names
  - [ ] Update section comment count
- [ ] Task 7 — Write tests for the 3 handlers + audit field verification (AC-6, AC-7)
  - [ ] Seed a task, emit `tier3.action_attempted`, assert `last_event_id` updated, status unchanged
  - [ ] Seed a task, emit `tier3.action_performed`, assert `last_event_id` updated, status unchanged
  - [ ] Seed a task, emit `tier3.license_override`, assert `last_event_id` updated, status unchanged
  - [ ] Test all 3 handlers raise `MaterializerError` on missing task
  - [ ] Test audit fields on envelopes (actor, emitted_at, request_id, payload.task_id)
  - [ ] Register tier3 event types in the autouse fixture
- [ ] Task 8 — Verification + commit (AC-7, AC-8)

## Dev Notes

### Key Insight: Why None of These Handlers Change Status

All three tier-3 events are **audit facts**, not lifecycle transitions:
- `tier3.action_attempted`: Records that a capability check was performed (accepted or denied). The task's lifecycle status is unaffected.
- `tier3.action_performed`: Records that a previously-approved action was actually executed. The worker lifecycle FSM (Story 6.7) owns any downstream transitions (e.g. resuming execution after a push).
- `tier3.license_override`: Records that an operator overrode a license flag alongside `approval.granted`. Status transitions are handled by the approval handler and worker FSM.

The materializer's job is to make these events queryable via the tasks table (`last_event_id`, `updated_at`) and the events table (FK-linked via `task_id`). It does not drive state changes.

### `_extract_ids` Fix Is Critical

The `_extract_ids` function in `materializer.py` (line 74) currently only extracts `task_id` for event types starting with `"task."` or `"approval."`:

```python
task_id_raw: object | None = (
    data.get("task_id") if env.type.startswith(("task.", "approval.")) else None
)
```

Without extending this to include `"tier3."`, the `events` table `task_id` FK column would be `NULL` for all tier-3 events, breaking the audit trail queryability contract. The fix is a one-line change:

```python
task_id_raw: object | None = (
    data.get("task_id") if env.type.startswith(("task.", "approval.", "tier3.")) else None
)
```

### Handler Pattern (Use `_touch_task`)

All three handlers follow the exact same pattern established in Story 6.5, using the `_touch_task` helper extracted in the Story 6.5 review pass:

```python
async def handle_tier3_action_attempted(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``tier3.action_attempted``.

    Does NOT change task status — the attempt is an audit fact, not a lifecycle
    transition.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, Tier3ActionAttemptedPayload)
    assert isinstance(payload, Tier3ActionAttemptedPayload)
    await _touch_task(session, payload.task_id, envelope)
```

The other two handlers are structurally identical, differing only in the payload model and event type.

### `Tier3ActionPerformedPayload` Model

The epics file describes this payload as: `{task_id, action: "git_push", performed_at, actor, approval_event_id}`. However, `actor` is already on the envelope, and `performed_at` is redundant with `envelope.emitted_at`. The model should include only the payload-specific fields:

```python
class Tier3ActionPerformedPayload(BaseModel):
    """Payload for the ``tier3.action_performed`` event (FR38 / Story 6.6).

    Emitted when a Tier-3 action is actually executed after approval.
    ``actor`` is carried on the envelope, not duplicated here.
    ``performed_at`` is ``envelope.emitted_at`` — not duplicated.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=2000)
    accepted: bool
    approval_event_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4096)
```

### Existing `Tier3ActionAttemptedPayload`

Already defined in `payloads.py` (line 699):

```python
class Tier3ActionAttemptedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    action: str = Field(min_length=1, max_length=2000)
    task_id: str = Field(min_length=1, max_length=64)
    accepted: bool
    reason: str | None = Field(default=None, max_length=4096)
```

Already registered in `event_types.py` (line 168):

```python
register("tier3.action_attempted", "1.0.0", Tier3ActionAttemptedPayload)
```

### `LicenseOverridePayload`

Already defined in `payloads.py` (line 763) and registered in `event_types.py` (line 174). Has `task_id`, `decision_id`, `actor_id`, `reason`. No new model needed for this one.

### structlog Gotcha

Never use `event=` as a kwarg with structlog loggers -- clashes with positional `event` param. Use `extra={...}` for structured data.

### Files to Touch

| File | Change |
|------|--------|
| `packages/events/src/events/payloads.py` | Add `Tier3ActionPerformedPayload` model + add to `__all__` |
| `services/registry-state/src/registry_state/domain/event_types.py` | Re-export `Tier3ActionPerformedPayload`, register `"tier3.action_performed"` + add to `__all__` |
| `services/registry-state/src/registry_state/domain/materializer.py` | Extend `_extract_ids` prefix tuple to include `"tier3."` |
| `services/registry-state/src/registry_state/domain/handlers.py` | Add 3 handler functions + register them + update `__all__` |
| `services/registry-state/src/registry_state/domain/test_handlers.py` | Add tests for the 3 new handlers |

### Relationship to Previous Stories

- **Story 6.2** defined the capability-tier enforcement helpers and registered `tier3.action_attempted` in the schema registry (payload model + registration only, no materializer handler).
- **Story 6.4** added the operator decision event types (`approval.granted`, `approval.rejected`, `task.retry_requested`, `tier3.license_override`) and their registrations. It also wired the emitter in the HTTP decisions route.
- **Story 6.5** added materializer handlers for the 4 decision audit events (`approval.granted`, `approval.rejected`, `task.stop_requested`, `task.retry_requested`). It extracted the `_touch_task` helper that this story reuses. It also fixed the `_extract_ids` prefix to include `"approval."`.
- **Story 6.7** (worker approval-wait state) will CONSUME the `approval.granted` event at the worker level and emit `tier3.action_performed` upon execution. This story only adds the materializer side for consuming those events.

### Scope Boundary

- Do NOT add worker lifecycle FSM changes (Story 6.7)
- Do NOT add the `tier3.action_performed` emitter (Story 6.7 emits it from the worker)
- Do NOT change the HTTP decisions endpoint (Story 6.4)
- Do NOT add `tier3.action_attempted` emitter logic (already wired in capabilities, Story 6.2/6.3)
- DO add `Tier3ActionPerformedPayload` model so the materializer can deserialize it
- DO register materializer handlers that make tier-3 events queryable via the tasks table
- DO extend `_extract_ids` so tier-3 events get their `task_id` FK populated in the events table
- DO write tests that verify the full event -> handler -> state change pipeline

### References

- [Source: epics.md -- Story 6.6 lines 1862-1878]
- [Source: architecture.md -- line 220 audit logging (LOCKED)]
- [Source: payloads.py -- `Tier3ActionAttemptedPayload` (line 699), `LicenseOverridePayload` (line 763)]
- [Source: event_types.py -- `tier3.action_attempted` registration (line 168), `tier3.license_override` registration (line 174)]
- [Source: handlers.py -- `_touch_task` helper (line 67), Story 6.5 handler pattern (line 246)]
- [Source: materializer.py -- `_extract_ids` prefix tuple (line 74)]
- [Source: test_handlers.py -- Story 6.5 test pattern (line 565)]

## Dev Agent Record

### Agent Model Used

(TBD)

### Debug Log References

None.

### Completion Notes List

(TBD)

### File List

(TBD)
