# Story 6.4: `POST /v1/tasks/{id}/decisions` handler + payload shapes

Status: in-progress

## Story

As the operator,
I want `POST /v1/tasks/{id}/decisions` to accept `{action: approve|reject|stop|retry, reason?, hint?, override?}` payloads,
so that Telegram and Console can route all operator decisions through a single endpoint.

## Acceptance Criteria

1. **AC-1: `DecisionRequest` Pydantic model** — New frozen+strict model in `routes/tasks.py`: `action: Literal["approve", "reject", "stop", "retry"]`, optional `reason: str` (max 4096 chars), optional `hint: str` (max 4096 chars), optional `override: str | None` (literal `"license"` only for Phase 1). All extra fields forbidden.

2. **AC-2: `DecisionResponse` Pydantic model** — Frozen model matching the wire shape expected by `telegram_gateway/handlers/registry_client.py:DecisionResponseLocal`: `task_id: str`, `decision_id: str` (generated `d-<uuidv7>`), `action: Literal["approve", "reject", "stop", "retry"]`, `decided_at: datetime`, `idempotency_status: Literal["applied", "replayed"]`. Response status code: `202 Accepted` for `approve`/`reject`, `200 OK` for `stop`/`retry`.

3. **AC-3: `POST /v1/tasks/{task_id}/decisions` route handler** — New route in `routes/tasks.py` (or a new `routes/decisions.py` router). Handler validates the task exists (404 if not) and checks task status against the action's preconditions:
   - `approve`: task must be in `plan_ready` or `awaiting_approval` status.
   - `reject`: task must be in `plan_ready` or `awaiting_approval` status.
   - `stop`: task must NOT be in a terminal state (`completed`, `failed`, `stopped`).
   - `retry`: task must be in `blocked` or `failed` status.
   - Precondition failure returns 409 Conflict with RFC 7807 problem+json body and detail explaining the state mismatch.

4. **AC-4: Event emission per action** — The handler emits the correct typed event for each action via `EventLogWriter.append()`:
   - `approve` → `approval.granted` event with payload `{task_id, decision_id, actor_id, override: <override or null>}`.
   - `reject` → `approval.rejected` event with payload `{task_id, decision_id, actor_id, reason}`.
   - `stop` → `task.stop_requested` event (already registered at schema version `1.0.0`/`1.0.1` with `TaskStopRequestedPayload`).
   - `retry` → `task.retry_requested` event with payload `{task_id, decision_id, actor_id, hint}`.

5. **AC-5: Payload models for new event types** — New Pydantic models in `packages/events/src/events/payloads.py`:
   - `ApprovalGrantedPayload` — `task_id: str`, `decision_id: str`, `actor_id: str`, `override: str | None = None`.
   - `ApprovalRejectedPayload` — `task_id: str`, `decision_id: str`, `actor_id: str`, `reason: str | None = None`.
   - `TaskRetryRequestedPayload` — `task_id: str`, `decision_id: str`, `actor_id: str`, `hint: str | None = None`.
   All frozen, strict, extra forbidden. Registered in `registry_state/domain/event_types.py` at schema version `1.0.0`.

6. **AC-6: Idempotency** — The decisions endpoint uses the same `IdempotencyCacheStore.get_or_run()` pattern as `POST /v1/tasks`. Cache key is `(actor_id, idempotency_key)`. On replay, returns the stored response with `idempotency_status: "replayed"`. The `X-Idempotency-Status` header is set to `"applied"` or `"replayed"` matching the task-creation convention.

7. **AC-7: Tier enforcement integration** — `ROUTE_TIER_MAP` in `adapters/middleware.py` gains `"POST /v1/tasks": Tier.ONE` already exists; the decisions sub-path `POST /v1/tasks/{id}/decisions` matches the longest-prefix rule from `_resolve_tier` so it inherits `Tier.ONE`. No `ROUTE_TIER_MAP` changes required — the existing prefix match covers it. Verify via a test that a worker-kind caller can POST to `/v1/tasks/{id}/decisions` (worker max tier is `Tier.TWO` ≥ `Tier.ONE`).

8. **AC-8: License override branch** — When `action == "approve"` and `override == "license"`, the handler emits BOTH `approval.granted` AND `tier3.license_override` (payload: `{task_id, decision_id, actor_id, reason: "operator_license_override"}`). The `tier3.license_override` event type and payload model (`LicenseOverridePayload`) are registered in `events/payloads.py` and `registry_state/domain/event_types.py`.

9. **AC-9: `_NEXT_COMMANDS` update** — Add `"awaiting_approval"` key to the `_NEXT_COMMANDS` dict in `routes/tasks.py`: `["approve", "reject", "stop"]`. This makes the GET task response correctly surface available commands for tasks awaiting operator approval.

10. **AC-10: Route wiring** — If the decisions route lives in a new `routes/decisions.py`, `build_app` includes the new router with `app.include_router(decisions_router, prefix="/v1")`. If it lives in the existing `routes/tasks.py`, no wiring changes needed. The router's `dependencies` list must NOT duplicate middleware-tier checks — that is the middleware's job.

11. **AC-11: Positive tests** — Tests for each action:
    - `approve` on `plan_ready` task → 202, `approval.granted` emitted, response body has correct shape.
    - `reject` with reason on `awaiting_approval` task → 202, `approval.rejected` emitted.
    - `stop` on `executing` task → 200, `task.stop_requested` emitted.
    - `retry` with hint on `blocked` task → 200, `task.retry_requested` emitted.
    - Idempotent replay returns stored response with `idempotency_status: "replayed"`.

12. **AC-12: Negative tests** — Tests for:
    - Decision on nonexistent task → 404.
    - `approve` on `completed` task → 409 with state mismatch detail.
    - `retry` on `pending` task → 409 with state mismatch detail.
    - Invalid `action` value → 422 (Pydantic validation).
    - `override: "license"` on non-approve action → 422 validation error.

13. **AC-13: No regression** — All existing tests pass. `check_imports.py` exits 0. `ruff check` clean. `just test` green.

14. **AC-14: Atomic commit** — Single commit with title `feat(registry-api): decisions handler endpoint (Story 6.4)`.

## Tasks

- [ ] Task 1 — Add new payload models to `events/payloads.py` (AC-5)
  - [ ] `ApprovalGrantedPayload` — task_id, decision_id, actor_id, optional override
  - [ ] `ApprovalRejectedPayload` — task_id, decision_id, actor_id, optional reason
  - [ ] `TaskRetryRequestedPayload` — task_id, decision_id, actor_id, optional hint
  - [ ] `LicenseOverridePayload` — task_id, decision_id, actor_id, reason
  - [ ] Export from `__all__`
  - [ ] Add unit tests for payload model validation
- [ ] Task 2 — Register new event types in `registry_state/domain/event_types.py` (AC-5)
  - [ ] `approval.granted` → `ApprovalGrantedPayload` at schema version `1.0.0`
  - [ ] `approval.rejected` → `ApprovalRejectedPayload` at schema version `1.0.0`
  - [ ] `task.retry_requested` → `TaskRetryRequestedPayload` at schema version `1.0.0`
  - [ ] `tier3.license_override` → `LicenseOverridePayload` at schema version `1.0.0`
- [ ] Task 3 — Implement `DecisionRequest` and `DecisionResponse` models (AC-1, AC-2)
  - [ ] `DecisionRequest` — action literal, optional reason/hint/override
  - [ ] `DecisionResponse` — task_id, decision_id, action, decided_at, idempotency_status
- [ ] Task 4 — Implement the decisions route handler (AC-3, AC-4, AC-6, AC-8)
  - [ ] Create `routes/decisions.py` with `APIRouter()` and `POST /tasks/{task_id}/decisions`
  - [ ] Task existence check (404)
  - [ ] State precondition validation (409)
  - [ ] Event emission per action via `EventLogWriter.append()`
  - [ ] License override branch (dual event emission)
  - [ ] Idempotency via `IdempotencyCacheStore.get_or_run()` with scoped key
  - [ ] Response with 202/200 status codes
- [ ] Task 5 — Wire decisions router into `build_app` (AC-10)
  - [ ] `app.include_router(decisions_router, prefix="/v1")`
  - [ ] Pass `idempotency_cache`, `response_body_cache`, `event_writer` via `app.state`
- [ ] Task 6 — Update `_NEXT_COMMANDS` with `awaiting_approval` state (AC-9)
  - [ ] Add `"awaiting_approval": ["approve", "reject", "stop"]` to `_NEXT_COMMANDS`
- [ ] Task 7 — Add positive tests (AC-11)
  - [ ] Test approve on plan_ready task
  - [ ] Test reject on awaiting_approval task
  - [ ] Test stop on executing task
  - [ ] Test retry with hint on blocked task
  - [ ] Test idempotent replay
  - [ ] Test license override dual event emission
- [ ] Task 8 — Add negative tests (AC-12)
  - [ ] Test 404 on nonexistent task
  - [ ] Test 409 on state mismatch for approve
  - [ ] Test 409 on state mismatch for retry
  - [ ] Test 422 on invalid action
  - [ ] Test 422 on override with non-approve action
- [ ] Task 9 — Verification + commit (AC-13, AC-14)
  - [ ] Run `check_imports.py`
  - [ ] Run `ruff check`
  - [ ] Run `just test`
  - [ ] Atomic commit

## Dev Notes

### Key Architecture Decision: Route Location

