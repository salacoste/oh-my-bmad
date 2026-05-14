# Story 7.3: Logs digest LLM adapter

Status: done

## Story

As the operator,
I want `services/registry-api/adapters/llm_digest.py` that calls the Anthropic API with a summarization prompt over the task's recent events and returns a human-readable digest,
So that `/logs` surfaces actionable context rather than raw event dumps.

## Acceptance Criteria

1. **Given** a task has >20 events
   **When** `GET /v1/tasks/{id}/logs/digest` is called
   **Then** the handler (a) pulls recent events from the event log, (b) passes them to the digest adapter with a bounded prompt, (c) returns a <=20-line summary naming key transitions, blockers, and the agent's last decision.

2. **And Given** the adapter's prompt + context exceed the model's token budget
   **When** the handler runs
   **Then** it degrades gracefully (truncates older events, adds a `"truncated": true` marker) rather than failing.

*Cites: FR5.*

## Tasks / Subtasks

- [x] Task 1 — Add `anthropic` dependency and config (AC: #1)
  - [x] Add `anthropic>=0.100` to `services/registry-api/pyproject.toml` dependencies.
  - [x] Add `ANTHROPIC_API_KEY` (or `LLM_DIGEST_API_KEY`) to registry-api config. Follow the existing pattern in `services/registry-api/src/registry_api/app.py` lifespan for environment-based config. Store the Anthropic client on `app.state` (e.g., `app.state.anthropic_client`).
  - [x] The `.env.example` already has `ANTHROPIC_API_KEY=` (line 15). Add a comment noting registry-api as a consumer.

- [x] Task 2 — Create `llm_digest.py` adapter (AC: #1, #2)
  - [x] Create `services/registry-api/src/registry_api/adapters/llm_digest.py`.
  - [x] Implement `async def summarize_events(events: list[EventRow], *, client: anthropic.AsyncAnthropic) -> tuple[str, bool]`:
    - Build a bounded prompt from event data. For each event, format: `{emitted_at HH:MM} {type}: {summary or payload_json excerpt}`.
    - Cap the event context at ~4000 tokens of input (roughly 50 events with short payloads). If more events exist, truncate from the oldest and set `truncated=True`.
    - Call `client.messages.create()` with model `claude-haiku-4-5-20251001` (fast/cheap for summarization), max_tokens=1024, system prompt directing <=20 line output.
    - Return `(digest_text, truncated_flag)`.
  - [x] Handle Anthropic API errors gracefully: on `anthropic.APIError`, return a fallback digest built from raw event formatting (no crash, no LLM-dependent failure mode).

- [x] Task 3 — Create `GET /v1/tasks/{id}/logs/digest` route handler (AC: #1)
  - [x] Add route in `services/registry-api/src/registry_api/routes/tasks.py` (or a new `routes/digest.py`).
  - [x] Handler signature: `async def get_logs_digest(task_id: str, request: Request) -> LogsDigestResponse`.
  - [x] Validate `task_id` matches `TASK_ID_PATTERN` (same pattern as `get_task_by_id`).
  - [x] Query events: `select(Event).where(Event.task_id == task_id).order_by(Event.emitted_at.desc()).limit(100)` using the existing `ix_events_task_id_emitted_at` index.
  - [x] If no events found, return 404 with RFC 7807 envelope.
  - [x] Call `summarize_events()` adapter. Count lines in result for `line_count` field.
  - [x] Return `LogsDigestResponse(task_id=task_id, digest=text, truncated=truncated, line_count=line_count)`.
  - [x] Response model `LogsDigestResponse` must serialize to JSON keys: `task_id`, `digest`, `truncated`, `line_count` — matching the wire contract in `telegram_gateway/handlers/registry_client.py:LogsDigestResponseLocal`.

- [x] Task 4 — Wire the route into the FastAPI app (AC: #1)
  - [x] Register the new route in `services/registry-api/src/registry_api/app.py` via `app.include_router()` with prefix `/v1`.
  - [x] Initialize `anthropic.AsyncAnthropic(api_key=...)` in lifespan and store on `app.state`.

- [x] Task 5 — Add unit tests (AC: #1, #2)
  - [x] In `services/registry-api/tests/` (or colocated test file), add tests:
    - `test_digest_returns_summary_with_events` — mock Anthropic client, feed 25 events, assert digest returned with `line_count >= 1`.
    - `test_digest_truncates_on_large_input` — feed 100+ events, assert `truncated=True`.
    - `test_digest_404_when_no_events` — no events for task_id, assert 404 response.
    - `test_digest_fallback_on_anthropic_error` — mock Anthropic to raise `APIError`, assert fallback digest returned (no crash).
    - `test_wire_contract_matches_local_model` — response JSON keys match `LogsDigestResponseLocal` fields.
  - [x] All tests use `httpx.MockTransport` or direct handler call pattern (same as existing registry-api tests).

- [x] Task 6 — Run tests and verify no regressions (AC: #1, #2)
  - [x] `uv run pytest services/registry-api/ -x -q` passes.
  - [x] `uv run pytest services/telegram-gateway/ -x -q` passes (existing `/logs` tests unchanged).
  - [x] `ruff check` clean on all modified/created files.

## Dev Notes

### Architecture: What This Story Does

This is the **first LLM integration** in the oh-my-bmad platform. It creates a server-side Anthropic API adapter that summarizes task events into a human-readable digest, served via `GET /v1/tasks/{id}/logs/digest`.

The Telegram gateway side is **already complete** (Story 3.15): `logs_command.py` calls `registry_client.get_logs_digest()`, and `LogsDigestResponseLocal` is already defined. Currently returns a placeholder "not yet available" message on 404. Once this story lands, the endpoint returns real digests and the placeholder message disappears automatically.

### Critical Architecture Constraints

1. **Read-only SQLite**: The route handler uses the read-only engine via `request.app.state.session_maker`. No writes to the database.

2. **No cross-service imports**: `services/registry-api/` must not import from `services/registry-state/domain/`. The approved exception is importing ORM models from `registry_state.schema` (e.g., `Event, Task`) with `# noqa: IMP001`.

3. **Wire contract**: The response JSON must exactly match `LogsDigestResponseLocal` fields in `telegram_gateway/handlers/registry_client.py:176-196`:
   - `task_id`: str (1-128 chars)
   - `digest`: str (1-20,000 chars)
   - `truncated`: bool
   - `line_count`: int (1-20)

4. **Anthropic SDK**: Use `anthropic` Python package (v0.100+). The `AsyncAnthropic` client for non-blocking calls within the async FastAPI handler. Install via `uv add anthropic --package registry-api`.

5. **Model selection**: Use `claude-haiku-4-5-20251001` for speed and cost-efficiency on summarization tasks. This is a digest, not a reasoning task.

6. **Graceful degradation**: If the Anthropic API is unavailable (network error, rate limit, invalid key), return a fallback digest built from raw event formatting. The endpoint must NEVER return a 500 due to LLM failures.

7. **Token budget**: Cap the event context at ~4000 input tokens (~50 events). Older events are truncated with `truncated=True`. The system prompt should request <=20 lines of output.

8. **Event query optimization**: The `Event` table already has index `ix_events_task_id_emitted_at` on `(task_id, emitted_at)` — perfect for the `ORDER BY emitted_at DESC` query.

### Event Data Formatting for Prompt

Each event row has:
- `type`: String(128) — e.g., "task.started", "task.blocker_raised", "file.edited"
- `emitted_at`: UTCDateTime — event timestamp
- `payload_json`: Text — canonical JSON payload

For the digest prompt, format each event as a single line:
```
[10:41] task.blocker_raised: 2 unit tests failed (middleware_rate_limit_test.py)
[10:38] file.edited: Edit server/middleware/rate.py:87
[10:30] task.step.completed: Step 2/5
```

Extract human-readable summaries from `payload_json` using `json.loads()` and field extraction (same pattern as `get_task_by_id`'s `payload_summary` extraction in Story 7.1).

### Response Model

Define in the route file (same pattern as `TaskResponse`, `CreateTaskResponse`):

```python
class LogsDigestResponse(BaseModel):
    task_id: str
    digest: str = Field(min_length=1, max_length=20_000)
    truncated: bool = False
    line_count: int = Field(ge=1, le=20)
```

### Anthropic Client Initialization

In `app.py` lifespan:
```python
import anthropic

# In lifespan startup:
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if api_key:
    app.state.anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
else:
    app.state.anthropic_client = None  # digest endpoint returns fallback
```

### Relationship to Other Stories

- **Story 3.15** (logs-command-telegram-surface): Created the Telegram handler, router, error branches, and placeholder rendering. This story provides the server-side endpoint it calls.
- **Story 7.4** (telegram-logs-business-logic): Downstream. May only need integration testing since the Telegram handler already calls the endpoint correctly.
- **Story 7.5** (events-raw-tail-endpoint): Related but separate — provides raw event stream for debugging (FR6), while this story provides the LLM digest (FR5).

### Scope Boundary

**DO create:**
- `services/registry-api/src/registry_api/adapters/llm_digest.py` — Anthropic adapter
- Route handler for `GET /v1/tasks/{id}/logs/digest`
- Response model `LogsDigestResponse`
- Tests for adapter + route

**DO modify:**
- `services/registry-api/pyproject.toml` — add `anthropic` dependency
- `services/registry-api/src/registry_api/app.py` — wire route + Anthropic client

**DO NOT modify:**
- `services/telegram-gateway/` — already complete (Story 3.15)
- `services/registry-state/` — event table and schema already complete
- `packages/events/` — no new events needed

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated (same as prior stories):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.3]
- [Source: _bmad-output/planning-artifacts/prd.md#FR5]
- [Source: _bmad-output/planning-artifacts/prd.md#FR6]
- [Source: _bmad-output/planning-artifacts/architecture.md#registry-api-service]
- [Source: services/registry-api/src/registry_api/routes/tasks.py — get_task_by_id pattern]
- [Source: services/registry-api/src/registry_api/app.py — lifespan, router wiring]
- [Source: services/registry-api/pyproject.toml — current dependencies]
- [Source: services/registry-state/src/registry_state/schema.py — Event table, ix_events_task_id_emitted_at]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py — LogsDigestResponseLocal, get_logs_digest()]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py — already-complete Telegram handler]
- [Source: _bmad-output/implementation-artifacts/7-1-reconstituted-state-handler.md — read-only SQLite pattern, Event query pattern]
- [Source: _bmad-output/implementation-artifacts/7-2-telegram-status-business-logic.md — state-aware rendering patterns]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

No debug cycles needed — clean implementation on first pass.

### Completion Notes List

- Created `adapters/llm_digest.py` with `EventRow` dataclass (decoupled from ORM), `summarize_events()` async function with bounded prompt (50 events / 16K chars cap), Anthropic `claude-haiku-4-5-20251001` model, and graceful fallback to raw-event formatting on API errors or missing client.
- Created `routes/digest.py` with `GET /v1/tasks/{id}/logs/digest` handler, `LogsDigestResponse` Pydantic model matching `LogsDigestResponseLocal` wire contract (task_id, digest, truncated, line_count).
- Wired digest router into `app.py` with `app.include_router(digest_router, prefix="/v1")`.
- Initialized `anthropic.AsyncAnthropic` client in lifespan, stored on `app.state.anthropic_client`. Gracefully set to `None` when `ANTHROPIC_API_KEY` is absent.
- Added 6 unit tests: happy path (mock Anthropic), truncation (120 events), 404 (no events), APIError fallback, no-client fallback, wire contract validation. All use `ASGITransport` + `LifespanManager` pattern matching existing test conventions.
- Final test counts: 104 registry-api (98 existing + 6 new), 366 telegram-gateway (0 regressions).
- Code review (3 adversarial reviewers): fixed 7 issues — CRITICAL line_count clamp, HIGH Anthropic client cleanup, HIGH exception broadening, MEDIUM fallback consistency, MEDIUM DB limit, MEDIUM empty events, LOW boundary tests. 2 deferred (model config, timestamp logging). Final: 106 tests (98 + 8 new), 0 regressions.

### File List

- services/registry-api/pyproject.toml — added `anthropic>=0.100` dependency
- services/registry-api/src/registry_api/app.py — added `anthropic` import, digest router wiring, Anthropic client initialization in lifespan
- services/registry-api/src/registry_api/adapters/llm_digest.py — created (EventRow, summarize_events, fallback digest logic)
- services/registry-api/src/registry_api/routes/digest.py — created (LogsDigestResponse, GET /tasks/{id}/logs/digest handler)
- services/registry-api/src/registry_api/test_digest.py — created (8 tests across 6 test classes)
- .env.example — added registry-api as consumer of ANTHROPIC_API_KEY

## Senior Developer Review (AI)

**Review Date:** 2026-05-12
**Review Outcome:** Changes Requested → All Fixed
**Reviewers:** Blind Hunter, Edge Case Hunter, Acceptance Auditor

### Action Items

- [x] [Review][Patch] **CRITICAL: `line_count` > 20 causes Pydantic 500** — Fallback digest produced 22 lines, LLM could exceed 20. Fixed: route handler now clamps digest to 20 lines, sets `truncated=True`. `_build_fallback_digest` capped to 19 events + header.
- [x] [Review][Patch] **HIGH: Anthropic client not closed on shutdown** — HTTP connection pool leak. Fixed: registered with `AsyncExitStack` via `stack.push_async_callback(llm_client.close)`.
- [x] [Review][Patch] **HIGH: `anthropic.APIError` catch too narrow** — Network/timeout errors not caught, causing 500s. Fixed: broadened to `except Exception` for graceful degradation.
- [x] [Review][Patch] **MEDIUM: Fallback receives untruncated events list** — Semantic inconsistency with LLM path. Fixed: `_build_fallback_digest` now receives pre-formatted `formatted` list instead of raw events.
- [x] [Review][Patch] **MEDIUM: Route fetches 100 events but adapter caps at 50** — Wasteful DB I/O. Fixed: reduced `.limit(100)` to `.limit(55)`.
- [x] [Review][Patch] **MEDIUM: Empty events returns `("", False)`** — Violated `min_length=1` if adapter called directly. Fixed: returns `"No events to summarize."`.
- [x] [Review][Patch] **LOW: No test for line_count > 20 boundary** — Added `test_llm_returns_over_20_lines_clamped` and `test_fallback_digest_stays_within_20_lines`.
- [x] [Review][Defer] Hardcoded model name `claude-haiku-4-5-20251001` — deferred, operational improvement for Phase 2
- [x] [Review][Defer] Silent `???` for malformed ISO timestamps — deferred, low-impact diagnostic improvement
