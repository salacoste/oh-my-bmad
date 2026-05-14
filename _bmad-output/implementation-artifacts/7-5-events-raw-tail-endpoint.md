# Story 7.5: `GET /v1/tasks/{id}/events` raw event tail

Status: done

## Story

As the operator debugging,
I want `GET /v1/tasks/{id}/events?since=<ts>&limit=N` to return raw typed events,
So that `oh-my-bmad-cli events --follow` and debugging workflows have structured data access.

## Acceptance Criteria

1. **Given** a task has events recorded
   **When** `GET /v1/tasks/t-0001/events?since=2026-04-22T00:00:00Z&limit=50` is called
   **Then** the response is a JSON array of envelope objects matching the filter, ordered by `emitted_at`.

*Cites: FR6.*

## Tasks / Subtasks

- [x] Task 1 — Create `routes/events.py` route handler (AC: #1)
  - [x] Create `services/registry-api/src/registry_api/routes/events.py`.
  - [x] Define `GET /tasks/{task_id}/events` route with `task_id` path param (reuse `_TASK_ID_PATTERN` from `routes/tasks.py`).
  - [x] Accept optional query params: `since: datetime | None = Query(None)` and `limit: int = Query(100, ge=1, le=1000)`.
  - [x] Validate `task_id` matches `_TASK_ID_PATTERN`.
  - [x] Query `Event` table: `select(Event).where(Event.task_id == task_id)`, add `.where(Event.emitted_at >= since)` if `since` provided, `.order_by(Event.emitted_at.asc())`, `.limit(limit)`.
  - [x] Return empty JSON array `[]` if no events found (NOT 404 — empty result is valid per CLI contract).
  - [x] Serialize each `Event` ORM row to an envelope dict matching the wire contract (see Dev Notes).
  - [x] Return `response_model=list[dict]` — bare JSON array of envelope objects.

- [x] Task 2 — Wire the route into the FastAPI app (AC: #1)
  - [x] Import `events_router` from `routes.events` in `app.py`.
  - [x] Add `app.include_router(events_router, prefix="/v1")`.

- [x] Task 3 — Add unit tests (AC: #1)
  - [x] Create `services/registry-api/src/registry_api/test_events.py`.
  - [x] Test: `test_events_returns_envelopes_ordered_by_emitted_at` — seed 5 events, call endpoint, assert array of 5 envelope dicts in ascending `emitted_at` order.
  - [x] Test: `test_events_filters_by_since` — seed events across time range, call with `since` param, assert only events after the timestamp returned.
  - [x] Test: `test_events_respects_limit` — seed 10 events, call with `limit=3`, assert exactly 3 returned.
  - [x] Test: `test_events_returns_empty_array_when_no_events` — no events for task_id, assert `[]` (200, not 404).
  - [x] Test: `test_events_rejects_invalid_task_id` — malformed task_id returns 422.
  - [x] Test: `test_events_default_limit_is_100` — verify default limit applied.
  - [x] All tests use `ASGITransport` + `LifespanManager` pattern (same as `test_digest.py`).

- [x] Task 4 — Run full regression suite (AC: #1)
  - [x] `uv run pytest services/registry-api/ -x -q` passes.
  - [x] `uv run pytest services/console-cli/ -x -q` passes (existing CLI tests should work with real endpoint).
  - [x] `ruff check` clean on all modified/created files.

- [x] Task 5 — Update stale docstrings (AC: #1)
  - [x] Remove "Server-side endpoint not yet implemented (Story 7.5)" from `console-cli/adapters/registry_api_client.py:get_task_events()` docstring.

## Dev Notes

### Architecture: What This Story Does

Creates the server-side `GET /v1/tasks/{task_id}/events` endpoint that the console-CLI `events` command (Story 4.4) already calls. The CLI client (`RegistryAPIClient.get_task_events()`), the CLI command (`events.py`), and the follow-mode polling loop (`_poll_events`) are **fully implemented and tested** — 15 CLI tests exist. This story only creates the missing server-side route.

This is a **read-only query endpoint** — no event emission, no state mutation. It returns raw typed event envelopes from the SQLite store.

### Critical: Wire Contract with CLI Client

The CLI client (`console-cli/adapters/registry_api_client.py:375-418`) expects:

1. **Response body**: bare JSON array `[...]` (NOT wrapped in `{"events": [...]}`). The client does `data = response.json()` and then `TaskEventsResponseLocal.model_validate({"events": data})` — it wraps the array client-side.
2. **Follow mode** (`_poll_events`): expects `response.json()` to be a list. Checks `isinstance(data, list)`.
3. **Query params**: `limit` (int, default 100) and optional `since` (ISO 8601 string).
4. **Each event dict** must have at minimum `emitted_at` (str) for cursor-based follow mode: `event.get("emitted_at")` is used as the `since` cursor.

### Event Envelope Serialization

Map each `Event` ORM row to an envelope dict:

```python
{
    "event_id": row.id,
    "schema_version": row.schema_version,
    "type": row.type,
    "emitted_at": row.emitted_at.isoformat(),  # UTC ISO 8601
    "emitted_at_monotonic_ns": row.emitted_at_monotonic_ns,
    "actor": {"kind": row.actor_kind, "id": row.actor_id},
    "session_id": row.session_id,
    "payload": json.loads(row.payload_json),
    "parent_event_id": row.parent_event_id,
    "trace_id": None,  # Phase 1 reserved — no ORM column yet
    "request_id": row.request_id,
}
```

Do NOT import the Pydantic `EventEnvelope` model for serialization — it validates via `create()` which requires schema-registry membership. The route constructs plain dicts from ORM columns, matching the envelope shape.

Note: `session_id` is an ORM column (not in the Pydantic envelope model) but is included because consumers may need it for session-scoped filtering. `extensions` is in the envelope model but intentionally omitted from Phase 1 raw-event responses — no consumer reads it yet.

### Route Pattern

Follow `routes/digest.py` exactly:

```python
from __future__ import annotations

import json
import logging

from datetime import datetime

from fastapi import APIRouter, Path, Query, Request
from registry_state.schema import Event  # noqa: IMP001
from sqlalchemy import select

from registry_api.routes.tasks import _TASK_ID_PATTERN

router = APIRouter()

@router.get("/tasks/{task_id}/events", status_code=200)
async def get_task_events(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    ...
```

### Query Optimization

The `Event` table has index `ix_events_task_id_emitted_at` on `(task_id, emitted_at)` — directly supports `WHERE task_id = ? AND emitted_at >= ? ORDER BY emitted_at ASC`. No new indexes needed.

Use `Event.emitted_at.asc()` (ascending) for chronological order. The CLI follow mode uses the last `emitted_at` value as the cursor for the next poll.

### Empty Results

Return `[]` (200 with empty array) when no events exist for the task. The CLI prints "No events found for task {task_id}." on empty result (line 177 of `events.py`). Do NOT return 404 for empty results.

### Relationship to Other Stories

- **Story 4.4** (events-follow-live-tail): Created the CLI `events` command with `--follow` polling. This story provides the server endpoint it calls.
- **Story 7.3** (logs-digest-llm-adapter): Created `GET /v1/tasks/{id}/logs/digest` (FR5, LLM digest). This story creates `GET /v1/tasks/{id}/events` (FR6, raw events). Different endpoints, same Event table.
- **Story 7.6** (retry-hint-injection): Downstream. Cites FR6 as part of "reconnaissance coupling" — the operator reads raw events to diagnose a blocker before retrying.

### Scope Boundary

**DO create:**
- `services/registry-api/src/registry_api/routes/events.py` — route handler
- `services/registry-api/src/registry_api/test_events.py` — unit tests

**DO modify:**
- `services/registry-api/src/registry_api/app.py` — wire events router
- `services/console-cli/src/console_cli/adapters/registry_api_client.py` — update stale docstring

**DO NOT modify:**
- `services/console-cli/src/console_cli/commands/events.py` — CLI command is complete
- `services/console-cli/src/console_cli/test_events_command.py` — 15 existing tests
- `services/telegram-gateway/` — no Telegram surface for raw events
- `services/registry-state/` — Event table and schema already complete
- `packages/events/` — no new events needed

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated (same as prior stories):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.5]
- [Source: _bmad-output/planning-artifacts/prd.md#FR6]
- [Source: _bmad-output/planning-artifacts/architecture.md#registry-api-service]
- [Source: services/registry-api/src/registry_api/routes/digest.py — route pattern to follow]
- [Source: services/registry-api/src/registry_api/routes/tasks.py — _TASK_ID_PATTERN, query patterns]
- [Source: services/registry-api/src/registry_api/app.py — router wiring, lifespan]
- [Source: services/registry-state/src/registry_state/schema.py — Event ORM, ix_events_task_id_emitted_at]
- [Source: services/console-cli/src/console_cli/commands/events.py — CLI consumer (complete)]
- [Source: services/console-cli/src/console_cli/adapters/registry_api_client.py — get_task_events(), TaskEventsResponseLocal]
- [Source: services/console-cli/src/console_cli/test_events_command.py — 15 existing CLI tests]
- [Source: packages/events/src/events/envelope.py — envelope shape reference]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- Created `routes/events.py` with `GET /tasks/{task_id}/events` route — returns bare JSON array of event envelope dicts ordered by `emitted_at` ascending.
- `_row_to_envelope()` maps ORM `Event` rows to envelope dicts with all fields: event_id, schema_version, type, emitted_at, emitted_at_monotonic_ns, actor, session_id, payload, parent_event_id, trace_id, request_id.
- Supports `since` (ISO 8601 cursor) and `limit` (default 100, max 1000) query params.
- Returns `[]` (200) for empty results — NOT 404. Matches CLI contract.
- Wired events router into `app.py` with `prefix="/v1"`.
- Fixed B008 ruff lint: added `# noqa: B008` for FastAPI `Query()` in argument defaults (standard FastAPI pattern).
- 6 unit tests: happy path (5 events, ascending order + envelope shape), since filter, limit, empty array, invalid task_id (422), default limit.
- All 112 registry-api tests pass (106 existing + 6 new), all 115 console-cli tests pass. Ruff clean.

### File List

- services/registry-api/src/registry_api/routes/events.py — created (route handler + _row_to_envelope)
- services/registry-api/src/registry_api/test_events.py — created (6 tests)
- services/registry-api/src/registry_api/app.py — wired events router
- services/console-cli/src/console_cli/adapters/registry_api_client.py — updated get_task_events() docstring

## Senior Developer Review (AI)

**Review Date:** 2026-05-12
**Review Outcome:** Changes Requested
**Reviewers:** Blind Hunter, Edge Case Hunter, Acceptance Auditor

### Action Items

- [x] [Review][Patch] **HIGH: Non-deterministic ordering for same-timestamp events** [`events.py:69`]
  Fixed: added `Event.emitted_at_monotonic_ns.asc()` as secondary sort. Added `TestSameTimestampOrdering` test with 2 events at same timestamp.

- [x] [Review][Patch] **LOW: Unused logger** [`events.py:26`]
  Fixed: added `_log.debug(...)` call at query entry point.

- [x] [Review][Defer] **trace_id hardcoded to None** [`events.py:39`] — deferred, Phase 1 reserved field. Will be addressed when ORM column is added.