The decisions endpoint can live either in the existing `routes/tasks.py` or in a new `routes/decisions.py`. Given that:
- The file `routes/tasks.py` is already 553 lines
- The decisions handler has its own distinct request/response models and state precondition logic
- architecture.md line 614 shows `http_routes.py` as a single file but the project has already split into `routes/tasks.py`

**Recommendation**: Create `routes/decisions.py` to keep each file focused. The two routers share the same `/v1` prefix and the same `app.state` dependencies.

### Event Emission Pattern

Follow the established pattern from `POST /v1/tasks` in `routes/tasks.py`:

1. Get `event_writer`, `idempotency_cache`, `response_body_cache` from `request.app.state`
2. Build scoped cache key `(actor_id, idempotency_key)`
3. Define `_factory()` closure that:
   - Generates `decision_id` via `new_decision_id(clock=clock)` (new helper in `events/ids.py`)
   - Builds `EventEnvelope.create()` with the correct event type and payload
   - Calls `await writer.append(envelope)`
   - Builds and caches the response
4. Call `idempotency_cache.get_or_run()` with the factory
5. Branch on `was_run` — applied vs replayed

### Actor Construction

Phase 1 continues using the hardcoded actor:
```python
actor = Actor(kind="operator", id=request.state.actor_id)
```

The middleware's `ActorIdMiddleware` sets `request.state.actor_id = "http-api"` and the `TierEnforcementMiddleware` populates `request.state.caller_context` with `CallerContext(actor_kind="operator", actor_id="http-api")`.

### State Precondition Validation

The handler must query the materialized task state from the read-only SQLite to validate preconditions. The read session maker is on `app.state.session_maker`. Query the `Task` table for the task's current status:

```python
async with app.state.session_maker() as session:
    result = await session.execute(select(Task).where(Task.task_id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, ...)
    current_status = task.status
```

State transition rules (from AC-3 and `_NEXT_COMMANDS`):

| Action | Valid Statuses |
|--------|---------------|
| `approve` | `plan_ready`, `awaiting_approval` |
| `reject` | `plan_ready`, `awaiting_approval` |
| `stop` | any non-terminal (`completed`, `failed`, `stopped` excluded) |
| `retry` | `blocked`, `failed` |

### Decision ID Generation

Add `new_decision_id()` to `events/ids.py` following the established pattern for `new_request_id()` and `new_task_id()`:

```python
def new_decision_id(*, clock: Clock) -> str:
    return f"d-{clock.uuidv7()}"
```

This produces `d-<uuidv7>` IDs consistent with the `DecisionResponseLocal` expectation in the Telegram gateway client.

### Idempotency and the 202/200 Status Code

The idempotency cache stores the response body AND the status code. On replay, the stored status code (202 or 200) is returned verbatim. The `idempotency_status` field in the response body distinguishes `"applied"` (first call) from `"replayed"` (cache hit).

This matches the `X-Idempotency-Status` header convention established in `POST /v1/tasks`.

### Why 202 for approve/reject and 200 for stop/retry

From architecture.md line 318:
- `202 Accepted` — decision accepted, async (the approval event triggers async worker resumption)
- `200 OK` — stop/retry are synchronous state changes with no async downstream consumer

### License Override Dual Event Emission

When `action == "approve"` and `override == "license"`, the handler must emit TWO events:
1. `approval.granted` — the normal approval event
2. `tier3.license_override` — the audit event recording the override

Both events share the same `decision_id` for correlation. The `tier3.license_override` payload contains `reason: "operator_license_override"` to distinguish it from automated approval.

The factory closure must emit both events within the same idempotency window — the second event's ID is generated inside the factory and the closure only runs once (idempotency guarantee from `get_or_run`).

### Import Graph Constraints

- `services/registry-api/` may import from `packages/*` — `from events import ...`, `from events.ids import ...`, `from events.payloads import ...` are all valid
- `services/registry-api/` may import from `events.envelope` for `Actor`, `ActorKind` — valid
- `services/registry-api/` may NOT import from `mcp-servers/*` or other `services/*`
- New payload models go in `packages/events/src/events/payloads.py` — shared across all services
- New event type registrations go in `services/registry-state/src/registry_state/domain/event_types.py` — the single source of truth

### Files to Touch

| File | Change |
|------|--------|
| `packages/events/src/events/payloads.py` | Add `ApprovalGrantedPayload`, `ApprovalRejectedPayload`, `TaskRetryRequestedPayload`, `LicenseOverridePayload` |
| `packages/events/src/events/__init__.py` | Re-export new payload models |
| `packages/events/src/events/ids.py` | Add `new_decision_id()` |
| `services/registry-state/src/registry_state/domain/event_types.py` | Register 4 new event types |
| `services/registry-api/src/registry_api/routes/decisions.py` | New file — decisions route handler |
| `services/registry-api/src/registry_api/routes/tasks.py` | Add `"awaiting_approval"` to `_NEXT_COMMANDS` |
| `services/registry-api/src/registry_api/app.py` | Wire `decisions_router` into `build_app` |
| `services/registry-api/src/registry_api/test_decisions.py` | Positive and negative tests |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | 6-4 status updates |

### Relationship to Previous Stories

- **Story 6.1** created `check_tier`, `Tier`, `CallerContext`, `CapabilityDenied` — the decisions handler runs inside the tier-enforced middleware stack but does not call `check_tier` directly (the middleware already checked).
- **Story 6.2** added `check_tier_with_approval` and the approval-lookup pattern — the decisions handler is the SOURCE of `approval.granted` events that the approval-lookup queries. This story creates the events that 6.2's mechanism looks for.
- **Story 6.3** added `TierEnforcementMiddleware` — the decisions route inherits `Tier.ONE` enforcement via the prefix match in `ROUTE_TIER_MAP` (`"POST /v1/tasks"` matches `/v1/tasks/{id}/decisions`). The middleware populates `request.state.caller_context` before the handler runs.

### Downstream Dependencies on This Story

- **Story 6.5** (approval audit events) — consumes the `approval.granted`/`approval.rejected` events emitted by this handler
- **Story 6.7** (worker approval-wait state) — the worker-wrapper lifecycle FSM wakes on `approval.granted` events
- **Story 6.10** (license-flagged event override) — builds on the `tier3.license_override` event
- **Story 6.12** (decision interleaving property test) — directly exercises this handler with randomized interleavings
- **Epic 3 Telegram commands** (`/approve`, `/reject`, `/stop`, `/retry`) — all call through to this endpoint
- **Epic 4 Console commands** — `decision` command calls this endpoint

### Gotchas from Previous Stories

- **structlog**: Never use `event=` as kwarg with structlog loggers — clashes with positional `event` param. Use `cap_event=` or similar.
- **`BaseHTTPMiddleware`**: Exceptions raised in middleware `dispatch()` do NOT propagate to FastAPI registered exception handlers — must catch and return JSONResponse inline. This is NOT an issue for route handlers (only middleware).
- **RFC 7807**: All error responses must use `application/problem+json` media type, never `application/json`.
- **`_MUTATING_METHODS`**: Already defined in `adapters/errors.py` as `frozenset({"POST", "PUT", "PATCH", "DELETE"})` — reuse this constant.
- **`request.state`**: Use `getattr(request.state, "actor_id", None)` defensively — middleware ordering is not guaranteed if someone misconfigures the stack.
- **`EventEnvelope.create()`**: Requires event type to be registered in the schema registry BEFORE calling `create()`. Register in `event_types.py` module-load time.
- **`IdempotencyCacheStore.get_or_run()`**: Returns `(CacheHit, was_run: bool)`. On `was_run=False` (cache hit), the `CacheHit` contains the stored result. The factory closure only runs when `was_run=True`.
- **Task status values**: Match the materialized `Task.status` column values exactly — these come from `registry_state` ORM model.

### Scope Boundary

- Do NOT add approval audit event consumption (Story 6.5)
- Do NOT implement the worker approval-wait state machine (Story 6.7)
- Do NOT add precommit validation hook (Story 6.8)
- Do NOT add license scan integration (Story 6.9)
- Do NOT add budget exceeded enforcement (Story 6.11)
- Do NOT add the decision interleaving property test (Story 6.12)
- DO create the decisions endpoint that all these downstream stories depend on
- DO register the event types that the worker lifecycle FSM and MCP handlers expect to query
- DO add the `awaiting_approval` state to `_NEXT_COMMANDS` so GET task responses surface correct commands

### References

- [Source: epics.md — Epic 6 Story 6.4]
- [Source: prd.md — FR7, FR8, FR41, FR44]
- [Source: prd.md — Journey 2 (approval gate), Journey 6 (stale blocker)]
- [Source: architecture.md — line 314 sub-resource routes]
- [Source: architecture.md — line 318 status codes (202 Accepted)]
- [Source: architecture.md — line 429 idempotency on mutating endpoints]
- [Source: architecture.md — line 897 approval flow trace]
- [Source: architecture.md — line 611 domain/decisions.py (planned)]
- [Source: 6-3 story artifact — TierEnforcementMiddleware, ROUTE_TIER_MAP prefix matching]
- [Source: 6-2 story artifact — approval-lookup pattern, check_tier_with_approval]
- [Source: telegram-gateway registry_client.py — DecisionResponseLocal wire shape]

## Dev Agent Record

### Agent Model Used

(TBD)

### Debug Log References

None.

### Completion Notes List

(TBD)

### File List

(TBD)
