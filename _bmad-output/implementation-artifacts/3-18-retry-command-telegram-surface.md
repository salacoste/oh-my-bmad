# Story 3.18: `/retry` command (Telegram surface)

Status: done

## Story

As **the operator**,
I want **`/retry <task-id> [hint]` to resume a blocked task with my clarifying hint injected into the orchestrator's next plan**,
so that **I can course-correct without re-submitting a full task (FR7)**.

This is the fifth operator-action command after `/approve` (Story 3.4), `/stop` (Story 3.16), and `/reject` (Story 3.17). It calls the same `POST /v1/tasks/{id}/decisions` endpoint with `{action: "retry"}` via the existing `RegistryAPIClient.submit_decision` method. The optional `[hint]` free-text argument maps to the `hint` parameter on `submit_decision` — identical to `/reject`'s reason passthrough.

**What this story is NOT:**
- **NOT** the `POST /v1/tasks/{id}/decisions` server-side handler — Story 6.4 owns it.
- **NOT** the hint-injection business logic — Story 7.6 owns it.
- **NOT** the console CLI `retry` command — Story 4.3 owns it.
- **NOT** `/reject` — that's Story 3.17 (reject with reason/hint passthrough, same structural pattern).
- **NOT** `/stop` — that's Story 3.16 (no hint passthrough).

**Placeholder behavior:** Same as `/approve` — `POST /v1/tasks/{id}/decisions` does NOT exist server-side yet (Story 6.4 owns it). Tests mock the transport layer. When 6.4 ships, this handler automatically works without code changes.

**Hint vs reason:** The epics AC shows `hint="..."` syntax, but the implementation uses positional text after the task-id (same as `/reject`'s reason). The hint is passed as `hint=<text>` to `submit_decision`. No `hint=` prefix or quoting required from the operator.

## Acceptance Criteria

1. **AC-1: `handle_retry` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` with:

   ```python
   async def handle_retry(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
   ```

   - No `bot: Bot` parameter (same as `/approve`, `/stop`, `/reject`).
   - Derives operator actor fields from `message.from_user` (same guard block as `/reject`).
   - Uses `split(None, 2)` and validates `parts[1]` directly against `_keys.TASK_ID_PATTERN.match(parts[1])` — same approach as `/reject` (NOT `extract_task_id_from_message`, which splits with maxsplit=1 and appends hint text to the task-id candidate).
   - If no task-id found: reply with usage `"Usage: /retry <task-id> [hint]"` or `"Usage: /retry <task-id> [hint]; example: /retry t-0192... rate limit per-user"` depending on whether args were present.
   - If task-id found: extract hint from `parts[2].strip()[:MAX_HINT_LENGTH]` if `len(parts) >= 3`, otherwise `None`.
   - `MAX_HINT_LENGTH = 1000` — same cap as `/reject`'s `MAX_REASON_LENGTH` (code-review fix from Story 3.17).
   - Derive idempotency key via `_keys.idempotency_key_from_message(message)`.
   - Calls `registry_client.submit_decision(task_id=task_id, action="retry", idempotency_key=idempotency_key, operator_actor_id=operator_actor_id, request_id=request_id, hint=hint)`.
   - `hint=hint` — when `hint` is `None`, `submit_decision` omits the `hint` key from the POST body.

2. **AC-2: Success reply** — on successful `submit_decision` response:

   ```
   🔄 Retried by @<handle> at <decided_at_iso>. Task resumed.
   ```

   - When `idempotency_status == "replayed"`: append ` (retry deduped)` before `. Task resumed.`
   - `operator_handle` and `decided_at_iso` are HTML-escaped.

3. **AC-3: Error handling** — follow the exact same try/except cascade as `/reject` (Story 3.17):

   - `httpx.TooManyRedirects` → `"⚠️ Registry misconfigured: too many redirects."`
   - `httpx.HTTPStatusError as exc` → `format_http_error(exc, command_label="Retry command")`.
   - `RegistryResponseError as exc` → `_log.exception(..., exc)` → `"⚠️ Registry returned an unexpected response. Logs captured."`
   - `httpx.HTTPError as exc` → `_log.warning(..., exc)` → `f"⚠️ Could not reach registry: {type(exc).__name__}."`
   - `Exception as exc` → backstop: `_log.exception(..., exc)` → `"⚠️ Internal error. Logs captured."`

4. **AC-4: Router factory and registration**:

   ```python
   def make_retry_router() -> Router:
       router = Router()
       router.message(Command("retry"))(handle_retry)
       return router
   ```

   - Import `make_retry_router` in `handlers/__init__.py` and add to `__all__`.
   - Register `dp.include_router(make_retry_router())` in `app/lifespan.py` after `make_reject_router()`.

5. **AC-5: `just lint` 9/9 green** — `ruff check`, `ruff format --check`, `mypy --strict`, `check_imports`, `check_event_registry`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`.

6. **AC-6: Co-located tests (>=14)** — in `services/telegram-gateway/src/telegram_gateway/test_retry_command.py`:

   - **Handler tests (11+):**
     - `test_handle_retry_success_renders_confirmation` — mock client returns `DecisionResponseLocal`; assert reply contains `@<handle>`, `decided_at`, `"Task resumed"`.
     - `test_handle_retry_success_with_retry_deduped` — `idempotency_status="replayed"`; assert `"(retry deduped)"` in reply.
     - `test_handle_retry_with_hint_passes_hint` — message has hint text; assert `submit_decision` called with `hint="rate limit must be per-user"`.
     - `test_handle_retry_without_hint_passes_none_hint` — message has no hint; assert `submit_decision` called with `hint=None`.
     - `test_handle_retry_no_args_shows_usage` — message text is just `"/retry"`; assert usage reply.
     - `test_handle_retry_invalid_task_id_shows_usage` — message text is `"/retry bad"`; assert usage with example.
     - `test_handle_retry_http_status_error` — mock raises `HTTPStatusError`; assert error reply via `format_http_error`.
     - `test_handle_retry_network_error` — mock raises `httpx.ReadTimeout`; assert `"Could not reach registry: ReadTimeout"` reply.
     - `test_handle_retry_too_many_redirects` — `TooManyRedirects` → `"too many redirects"` reply.
     - `test_handle_retry_malformed_response` — `RegistryResponseError` → `"unexpected response"` reply.
     - `test_handle_retry_unexpected_exception` — `RuntimeError` backstop → `"Internal error"` reply.
     - `test_handle_retry_from_user_none_uses_unknown_actor` — `from_user` is None; assert `"unknown"` actor, `@operator` handle (no double-`@`).

   - **HTML security test (1):**
     - `test_handle_retry_html_chars_in_username_are_escaped` — username with HTML chars; assert escaped in reply.

   - **Router test (1):**
     - `test_make_retry_router_returns_fresh_routers` — factory produces distinct instances.

   - **Code-review fix tests (3):**
     - `test_handle_retry_hint_truncated_at_max_length` — hint > `MAX_HINT_LENGTH` is truncated.
     - `test_handle_retry_unicode_hint_passes_through` — parametrized with emoji, RTL, newlines, ZWJ.
     - `test_handle_retry_from_user_none_logs_chat_id` — `from_user=None` with valid chat still replies.

   Target: **17+ new tests** (matching Story 3.17 post-review test count).

7. **AC-7: Scope boundary** — files modifiable in this story:
   - **New (1):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py`
   - **Modified (3 source + 2 process):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/test_retry_command.py` (AC-6 — new test file)
   - **Not modifiable:** `registry_client.py`, existing handler files, other services.

8. **AC-8: No new dependencies** — `httpx`, `aiogram`, `pydantic` already available. No new client methods or response models.

9. **AC-9: Atomic commit + independent gate verify** — single commit titled exactly:

    ```
    feat(telegram-gateway): story 3.18 — /retry command · FR7
    ```

    `just lint` 9/9 green. `just test` count grows by >=17. Independently re-verify before flipping status.

## Tasks / Subtasks

- [x] **Task 1: `handle_retry` handler + success/error rendering** (AC: #1, #2, #3)
  - [x] Create `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py`.
  - [x] Implement `handle_retry(message, registry_client)` — clone of `/reject` with: action="retry", emoji="🔄", success suffix="Task resumed.", `MAX_HINT_LENGTH = 1000`.
  - [x] Success reply: `"🔄 Retried by @{operator_handle} at {decided_at_iso}. Task resumed."` with retry-deduped variant.
  - [x] Usage reply on missing/invalid task-id — `"Usage: /retry <task-id> [hint]"`.
  - [x] 5-branch error cascade matching `/reject` (bind `exc` in all catch blocks, use `type(exc).__name__` in network error).

- [x] **Task 2: Router registration + handler tests** (AC: #4, #6)
  - [x] Implement `make_retry_router()` factory in `retry_command.py`.
  - [x] Import and re-export `make_retry_router` in `handlers/__init__.py` (alphabetically between `make_reject_router` and `make_status_router`).
  - [x] Register `dp.include_router(make_retry_router())` in `app/lifespan.py` after `make_reject_router()`.
  - [x] Create `test_retry_command.py` with 17+ tests (clone from `test_reject_command.py`, adapt for retry).

- [x] **Task 3: Regression verification + atomic commit** (AC: #5, #9)
  - [x] `uv sync --all-packages` (Epic-1-retro AI #2).
  - [x] `just test` — confirm test count grows by >=17. (323 passed, 20 new from this story)
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] Independent gate verify before flipping `review → done` (Epic-2-retro AI #1).
  - [x] Flip sprint-status.yaml: `3-18-retry-command-telegram-surface: backlog → ready-for-dev → in-progress → review`; bump `last_updated`.
  - [x] Atomic commit with the exact title from AC-9.

## Dev Notes

### Quoted Requirements

> **FR7** (`prd.md`): *"Operator can approve, reject, stop, or retry a task at any approval or blocker checkpoint, with an optional free-text hint injected into the orchestrator's next planning pass."*

> **Epic 3 Story 3.18 AC** (`epics.md:1254-1266`):
> *Given a task is in `blocked`*
> *When I send `/retry t-0001 hint="rate limit must be per-user, not per-IP"`*
> *Then the bot calls `POST /v1/tasks/t-0001/decisions {action:retry, hint:"..."}`, the task transitions to `planning` with the hint carried in the event payload, and the bot confirms.*

> **FR8** (`prd.md`): *"Platform can transition tasks through explicit lifecycle states … and record each transition as a typed event."*

### Architecture: Service Boundary

The `telegram-gateway` service is a **read + command-emit client** of `registry-api` via HTTP. This story adds another command path — `POST /v1/tasks/{id}/decisions {action: "retry", hint: "..."}` — reusing the existing `submit_decision` method. No cross-service imports, no database access.

### Architecture: Clone of `/reject` with different action/emoji/suffix

This story is structurally identical to `/reject` (Story 3.17) with these differences:

| Aspect | `/reject` (Story 3.17) | `/retry` (Story 3.18) |
|--------|------------------------|------------------------|
| Action string | `"reject"` | `"retry"` |
| Success emoji | `🚫` | `🔄` |
| Success suffix | `"Task stopped."` | `"Task resumed."` |
| Router command | `Command("reject")` | `Command("retry")` |
| Handler name | `handle_reject` | `handle_retry` |
| Router factory | `make_reject_router()` | `make_retry_router()` |
| Logger name | `reject_command` | `retry_command` |
| Test file | `test_reject_command.py` | `test_retry_command.py` |
| Free-text param | `reason` → `hint` | `hint` → `hint` |
| Length cap constant | `MAX_REASON_LENGTH = 1000` | `MAX_HINT_LENGTH = 1000` |
| Usage text | `"Usage: /reject <task-id> [reason]"` | `"Usage: /retry <task-id> [hint]"` |

Everything else — actor derivation, task-id parsing (`split(None, 2)` + inline `TASK_ID_PATTERN.match`), idempotency key, error cascade, `DecisionResponseLocal` handling, HTML escaping — is identical. **Copy from `/reject` and change only the diff table above.**

### Hint Extraction Logic

Identical to `/reject`'s reason extraction:

```python
raw_text = message.text or ""
parts = raw_text.split(None, 2)  # split into max 3 parts
task_id = parts[1] if len(parts) >= 2 and _keys.TASK_ID_PATTERN.match(parts[1]) else None
hint = parts[2].strip()[:MAX_HINT_LENGTH] if len(parts) >= 3 else None
```

**Why NOT `extract_task_id_from_message`:** That function uses `split(None, 1)` (maxsplit=1), which appends hint text to the task-id candidate and fails the `TASK_ID_PATTERN` regex. This is documented in the `/reject` handler comments — same issue, same solution.

### Key Shared Modules (DO NOT reinvent)

- `_keys.py` — `TASK_ID_PATTERN`, `idempotency_key_from_message(message)` (NOT `extract_task_id_from_message`)
- `_errors.py` — `format_http_error(exc, command_label="Retry command")`
- `_safe_reply.py` — `safe_reply(message, text, **kwargs)`
- `registry_client.py` — `RegistryAPIClient.submit_decision()` + `DecisionResponseLocal` (REUSE). The `hint` parameter already exists.

### Carry-Forwards from Previous Stories

| Carry-forward | Source | How 3.18 uses it |
|---|---|---|
| `submit_decision()` + `DecisionResponseLocal` | Story 3.4 | Call with `action="retry"`, `hint=hint` |
| `submit_decision(hint=...)` parameter | Story 3.4 | Pass hint text — already supported |
| Operator actor derivation guard block | Story 3.4 | Copy from `/reject` — use `"operator"` (no `@` prefix!) |
| `idempotency_key_from_message()` | Story 3.4 | Reuse for idempotency |
| `split(None, 2)` + inline `TASK_ID_PATTERN.match` | Story 3.17 | Same approach — NOT `extract_task_id_from_message` |
| `MAX_*_LENGTH = 1000` cap on free-text | Story 3.17 review | `MAX_HINT_LENGTH = 1000` — same cap |
| `format_http_error()` | Story 3.4 | Use with `command_label="Retry command"` |
| `safe_reply()` | Story 3.5 | Reuse for reply safety |
| `make_*_router()` factory pattern | Stories 3.3-3.5 | `make_retry_router()` following same convention |
| `dp.include_router()` registration | Stories 3.3-3.5 | Add after `make_reject_router()` in `lifespan.py` |
| `html.escape()` on operator-visible strings | Story 3.5 H5 | Escape `operator_handle` and `decided_at_iso` |
| Error cascade (5 exception types + backstop) | Stories 3.3-3.4 | Same cascade — bind `exc` in all blocks |
| `X-Request-ID` header on all API calls | Story 3.3 | Included via `submit_decision(request_id=...)` |
| `request_id` via `new_request_id()` | Story 3.5 | Generate for observability |
| `uv sync --all-packages` before lint | Epic-1-retro AI #2 | Use in Task 3 |

### Learnings from Story 3.17 Code Review

1. **Cap free-text at 1000 chars** — add `MAX_HINT_LENGTH = 1000` constant, apply `[:MAX_HINT_LENGTH]` slice. Prevents unbounded hint payload.
2. **Bind `exc` in ALL catch blocks** — `RegistryResponseError as exc`, `HTTPError as exc`, `Exception as exc`. Include `exc` in log format strings.
3. **Network error reply must include `type(exc).__name__`** — use `f"Could not reach registry: {type(exc).__name__}."`.
4. **No double `@` in `from_user=None` fallback** — set `operator_handle = "operator"` (without `@`), since the template adds `@` via `@{operator_handle}`.
5. **HTML escaping on ALL externally-sourced strings** — escape `operator_handle`, `decided_at_iso`.
6. **Test all 5 error-catch branches** — include tests for TooManyRedirects, HTTPStatusError, RegistryResponseError, HTTPError, and Exception backstop.
7. **Include code-review fix tests from the start** — reason truncation, Unicode passthrough, from_user=None with valid chat. Don't wait for review to add these.
8. **`command_label` in `format_http_error()`** — use `"Retry command"`.
9. **Import ordering** — keep alphabetical in `__init__.py` and `lifespan.py`.
10. **`__all__` multi-line format** — keep the existing multi-line list format.
11. **`extract_task_id_from_message` cannot be used** — it splits with maxsplit=1, appending trailing hint text to the task-id candidate. Use `split(None, 2)` + inline `TASK_ID_PATTERN.match(parts[1])` instead.

### Architecture References

- `prd.md` — FR7 (operator decisions), FR8 (lifecycle states).
- `epics.md:1254-1266` — Story 3.18 user story + AC.
- `epics.md:1811-1832` — Story 6.4 (future decisions handler).
- `epics.md:1834-1848` — Story 6.5 (future audit events).
- `architecture.md` — Component 3 (telegram-gateway); HTTP client pattern; service boundaries.
- Story 3.4 — `/approve` handler (original template).
- Story 3.16 — `/stop` handler (clone template, includes review fixes).
- Story 3.17 — `/reject` handler (closest clone — includes review fixes + reason/hint passthrough). **COPY FROM THIS FILE.**
- Story 3.5 — `/ping` handler + `safe_reply`.
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe.
- Epic-2-retro AI #1 — Independent gate verify mandatory.

### Project Structure Notes

- Handler: `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` (new)
- Handler test: `services/telegram-gateway/src/telegram_gateway/test_retry_command.py` (new)
- Init: `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (modify)
- Lifespan: `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modify)
- Spec: `_bmad-output/implementation-artifacts/3-18-retry-command-telegram-surface.md` (this file)
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips)

No detected conflicts with unified project structure. Handler placement follows the established `handlers/<command>_command.py` convention.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` | New — `handle_retry` + `make_retry_router` |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Modified — re-export `make_retry_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Modified — register `make_retry_router()` |
| `services/telegram-gateway/src/telegram_gateway/test_retry_command.py` | New — 17+ tests |
| `_bmad-output/implementation-artifacts/3-18-retry-command-telegram-surface.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

### Completion Notes List

- All 3 AC tasks completed sequentially.
- Handler cloned from `/reject` (Story 3.17) with: action="retry", emoji=🔄, suffix="Task resumed.", MAX_HINT_LENGTH=1000.
- Hint extraction uses `split(None, 2)` + inline `TASK_ID_PATTERN.match(parts[1])` — same approach as `/reject` (NOT `extract_task_id_from_message`).
- Code-review fix tests included from the start (hint truncation, Unicode passthrough, from_user=None with valid chat).
- 20 new tests added, all passing (323 total telegram-gateway, 0 new failures).
- Lint clean (ruff check all pass, ruff format clean).

### Code Review Record

#### Review Agents

Three parallel review agents ran:

1. **Blind Hunter** (code-reviewer) — 6 findings: 0 HIGH, 4 MEDIUM, 2 LOW
2. **Edge Case Hunter** (code-reviewer) — 6 findings: 0 HIGH, 2 MEDIUM, 4 LOW
3. **Acceptance Auditor** (verifier) — All 9 ACs PASS. APPROVE.

#### Fix Summary

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| 1 | Missing test for `username=None, first_name="Alice"` fallback path | MEDIUM | Added `test_handle_retry_username_none_uses_first_name` |
| 2 | Missing test for `username=None, first_name=None` → `@operator` fallback | MEDIUM | Added `test_handle_retry_no_username_no_first_name_uses_operator` |
| 3 | Missing test for `first_name` HTML escaping path | MEDIUM | Added `test_handle_retry_html_chars_in_first_name_are_escaped` |
| 4 | Unused `_VALID_DECISION_JSON` constant and `import json` | MEDIUM | Removed dead code |
| 5 | Docstring test count understated (said ">=17", actually 20+) | MEDIUM | Updated docstring to ">=24 tests" with accurate breakdown |
| 6 | Missing boundary test for hint exactly at MAX_HINT_LENGTH | LOW | Added `test_handle_retry_hint_exactly_at_max_length_passes` |

#### Dismissed Findings

| Finding | Reason |
|---------|--------|
| RTL override in hint passes unsanitized | Defense-in-depth for registry-api, not telegram-gateway. Same as /reject. |
| Whitespace-only username renders invisible | Telegram enforces username format server-side. Shared pattern. |
| `(retry deduped)` label semantically ambiguous | Shared UX issue across all handlers, not /retry-specific. |
| Spec AC-6 hint text cosmetic mismatch | Test matches handler's usage example; specific words not a contract. |
| `approve_command.py` double-@ inconsistency | Pre-existing bug in different story, out of scope. |

#### Post-Fix Test Count

24 tests total (20 original + 4 code-review additions). All 327 telegram-gateway tests pass.

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/test_retry_command.py` — New
- `_bmad-output/implementation-artifacts/3-18-retry-command-telegram-surface.md` — Modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified
