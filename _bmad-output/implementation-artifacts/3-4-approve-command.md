# Story 3.4: /approve command (Bootstrap Minimum #2)

Status: review

## Story

As **the operator (FR7, NFR-P2)**,
I want **to send `/approve <task-id>` from Telegram and have the bot call `POST /v1/tasks/{id}/decisions` with `{"action": "approve"}`, confirm with `Approved by @<handle> at <ts>. Pushing.` within 3 s, and deduplicate Telegram retries via the same deterministic idempotency key scheme established in Story 3.3**,
so that **I can unblock a `git push` gating a `plan_ready` approval checkpoint from my phone (Bootstrap Minimum #2), the <2.5 s p95 operator-latency target (NFR-P2) is contractually verified before stories 3.16–3.18 reuse this handler pattern, and the shared `_keys.py` module is established so the task-id regex and idempotency-key derivation are not duplicated across future decision commands**.

This is the second story that calls an external HTTP service from the telegram-gateway. It **extends** `RegistryAPIClient` with a new `submit_decision(...)` method, extracts the idempotency-key helper and the UUIDv7 task-id regex into a shared `handlers/_keys.py` module, and establishes the handler pattern that stories 3.16 (`/stop`), 3.17 (`/reject`), and 3.18 (`/retry`) will mirror identically. The registry-api endpoint (`POST /v1/tasks/{id}/decisions`) is not yet implemented; Story 6.4 owns the server-side. This story ships the bot-side handler with httpx-mocked tests that are runnable today and forward-compatible with Story 6.4's eventual response shape.

## Acceptance Criteria

1. **AC-1: `DecisionResponseLocal` model in `registry_client.py`** — add to `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:
   ```python
   class DecisionResponseLocal(BaseModel):
       """Local mirror of registry-api's eventual DecisionResponse (Story 6.4 owns server-side).

       Forward-compatible shape pinned by 3.4's mocked tests; review-time validation
       must align with 6.4's POST /v1/tasks/{id}/decisions response when that endpoint lands.
       Source-of-truth: services/registry-api/src/registry_api/routes/tasks.py (Story 6.4).
       Architecture note: local redefinition keeps cross-service contract as HTTP/JSON
       (architecture.md:231) — same decision as CreateTaskResponseLocal (Story 3.3 AC-2).
       """
       model_config = ConfigDict(frozen=True)
       task_id: str
       decision_id: str           # "d-<uuidv7>" per FR7 audit trail
       action: Literal["approve", "reject", "stop", "retry"]
       decided_at: datetime
       idempotency_status: Literal["applied", "replayed"] = "applied"
   ```

2. **AC-2: `RegistryAPIClient.submit_decision(...)` method** — extend `RegistryAPIClient` in `registry_client.py`:
   ```python
   async def submit_decision(
       self,
       *,
       task_id: str,
       action: Literal["approve", "reject", "stop", "retry"],
       idempotency_key: str,
       operator_actor_id: str,
       request_id: str | None = None,
       hint: str | None = None,
   ) -> DecisionResponseLocal: ...
   ```
   POSTs `{"action": action, "hint": hint}` (omitting `hint` key when `None`) to `/v1/tasks/{task_id}/decisions` with `Idempotency-Key: <key>`, `X-Request-ID: <request_id>`, `X-Actor-Id: <operator_actor_id>` headers. On 2xx returns typed `DecisionResponseLocal`. Applies the same JSON-decode + Pydantic-parse error-wrapping established by Story 3.3 H2: parse failures raise `httpx.HTTPError` so the handler's existing catch blocks cover them.

3. **AC-3: Shared `handlers/_keys.py` module** — new file `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py`:
   ```python
   # Public helpers shared by all decision-command handlers (3.4, 3.16, 3.17, 3.18).
   # Idempotency-key derivation: same UUIDv5→UUIDv7 reshape as Story 3.3 H1.
   # Task-id validation: UUIDv7 shape with "t-" prefix per architecture naming rules.

   _TELEGRAM_NAMESPACE_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

   TASK_ID_PATTERN: re.Pattern[str] = re.compile(
       r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
   )

   def idempotency_key_from_message(message: Message) -> str:
       """Derive a deterministic UUIDv7-shaped idempotency key from (chat_id, message_id).

       Same derivation as Story 3.3 H1 — UUIDv5 from _TELEGRAM_NAMESPACE_UUID
       and seed "{chat_id}:{message_id}", reshaped so version nibble = 7 and
       variant nibble = 10xx. Satisfies registry-api's IdempotencyKeyMiddleware
       UUIDv7 regex. WARNING: do not revert to the plain-string format
       "telegram-{chat_id}-{message_id}" — that format fails the regex and
       silently creates duplicate tasks on Telegram retries (Story 3.3 H1).
       """
       ...
   ```
   `task_command.py` is refactored to import `idempotency_key_from_message` and `TASK_ID_PATTERN` from `_keys.py` (removing its local `_idempotency_key_from_message` and `_TELEGRAM_NAMESPACE_UUID`). `test_task_command.py` import paths updated accordingly. This is a coordinated cross-handler change in the same commit — document in Dev Notes.

4. **AC-4: `_extract_task_id` helper** — in `approve_command.py` (importable by future stories but private by convention):
   ```python
   def _extract_task_id(message: Message) -> str | None:
       """Parse "/approve <task-id>" and validate UUIDv7 shape.

       Returns the task-id string if valid, None otherwise.
       Rejects uppercase hex, t-less IDs, and non-UUIDv7 version nibbles.
       Stories 3.16/3.17/3.18 copy this function verbatim, importing
       TASK_ID_PATTERN from _keys.py.
       """
       parts = (message.text or "").split(None, 2)
       if len(parts) < 2:
           return None
       candidate = parts[1]
       return candidate if _keys.TASK_ID_PATTERN.match(candidate) else None
   ```
   On `None` result the handler replies: `"Usage: /approve <task-id>; example: /approve t-0192a1b5-1234-7abc-89de-f0123456789a"`. On missing arg (no text after `/approve`): `"Usage: /approve <task-id>"`.

5. **AC-5: `approve_command.py` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py`:
   ```python
   def make_approve_router() -> Router:
       """Factory — creates a fresh Router per dispatcher instance.

       Avoids "Router already attached" RuntimeError across test lifespans.
       Same judgment call as Story 3.3's make_task_router() factory pattern.
       """
       router = Router()

       @router.message(Command("approve"))
       async def handle_approve(
           message: Message,
           registry_client: RegistryAPIClient,
       ) -> None: ...

       return router
   ```
   Handler flow: extract task-id → on None reply usage and return → derive idempotency key via `_keys.idempotency_key_from_message(message)` → call `registry_client.submit_decision(...)` → on success reply with formatted confirmation → on `httpx.HTTPStatusError` call `_format_http_error(exc)` → on `httpx.HTTPError` reply unreachable message → top-level `except Exception` backstop replies `"⚠️ Internal error. Logs captured."`. Handler ALWAYS returns normally (Story 3.1 M3 fire-and-forget contract: never propagate to Telegram webhook).

6. **AC-6: Success reply text format** — exact template:
   ```
   f"✅ Approved by @{operator_handle} at {decided_at_iso}. Pushing."
   ```
   Where `operator_handle` = `message.from_user.username` if present, else `message.from_user.first_name`, else literal `"operator"`. `decided_at_iso` = `response.decided_at.isoformat()`. When `response.idempotency_status == "replayed"`, append `" (retry deduped)"` before the period. All interpolated values wrapped in `html.escape()`. `parse_mode="HTML"` via `DefaultBotProperties` already set by Story 3.1 M5 — no per-call kwarg needed.

7. **AC-7: State-error reply text format** — when registry-api returns a 4xx with RFC 7807 `detail` field (e.g., `"Task is in state 'planning'; cannot approve"`), the bot replies via `_format_http_error(exc)` from `task_command.py`:
   - `409` → duplicate idempotency key message (existing branch)
   - `422` / other 4xx → `f"⚠️ Task rejected: {html.escape(detail)}"` (existing branch; registry-api owns state-error message text; bot just renders it)
   - `401` / `403` → `"⚠️ Not authorized. Contact your administrator."` (Story 3.3 M2 branch)
   - `5xx` → `"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."`
   `_format_http_error` lives in `task_command.py` and is imported by `approve_command.py`. No duplication.

8. **AC-8: No audit-event emission from bot** — the bot does NOT emit `approval.granted` or any `task.*` event. Registry-api's eventual `POST /v1/tasks/{id}/decisions` handler (Story 6.4) emits `approval.granted` server-side; Story 6.5 owns the full audit envelope. The bot emitting a second envelope would violate the single-writer rule (FR26). Document in module docstring.

9. **AC-9: Lifespan wiring** — `services/telegram-gateway/src/telegram_gateway/app/lifespan.py`: call `make_approve_router()` and `dp.include_router(approve_router)` after the existing task router registration. No new `dp.workflow_data` keys required — `registry_client` is already injected by Story 3.3.

10. **AC-10: Co-located tests (≥12)** — `services/telegram-gateway/src/telegram_gateway/test_approve_command.py`:
    - `test_approve_handler_calls_registry` — happy path, mocked 200; assert `POST /v1/tasks/{task_id}/decisions` called with `{"action": "approve"}`.
    - `test_approve_handler_replies_with_username_and_timestamp` — assert reply contains HTML-escaped @-handle and ISO timestamp from `decided_at`.
    - `test_approve_handler_uses_uuidv7_idempotency_key` — assert `Idempotency-Key` header matches `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`.
    - `test_approve_handler_propagates_request_id` — assert `X-Request-ID` header is a bare UUIDv7.
    - `test_approve_handler_no_arg_replies_usage` — `/approve` (no arg) → reply contains `"Usage: /approve <task-id>"`.
    - `test_approve_handler_invalid_task_id_replies_usage` — `/approve foo` → reply contains usage + example.
    - `test_approve_handler_409_renders_state_error` — mock 4xx RFC 7807 `{"detail": "Task is in state 'planning'; cannot approve"}`; assert reply contains `"⚠️ Task rejected: Task is in state"`.
    - `test_approve_handler_5xx_replies_retry_message` — mock 500; assert reply starts with `"⚠️ Registry unavailable: HTTP 500"`.
    - `test_approve_handler_timeout_replies_unreachable` — mock `httpx.ReadTimeout`; assert reply contains `"Could not reach registry"`.
    - `test_approve_handler_replays_when_idempotency_status_replayed` — `idempotency_status="replayed"` in response; assert reply contains `"(retry deduped)"`.
    - `test_approve_handler_unexpected_exception_replies_internal_error` — mock raises bare `RuntimeError`; assert reply contains `"Internal error"` (H2 backstop).
    - `test_approve_handler_html_escapes_state_error_detail` — detail contains `"<script>">`; assert reply has `&lt;script&gt;` not raw `<script>` (carry-forward 3.3 H5).
    - `test_approve_handler_handles_no_username_falls_back_to_first_name` — `message.from_user.username = None`, `first_name = "Ivan"`; assert reply contains `"Ivan"`.
    - `test_approve_handler_handles_no_username_no_first_name` — both absent; assert reply contains `"operator"`.
    - `test_extract_task_id_accepts_valid_uuidv7` / `test_extract_task_id_rejects_uppercase` / `test_extract_task_id_rejects_legacy_format` — direct calls to `_extract_task_id`.
    - `test_approve_handler_latency_under_p95_budget` — `@pytest.mark.slow`; 100 sequential invocations with mocked 200 ms registry response; assert `p95 < 0.25 s` (mirrors 3.3 M5 threshold). Use `math.ceil(0.95 * n) - 1` percentile formula (mirrors 3.3 M4 fix).
    Target: ≥16 tests (spec minimum ≥12 per task brief).

11. **AC-11: Architectural gates green**:
    - `check_imports`: `approve_command.py` imports `_format_http_error` from `task_command.py` (same-service, allowed). Imports `_keys` from `handlers._keys` (same-service, allowed). No `registry_api.*` cross-service import.
    - `check_event_registry`: vacuously green — no new event types registered. Bot does not emit events.
    - `check_single_writer`: vacuously green — telegram-gateway writes nothing to SQLite.
    - `secret-hygiene-precommit`: clean — task-ids, actor-ids, and idempotency keys are non-secret.

12. **AC-12: Scope boundary** — files modifiable in this story:
    - **New (2):** `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py`, `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py`, `services/telegram-gateway/src/telegram_gateway/test_approve_command.py` *(3 new)*
    - **Modified (4):** `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (add `DecisionResponseLocal` + `submit_decision`), `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` (refactor to import from `_keys.py`), `services/telegram-gateway/src/telegram_gateway/test_task_command.py` (update import paths), `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (router registration)
    - **Not modifiable:** `.env.example` (no new env-vars; `REGISTRY_API_BASE_URL` already set by 3.3), story file, sprint-status.yaml, any `registry-api` source.

13. **AC-13: No new env-vars** — `/approve` reuses `REGISTRY_API_BASE_URL` from Story 3.3. `.env.example` requires no changes.

14. **AC-14: Regression + atomic commit** — `just test` count grows by ≥12 (target ~731, excluding `@pytest.mark.slow`). `just lint` 8/8 green. **Independently re-verify** before flipping `ready-for-dev → done` (Epic-2-retro AI #1). `just bootstrap-verify` no version churn. Single atomic commit titled exactly:
    ```
    feat(telegram-gateway): story 3.4 — /approve command (Bootstrap Minimum #2) · FR7 NFR-P2
    ```

15. **AC-15: `_keys.py` refactor does not break 3.3 tests** — after moving `_idempotency_key_from_message` to `_keys.py` and updating `task_command.py` to import from there, all 719 existing tests from Story 3.3 must still pass with zero changes to test logic. Only import paths in `task_command.py` and `test_task_command.py` change.

## Tasks / Subtasks

- [ ] **Task 1: Shared `_keys.py` module + refactor `task_command.py`** (AC: #3, #15)
  - [ ] New file `handlers/_keys.py` with `TASK_ID_PATTERN`, `_TELEGRAM_NAMESPACE_UUID`, and public `idempotency_key_from_message(message: Message) -> str`.
  - [ ] Move `_idempotency_key_from_message` logic verbatim from `task_command.py` into `_keys.idempotency_key_from_message`; remove the private function from `task_command.py`.
  - [ ] Update `task_command.py` to `from telegram_gateway.handlers._keys import idempotency_key_from_message`.
  - [ ] Update `test_task_command.py` import paths where `_idempotency_key_from_message` was imported directly (if any); assert the renamed public function still produces the same output.
  - [ ] Run `just test` to confirm all 719 existing tests pass before proceeding.

- [ ] **Task 2: `DecisionResponseLocal` + `submit_decision` in `registry_client.py`** (AC: #1, #2)
  - [ ] Add `DecisionResponseLocal` Pydantic model with `task_id`, `decision_id`, `action`, `decided_at`, `idempotency_status` fields.
  - [ ] Add `submit_decision(*, task_id, action, idempotency_key, operator_actor_id, request_id, hint)` async method.
  - [ ] POST body omits `hint` key when `hint is None` (use `{k: v for k, v in ... if v is not None}` pattern).
  - [ ] Apply same H2 body-parse error-wrapping as `create_task`: `try/except (JSONDecodeError, KeyError, ValidationError)` → re-raise as `httpx.HTTPError`.

- [ ] **Task 3: `/approve` handler** (AC: #4, #5, #6, #7, #8)
  - [ ] New file `handlers/approve_command.py` with `make_approve_router()` factory.
  - [ ] `_extract_task_id(message: Message) -> str | None` using `_keys.TASK_ID_PATTERN`.
  - [ ] `handle_approve` handler: extract → validate → idempotency key → `submit_decision` → format reply.
  - [ ] Success reply: `html.escape` applied to operator handle AND timestamp.
  - [ ] `_format_http_error` imported from `task_command.py`; no duplication.
  - [ ] Top-level `except Exception` backstop (Story 3.3 H2 pattern).
  - [ ] Module docstring: documents no-audit-event rule (AC-8, FR26, Story 6.4/6.5).

- [ ] **Task 4: Lifespan wiring** (AC: #9)
  - [ ] Import `make_approve_router` in `lifespan.py`.
  - [ ] Call `dp.include_router(make_approve_router())` after the task router include.
  - [ ] No new `dp.workflow_data` keys; `registry_client` already present.

- [ ] **Task 5: Co-located tests** (AC: #10, #11)
  - [ ] `test_approve_command.py` with ≥16 tests per AC-10 breakdown.
  - [ ] `_make_registry_client` async fixture pattern (Story 3.3 M6 fix) for teardown hygiene.
  - [ ] `@pytest.mark.slow` on latency test; `math.ceil(0.95 * n) - 1` percentile formula.
  - [ ] `test_extract_task_id_*` tests for regex accept/reject cases.
  - [ ] Verify `check_imports` gate: no `registry_api.*` import in gateway handlers.

- [ ] **Task 6: Gates + atomic commit** (AC: #11, #14)
  - [ ] `just lint` 8/8 green INDEPENDENTLY (Epic-2-retro AI #1).
  - [ ] `just test` (excluding slow) ≥731 passed.
  - [ ] `just bootstrap-verify` no version churn.
  - [ ] Single atomic commit with the AC-14 title.

## Dev Notes

### Cited requirements

- **FR7** (`prd.md:818`): "Operator can approve, reject, stop, or retry a task at any approval or blocker checkpoint, with an optional free-text hint injected into the orchestrator's next planning pass." This story implements the Telegram surface for `approve` only; the `hint` parameter is wired through `submit_decision` for forward-compatibility with Story 3.18 (`/retry hint="..."`).
- **NFR-P2** (`prd.md:905`): "Operator latency: <2.5 s p95 task-create → Telegram ack over 3×100 sequential submissions; all three batches must clear threshold." Same budget applies to `/approve` ack. Verified by AC-10 latency test (`p95 < 0.25 s` with 200 ms mock).
- **architecture.md:228** — RFC 7807 `application/problem+json`: bot consumes state errors from registry-api as structured JSON; `detail` field is the human-readable state-error string (e.g., `"Task is in state 'planning'; cannot approve"`). Registry-api owns the message text; bot renders it via `_format_http_error`.
- **architecture.md:231** — Bot → Registry-API HTTP/JSON contract: `POST /v1/tasks/{id}/decisions` is the second concrete mutation endpoint (after `POST /v1/tasks`). Same HTTP/JSON boundary — no shared Python objects across services.
- **architecture.md:374** — `application/problem+json` example with `task_id` in `extensions`; `_format_http_error` must handle `extensions.task_id` for the 409 branch.

### Registry-api endpoint dependency (Story 6.4)

`POST /v1/tasks/{id}/decisions` does NOT exist yet in `services/registry-api/src/registry_api/routes/tasks.py`. The current `_NEXT_COMMANDS` map (line ~87) shows `plan_ready: ["approve", "reject", "stop"]` — this is the state from which `/approve` is valid. Story 6.4 owns the full decisions handler, including request validation, state-machine enforcement, event emission, and the eventual `DecisionResponse` shape.

**This story's tests mock the registry-api response** using `httpx.MockTransport`. The mocked response shape (`task_id`, `decision_id`, `action`, `decided_at`) is pinned by `DecisionResponseLocal`. When Story 6.4 ships, a review-time check is required: verify that `DecisionResponseLocal` field names match Story 6.4's serialized JSON keys. A `TODO(story-6.4)` comment in `registry_client.py` marks this gate.

**End-to-end path**: this story's `/approve` handler is fully functional once Story 6.4 lands. Until then, a live call to registry-api returns 404 (no route). Tests are unaffected because they mock the transport layer.

### Shared `_keys.py` module — design decision

The UUIDv5→UUIDv7 idempotency-key derivation and the `^t-[0-9a-f]{8}-…` task-id regex are both needed by stories 3.4, 3.16, 3.17, and 3.18. Duplicating them across four handler files creates drift risk (Story 3.3 H1 established that the wrong key format silently breaks FR28 idempotency). Centralizing in `handlers/_keys.py` with a leading underscore signals it is internal to the `handlers` package (not a service-level public API) while remaining importable by all handlers within the package.

**Refactor scope in this story's commit**: `task_command.py` is updated to import `idempotency_key_from_message` from `_keys.py`; its local `_idempotency_key_from_message` and `_TELEGRAM_NAMESPACE_UUID` are removed. `test_task_command.py` import paths are updated. All 719 existing tests must pass before the `/approve` handler is added (Task 1 is a prerequisite checkpoint).

**WARNING for future stories**: do NOT reintroduce a local `_idempotency_key_from_message` in any handler file. The UUIDv5→UUIDv7 reshape must live exclusively in `_keys.py`. Story 3.3 H1 documents why the plain-string format fails registry-api's middleware.

### Idempotency key reuse pattern (Story 3.3 H1 carry-forward)

Same derivation as Story 3.3 H1:
1. `uuid.uuid5(_TELEGRAM_NAMESPACE_UUID, f"{chat_id}:{message_id}")` — namespace `6ba7b810-9dad-11d1-80b4-00c04fd430c8`, seed `"{chat_id}:{message_id}"`.
2. Reshape: `bytes[6] = (bytes[6] & 0x0F) | 0x70` (version nibble → 7), `bytes[8] = (bytes[8] & 0x3F) | 0x80` (variant nibble → 10xx).
3. Return `str(uuid.UUID(bytes=reshaped))`.

The seed is `"{chat_id}:{message_id}"` — NOT `"{chat_id}:{message_id}:{task_id}"` and NOT `"{chat_id}:{message_id}:{action}"`. The `message_id` is unique per Telegram message; the `/approve` command IS that message. Adding action to the seed would allow a future client bug to re-use the same message with a different action and bypass idempotency.

### `_format_http_error` reuse (Story 3.3 H5 carry-forward)

`_format_http_error` in `task_command.py` is imported by `approve_command.py`. Its existing branches cover all cases needed by `/approve`: 409 (idempotency), 401/403 (auth — Story 3.3 M2), 4xx (state error — registry-api `detail` rendered directly), 5xx (unavailable). Adding a `"Task is in state <X>; cannot approve"` specific branch is NOT required — registry-api owns the message; bot renders whatever `detail` contains. The BDD example in the epic spec (`"Task is in state 'planning'; cannot approve"`) is the EXPECTED message from Story 6.4's handler; 3.4's tests pin the bot's rendering via mocked 4xx responses.

HTML-escape contract: all values interpolated into the reply MUST be wrapped in `html.escape()`. This includes `detail`, `task_id_from_body`, operator username, and `decided_at` ISO string. The Story 3.3 H5 fix already applies this in `_format_http_error`; `handle_approve`'s success path must apply it independently.

### `awaiting_approval` vs `plan_ready` — state naming

The registry-api `_NEXT_COMMANDS` map (tasks.py line ~87) uses `plan_ready` as the state where `approve` is valid (not `awaiting_approval`). The epic spec and PRD use `awaiting_approval`. This discrepancy is pre-existing and owned by Story 6.4 to resolve. This story's tests use `plan_ready`-shaped mock responses; document the naming discrepancy in a comment in `approve_command.py`.

### What this story does NOT do

- Does NOT implement `POST /v1/tasks/{id}/decisions` server-side — Story 6.4 owns this.
- Does NOT emit `approval.granted` or any audit event — Story 6.5 owns the full audit envelope; FR26 prohibits the bot from writing state.
- Does NOT add `/reject` (Story 3.17), `/stop` (Story 3.16), `/retry` (Story 3.18) — same `submit_decision` client method; different routers; different valid states.
- Does NOT add FastAPI middleware on the registry-api side — Story 3.6.
- Does NOT add task-thread binding for reply routing — Story 3.9.
- Does NOT add `/status` — Story 3.14.
- Does NOT add command-injection fuzz coverage — Story 3.8.
- Does NOT add the Console CLI surface — Story 4.2 (FR12 parity comes later).
- Does NOT add per-user rate limiting on the webhook — Story 3.6.
- Does NOT add the `hint` option to `/approve` itself — FR7 mentions hint for retry path; `/approve` with a hint is an edge case not in scope for Phase 1 minimal bootstrap. The `submit_decision` method accepts `hint: str | None` for forward-compat with Story 3.18.

### Previous-story intelligence

- **Story 3.3 H1** (FR28 idempotency fix): `telegram-{chat_id}-{message_id}` string format failed registry-api's `IdempotencyKeyMiddleware` UUIDv7 regex; replaced with UUIDv5→UUIDv7 reshape. `_keys.py` centralizes this fix. Future stories must not re-introduce the plain-string format.
- **Story 3.3 H2** (backstop): `except Exception` in `handle_task` catches parse failures and unknown errors. Same pattern required in `handle_approve`.
- **Story 3.3 H4** (lifespan teardown order): `http_client.aclose` pushed before `_drain_dispatch_tasks`. Adding `approve_router` to `dp` does not change teardown order; no action needed.
- **Story 3.3 H5** (HTML injection): `html.escape()` on all interpolated values in `_format_http_error` and success replies. `approve_command.py` inherits the escape contract.
- **Story 3.3 M5** (latency test): threshold `p95 < 0.25 s` with 200 ms mock, `math.ceil(0.95 * n) - 1` formula. Mirror exactly in `test_approve_command.py`.
- **Story 3.3 M6** (fixture teardown): `_make_registry_client` as an async fixture with `async with` teardown to avoid `ResourceWarning` on unclosed `httpx.AsyncClient`.
- **Story 3.2** (AllowlistMiddleware): runs first; non-allowlisted users never reach `/approve`. No action needed in this story.
- **Story 3.1 M3** (fire-and-forget): handler must always return normally; errors reply to Telegram, never propagate to webhook.
- **Story 3.1 M5** (`DefaultBotProperties(parse_mode=ParseMode.HTML)`): already set; no per-call `parse_mode` kwarg needed in `handle_approve`.
- **Epic-2-retro AI #1**: independently run `just lint` and `just test` before marking done — do not assume CI passed.
- **Epic-2-retro AI #4**: `uv sync --all-groups --all-packages` before running tests; no version churn allowed.
- **Epic-2-retro AI #5**: no new event types in this story; `check_event_registry` gate is vacuously green.
- **Story 6.4** (future owner of `POST /v1/tasks/{id}/decisions`): the eventual endpoint will enforce state-machine rules and emit audit events. This story's tests pin only the bot-side rendering; they are forward-compatible because they mock the transport layer.

### Predicted file list

**New (3):**
- `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py`
- `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py`
- `services/telegram-gateway/src/telegram_gateway/test_approve_command.py`

**Modified (4):**
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — add `DecisionResponseLocal` + `submit_decision`.
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` — remove local `_idempotency_key_from_message`; import from `_keys`.
- `services/telegram-gateway/src/telegram_gateway/test_task_command.py` — update import paths for refactored key helper.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — `make_approve_router()` import + `dp.include_router(...)`.

### References

- `_bmad-output/planning-artifacts/epics.md:1039` — Story 3.4 spec.
- `_bmad-output/planning-artifacts/epics.md:1226` — Story 3.16 (`/stop`) — mirror candidate.
- `_bmad-output/planning-artifacts/epics.md:1240` — Story 3.17 (`/reject`) — mirror candidate.
- `_bmad-output/planning-artifacts/epics.md:1254` — Story 3.18 (`/retry`) — mirror candidate.
- `_bmad-output/planning-artifacts/epics.md:1811` — Story 6.4 (`POST /v1/tasks/{id}/decisions`) — server-side owner.
- `_bmad-output/planning-artifacts/prd.md:818` (FR7), `:905` (NFR-P2).
- `_bmad-output/planning-artifacts/architecture.md:228` (RFC 7807), `:231` (Bot→Registry HTTP/JSON), `:374` (problem+json example with extensions).
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — `RegistryAPIClient`, `CreateTaskResponseLocal`, `_TELEGRAM_NAMESPACE_UUID`.
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` — `_format_http_error`, `make_task_router`, handler pattern.
- `services/telegram-gateway/src/telegram_gateway/test_task_command.py` — `_make_registry_client` fixture, `_TELEGRAM_NAMESPACE_UUID`, test patterns.
- `services/registry-api/src/registry_api/routes/tasks.py:87` — `_NEXT_COMMANDS` map; `plan_ready` is the approval-valid state.
- `_bmad-output/implementation-artifacts/3-3-task-command.md` — full Story 3.3 record including H1/H2/H4/H5/M2/M5/M6 review-fix details.
- `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-27.md` — AI #1 (trust-but-verify lint), AI #4 (uv sync flags), AI #5 (autouse re-register).

## Dev Agent Record

### Agent Model Used

_to be filled by dev agent_

### Debug Log References

_to be filled by dev agent_

### Completion Notes List

_to be filled by dev agent_

### File List

_to be filled by dev agent_
