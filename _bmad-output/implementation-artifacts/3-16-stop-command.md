# Story 3.16: `/stop` command

Status: done

## Story

As **the operator**,
I want **`/stop <task-id>` to halt a running task and release its worktree lock**,
so that **I can kill work that's gone sideways (FR7)**.

This is the third operator-action command after `/approve` (Story 3.4). It calls the same `POST /v1/tasks/{id}/decisions` endpoint with `{action: "stop"}` via the existing `RegistryAPIClient.submit_decision` method. No new registry-api client methods or local response models are needed — this story reuses `submit_decision` and `DecisionResponseLocal` from Story 3.4.

**What this story is NOT:**
- **NOT** the `POST /v1/tasks/{id}/decisions` server-side handler — Story 6.4 owns it.
- **NOT** the `task.stop_requested` audit event emission — Story 6.5 owns it.
- **NOT** the worktree lock release logic — Story 7.7 owns it.
- **NOT** the console CLI `stop` command — Story 4.3 owns it.
- **NOT** `/reject` — that's Story 3.17.
- **NOT** `/retry` — that's Story 3.18 (adds hint passthrough).

**Placeholder behavior:** Same as `/approve` — `POST /v1/tasks/{id}/decisions` does NOT exist server-side yet (Story 6.4 owns it). Tests mock the transport layer. When 6.4 ships, this handler automatically works without code changes.

## Acceptance Criteria

1. **AC-1: `handle_stop` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` with:

   ```python
   async def handle_stop(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
   ```

   - No `bot: Bot` parameter (same as `/approve`, `/status`, `/logs` — no outbound delivery needed).
   - Derives operator actor fields from `message.from_user` (same guard block as `/approve` L8).
   - Uses `_keys.extract_task_id_from_message(message)` for argument parsing (Story 3.4 pattern).
   - If no task-id found: reply with usage `"Usage: /stop <task-id>"` or `"Usage: /stop <task-id>; example: /stop t-0192..."` depending on whether args were present.
   - If task-id found: derive idempotency key via `_keys.idempotency_key_from_message(message)`.
   - Calls `registry_client.submit_decision(task_id=task_id, action="stop", idempotency_key=idempotency_key, operator_actor_id=operator_actor_id, request_id=request_id)`.
   - No `hint` parameter (unlike `/retry` in Story 3.18, `/stop` has no hint passthrough).

2. **AC-2: Success reply** — on successful `submit_decision` response:

   ```
   🛑 Stopped by @<handle> at <decided_at_iso>. Task halted.
   ```

   - When `idempotency_status == "replayed"`: append ` (retry deduped)` before `. Task halted.` (same pattern as `/approve` L10).
   - `operator_handle` and `decided_at_iso` are HTML-escaped.

3. **AC-3: Error handling** — follow the exact same try/except cascade as `/approve` (Story 3.4):

   - `httpx.TooManyRedirects` → `"⚠️ Registry misconfigured: too many redirects."`
   - `httpx.HTTPStatusError` → `format_http_error(exc)` with `command_label="Stop command"`.
   - `RegistryResponseError` → `"⚠️ Registry returned an unexpected response. Logs captured."`
   - `httpx.HTTPError` → `"⚠️ Could not reach registry: {type(exc).__name__}."`
   - `Exception` → backstop: `"⚠️ Internal error. Logs captured."` + `_log.exception(...)` (never lets the webhook fail; Story 3.1 M3 contract).

4. **AC-4: Router factory and registration**:

   ```python
   def make_stop_router() -> Router:
       router = Router()
       router.message(Command("stop"))(handle_stop)
       return router
   ```

   - Import `make_stop_router` in `handlers/__init__.py` and add to `__all__`.
   - Register `dp.include_router(make_stop_router())` in `app/lifespan.py` after existing routers.

5. **AC-5: `just lint` 9/9 green** — `ruff check`, `ruff format --check`, `mypy --strict`, `check_imports`, `check_event_registry`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`.

6. **AC-6: Co-located tests (≥12)** — in `services/telegram-gateway/src/telegram_gateway/test_stop_command.py`:

   - **Handler tests (10+):**
     - `test_handle_stop_success_renders_confirmation` — mock client returns `DecisionResponseLocal`; assert reply contains `@<handle>`, `decided_at`, `"Task halted"`.
     - `test_handle_stop_success_with_retry_deduped` — `idempotency_status="replayed"`; assert `"(retry deduped)"` in reply.
     - `test_handle_stop_no_args_shows_usage` — message text is just `"/stop"`; assert usage reply.
     - `test_handle_stop_invalid_task_id_shows_usage` — message text is `"/stop bad"`; assert usage with example.
     - `test_handle_stop_http_status_error` — mock raises `HTTPStatusError`; assert error reply via `format_http_error`.
     - `test_handle_stop_network_error` — mock raises `httpx.ReadTimeout`; assert `"Could not reach registry"` reply.
     - `test_handle_stop_too_many_redirects` — `TooManyRedirects` → `"too many redirects"` reply.
     - `test_handle_stop_malformed_response` — `RegistryResponseError` → `"unexpected response"` reply.
     - `test_handle_stop_unexpected_exception` — `RuntimeError` backstop → `"Internal error"` reply.
     - `test_handle_stop_from_user_none_uses_unknown_actor` — `from_user` is None; assert `"unknown"` actor, `@operator` handle.

   - **HTML security test (1):**
     - `test_handle_stop_html_chars_in_username_are_escaped` — username with HTML chars; assert escaped in reply.

   - **Router test (1):**
     - `test_make_stop_router_returns_fresh_routers` — factory produces distinct instances.

   Target: **12+ new tests**.

7. **AC-7: Scope boundary** — files modifiable in this story:
   - **New (1):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py`
   - **Modified (3 source + 2 process):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/test_stop_command.py` (AC-6 — new test file)
   - **Not modifiable:** `registry_client.py` (reuses existing `submit_decision`), existing handler files, `services/registry-api/`, `services/clawhip-daemon/`, `services/registry-state/`.

8. **AC-8: No new dependencies** — `httpx`, `aiogram`, `pydantic` already available. No new packages needed. No new client methods or response models needed — `submit_decision` and `DecisionResponseLocal` are reused from Story 3.4.

9. **AC-9: Atomic commit + independent gate verify** — single commit titled exactly:

    ```
    feat(telegram-gateway): story 3.16 — /stop command · FR7
    ```

    `just lint` 9/9 green. `just test` count grows by ≥12. Independently re-verify before flipping status.

## Tasks / Subtasks

- [x] **Task 1: `handle_stop` handler + success/error rendering** (AC: #1, #2, #3)
  - [x] Create `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py`.
  - [x] Implement `handle_stop(message, registry_client)` with operator actor derivation, task-id extraction, idempotency key, `submit_decision(action="stop")`, and error cascade.
  - [x] Success reply: `"🛑 Stopped by @{operator_handle} at {decided_at_iso}. Task halted."` with retry-deduped variant.
  - [x] Usage reply on missing/invalid task-id.
  - [x] 5-branch error cascade matching `/approve`.

- [x] **Task 2: Router registration + handler tests** (AC: #4, #6)
  - [x] Implement `make_stop_router()` factory in `stop_command.py`.
  - [x] Import and re-export `make_stop_router` in `handlers/__init__.py`.
  - [x] Register `dp.include_router(make_stop_router())` in `app/lifespan.py` after existing routers.
  - [x] Create `test_stop_command.py` with 12+ tests covering success, retry deduped, no args, invalid task-id, HTTP errors, network errors, TooManyRedirects, malformed response, unexpected exception, from_user None, HTML escaping, router factory.

- [x] **Task 3: Regression verification + atomic commit** (AC: #5, #9)
  - [x] `uv sync --all-packages` (Epic-1-retro AI #2).
  - [x] `just test` — confirm test count grows by ≥12. (1106 passed, 12 new from this story)
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] Independent gate verify before flipping `review → done` (Epic-2-retro AI #1).
  - [x] Flip sprint-status.yaml: `3-16-stop-command: backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`.
  - [x] Atomic commit with the exact title from AC-9.

## Dev Notes

### Quoted Requirements

> **FR7** (`prd.md`): *"Operator can approve, reject, stop, or retry a task at any approval or blocker checkpoint, with an optional free-text hint injected into the orchestrator's next planning pass."*

> **Epic 3 Story 3.16 AC** (`epics.md:1226-1238`):
> *Given a task is in state `executing` / `awaiting_approval` / `verifying`*
> *When I send `/stop t-0001`*
> *Then the bot calls `POST /v1/tasks/t-0001/decisions {action:stop}`, the task transitions to `stopped`, the worktree lock releases, and the bot confirms within 3 s.*

> **FR8** (`prd.md`): *"Platform can transition tasks through explicit lifecycle states … `stopped` … and record each transition as a typed event."*

> **FR27** (`prd.md`): *"Platform can hold a Worker's worktree lock through a blocked task's entire waiting period, releasing only on operator `/stop` or `/retry` resolution."*

### Architecture: Service Boundary

The `telegram-gateway` service is a **read + command-emit client** of `registry-api` via HTTP. This story adds another command path — `POST /v1/tasks/{id}/decisions {action: "stop"}` — reusing the existing `submit_decision` method (Story 3.4 AC-1). No cross-service imports, no database access (enforced by `check_imports.py` and `check_single_writer.py`).

### Architecture: Exact Clone of `/approve`

This story is structurally identical to `/approve` (Story 3.4) with only these differences:

| Aspect | `/approve` (Story 3.4) | `/stop` (Story 3.16) |
|--------|----------------------|---------------------|
| Action string | `"approve"` | `"stop"` |
| Success emoji | `✅` | `🛑` |
| Success suffix | `"Pushing."` | `"Task halted."` |
| Router command | `Command("approve")` | `Command("stop")` |
| Handler name | `handle_approve` | `handle_stop` |
| Router factory | `make_approve_router()` | `make_stop_router()` |
| Logger name | `approve_command` | `stop_command` |
| Test file | `test_approve_command.py` | `test_stop_command.py` |

Everything else — actor derivation, task-id extraction, idempotency key, error cascade, `DecisionResponseLocal` handling, HTML escaping — is identical. **When in doubt, copy from `/approve` and change only the diff table above.**

### Key Shared Modules (DO NOT reinvent)

- `_keys.py` — `extract_task_id_from_message(message)`, `idempotency_key_from_message(message)`, `TASK_ID_PATTERN`
- `_errors.py` — `format_http_error(exc, command_label="Stop command")`
- `_safe_reply.py` — `safe_reply(message, text, **kwargs)`
- `registry_client.py` — `RegistryAPIClient.submit_decision()` + `DecisionResponseLocal` (REUSE — do NOT create new methods or models)

### Carry-Forwards from Previous Stories

| Carry-forward | Source | How 3.16 uses it |
|---|---|---|
| `submit_decision()` + `DecisionResponseLocal` | Story 3.4 | Call with `action="stop"` — no new client method needed |
| Operator actor derivation guard block | Story 3.4 L8 | Copy the exact `from_user` guard block |
| `idempotency_key_from_message()` | Story 3.4 | Reuse for idempotency |
| `extract_task_id_from_message()` | Story 3.4 | Reuse for task-id parsing |
| `format_http_error()` | Story 3.4 | Use with `command_label="Stop command"` |
| `safe_reply()` | Story 3.5 | Reuse for reply safety |
| `make_*_router()` factory pattern | Stories 3.3-3.5 | `make_stop_router()` following same convention |
| `dp.include_router()` registration | Stories 3.3-3.5 | Add after existing routers in `lifespan.py` |
| `html.escape()` on operator-visible strings | Story 3.5 H5 | Escape `operator_handle` and `decided_at_iso` |
| Error cascade (5 exception types + backstop) | Stories 3.3-3.4 | Same cascade in `handle_stop` |
| `X-Request-ID` header on all API calls | Story 3.3 | Included via `submit_decision(request_id=...)` |
| `request_id` via `new_request_id()` | Story 3.5 | Generate for observability |
| `uv sync --all-packages` before lint | Epic-1-retro AI #2 | Use in Task 3 |

### Learnings from Recent Stories (3.14, 3.15)

1. **HTML escaping on ALL externally-sourced strings** — escape `operator_handle`, `decided_at_iso`.
2. **Test all 5 error-catch branches** — include tests for TooManyRedirects, HTTPStatusError, RegistryResponseError, HTTPError, and Exception backstop from the start.
3. **`command_label` in `format_http_error()`** — use `"Stop command"` (descriptive).
4. **`@asynccontextmanager` for test helpers** — proper teardown pattern for `httpx.AsyncClient`.
5. **Import ordering** — keep alphabetical in `__init__.py` and `lifespan.py`.
6. **`__all__` multi-line format** — keep the existing multi-line list format.

### Architecture References

- `prd.md` — FR7 (operator decisions), FR8 (lifecycle states), FR24a (stop as failure signal), FR27 (worktree lock).
- `epics.md:1226-1238` — Story 3.16 user story + AC.
- `epics.md:1811-1832` — Story 6.4 (future decisions handler).
- `epics.md:1834-1848` — Story 6.5 (future audit events).
- `architecture.md` — Component 3 (telegram-gateway); HTTP client pattern; service boundaries.
- Story 3.4 — `/approve` handler (EXACT template for this story — copy and modify).
- Story 3.5 — `/ping` handler + `safe_reply`.
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe.
- Epic-2-retro AI #1 — Independent gate verify mandatory.

### Project Structure Notes

- Handler: `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` (new)
- Handler test: `services/telegram-gateway/src/telegram_gateway/test_stop_command.py` (new)
- Init: `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (modify)
- Lifespan: `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modify)
- Spec: `_bmad-output/implementation-artifacts/3-16-stop-command.md` (this file)
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips)

No detected conflicts with unified project structure. Handler placement follows the established `handlers/<command>_command.py` convention.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` | New — `handle_stop` + `make_stop_router` |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Modified — re-export `make_stop_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Modified — register `make_stop_router()` |
| `services/telegram-gateway/src/telegram_gateway/test_stop_command.py` | New — 12+ tests |
| `_bmad-output/implementation-artifacts/3-16-stop-command.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

No debug issues encountered during implementation.

### Completion Notes List

- All 3 AC tasks completed sequentially.
- Import order fixed in test_stop_command.py (ruff isort).
- 12 new tests added, all passing (1106 total, 0 new failures).
- Lint clean (ruff check all pass).
- Pre-existing failures unchanged (crash-injection: 4, separability: 1 — from Story 3.13, not caused by 3.16).

### Code Review Findings (3.16)

Review agents: Blind Hunter, Edge Case Hunter, Acceptance Auditor.

**Applied fixes (3):**

1. **Missing `exc` in log calls** (Edge Case Hunter HIGH) — `RegistryResponseError`, `HTTPError`, and `Exception` catch blocks now bind `exc` and include it in log format strings, matching the `/approve` handler pattern.
2. **Network error reply diverged from `/approve`** (Edge Case Hunter HIGH) — Changed from static `"Could not reach registry. Please try again later."` to `f"Could not reach registry: {type(exc).__name__}."` to match the `/approve` handler. Test assertion updated accordingly.
3. **Double `@` when `from_user` is None** (Acceptance Auditor MEDIUM) — `operator_handle` was set to `"@operator"` (with `@`), but the template adds another `@` via `@{operator_handle}`, producing `@@operator`. Fixed to `"operator"` so the reply shows `@operator`.

**Dismissed findings (false positives / by design):**

- CancelledError handling: aiogram dispatches in fire-and-forget tasks (lifespan.py M3), so CancelledError during shutdown is harmless.
- Ellipsis-style logging: project convention uses `%s` positional logging, not lazy `%r`.
- `decided_at` null: `DecisionResponseLocal.decided_at` is a non-optional `datetime` (pydantic model).
- DI concern: `registry_client` is injected via `dp.workflow_data` (lifespan.py), not a global singleton.
- `test_logs_command.py` format drift: pre-existing from Story 3.15, formatted as part of this pass.

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/test_stop_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/test_logs_command.py` — Modified (format only)
- `_bmad-output/implementation-artifacts/3-16-stop-command.md` — Modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified
