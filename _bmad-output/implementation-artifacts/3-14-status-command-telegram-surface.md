# Story 3.14: `/status` command (Telegram surface)

Status: done

## Story

As **the operator**,
I want **`/status <task-id>` to render whatever `GET /v1/tasks/{id}` returns in a single human-friendly Telegram message**,
so that **reconnaissance after a blocker doesn't require scrollback (FR4), and the Telegram surface mirrors the console CLI `oh-my-bmad-cli status <task-id>` command (FR12 parity)**.

This is the first Telegram query command after the Bootstrap Minimum Subset (`/task`, `/approve`, `/ping`). It follows the same handler-registration + registry-client GET pattern established by `/ping` (Story 3.5) but adds task-id argument parsing from `/approve` (Story 3.4).

**Future note (NOT this story):** Story 7.1 enhances `GET /v1/tasks/{id}` to return full reconstituted state (since-timestamp, current step, last agent action, worktree lock state). When 7.1 lands, this command's output automatically becomes richer without further surface changes — the Telegram surface just renders whatever the API returns.

### What this story is NOT

- **NOT** the business logic — `GET /v1/tasks/{id}` already exists (Story 2.9). This story is the Telegram surface layer that calls it and renders the response.
- **NOT** the LLM-summarized `/logs` command (Story 3.15 / Story 7.3).
- **NOT** `/events` raw stream tail (CLI only, Story 4.4).
- **NOT** the enhanced reconstituted state — Story 7.1 owns that enrichment.
- **NOT** the console CLI `status` command (Epic 4) — though both call the same API endpoint.

## Acceptance Criteria

1. **AC-1: `get_task` method on `RegistryAPIClient`** — add a new async method to the existing `RegistryAPIClient` class in `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:

   ```python
   async def get_task(
       self,
       *,
       task_id: str,
       request_id: str | None = None,
   ) -> TaskResponseLocal:
   ```

   - Calls `GET /v1/tasks/{task_id}`.
   - Does NOT send `Idempotency-Key` header (GET is idempotent by HTTP semantics; same as `get_platform_health`).
   - Sends `X-Request-ID` header when provided.
   - Calls `response.raise_for_status()`.
   - Parses `response.json()` into a new local `TaskResponseLocal` Pydantic model.
   - Wraps parse failures in `RegistryResponseError` (same pattern as existing methods).

2. **AC-2: Local response model `TaskResponseLocal`** — declare in `registry_client.py` alongside the other `*Local` models (Story 3.3 AC-2 pattern — local mirrors, NOT imported from registry-api to respect the service boundary enforced by `check_imports.py`):

   ```python
   class ActorLocal(BaseModel):
       kind: str
       id: str

   class LastEventLocal(BaseModel):
       id: str
       type: str
       emitted_at: datetime

   class TaskResponseLocal(BaseModel):
       task_id: str
       status: str
       title: str | None
       created_at: datetime
       updated_at: datetime
       actor: ActorLocal
       last_event: LastEventLocal | None
       next_commands: list[str]
       chat_id: int | None
       reply_to_message_id: int | None
   ```

   These fields mirror the `TaskResponse` from `services/registry-api/src/registry_api/routes/tasks.py` lines 184-216 exactly.

3. **AC-3: `handle_status` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` with:

   ```python
   async def handle_status(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
   ```

   - No `bot: Bot` parameter (same as `/approve` and `/ping` — no outbound delivery needed).
   - Uses `_keys.extract_task_id_from_message(message)` for argument parsing (Story 3.4 pattern).
   - If no task-id found: reply with usage `"Usage: /status <task-id>"` or `"Usage: /status <task-id>; example: /status t-0192..."` depending on whether args were present (Story 3.4 pattern).
   - If task-id found: call `registry_client.get_task(task_id=task_id, request_id=request_id)`.
   - Format the `TaskResponseLocal` into a human-friendly Telegram message (AC-4).
   - Uses the standard try/except cascade from `/approve` (AC-5).

4. **AC-4: Status message rendering** — the success message renders every field from `TaskResponseLocal`:

   ```
   📋 Task <task_id>
   Status: <status>
   Title: <title or "(none)">
   Created: <created_at isoformat seconds>
   Updated: <updated_at isoformat seconds>
   Actor: <actor.kind>/<actor.id>
   Last event: <last_event.type> at <last_event.emitted_at isoformat seconds> or "(none)"
   Available: /<cmd1>, /<cmd2>, ... or "(none)"
   ```

   Rendering rules:
   - `task_id`: displayed as HTML `<code>` element (consistent with `/task` reply pattern): `<code>{html.escape(task_id)}</code>`.
   - `status`: `html.escape(status)`.
   - `title`: `html.escape(title)` if present, else `"(none)"`.
   - `created_at` / `updated_at`: `isoformat(timespec="seconds")` (Story 3.12 M14 carry-forward pattern).
   - `actor`: `f"{html.escape(actor.kind)}/{html.escape(actor.id)}"`.
   - `last_event`: `f"{html.escape(ev.type)} at {ev.emitted_at.isoformat(timespec='seconds')}"` if present, else `"(none)"`.
   - `next_commands`: joined as `"/" + cmd` separated by `", "` if non-empty, else `"(none)"`.
   - `chat_id` and `reply_to_message_id`: NOT rendered (internal routing fields, not operator-visible).
   - All operator-visible strings are HTML-escaped via `html.escape()` (Story 3.5 H5 carry-forward).
   - Message uses `parse_mode="HTML"` on the reply (same as `/task` and `/approve`).

