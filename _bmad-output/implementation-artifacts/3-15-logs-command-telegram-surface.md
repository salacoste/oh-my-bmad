# Story 3.15: `/logs` command (Telegram surface)

Status: done

## Story

As **the operator**,
I want **`/logs <task-id>` to call `GET /v1/tasks/{id}/logs/digest` and render the LLM-digest response in a single Telegram message**,
so that **I get actionable context about a task's recent activity without scrollback (FR5)**.

This is the second Telegram query command after `/status` (Story 3.14). It follows the same handler-registration + registry-client GET pattern but introduces a **placeholder fallback path**: `GET /v1/tasks/{id}/logs/digest` does NOT exist yet (Story 7.3 owns it), so the handler must gracefully return a placeholder message until that endpoint ships.

**Dependency chain:** Story 3.15 (this story — Telegram surface) → Story 7.3 (LLM digest adapter) → Story 7.4 (wiring). When 7.3 lands, this command's output automatically becomes the real LLM digest without further changes to this Telegram surface layer.

**What this story is NOT:**
- **NOT** the LLM digest business logic — Story 7.3 owns `llm_digest.py` and `GET /v1/tasks/{id}/logs/digest`.
- **NOT** `/events` raw stream tail — that's CLI-only (Story 4.4).
- **NOT** the console CLI `logs` command (Epic 4) — though both call the same API endpoint (FR12 parity).
- **NOT** the Telegram `/logs` business logic wiring — Story 7.4 owns the integration test that exercises the real digest end-to-end.

## Acceptance Criteria

1. **AC-1: `get_logs_digest` method on `RegistryAPIClient`** — add a new async method to the existing `RegistryAPIClient` class in `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:

   ```python
   async def get_logs_digest(
       self,
       *,
       task_id: str,
       request_id: str | None = None,
   ) -> LogsDigestResponseLocal:
   ```

   - Calls `GET /v1/tasks/{task_id}/logs/digest`.
   - Does NOT send `Idempotency-Key` header (GET is idempotent; same as `get_task` and `get_platform_health`).
   - Sends `X-Request-ID` header when provided.
   - Calls `response.raise_for_status()`.
   - Parses `response.json()` into a new local `LogsDigestResponseLocal` Pydantic model.
   - Wraps parse failures in `RegistryResponseError` (same pattern as existing methods).
   - Validates `task_id` against `TASK_ID_PATTERN` before making the HTTP call (same as `get_task()` and `submit_decision()`).

2. **AC-2: Local response model `LogsDigestResponseLocal`** — declare in `registry_client.py` alongside the other `*Local` models (Story 3.3 AC-2 pattern — local mirrors, NOT imported from registry-api):

   ```python
   class LogsDigestResponseLocal(BaseModel):
       task_id: str = Field(min_length=1, max_length=128)
       digest: str = Field(min_length=1)
       truncated: bool = False
       line_count: int = Field(ge=1, le=20)
   ```

   These fields are a **forward-compatible contract** aligned with Story 7.3's AC:
   - `task_id`: identifies the task.
   - `digest`: the LLM-summarized ≤20-line summary (key transitions, blockers, agent's last decision).
   - `truncated`: `true` when older events were truncated to fit the model's token budget (Story 7.3 degradation criterion).
   - `line_count`: number of lines in the digest (1–20).

   **Review-time validation:** When Story 7.3 ships, verify field names match the serialised JSON keys from `GET /v1/tasks/{id}/logs/digest`. Story 7.4 explicitly owns this alignment check.

   TODO(story-7.3): verify `LogsDigestResponseLocal` field names match Story 7.3's serialised JSON keys.

3. **AC-3: `handle_logs` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` with:

   ```python
   async def handle_logs(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
   ```

   - No `bot: Bot` parameter (same as `/status`, `/approve`, `/ping` — no outbound delivery needed).
   - Uses `_keys.extract_task_id_from_message(message)` for argument parsing (Story 3.4 pattern).
   - If no task-id found: reply with usage `"Usage: /logs <task-id>"` or `"Usage: /logs <task-id>; example: /logs t-0192..."` depending on whether args were present.
   - If task-id found: call `registry_client.get_logs_digest(task_id=task_id, request_id=request_id)`.
   - **Placeholder behavior:** The digest endpoint does not exist yet. The HTTPStatusError catch for 404 returns a placeholder message (AC-5) instead of "Task not found".
   - Uses the standard try/except cascade from `/status` (AC-6).

4. **AC-4: Digest message rendering** — the success message renders the digest:

   ```
   📋 Logs digest for <code>{task_id_safe}</code>

   {digest_safe}

   {truncation_notice}
   ```

   Rendering rules:
   - `task_id`: HTML `<code>` element: `<code>{html.escape(task_id)}</code>` (consistent with `/status` and `/task` reply pattern).
   - `digest`: `html.escape(digest)` — the LLM summary text, potentially multiline. Newlines preserved by Telegram's HTML renderer.
   - `truncation_notice`: omitted when `truncated=False`. When `truncated=True`:
     ```
     ⚠️ Older events were truncated to fit the digest. Run `oh-my-bmad-cli events {task_id}` for the full raw stream.
     ```
     The task_id inside the CLI command is NOT HTML-escaped (it's inside backticks/plain text context) but IS safe to include since it passed TASK_ID_PATTERN validation.
   - Message uses HTML parse mode (inherited from `DefaultBotProperties(parse_mode=ParseMode.HTML)` in `lifespan.py`).
   - Message-length truncation guard at `_MAX_REPLY_LEN = 4000` (same as `/status`; Telegram's limit is 4096).

5. **AC-5: Placeholder rendering** — when the digest endpoint returns 404 (endpoint not deployed yet, or task doesn't exist):

   ```
   📋 Logs for <code>{task_id_safe}</code>

   ⚠️ Log digest not yet available — the LLM digest service has not been deployed.

   View raw events with:
   oh-my-bmad-cli events {task_id}

   This command will automatically display digests once the service is live.
   ```

   **Design rationale:** Until Story 7.3 lands, `GET /v1/tasks/{id}/logs/digest` returns 404 regardless of whether the task exists. The placeholder message covers both cases and tells the operator exactly what to do. When Story 7.4 wires the real backend, the 404 path becomes "task genuinely not found" and can be refined at that point.

6. **AC-6: Error handling** — follow the exact same try/except cascade as `/status` (Story 3.14):

   - `httpx.TooManyRedirects` → `"⚠️ Registry unreachable. Try again in a moment."`
   - `httpx.HTTPStatusError` → special case 404 → placeholder message (AC-5). For other status codes: `format_http_error(exc, command_label="Logs query")`.
   - `RegistryResponseError` → `"⚠️ Received malformed response from registry."`
   - `httpx.HTTPError` (e.g. `ReadTimeout`, `ConnectError`) → `"⚠️ Could not reach registry. Please try again later."`
   - `Exception` → backstop: `"⚠️ Unexpected error. Please try again later."` + `_log.exception(...)` (never lets the webhook fail; Story 3.1 M3 contract carry-forward).

7. **AC-7: Router factory and registration**:

   ```python
   def make_logs_router() -> Router:
       router = Router()
       router.message(Command("logs"))(handle_logs)
       return router
   ```

   - Import `make_logs_router` in `handlers/__init__.py` and add to `__all__`.
   - Register `dp.include_router(make_logs_router())` in `app/lifespan.py` after the existing routers (Story 3.3 AC-5 ordering carry-forward: `workflow_data` update happens before `include_router` calls).

8. **AC-8: `just lint` 9/9 green** — `ruff check`, `ruff format --check`, `mypy --strict` on telegram-gateway, `check_imports`, `check_event_registry`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`.

9. **AC-9: Co-located tests (≥15)** — in `services/telegram-gateway/src/telegram_gateway/test_logs_command.py`:

   - **RegistryAPIClient.get_logs_digest tests (4):**
     - `test_get_logs_digest_success` — mock transport returns 200 with valid digest JSON; assert parsed fields match.
     - `test_get_logs_digest_404_raises` — mock transport returns 404; assert `httpx.HTTPStatusError` raised.
     - `test_get_logs_digest_malformed_json_raises_registry_response_error` — 200 with invalid JSON body; assert `RegistryResponseError`.
     - `test_get_logs_digest_sends_request_id_header` — verify `X-Request-ID` header present when provided.

   - **Handler tests (10+):**
     - `test_handle_logs_success_renders_digest` — mock client returns full `LogsDigestResponseLocal`; assert reply contains digest text, task_id in `<code>`, line count, no truncation notice.
     - `test_handle_logs_success_with_truncation_notice` — `truncated=True`; assert truncation notice in reply mentioning "truncated".
     - `test_handle_logs_no_args_shows_usage` — message text is just `"/logs"`; assert usage reply.
     - `test_handle_logs_invalid_task_id_shows_usage` — message text is `"/logs not-a-task-id"`; assert usage reply with example.
     - `test_handle_logs_404_returns_placeholder` — mock raises `HTTPStatusError` with 404; assert placeholder message containing "not yet available" and `oh-my-bmad-cli events`.
     - `test_handle_logs_404_placeholder_contains_task_id` — verify `<code>{task_id}</code>` in placeholder.
     - `test_handle_logs_network_error` — mock raises `httpx.ReadTimeout`; assert `"Could not reach registry"` reply.
     - `test_handle_logs_too_many_redirects` — `TooManyRedirects` → `"Registry unreachable"` reply.
     - `test_handle_logs_5xx_replies_retry_message` — 500 via `format_http_error`; assert ⚠️ prefix.
     - `test_handle_logs_malformed_response` — `RegistryResponseError` → `"malformed response"` reply.
     - `test_handle_logs_unexpected_exception` — `RuntimeError` backstop → `"Unexpected error"` reply.

   - **HTML security test (1):**
     - `test_handle_logs_html_chars_are_escaped` — digest text contains `<script>`, `&`, `<`, `>`; assert all escaped in reply.

   Target: **15+ new tests**.

10. **AC-10: Scope boundary** — files modifiable in this story:
    - **New (1):**
      - `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py`
    - **Modified (4 source + 2 process):**
      - `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (AC-1/AC-2 — add `get_logs_digest` method + `LogsDigestResponseLocal` model)
      - `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (AC-7 — re-export `make_logs_router`)
      - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (AC-7 — register router)
      - `services/telegram-gateway/src/telegram_gateway/test_logs_command.py` (AC-9 — new test file)
    - **Not modifiable:** `services/registry-api/`, `services/clawhip-daemon/`, `services/registry-state/`, existing handler files (`status_command.py`, `task_command.py`, `approve_command.py`, `ping_command.py`, `_keys.py`, `_errors.py`, `_safe_reply.py`).

11. **AC-11: No new dependencies** — `httpx`, `aiogram`, and `pydantic` already available. No new packages needed.

12. **AC-12: Atomic commit + independent gate verify** — single commit titled exactly:

    ```
    feat(telegram-gateway): story 3.15 — /logs command Telegram surface · FR5
    ```

    `just lint` 9/9 green. `just test` count grows by ≥15. `just bootstrap-verify` clean. Independently re-verify before flipping status.

## Tasks / Subtasks

- [x] **Task 1: Local response model + `get_logs_digest` on `RegistryAPIClient`** (AC: #1, #2)
  - [x] Declare `LogsDigestResponseLocal` Pydantic model in `registry_client.py` with `task_id`, `digest`, `truncated`, `line_count` fields and `Field` constraints.
  - [x] Add `async def get_logs_digest(self, *, task_id: str, request_id: str | None = None) -> LogsDigestResponseLocal` method to `RegistryAPIClient`.
  - [x] Method validates `task_id` against `TASK_ID_PATTERN`, calls `self._http_client.get(f"/v1/tasks/{task_id}/logs/digest", headers=headers)`, raises on error, parses response into `LogsDigestResponseLocal`.
  - [x] Add to `__all__` in `registry_client.py`.
  - [x] Add 4 unit tests for `get_logs_digest`: success, 404, malformed response, request-id header verification.

- [x] **Task 2: `handle_logs` handler + digest/placeholder rendering** (AC: #3, #4, #5, #6)
  - [x] Create `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py`.
  - [x] Implement `handle_logs(message, registry_client)` with task-id extraction via `_keys.extract_task_id_from_message`, API call, rendering, and error cascade.
  - [x] Digest rendering: task_id in `<code>`, HTML-escaped digest text, optional truncation notice.
  - [x] Placeholder rendering: 404 path returns "not yet available" message with CLI fallback.
  - [x] Usage reply on missing/invalid task-id.
  - [x] Message-length truncation guard at `_MAX_REPLY_LEN = 4000`.

- [x] **Task 3: Router registration + handler tests** (AC: #7, #9)
  - [x] Implement `make_logs_router()` factory in `logs_command.py`.
  - [x] Import and re-export `make_logs_router` in `handlers/__init__.py`.
  - [x] Register `dp.include_router(make_logs_router())` in `app/lifespan.py` after existing routers.
  - [x] Create `test_logs_command.py` with 10+ handler tests covering: success with digest, truncation notice, no args, invalid task-id, 404 placeholder (with task_id in message), network error, TooManyRedirects, 5xx, malformed response, unexpected exception, HTML escaping.

- [x] **Task 4: Regression verification + atomic commit** (AC: #12)
  - [x] `uv sync --all-packages` (Epic-1-retro AI #2).
  - [x] `just test` — confirm test count grows by ≥15. (1083 passed, 17 new from this story)
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] Independent gate verify before flipping `review → done` (Epic-2-retro AI #1).
  - [x] Flip sprint-status.yaml: `3-15-logs-command-telegram-surface: backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`.
  - [x] Atomic commit with the exact title from AC-12.

### Review Findings

- [x] [Review][Patch] HTML entity splitting during truncation slice [logs_command.py:167] — slicing `digest_safe[:max_digest_chars]` could split an HTML entity (e.g. `&amp;` → `&am…`). Fixed: added `re.sub(r"&[^;]*$", "", ...)` after slice.
- [x] [Review][Patch] Locally-truncated digests omit CLI escape hatch [logs_command.py:156-170] — when `truncated=False` but digest exceeds `_MAX_REPLY_LEN`, no CLI escape hatch shown. Fixed: truncation notice now fires on server OR local truncation.
- [x] [Review][Patch] Negative `max_digest_chars` floor guard [logs_command.py:164] — overhead could theoretically exceed `_MAX_REPLY_LEN` with very long task_id. Fixed: `max(_MAX_REPLY_LEN - overhead, 0)`.

## Dev Notes

### Quoted Requirements

> **FR5** (`prd.md`): *"Operator can retrieve an LLM-summarized digest of a task's recent events (not a raw log dump)."*

> **Epic 3 Story 3.15 AC** (`epics.md:1210-1224`):
> *Given a task exists and Story 7.3 has delivered the digest endpoint*
> *When I send `/logs t-0001`*
> *Then the bot calls `GET /v1/tasks/t-0001/logs/digest` and replies with a ≤20-line summary.*
>
> *Until Story 7.3 lands, this command returns a placeholder message explaining that the digest is not yet available — callers should run `oh-my-bmad-cli events t-0001` for the raw stream.*

> **FR12** (`prd.md`): *"Console Client can perform every task-lifecycle command available via Telegram (full surface parity); no operator capability is Telegram-only."*

> **FR17b** (`prd.md`): *"The `/logs` digest and `/status` reconstituted state must surface at least the last reasoning breadcrumb in human-readable form."*

### Architecture: Service Boundary

The `telegram-gateway` service is a **read + command-emit client** of `registry-api` via HTTP. It NEVER touches the database or event log directly (enforced by `check_imports.py` and `check_single_writer.py`). This story adds another read path (`GET /v1/tasks/{id}/logs/digest`).

The response model (`LogsDigestResponseLocal`) is a **local Pydantic mirror** declared inside `telegram-gateway`, NOT imported from `registry-api`. Same pattern as `TaskResponseLocal`, `CreateTaskResponseLocal`, `DecisionResponseLocal`, `HealthResponseLocal` (Stories 3.3, 3.5, 3.14).

### Architecture: The Placeholder Pattern

This story introduces a **new pattern not seen in previous handlers**: graceful degradation when the backing API endpoint does not exist yet. The key design decisions:

1. **Single API call** (no `get_task` pre-validation) — simpler, fewer round-trips, acceptable because Story 7.4 will refine the UX when the real endpoint ships.
2. **404 = placeholder** — the 404 status code covers both "task not found" and "endpoint not deployed" until Story 7.3 lands. The placeholder message addresses both cases.
3. **Forward-compatible `LogsDigestResponseLocal`** — the model shape is pinned by this story's tests; Story 7.4 verifies alignment when the real endpoint ships.

### Architecture: Future Endpoint (`GET /v1/tasks/{id}/logs/digest`)

This endpoint does NOT exist yet. Story 7.3 owns its implementation. The expected contract (from Story 7.3's AC):

- Pulls recent events from the event log.
- Passes them to an LLM digest adapter (`llm_digest.py`) with a bounded prompt.
- Returns a ≤20-line summary naming key transitions, blockers, and the agent's last decision.
- Graceful degradation: truncates older events and adds `"truncated": true` when the prompt exceeds the model's token budget.

The wire shape is expected to be:
```json
{
  "task_id": "t-...",
  "digest": "multi-line summary text...",
  "truncated": false,
  "line_count": 15
}
```

**Review-time validation:** Story 7.4 must verify `LogsDigestResponseLocal` field names match Story 7.3's actual response keys.

### Handler Pattern: Composition of `/status` + `/ping`

This story's handler composes patterns from existing handlers:

1. **From `/status` (Story 3.14):** task-id argument extraction, usage reply pattern, GET request pattern, `request_id` generation, error cascade, message-length truncation guard, `html.escape()` on all fields.
2. **From `/ping` (Story 3.5):** GET request without `Idempotency-Key` header, `safe_reply`.

The ONLY new pattern is the **placeholder fallback** on 404 (AC-5) — every other element is carry-forward.

### Key Shared Modules (DO NOT reinvent)

- `_keys.py` — `extract_task_id_from_message(message)`, `TASK_ID_PATTERN`
- `_errors.py` — `format_http_error(exc, command_label)`
- `_safe_reply.py` — `safe_reply(message, text, **kwargs)` (wraps `message.reply` with safety)
- `registry_client.py` — `RegistryAPIClient` class (add new method, don't replace)

### Carry-Forwards from Previous Stories

| Carry-forward | Source | How 3.15 uses it |
|---|---|---|
| Local response models (`*Local`) | Story 3.3 AC-2 | Declare `LogsDigestResponseLocal` with `Field` constraints |
| `RegistryAPIClient` with httpx | Story 3.3 AC-1 | Add `get_logs_digest()` following `get_task()` pattern |
| `extract_task_id_from_message()` | Story 3.4 | Re-use for task-id parsing |
| `format_http_error()` | Story 3.4 | Re-use for HTTPStatusError formatting with `command_label="Logs query"` |
| `safe_reply()` | Story 3.5 | Re-use for reply safety |
| `make_*_router()` factory pattern | Stories 3.3-3.5 | `make_logs_router()` following same convention |
| `dp.include_router()` registration | Stories 3.3-3.5 | Add after existing routers in `lifespan.py` |
| `html.escape()` on operator-visible strings | Story 3.5 H5 (review fix) | Apply to task_id, digest text |
| Error cascade (5 exception types + backstop) | Stories 3.3-3.4, refined in 3.14 | Same cascade in `handle_logs` |
| `X-Request-ID` header on all API calls | Story 3.3 | Include in `get_logs_digest()` |
| `request_id` via `new_request_id()` | Story 3.5 | Generate for observability |
| `TASK_ID_PATTERN` validation in client methods | Story 3.14 review fix | Validate in `get_logs_digest()` |
| `Field(min_length=, max_length=)` constraints | Story 3.14 review fix | Apply to `LogsDigestResponseLocal` fields |
| `_MAX_REPLY_LEN = 4000` truncation guard | Story 3.14 review fix | Same guard in `handle_logs` |
| `uv sync --all-packages` before lint | Epic-1-retro AI #2 | Use in Task 4 |
| `@asynccontextmanager` test helper with proper teardown | Story 3.14 review fix | Use same `_make_registry_client` pattern |

### Learnings from Story 3.14 (Previous Story)

Story 3.14's code review identified 13 findings that were all fixed. Key learnings to carry into 3.15:

1. **HTML escaping on ALL externally-sourced strings** — 3.14's initial implementation missed `html.escape(cmd)` on `next_commands` items. For 3.15, escape both `task_id` AND `digest` text.
2. **Test all 5 error-catch branches** — 3.14's initial test suite missed `TooManyRedirects`, `5xx`, `RegistryResponseError`, and `Exception` backstop. 3.15 must include tests for ALL branches from the start.
3. **`Field` constraints on local response models** — 3.14 review added `min_length`/`max_length` to all `*Local` model fields. Apply proactively to `LogsDigestResponseLocal`.
4. **`TASK_ID_PATTERN` validation in client methods** — 3.14 review added `TASK_ID_PATTERN.match(task_id)` to `get_task()`. Do the same for `get_logs_digest()`.
5. **`command_label` in `format_http_error()`** — Use a descriptive label: `"Logs query"` (not just `"logs"` or `"Log"`).
6. **Message-length truncation guard** — 3.14 review added `_MAX_REPLY_LEN = 4000`. Include from the start in 3.15.
7. **`@asynccontextmanager` for test helpers** — 3.14 review converted `_make_registry_client` from a raw function to a proper async context manager to avoid `ResourceWarning` from unclosed `httpx.AsyncClient`. Use the same pattern.
8. **TooManyRedirects message alignment** — 3.14 aligned with `/ping`'s message: `"⚠️ Registry unreachable. Try again in a moment."` Use the same message for consistency.
9. **Import line length** — 3.14's lifespan.py import exceeded 100 chars; split into multi-line. For 3.15, add `make_logs_router` to the existing multi-line import.

### Architecture References

- `prd.md` — FR5 (LLM digest), FR12 (CLI parity), FR17b (reasoning breadcrumbs in digest).
- `epics.md:1210-1224` — Story 3.15 user story + AC + placeholder note.
- `epics.md:2042-2058` — Story 7.3 (future LLM digest adapter).
- `epics.md:2060-2072` — Story 7.4 (future Telegram /logs business logic wiring).
- `architecture.md` — Component 3 (telegram-gateway); HTTP client pattern; service boundaries; `/v1/tasks/{id}/logs/digest` endpoint definition.
- Story 3.14 — `/status` handler + `get_task` + `TaskResponseLocal` + lifespan registration (closest reference for this story).
- Story 3.5 — `/ping` handler + GET request pattern + `safe_reply`.
- Story 3.4 — `/approve` handler + task-id extraction pattern.
- Story 3.3 — `/task` handler + `RegistryAPIClient` + local models + lifespan registration.
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe.
- Epic-2-retro AI #1 — Independent gate verify mandatory.

### Project Structure Notes

- Handler: `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` (new)
- Handler test: `services/telegram-gateway/src/telegram_gateway/test_logs_command.py` (new)
- Client: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (modify)
- Init: `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (modify)
- Lifespan: `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modify)
- Spec: `_bmad-output/implementation-artifacts/3-15-logs-command-telegram-surface.md` (this file)
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips)

No detected conflicts with unified project structure. Handler placement follows the established `handlers/<command>_command.py` convention.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` | New — `handle_logs` + `make_logs_router` |
| `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` | Modified — add `get_logs_digest` method + `LogsDigestResponseLocal` model |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Modified — re-export `make_logs_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Modified — register `make_logs_router()` |
| `services/telegram-gateway/src/telegram_gateway/test_logs_command.py` | New — 15+ tests |
| `_bmad-output/implementation-artifacts/3-15-logs-command-telegram-surface.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

No debug issues encountered during implementation.

### Completion Notes List

- All 4 AC tasks completed sequentially.
- Ruff format applied to logs_command.py (1 reformat).
- Import order fixed in test_logs_command.py (ruff isort).
- Docstring line length fixed in registry_client.py (105→100).
- 17 new tests added, all passing (1083 total, 0 failures).
- Lint 9/9 green.
- Pre-existing separability test failure (test_spine_source_code_unchanged) from Story 3.13 — not caused by 3.15.

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/test_logs_command.py` — New
- `_bmad-output/implementation-artifacts/3-15-logs-command-telegram-surface.md` — Modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified
