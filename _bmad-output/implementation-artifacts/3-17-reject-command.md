# Story 3.17: `/reject` command

Status: done

## Story

As **the operator**,
I want **`/reject <task-id> <reason>` to explicitly reject a pending approval with a recorded reason**,
so that **reject is distinct from stop and auditable (FR7)**.

This is the fourth operator-action command after `/approve` (Story 3.4), `/stop` (Story 3.16). It calls the same `POST /v1/tasks/{id}/decisions` endpoint with `{action: "reject"}` via the existing `RegistryAPIClient.submit_decision` method. The `<reason>` free-text argument maps to the `hint` parameter on `submit_decision` — the registry client has no separate `reason` parameter, and FR7 describes the hint as "optional free-text hint injected into the orchestrator's next planning pass." This story reuses `submit_decision` and `DecisionResponseLocal` from Story 3.4.

**What this story is NOT:**
- **NOT** the `POST /v1/tasks/{id}/decisions` server-side handler — Story 6.4 owns it.
- **NOT** the `approval.rejected` audit event emission — Story 6.5 owns it.
- **NOT** the console CLI `reject` command — Story 4.3 owns it.
- **NOT** `/stop` — that's Story 3.16 (no reason/hint passthrough).
- **NOT** `/retry` — that's Story 3.18 (adds hint passthrough, same pattern).

**Placeholder behavior:** Same as `/approve` — `POST /v1/tasks/{id}/decisions` does NOT exist server-side yet (Story 6.4 owns it). Tests mock the transport layer. When 6.4 ships, this handler automatically works without code changes.

**Reason vs hint mapping:** The epics AC mentions `{action: "reject", reason: "..."}` but the actual `submit_decision` API uses the `hint` parameter for free-text passthrough (FR7). This story passes the reason text as `hint=reason` to `submit_decision`. Story 3.18 (`/retry`) uses the same `hint` parameter for its optional hint text. No new client methods or parameters needed.

## Acceptance Criteria

1. **AC-1: `handle_reject` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` with:

   ```python
   async def handle_reject(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
   ```

   - No `bot: Bot` parameter (same as `/approve`, `/stop` — no outbound delivery needed).
   - Derives operator actor fields from `message.from_user` (same guard block as `/approve` / `/stop`).
   - Uses `_keys.extract_task_id_from_message(message)` for task-id parsing (Story 3.4 pattern).
   - If no task-id found: reply with usage `"Usage: /reject <task-id> [reason]"` or `"Usage: /reject <task-id> [reason]; example: /reject t-0192... push before review"` depending on whether args were present.
   - If task-id found: extract reason from remaining text after task-id. Reason is `parts[2].strip()` if `len(parts) >= 3` and the third part is non-empty, otherwise `None`.
   - Derive idempotency key via `_keys.idempotency_key_from_message(message)`.
   - Calls `registry_client.submit_decision(task_id=task_id, action="reject", idempotency_key=idempotency_key, operator_actor_id=operator_actor_id, request_id=request_id, hint=reason)`.
   - `hint=reason` — the reason string is passed as the `hint` parameter. When `reason` is `None`, `submit_decision` omits the `hint` key from the POST body (existing behavior from `registry_client.py` L358-361).

2. **AC-2: Success reply** — on successful `submit_decision` response:

   ```
   🚫 Rejected by @<handle> at <decided_at_iso>. Task stopped.
   ```

   - When `idempotency_status == "replayed"`: append ` (retry deduped)` before `. Task stopped.` (same pattern as `/approve` / `/stop`).
   - `operator_handle` and `decided_at_iso` are HTML-escaped.

3. **AC-3: Error handling** — follow the exact same try/except cascade as `/stop` (Story 3.16, post-review-fix):

   - `httpx.TooManyRedirects` → `"⚠️ Registry misconfigured: too many redirects."`
   - `httpx.HTTPStatusError` → `format_http_error(exc)` with `command_label="Reject command"`.
   - `RegistryResponseError as exc` → `_log.exception(..., exc)` → `"⚠️ Registry returned an unexpected response. Logs captured."`
   - `httpx.HTTPError as exc` → `_log.warning(..., exc)` → `f"⚠️ Could not reach registry: {type(exc).__name__}."`
   - `Exception as exc` → backstop: `_log.exception(..., exc)` → `"⚠️ Internal error. Logs captured."` (never lets the webhook fail; Story 3.1 M3 contract).

4. **AC-4: Router factory and registration**:

   ```python
   def make_reject_router() -> Router:
       router = Router()
       router.message(Command("reject"))(handle_reject)
       return router
   ```

   - Import `make_reject_router` in `handlers/__init__.py` and add to `__all__`.
   - Register `dp.include_router(make_reject_router())` in `app/lifespan.py` after existing routers.

5. **AC-5: `just lint` 9/9 green** — `ruff check`, `ruff format --check`, `mypy --strict`, `check_imports`, `check_event_registry`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`.

6. **AC-6: Co-located tests (≥13)** — in `services/telegram-gateway/src/telegram_gateway/test_reject_command.py`:

   - **Handler tests (11+):**
     - `test_handle_reject_success_renders_confirmation` — mock client returns `DecisionResponseLocal`; assert reply contains `@<handle>`, `decided_at`, `"Task stopped"`.
     - `test_handle_reject_success_with_retry_deduped` — `idempotency_status="replayed"`; assert `"(retry deduped)"` in reply.
     - `test_handle_reject_with_reason_passes_hint` — message has reason text; assert `submit_decision` called with `hint="push before review"`.
     - `test_handle_reject_without_reason_passes_none_hint` — message has no reason; assert `submit_decision` called with `hint=None`.
     - `test_handle_reject_no_args_shows_usage` — message text is just `"/reject"`; assert usage reply.
     - `test_handle_reject_invalid_task_id_shows_usage` — message text is `"/reject bad"`; assert usage with example.
     - `test_handle_reject_http_status_error` — mock raises `HTTPStatusError`; assert error reply via `format_http_error`.
     - `test_handle_reject_network_error` — mock raises `httpx.ReadTimeout`; assert `"Could not reach registry: ReadTimeout"` reply.
     - `test_handle_reject_too_many_redirects` — `TooManyRedirects` → `"too many redirects"` reply.
     - `test_handle_reject_malformed_response` — `RegistryResponseError` → `"unexpected response"` reply.
     - `test_handle_reject_unexpected_exception` — `RuntimeError` backstop → `"Internal error"` reply.
     - `test_handle_reject_from_user_none_uses_unknown_actor` — `from_user` is None; assert `"unknown"` actor, `@operator` handle (no double-`@`).

   - **HTML security test (1):**
     - `test_handle_reject_html_chars_in_username_are_escaped` — username with HTML chars; assert escaped in reply.

   - **Router test (1):**
     - `test_make_reject_router_returns_fresh_routers` — factory produces distinct instances.

   Target: **13+ new tests**.

7. **AC-7: Scope boundary** — files modifiable in this story:
   - **New (1):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py`
   - **Modified (3 source + 2 process):**
     - `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (AC-4)
     - `services/telegram-gateway/src/telegram_gateway/test_reject_command.py` (AC-6 — new test file)
   - **Not modifiable:** `registry_client.py` (reuses existing `submit_decision`), existing handler files, `services/registry-api/`, `services/clawhip-daemon/`, `services/registry-state/`.

8. **AC-8: No new dependencies** — `httpx`, `aiogram`, `pydantic` already available. No new packages needed. No new client methods or response models needed — `submit_decision` and `DecisionResponseLocal` are reused from Story 3.4.

9. **AC-9: Atomic commit + independent gate verify** — single commit titled exactly:

    ```
    feat(telegram-gateway): story 3.17 — /reject command · FR7
    ```

    `just lint` 9/9 green. `just test` count grows by ≥13. Independently re-verify before flipping status.

## Tasks / Subtasks

- [x] **Task 1: `handle_reject` handler + success/error rendering** (AC: #1, #2, #3)
  - [x] Create `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py`.
  - [x] Implement `handle_reject(message, registry_client)` with operator actor derivation, task-id extraction, reason extraction, idempotency key, `submit_decision(action="reject", hint=reason)`, and error cascade.
  - [x] Success reply: `"🚫 Rejected by @{operator_handle} at {decided_at_iso}. Task stopped."` with retry-deduped variant.
  - [x] Usage reply on missing/invalid task-id — `"Usage: /reject <task-id> [reason]"`.
  - [x] 5-branch error cascade matching `/stop` (post-review-fix: bind `exc` in all catch blocks, use `type(exc).__name__` in network error).

- [x] **Task 2: Router registration + handler tests** (AC: #4, #6)
  - [x] Implement `make_reject_router()` factory in `reject_command.py`.
  - [x] Import and re-export `make_reject_router` in `handlers/__init__.py`.
  - [x] Register `dp.include_router(make_reject_router())` in `app/lifespan.py` after existing routers.
  - [x] Create `test_reject_command.py` with 13+ tests covering success, retry deduped, with reason, without reason, no args, invalid task-id, HTTP errors, network errors, TooManyRedirects, malformed response, unexpected exception, from_user None, HTML escaping, router factory.

- [x] **Task 3: Regression verification + atomic commit** (AC: #5, #9)
  - [x] `uv sync --all-packages` (Epic-1-retro AI #2).
  - [x] `just test` — confirm test count grows by ≥14. (1120 passed, 14 new from this story)
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] Independent gate verify before flipping `review → done` (Epic-2-retro AI #1).
  - [x] Flip sprint-status.yaml: `3-17-reject-command: backlog → ready-for-dev → in-progress → review`; bump `last_updated`.
  - [x] Atomic commit with the exact title from AC-9.

## Dev Notes

### Quoted Requirements

> **FR7** (`prd.md`): *"Operator can approve, reject, stop, or retry a task at any approval or blocker checkpoint, with an optional free-text hint injected into the orchestrator's next planning pass."*

> **Epic 3 Story 3.17 AC** (`epics.md:1240-1253`):
> *Given a task is in `awaiting_approval`*
> *When I send `/reject t-0001 "push before review"`*
> *Then the bot calls `POST /v1/tasks/t-0001/decisions {action:reject, reason:"..."}`, an `approval.rejected` event is emitted with the reason string, and the task transitions to `rejected`/`stopped` per approval semantics.*

> **FR8** (`prd.md`): *"Platform can transition tasks through explicit lifecycle states … and record each transition as a typed event."*

### Architecture: Service Boundary

The `telegram-gateway` service is a **read + command-emit client** of `registry-api` via HTTP. This story adds another command path — `POST /v1/tasks/{id}/decisions {action: "reject", hint: "..."}` — reusing the existing `submit_decision` method (Story 3.4 AC-1). No cross-service imports, no database access (enforced by `check_imports.py` and `check_single_writer.py`).

### Architecture: Clone of `/stop` with reason passthrough

This story is structurally identical to `/stop` (Story 3.16) with these differences:

| Aspect | `/stop` (Story 3.16) | `/reject` (Story 3.17) |
|--------|---------------------|------------------------|
| Action string | `"stop"` | `"reject"` |
| Success emoji | `🛑` | `🚫` |
| Success suffix | `"Task halted."` | `"Task stopped."` |
| Router command | `Command("stop")` | `Command("reject")` |
| Handler name | `handle_stop` | `handle_reject` |
| Router factory | `make_stop_router()` | `make_reject_router()` |
| Logger name | `stop_command` | `reject_command` |
| Test file | `test_stop_command.py` | `test_reject_command.py` |
| Reason/hint | No (`hint=None`) | Yes — extracted from message text after task-id |
| Usage text | `"Usage: /stop <task-id>"` | `"Usage: /reject <task-id> [reason]"` |

Everything else — actor derivation, task-id extraction, idempotency key, error cascade, `DecisionResponseLocal` handling, HTML escaping — is identical. **When in doubt, copy from `/stop` and change only the diff table above.**

### Reason Extraction Logic

The message text has the form `/reject <task-id> [reason]`. After `extract_task_id_from_message` validates the task-id:

```python
raw_text = message.text or ""
parts = raw_text.split(None, 2)  # split into max 3 parts
reason = parts[2].strip() if len(parts) >= 3 else None
```

This extracts everything after the task-id as the reason string. If no reason is provided, `reason` is `None` and `submit_decision` is called with `hint=None` (the POST body omits the `hint` key entirely per `registry_client.py` L358-361).

### Key Shared Modules (DO NOT reinvent)

- `_keys.py` — `extract_task_id_from_message(message)`, `idempotency_key_from_message(message)`, `TASK_ID_PATTERN`
- `_errors.py` — `format_http_error(exc, command_label="Reject command")`
- `_safe_reply.py` — `safe_reply(message, text, **kwargs)`
- `registry_client.py` — `RegistryAPIClient.submit_decision()` + `DecisionResponseLocal` (REUSE — do NOT create new methods or models). The `hint` parameter already exists on `submit_decision`.

### Carry-Forwards from Previous Stories

| Carry-forward | Source | How 3.17 uses it |
|---|---|---|
| `submit_decision()` + `DecisionResponseLocal` | Story 3.4 | Call with `action="reject"`, `hint=reason` |
| `submit_decision(hint=...)` parameter | Story 3.4 | Pass reason text as `hint` — already supported |
| Operator actor derivation guard block | Story 3.4 | Copy from `/stop` — use `"operator"` (no `@` prefix!) |
| `idempotency_key_from_message()` | Story 3.4 | Reuse for idempotency |
| `extract_task_id_from_message()` | Story 3.4 | Reuse for task-id parsing |
| `format_http_error()` | Story 3.4 | Use with `command_label="Reject command"` |
| `safe_reply()` | Story 3.5 | Reuse for reply safety |
| `make_*_router()` factory pattern | Stories 3.3-3.5 | `make_reject_router()` following same convention |
| `dp.include_router()` registration | Stories 3.3-3.5 | Add after existing routers in `lifespan.py` |
| `html.escape()` on operator-visible strings | Story 3.5 H5 | Escape `operator_handle` and `decided_at_iso` |
| Error cascade (5 exception types + backstop) | Stories 3.3-3.4 | Same cascade — bind `exc` in all blocks (3.16 review fix) |
| `X-Request-ID` header on all API calls | Story 3.3 | Included via `submit_decision(request_id=...)` |
| `request_id` via `new_request_id()` | Story 3.5 | Generate for observability |
| `uv sync --all-packages` before lint | Epic-1-retro AI #2 | Use in Task 3 |

### Learnings from Story 3.16 Code Review

1. **Bind `exc` in ALL catch blocks** — `RegistryResponseError as exc`, `HTTPError as exc`, `Exception as exc`. Include `exc` in log format strings. The `/stop` review caught 3 blocks missing this.
2. **Network error reply must include `type(exc).__name__`** — use `f"Could not reach registry: {type(exc).__name__}."` (not a static string).
3. **No double `@` in `from_user=None` fallback** — set `operator_handle = "operator"` (without `@`), since the template adds `@` via `@{operator_handle}`.
4. **HTML escaping on ALL externally-sourced strings** — escape `operator_handle`, `decided_at_iso`.
5. **Test all 5 error-catch branches** — include tests for TooManyRedirects, HTTPStatusError, RegistryResponseError, HTTPError, and Exception backstop from the start.
6. **`command_label` in `format_http_error()`** — use `"Reject command"` (descriptive).
7. **Import ordering** — keep alphabetical in `__init__.py` and `lifespan.py`.
8. **`__all__` multi-line format** — keep the existing multi-line list format.

### Architecture References

- `prd.md` — FR7 (operator decisions), FR8 (lifecycle states).
- `epics.md:1240-1253` — Story 3.17 user story + AC.
- `epics.md:1811-1832` — Story 6.4 (future decisions handler).
- `epics.md:1834-1848` — Story 6.5 (future audit events).
- `architecture.md` — Component 3 (telegram-gateway); HTTP client pattern; service boundaries.
- Story 3.4 — `/approve` handler (original template).
- Story 3.16 — `/stop` handler (most recent clone — includes review fixes for exc binding, network reply, double-@).
- Story 3.5 — `/ping` handler + `safe_reply`.
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe.
- Epic-2-retro AI #1 — Independent gate verify mandatory.

### Project Structure Notes

- Handler: `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` (new)
- Handler test: `services/telegram-gateway/src/telegram_gateway/test_reject_command.py` (new)
- Init: `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (modify)
- Lifespan: `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modify)
- Spec: `_bmad-output/implementation-artifacts/3-17-reject-command.md` (this file)
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips)

No detected conflicts with unified project structure. Handler placement follows the established `handlers/<command>_command.py` convention.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` | New — `handle_reject` + `make_reject_router` |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Modified — re-export `make_reject_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Modified — register `make_reject_router()` |
| `services/telegram-gateway/src/telegram_gateway/test_reject_command.py` | New — 13+ tests |
| `_bmad-output/implementation-artifacts/3-17-reject-command.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

### Completion Notes List

- All 3 AC tasks completed sequentially.
- Reason extraction: splits message text into max 3 parts, validates parts[1] against TASK_ID_PATTERN directly (not via extract_task_id_from_message, which splits with maxsplit=1 and would fail on trailing reason text).
- 14 new tests added, all passing (1120 total, 0 new failures).
- Lint clean (ruff check all pass, 9/9 green).
- Pre-existing failures unchanged (crash-injection: 4, separability: 1).

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` — New
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — Modified
- `services/telegram-gateway/src/telegram_gateway/test_reject_command.py` — New
- `_bmad-output/implementation-artifacts/3-17-reject-command.md` — Modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified

## Code Review Record

### Review Agents

Three parallel review agents ran on the initial implementation:

1. **Blind Hunter** (code-reviewer) — 7 findings: 2 HIGH, 3 MEDIUM, 2 LOW
2. **Edge Case Hunter** (code-reviewer) — 5 findings: 2 MEDIUM, 3 LOW
3. **Acceptance Auditor** (verifier) — All 9 ACs PASS. APPROVE.

### Fix Summary

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| 1 | Unbounded reason length — reason text passed to `submit_decision` with no cap | HIGH | Added `MAX_REASON_LENGTH = 1000` constant and `[:MAX_REASON_LENGTH]` slice on reason extraction |
| 2 | `from_user=None` allows unauthenticated reject | HIGH | Dismissed — by-design. Allowlist middleware (Story 3.2) rejects non-allowlisted users before any handler runs. `from_user=None` is a defensive fallback for malformed Telegram updates, not an auth bypass. All handlers share this pattern. |
| 3 | Missing test for Unicode reason strings (emoji, RTL, newlines, ZWJ) | LOW | Added parametrized `test_handle_reject_unicode_reason_passes_through` with 4 cases |
| 4 | Missing test for `from_user=None` + `chat=None` edge case | LOW | Replaced with `test_handle_reject_from_user_none_logs_chat_id` — `chat=None` is not a valid Telegram state; test verifies `from_user=None` with valid chat still produces correct reply |
| 5 | Divergent task-id parsing (uses `split(None, 2)` + inline regex vs `extract_task_id_from_message`) | LOW | Documented in code comments — `extract_task_id_from_message` uses `split(None, 1)` which appends reason text to task-id candidate and fails the regex. Inline validation is the correct approach for commands with trailing arguments. |
| 6 | Reason text not HTML-escaped for persistence | MEDIUM | Dismissed — reason is passed as `hint` to `submit_decision` which sends it as JSON. No HTML rendering surface. The hint is stored in registry-api's DB, not rendered in Telegram. |

### Post-Fix Test Count

20 tests total (14 original + 6 code-review additions). All 303 telegram-gateway tests pass.