5. **AC-5: Error handling** — follow the exact same try/except cascade as `/approve` (Story 3.4):

   - `httpx.TooManyRedirects` → `"⚠️ Service misconfiguration. Please try again later."`
   - `httpx.HTTPStatusError` → use `format_http_error(exc, command_label="status")` from `_errors.py`. Special case: 404 → `"⚠️ Task not found: <code>{html.escape(task_id)}</code>"` (distinct from generic error so the operator knows the task-id is valid format but doesn't exist).
   - `RegistryResponseError` → `"⚠️ Received malformed response from registry."`
   - `httpx.HTTPError` (e.g. `ReadTimeout`, `ConnectError`) → `"⚠️ Could not reach registry. Please try again later."`
   - `Exception` → backstop: `"⚠️ Unexpected error. Please try again later."` + `_log.exception(...)` (never lets the webhook fail; Story 3.3 AC-8 / Story 3.4 carry-forward).

6. **AC-6: Router factory and registration**:

   ```python
   def make_status_router() -> Router:
       router = Router()
       router.message(Command("status"))(handle_status)
       return router
   ```

   - Import `make_status_router` in `handlers/__init__.py` and add to `__all__`.
   - Register `dp.include_router(make_status_router())` in `app/lifespan.py` after the existing three routers (Story 3.3 AC-5 ordering carry-forward: `workflow_data` update happens before `include_router` calls).

7. **AC-7: `just lint` 9/9 green** — `ruff check`, `ruff format --check`, `mypy --strict` on telegram-gateway, `check_imports`, `check_event_registry`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`.

8. **AC-8: Co-located tests (≥12)** — in `services/telegram-gateway/src/telegram_gateway/test_status_command.py`:

   - **RegistryAPIClient.get_task tests (4):**
     - `test_get_task_success` — mock transport returns 200 with valid `TaskResponse` JSON; assert parsed fields match.
     - `test_get_task_404_raises` — mock transport returns 404; assert `httpx.HTTPStatusError` raised.
     - `test_get_task_malformed_json_raises_registry_response_error` — 200 with invalid JSON body; assert `RegistryResponseError`.
     - `test_get_task_sends_request_id_header` — verify `X-Request-ID` header present when provided.

   - **Handler tests (8+):**
     - `test_handle_status_success_renders_all_fields` — mock client returns full `TaskResponseLocal`; assert reply contains all rendered fields (task_id, status, title, created_at, updated_at, actor, last_event, next_commands).
     - `test_handle_status_no_title_shows_none` — `title=None`; assert `"(none)"` rendered.
     - `test_handle_status_no_last_event_shows_none` — `last_event=None`; assert `"(none)"` rendered.
     - `test_handle_status_empty_next_commands_shows_none` — `next_commands=[]`; assert `"(none)"` rendered.
     - `test_handle_status_no_args_shows_usage` — message text is just `"/status"`; assert usage reply.
     - `test_handle_status_invalid_task_id_shows_usage` — message text is `"/status not-a-task-id"`; assert usage reply with example.
     - `test_handle_status_task_not_found_404` — mock raises `HTTPStatusError` with 404; assert `"Task not found"` reply.
     - `test_handle_status_network_error` — mock raises `httpx.ReadTimeout`; assert `"Could not reach registry"` reply.

   Target: **12+ new tests**.

9. **AC-9: Scope boundary** — files modifiable in this story:
   - **New (1):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py`
   - **Modified (5 source + 2 process):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (AC-1/AC-2 — add `get_task` method + local models)
     - `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (AC-6 — re-export `make_status_router`)
     - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (AC-6 — register router)
     - `services/telegram-gateway/src/telegram_gateway/test_status_command.py` (AC-8 — new test file)
   - **Not modifiable:** `services/registry-api/` (API endpoint already exists), `services/clawhip-daemon/` (outbound rendering not involved), `services/registry-state/`, existing handler files (`task_command.py`, `approve_command.py`, `ping_command.py`, `_keys.py`, `_errors.py`, `_safe_reply.py`).

10. **AC-10: No new dependencies** — `httpx`, `aiogram`, and `pydantic` already available. No new packages needed.

11. **AC-11: Atomic commit + independent gate verify** — single commit titled exactly:

    ```
    feat(telegram-gateway): story 3.14 — /status command Telegram surface · FR4
    ```

    `just lint` 9/9 green. `just test` count grows by ≥12. `just bootstrap-verify` clean. Independently re-verify before flipping status.

## Tasks / Subtasks

- [ ] **Task 1: Local response model + `get_task` on `RegistryAPIClient`** (AC: #1, #2)
  - [ ] Declare `ActorLocal`, `LastEventLocal`, and `TaskResponseLocal` Pydantic models in `registry_client.py` (local mirrors of `TaskResponse` fields — Story 3.3 AC-2 pattern).
  - [ ] Add `async def get_task(self, *, task_id: str, request_id: str | None = None) -> TaskResponseLocal` method to `RegistryAPIClient`.
  - [ ] Method calls `self._http_client.get(f"/v1/tasks/{task_id}", headers=headers)`, raises on error, parses response into `TaskResponseLocal`.
  - [ ] Add 4 unit tests for `get_task`: success, 404, malformed response, request-id header verification.

- [ ] **Task 2: `handle_status` handler + message rendering** (AC: #3, #4, #5)
  - [ ] Create `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py`.
  - [ ] Implement `handle_status(message, registry_client)` with task-id extraction via `_keys.extract_task_id_from_message`, API call, rendering, and error cascade.
  - [ ] Rendering formats all visible fields: task_id (HTML `<code>`), status, title, created_at, updated_at, actor, last_event, next_commands. Uses `html.escape()` on all operator-visible strings and `isoformat(timespec="seconds")` on datetimes.
  - [ ] 404 special case: distinct "Task not found" message.
  - [ ] Usage reply on missing/invalid task-id.

- [ ] **Task 3: Router registration + handler tests** (AC: #6, #8)
  - [ ] Implement `make_status_router()` factory in `status_command.py`.
  - [ ] Import and re-export `make_status_router` in `handlers/__init__.py`.
  - [ ] Register `dp.include_router(make_status_router())` in `app/lifespan.py` after the three existing routers.
  - [ ] Create `test_status_command.py` with 8+ handler tests: success with all fields, missing title/event/commands edge cases, no args, invalid task-id, 404, network error.

- [ ] **Task 4: Regression verification + atomic commit** (AC: #11)
  - [ ] `uv sync --all-packages` (Epic-1-retro AI #2).
  - [ ] `just test` — confirm test count grows by ≥12.
  - [ ] `just lint` 9/9 green.
  - [ ] `just bootstrap-verify` clean.
  - [ ] Independent gate verify before flipping `review → done` (Epic-2-retro AI #1).
  - [ ] Flip sprint-status.yaml: `3-14-status-command-telegram-surface: backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`.
  - [ ] Atomic commit with the exact title from AC-11.

## Dev Notes

### Quoted Requirements

> **FR4** (`prd.md`): *"Operator can retrieve the full current state of any task in a single response, including current step, last event, last agent action, and available next commands — without relying on chat scrollback."*

> **Epic 3 Story 3.14 AC** (`epics.md:1194-1208`):
> *Given a task exists in any state (and Story 2.9 has delivered the basic endpoint)*
> *When I send `/status t-0001`*
> *Then the bot calls `GET /v1/tasks/t-0001` and replies with a single message rendering every available field from the response.*

> **FR12** (`prd.md`): *"Console Client can perform every task-lifecycle command available via Telegram (full surface parity); no operator capability is Telegram-only."*

### Architecture: Service Boundary

The `telegram-gateway` service is a **read + command-emit client** of `registry-api` via HTTP. It NEVER touches the database or event log directly (enforced by `check_imports.py` and `check_single_writer.py`). This story only adds a read path (`GET /v1/tasks/{id}`).

The response models (`TaskResponseLocal`, `ActorLocal`, `LastEventLocal`) are **local Pydantic mirrors** declared inside `telegram-gateway`, NOT imported from `registry-api`. This is the established pattern from Story 3.3 (AC-2) which created `CreateTaskResponseLocal` and `DecisionResponseLocal` for the same boundary-respect reason.

### Architecture: Existing `GET /v1/tasks/{id}` Endpoint

The endpoint exists at `services/registry-api/src/registry_api/routes/tasks.py` line 491. It returns a `TaskResponse` with these fields:

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | Pattern: `^t-[0-9a-f]{8}-...-[0-9a-f]{12}$` |
| `status` | `str` | One of: `pending`, `planning`, `plan_ready`, `executing`, `completed`, `failed`, `stopped`, `blocked` |
| `title` | `str \| None` | Optional task title |
| `created_at` | `datetime` | ISO 8601 |
| `updated_at` | `datetime` | ISO 8601 |
| `actor` | `ActorOut` | `{ kind: str, id: str }` |
| `last_event` | `LastEventOut \| None` | `{ id: str, type: str, emitted_at: datetime }` |
| `next_commands` | `list[str]` | Available commands for current status |
| `chat_id` | `int \| None` | Internal routing field (NOT rendered) |
| `reply_to_message_id` | `int \| None` | Internal routing field (NOT rendered) |

Returns 200 on success, 404 with RFC 7807 if not found.

The `_NEXT_COMMANDS` dict (lines 86-95) maps statuses to commands:
```python
_NEXT_COMMANDS = {
    "pending": ["stop"],
    "planning": ["stop"],
    "plan_ready": ["approve", "reject", "stop"],
    "executing": ["stop"],
    "completed": [],
    "failed": [],
    "stopped": [],
    "blocked": ["retry", "stop"],
}
```

### Handler Pattern: Composition of `/approve` + `/ping`

This story's handler composes patterns from two existing handlers:

1. **From `/approve` (Story 3.4):** task-id argument extraction via `_keys.extract_task_id_from_message()`, usage reply pattern, no `bot: Bot` parameter.
2. **From `/ping` (Story 3.5):** GET request pattern (no `Idempotency-Key` header), `request_id` generation, `safe_reply`, error cascade.

The `/task` handler (Story 3.3) is NOT the right model — it takes a free-text description argument, not a task-id, and it's a POST request with idempotency.

### Key Shared Modules (DO NOT reinvent)

- `_keys.py` — `extract_task_id_from_message(message)`, `TASK_ID_PATTERN`
- `_errors.py` — `format_http_error(exc, command_label)`
- `_safe_reply.py` — `safe_reply(message, text, **kwargs)` (wraps `message.reply` with safety)
- `registry_client.py` — `RegistryAPIClient` class (add new method, don't replace)

### Carry-Forwards from Previous Stories

| Carry-forward | Source | How 3.14 uses it |
|---|---|---|
| Local response models (`*Local`) | Story 3.3 AC-2 | Mirror `TaskResponse` as `TaskResponseLocal` + sub-models |
| `RegistryAPIClient` with httpx | Story 3.3 AC-1 | Add `get_task()` method following `get_platform_health()` pattern |
| `extract_task_id_from_message()` | Story 3.4 | Re-use for task-id parsing |
| `format_http_error()` | Story 3.4 | Re-use for HTTPStatusError formatting |
| `safe_reply()` | Story 3.5 | Re-use for reply safety |
| `make_*_router()` factory pattern | Stories 3.3-3.5 | `make_status_router()` following same convention |
| `dp.include_router()` registration | Stories 3.3-3.5 | Add after existing routers in `lifespan.py` |
| `html.escape()` on operator-visible strings | Story 3.5 H5 | Apply to all rendered fields |
| `isoformat(timespec="seconds")` | Story 3.12 M14 | Apply to `created_at`, `updated_at`, `last_event.emitted_at` |
| Error cascade (5 exception types + backstop) | Stories 3.3-3.4 | Same cascade in `handle_status` |
| `X-Request-ID` header on all API calls | Story 3.3 | Include in `get_task()` |
| `request_id` via `new_request_id()` | Story 3.5 | Generate for observability |
| `uv sync --all-packages` before lint | Epic-1-retro AI #2 | Use in Task 4 |

### Architecture References

- `prd.md` — FR4 (status), FR12 (CLI parity).
- `epics.md:1194-1208` — Story 3.14 user story + AC.
- `epics.md:2030-2038` — Story 7.1 future enhancement (reconstituted state).
- `architecture.md` — Component 3 (telegram-gateway); HTTP client pattern; service boundaries.
- Story 2.9 — `GET /v1/tasks/{id}` endpoint (already exists; this story calls it).
- Story 3.3 — `/task` handler + `RegistryAPIClient` + local models + lifespan registration.
- Story 3.4 — `/approve` handler + task-id extraction pattern.
- Story 3.5 — `/ping` handler + GET request pattern + `safe_reply`.
- Story 3.9 — Task thread binding (established `chat_id` + `reply_to_message_id` fields).
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe.
- Epic-2-retro AI #1 — Independent gate verify mandatory.

### Project Structure Notes

- Handler: `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` (new)
- Handler test: `services/telegram-gateway/src/telegram_gateway/test_status_command.py` (new)
- Client: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (modify)
- Init: `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (modify)
- Lifespan: `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modify)
- Spec: `_bmad-output/implementation-artifacts/3-14-status-command-telegram-surface.md` (this file)
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips)

No detected conflicts with unified project structure. Handler placement follows the established `handlers/<command>_command.py` convention.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` | New — `handle_status` + `make_status_router` |
| `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` | Modified — add `get_task` method + `ActorLocal` + `LastEventLocal` + `TaskResponseLocal` models |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Modified — re-export `make_status_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Modified — register `make_status_router()` |
| `services/telegram-gateway/src/telegram_gateway/test_status_command.py` | New — 12+ tests |
| `_bmad-output/implementation-artifacts/3-14-status-command-telegram-surface.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

No debug issues encountered during implementation.

### Completion Notes List

- All 4 AC tasks completed sequentially.
- Lint fix: lifespan.py import line exceeded 100-char limit — split into multi-line import.
- Ruff format applied to 2 new files (status_command.py, test_status_command.py).
- Pre-existing separability test failure (test_spine_source_code_unchanged) from Story 3.13's test_event_types.py modification — not caused by 3.14 changes.
- 12 new tests added, all passing.

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/test_status_command.py` — New
- `_bmad-output/implementation-artifacts/3-14-status-command-telegram-surface.md` — Modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified
