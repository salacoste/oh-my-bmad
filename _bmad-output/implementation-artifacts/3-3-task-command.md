# Story 3.3: /task command (Bootstrap Minimum #1)

Status: review

## Story

As **the operator (FR1, FR28, NFR-P2)**,
I want **to send `/task <description>` from Telegram and have the bot create the task through `POST /v1/tasks`, reply with the new `t-…` id within 3 s, and deduplicate Telegram retries via a deterministic idempotency key derived from `(chat_id, message_id)`**,
so that **I can kick off autonomous work from my phone (Bootstrap Minimum #1), idempotency is handled at the registry-api layer (FR28), and the <2.5 s p95 operator-latency target (NFR-P2) is contractually verified before Stories 3.4 and 3.5 reuse this handler pattern**.

This is the first story that calls an external HTTP service from the telegram-gateway. It establishes the `RegistryAPIClient` abstraction, the long-lived `httpx.AsyncClient` lifespan pattern (Story 3.1 H4), and the `_safe_emit`-style error-wrapping contract (Story 3.2 M2/M4) that all subsequent command handlers inherit.

## Acceptance Criteria

1. **AC-1: `RegistryAPIClient` typed httpx client** — new file `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:
   ```python
   class RegistryAPIClient:
       """httpx-based client for registry-api endpoints used by Telegram handlers.

       Wraps POST /v1/tasks. Constructor takes a pre-built AsyncClient (lifespan-owned,
       reusable across requests — Story 3.1 H4 cache-once pattern).
       """
       def __init__(self, *, base_url: str, http_client: httpx.AsyncClient) -> None: ...

       async def create_task(
           self,
           *,
           description: str,
           idempotency_key: str,
           operator_actor_id: str,
           request_id: str | None = None,
       ) -> "CreateTaskResponseLocal": ...
   ```
   `create_task` POSTs `{"title": description}` to `/v1/tasks` with `Idempotency-Key: <key>` and `X-Request-ID: <request_id>` headers. On 201 it returns a typed local response model (see AC-12). On non-2xx it raises `httpx.HTTPStatusError` with the response attached so the caller can inspect status and RFC 7807 body.

2. **AC-2: Local response model in `registry_client.py`** — define `CreateTaskResponseLocal` locally rather than importing from `registry_api.routes.tasks`:
   ```python
   class CreateTaskResponseLocal(BaseModel):
       """Local mirror of registry-api's CreateTaskResponse (Story 2.9).

       Redefined here to avoid a services→services import (architecture.md:231 keeps
       the cross-service contract as HTTP/JSON, not shared Python objects).
       Source-of-truth for field layout: services/registry-api/src/registry_api/routes/tasks.py
       class CreateTaskResponse. Review-time validation: field names must match registry-api's
       serialised JSON keys. TODO(architecture): migrate shared models to packages/events/ if
       the cross-service model count grows beyond 3.
       """
       model_config = ConfigDict(frozen=True)
       task_id: str
       event_id: str
       created_at: datetime
   ```
   **Decision: local redefinition, not cross-service import.** Avoids `# noqa: IMP001` on a model that has no architectural reason to live in `registry_api`; keeps the transport boundary clean. A doc-comment points at the registry-api source-of-truth for review-time validation.

3. **AC-3: `TelegramSettings.registry_api_base_url`** — extend `services/telegram-gateway/src/telegram_gateway/app/config.py`:
   ```python
   registry_api_base_url: HttpUrl = Field(
       default="http://registry-api:8080",
       validation_alias="REGISTRY_API_BASE_URL",
       description=(
           "Base URL for registry-api HTTP calls. Default points at the "
           "docker-compose service name. Override for non-compose deployments."
       ),
   )
   ```
   Field validation: `http` scheme permitted (internal-network HTTP is fine; no HTTPS requirement for intra-compose traffic). Trailing slash is normalised away if present (httpx joins correctly regardless, but document the convention).

4. **AC-4: `httpx.AsyncClient` in lifespan** — `services/telegram-gateway/src/telegram_gateway/app/lifespan.py`, after the `set_webhook` call succeeds and BEFORE `app.state.*` assignments:
   ```python
   http_client = httpx.AsyncClient(
       base_url=str(audited.registry_api_base_url),
       timeout=httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0),
   )
   stack.push_async_callback(http_client.aclose)
   registry_client = RegistryAPIClient(
       base_url=str(audited.registry_api_base_url),
       http_client=http_client,
   )
   app.state.http_client = http_client
   app.state.registry_client = registry_client
   ```
   The `AsyncClient` is constructed ONCE and reused across all handler invocations (Story 3.1 H4 pattern — never construct per-request). `stack.push_async_callback(http_client.aclose)` registers clean shutdown in the `AsyncExitStack` LIFO order before `app.state.*` is assigned, so a failed `set_webhook` never leaves a dangling client.

5. **AC-5: `/task` command handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py`:
   ```python
   router = Router()

   @router.message(Command("task"))
   async def handle_task(message: Message, bot: Bot, registry_client: RegistryAPIClient) -> None:
       description = (message.text or "").removeprefix("/task").strip()
       if not description:
           await message.reply("Usage: /task <description>")
           return
       idempotency_key = _idempotency_key_from_message(message)
       request_id = new_request_id()
       try:
           response = await registry_client.create_task(
               description=description,
               idempotency_key=idempotency_key,
               operator_actor_id=str(message.from_user.id),
               request_id=request_id,
           )
       except httpx.HTTPStatusError as exc:
           reply = _format_http_error(exc)
           await message.reply(reply)
           return
       except httpx.HTTPError as exc:
           await message.reply(f"⚠️ Could not reach registry: {type(exc).__name__}.")
           return
       status_suffix = " (retry deduped)" if _is_replay(registry_client, response) else ""
       await message.reply(
           f"Task <code>{response.task_id}</code> created. "
           f"Planning. Events on thread.{status_suffix}"
       )
   ```
   The `Router` is registered on `dp` in `lifespan.py` after the dispatcher is built. `bot` and `registry_client` are injected via aiogram's data-injection mechanism: the lifespan passes them via `dp.workflow_data.update({"bot": bot, "registry_client": registry_client})`.

6. **AC-6: Idempotency-key derivation helper** — in `task_command.py`:
   ```python
   def _idempotency_key_from_message(message: Message) -> str:
       """Derive a deterministic idempotency key from Telegram (chat_id, message_id).

       Format: "telegram-{chat_id}-{message_id}".
       Telegram retries deliver the same message_id for the same physical message,
       so registry-api (FR28) will deduplicate duplicate deliveries and return the
       same task_id. The key is opaque to registry-api but deterministic for the bot.
       Future commands (/approve 3.4, /retry 3.18) follow the same pattern.
       """
       return f"telegram-{message.chat.id}-{message.message_id}"
   ```
   This helper is a standalone function (not a method) for testability. Tests can call it directly without constructing a full handler context.

7. **AC-7: Reply text format and `parse_mode` decision** — exact reply on success: `"Task <code>{task_id}</code> created. Planning. Events on thread."`. When the registry-api response header `X-Idempotency-Status: replayed` is present (or inferable from the response), append `" (retry deduped)"`.

   **Decision: HTML over MarkdownV2.** `DefaultBotProperties(parse_mode=ParseMode.HTML)` is already set by Story 3.1's lifespan (review-fix M5). HTML entities (`<code>`) are predictable and do not require escaping task-ids or free text for corner-case characters. MarkdownV2 would require escaping `.-_~` in UUIDv7 task-ids and any operator-supplied description echoed in error messages, creating injection risk.

   **`_is_replay` detection**: `RegistryAPIClient.create_task` stores the raw httpx `Response.headers` on the returned model or exposes them via a companion attribute. A simpler approach: the `RegistryAPIClient` inspects `X-Idempotency-Status` and attaches an `idempotency_status: Literal["applied", "replayed"]` field to `CreateTaskResponseLocal`. The handler reads this to decide whether to append `" (retry deduped)"`.

   **Idempotency key strategy (review-fix H1)**: The key is a UUIDv5 derived from a fixed Telegram-service namespace UUID (`6ba7b810-9dad-11d1-80b4-00c04fd430c8`) and the seed string `"{chat_id}:{message_id}"`, then reshaped so the version nibble reads `7` and the variant nibble reads `10xx`. This satisfies registry-api's `IdempotencyKeyMiddleware` UUIDv7 regex: `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`. The original `telegram-{chat_id}-{message_id}` string format failed this regex, causing the middleware to regenerate a fresh key per retry and creating duplicate tasks. The namespace UUID encodes the `tg:` service discriminator (L4/L6).

8. **AC-8: RFC 7807 error surface in `_format_http_error`** — handler differentiates HTTP error classes:
   - `409` (idempotency collision from a concurrent bot instance using the same key via a different path) → `"⚠️ Duplicate idempotency key — another instance already submitted this message. Stored result: {task_id_from_body_if_available}."`
   - `4xx` other (validation, 422 Pydantic, etc.) → parse RFC 7807 `detail` field: `"⚠️ Task rejected: {detail}"`. Falls back to `"⚠️ Task rejected: HTTP {status}"` if body is not valid JSON or lacks `detail`.
   - `5xx` → `"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."`
   In ALL cases the handler returns normally — Telegram receives a 200 ACK from the webhook endpoint (Story 3.1 M3 fire-and-forget contract: handler errors must not propagate to Telegram and cause a retry storm).

9. **AC-9: NFR-P2 latency test** — `services/telegram-gateway/src/telegram_gateway/test_task_command.py`:
   `test_task_handler_latency_under_p95_budget` runs 100 sequential invocations against a mocked registry-api responding in 200 ms (via `pytest-httpx` or equivalent). Asserts `p95 < 1.0 s` (1.5 s headroom before the 2.5 s NFR-P2 threshold; the 200 ms mock represents realistic registry-api latency). Uses `time.perf_counter()` over the handler body (not over network); mirrors Story 3.2 AC-9 `test_middleware_p50_latency_under_1ms` pattern. Mark with `@pytest.mark.slow` so CI default run (`pytest -m "not slow"`) excludes it; NFR-P2 verification runs on the `just benchmark` recipe.

10. **AC-10: Idempotency replay behavior** — when the same `(chat_id, message_id)` is delivered twice by Telegram, both calls return the same `task_id` (registry-api FR28 deduplication). The bot does NOT maintain its own memory of "already replied for this message_id" — it submits both deliveries and relies on the `X-Idempotency-Status: replayed` response header. The first delivery replies `"Task … created. Planning. Events on thread."` and the second appends `" (retry deduped)"`. **No duplicate task is ever created** — that invariant is owned by registry-api (FR28 / NFR-R4), not the bot. Document this in the handler's module docstring to avoid double-dedup logic creeping in.

11. **AC-11: `task.created` audit non-emission** — the bot does NOT emit a `task.created` event. Registry-api emits it internally when `POST /v1/tasks` succeeds (Story 2.9). The bot emitting a second envelope would violate the single-writer rule (FR26) and create a duplicate audit signal. Document this explicitly in `task_command.py`'s module docstring with a reference to Story 2.9 and FR26.

12. **AC-12: Co-located tests (≥10)** — `services/telegram-gateway/src/telegram_gateway/test_task_command.py`:
    - `test_task_handler_replies_with_task_id` — mock registry-api 201; `/task hello`; assert reply contains `task_id`.
    - `test_task_handler_uses_message_id_for_idempotency_key` — mock registry-api; assert outbound request had `Idempotency-Key: telegram-<chat>-<msg>`.
    - `test_task_handler_propagates_request_id` — assert outbound `X-Request-ID` header is set and matches a bare UUIDv7 pattern.
    - `test_task_handler_empty_description_replies_usage` — `/task` (no args); assert reply is exactly `"Usage: /task <description>"`.
    - `test_task_handler_whitespace_only_description_replies_usage` — `/task   ` (spaces only after strip); assert usage reply.
    - `test_task_handler_idempotency_replayed_appends_suffix` — registry-api returns 201 with `X-Idempotency-Status: replayed`; assert reply contains `"(retry deduped)"`.
    - `test_task_handler_4xx_replies_rejected_message` — mock 422 with RFC 7807 body; assert reply starts with `"⚠️ Task rejected:"`.
    - `test_task_handler_5xx_replies_retry_message` — mock 500; assert reply matches `"⚠️ Registry unavailable: HTTP 500"`.
    - `test_task_handler_timeout_replies_unreachable` — mock `httpx.ReadTimeout`; assert reply matches `"⚠️ Could not reach registry: ReadTimeout"`.
    - `test_task_handler_latency_under_p95_budget` — 100 sequential calls with mocked 200 ms registry; assert p95 < 1.0 s. Marked `@pytest.mark.slow`.
    - `test_registry_client_reuses_http_session` — assert the `http_client` passed to `RegistryAPIClient` is not re-instantiated across two `create_task` calls (identity check on the client object).
    - `test_idempotency_key_from_message_format` — call `_idempotency_key_from_message` directly; assert result is `"telegram-{chat_id}-{message_id}"`.
    Target: ≥12 tests (exceeds AC minimum of ≥10).

13. **AC-13: Architectural gates green**:
    - `check_imports`: `telegram-gateway` → `events` (pre-existing canonical edge). New import: `from events.ids import new_request_id` — clean. `registry_client.py` does NOT import from `registry_api.*` (local model redefinition per AC-2). No `# noqa: IMP001` needed.
    - `check_event_registry`: vacuously green (no new event types).
    - `check_single_writer`: vacuously green (telegram-gateway writes nothing to SQLite).
    - `secret-hygiene-precommit`: clean (no bot tokens or PII in test fixtures; idempotency keys are non-secret).

14. **AC-14: `.env.example` addition**:
    ```
    # Registry API base URL for bot → registry HTTP calls.
    # Default: docker-compose service name (internal network, HTTP).
    # Override for non-compose deployments (e.g., REGISTRY_API_BASE_URL=http://localhost:8080).
    # Consumed by: telegram-gateway. FR1 / FR28.
    REGISTRY_API_BASE_URL=http://registry-api:8080
    ```

15. **AC-15: Regression + atomic commit** — `just test` count grows by ≥10 (target ~691, excluding `@pytest.mark.slow`). `just lint` 8/8 green; **independently re-verify** before flipping `review → done` (Epic-2-retro AI #1). `just bootstrap-verify` shows no version churn. Single atomic commit titled exactly:
    ```
    feat(telegram-gateway): story 3.3 — /task command (Bootstrap Minimum #1) · FR1 FR28 NFR-P2
    ```

## Tasks / Subtasks

- [x] **Task 1: `RegistryAPIClient` + local response model** (AC: #1, #2)
  - [x] New file `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`.
  - [x] Define `CreateTaskResponseLocal` with `task_id`, `event_id`, `created_at`, `idempotency_status` fields.
  - [x] `RegistryAPIClient.__init__` accepts `base_url: str` and `http_client: httpx.AsyncClient`.
  - [x] `create_task` POSTs `/v1/tasks`, sets `Idempotency-Key` + `X-Request-ID` headers, returns typed model.
  - [x] Parses `X-Idempotency-Status` response header into `idempotency_status` field.

- [x] **Task 2: `TelegramSettings` extension + lifespan wiring** (AC: #3, #4)
  - [x] Add `registry_api_base_url: HttpUrl` field to `config.py` with docker-compose default.
  - [x] Extend `lifespan.py`: construct `httpx.AsyncClient`, push `aclose` callback, build `RegistryAPIClient`, store both on `app.state`.
  - [x] Register `task_command.router` on `dp` and inject `registry_client` via `dp.workflow_data`.
  - [x] Append `REGISTRY_API_BASE_URL=...` line to `.env.example`.

- [x] **Task 3: `/task` handler + helpers** (AC: #5, #6, #7, #8, #10, #11)
  - [x] New file `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py`.
  - [x] `_idempotency_key_from_message` helper function.
  - [x] `_format_http_error` function covering 409 / 4xx / 5xx cases, RFC 7807 `detail` parsing.
  - [x] Handler replies with HTML-mode `<code>` tag; appends `" (retry deduped)"` on replay.
  - [x] Handler ALWAYS returns normally (never propagates to Telegram webhook — Story 3.1 M3 pattern).
  - [x] Module docstring documents: no `task.created` emission from bot (AC-11), idempotency ownership (AC-10).

- [x] **Task 4: Populate `handlers/__init__.py`** (AC: #5)
  - [x] Export `make_task_router` from `task_command.py` so `lifespan.py` imports cleanly.
  - [x] Update module docstring to reflect Story 3.3 content.

- [x] **Task 5: Co-located tests** (AC: #9, #12, #13)
  - [x] `test_task_command.py` with ≥12 tests per AC-12 breakdown (20 tests total, 19 non-slow).
  - [x] `RegistryAPIClient` unit tests inline in `test_task_command.py`.
  - [x] Verify `check_imports` gate by confirming no `registry_api.*` import in gateway code.
  - [x] Mark `test_task_handler_latency_under_p95_budget` with `@pytest.mark.slow`.

- [x] **Task 6: Gates + atomic commit** (AC: #13, #15)
  - [x] `just lint` 8/8 green INDEPENDENTLY (Epic-2-retro AI #1).
  - [x] `just test` (excluding slow) 701 passed (+20 from 681 baseline).
  - [x] `just bootstrap-verify` no version churn.
  - [x] Single atomic commit with the AC-15 title.

## Dev Notes

### Cited requirements

- **FR1** (`prd.md:812`): "Operator can submit a task via free-text description from Telegram or Console Client, optionally including a repository target and a free-text hint."
- **FR28** (`prd.md:852`): "Platform can dedupe incoming control commands by a client-generated idempotency key, returning the prior result on collision and never producing duplicate task execution on retry or network partition."
- **NFR-P2** (`prd.md:905`): "Operator latency: <2.5 s p95 task-create → Telegram ack over 3×100 sequential submissions; all three batches must clear threshold."
- **architecture.md:228** — RFC 7807 `application/problem+json` error envelope; handler must parse `detail` field and surface human-readable messages.
- **architecture.md:231** — Bot → Registry API contract is HTTP/JSON (`POST /v1/tasks`). This story is the first concrete consumer.
- **architecture.md:313** — `X-Request-ID` header: UUIDv7, generated if absent; bound into every emitted event + log line.

### Why local model redefinition (AC-2 decision)

Story 3.2 used `# noqa: IMP001` on `TelegramRejectedPayload` because that type owns its own schema registration and lives in `registry_state.domain.event_types` for architectural reasons. `CreateTaskResponse` has no such architectural home in `registry_api` — it's a transport DTO. Importing it cross-service would:
1. Create a `services → services` dependency just for a Pydantic model with 3 fields.
2. Tie the gateway's build to registry-api's internal module structure.
3. Require a `# noqa: IMP001 — <reason>` that's hard to justify architecturally.

Local redefinition with a source-of-truth doc-comment achieves the same type safety with clean boundaries. If model-sharing grows to >3 cross-service DTOs, the right fix is `packages/events/` migration (tracked in a TODO comment).

### Carry-forward from prior stories

- **Story 3.1 H4** — `httpx.AsyncClient` MUST be long-lived (lifespan-constructed). Never construct per-request.
- **Story 3.2 M2/M4/M6** — `_safe_emit`-style defensive wrapping around external calls; `request_id` correlation; never let exceptions kill the webhook handler.
- **Story 3.1 M3** — handler must always return 200 to Telegram. Errors go to operator as a Telegram reply.
- **Story 3.1 M5** — `DefaultBotProperties(parse_mode=ParseMode.HTML)` already set; use HTML tags in replies.
- **Story 3.2 M6** — `request_id` correlation: generate with `new_request_id()` at handler entry; attach to outbound `X-Request-ID` header.
- **Epic-2-retro AI #1** — trust-but-verify lint; run `just lint` independently before marking done.

### Previous-story intelligence

- **Story 3.1** ships `Bot`, `Dispatcher`, `app.state.*` wiring, `_TELEGRAM_GATEWAY_ACTOR`, fire-and-forget dispatch, `AsyncExitStack` lifespan. This story adds `http_client` + `registry_client` to the same stack.
- **Story 3.2** ships `AllowlistMiddleware` as the FIRST outer middleware. The `/task` handler runs ONLY for allowlisted users. This story does NOT touch `AllowlistMiddleware`.
- **Story 2.9** ships `POST /v1/tasks` at registry-api. The endpoint returns 201 `{"task_id": "t-…", "event_id": "e-…", "created_at": "…"}` with `X-Idempotency-Status` header and `Idempotency-Key` echo header. The `CreateTaskRequest` body requires `title: str` (1-512 chars). The operator's `/task <description>` text maps to `title`.

### aiogram data injection pattern

aiogram v3's `dp.workflow_data` is a dict injected into every handler call. Setting `dp.workflow_data["registry_client"] = registry_client` makes `registry_client: RegistryAPIClient` available as a handler parameter by name (aiogram resolves by name). Same mechanism works for `bot: Bot` (aiogram injects it automatically from the `Bot` instance). No custom middleware or `@dp.startup` decorator needed.

### Idempotency key strategy

**Review-fix H1**: The original `telegram-{chat_id}-{message_id}` string format failed registry-api's `IdempotencyKeyMiddleware` UUIDv7 regex, causing the middleware to regenerate a fresh key per Telegram retry and creating duplicate tasks. Replaced with a deterministic UUIDv7-shaped key derived as follows:

1. Compute `uuid.uuid5(_TELEGRAM_NAMESPACE_UUID, f"{chat_id}:{message_id}")` where `_TELEGRAM_NAMESPACE_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")`.
2. Reshape the byte array: set version nibble to `7` (`bytes[6] = (bytes[6] & 0x0F) | 0x70`) and variant nibble to `10xx` (`bytes[8] = (bytes[8] & 0x3F) | 0x80`).
3. Return `str(uuid.UUID(bytes=reshaped))`.

The result satisfies the `IdempotencyKeyMiddleware` regex: `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`.

Key properties:
- **Deterministic**: same `(chat_id, message_id)` always produces the same UUID-shaped key.
- **Accepted by registry-api**: passes the `IdempotencyKeyMiddleware` UUIDv7 regex constraint.
- **Service-namespaced**: the namespace UUID encodes the Telegram gateway identity (L4); a Slack gateway with the same numeric ids would use a different namespace UUID and never collide.
- **Negative chat_id safe**: negative values (supergroup ids start at -100…) are embedded in the SHA-1 seed string; the UUID byte representation hides the sign (L6).

### HTTP client timeout rationale

`httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)`: the `read` timeout of 3.0 s is the binding constraint for NFR-P2. Registry-api must reply within the read window; the bot then does a local `await message.reply(...)` which is fast. Total budget: 2.0 s connect + 3.0 s read ≤ 5.0 s worst-case, but the p95 target of 2.5 s is tested with 200 ms registry mock (AC-9).

### What this story does NOT do

- **Story 3.4** owns `/approve`.
- **Story 3.5** owns `/ping`.
- **Story 3.6** owns request-id middleware on the FastAPI side (outbound `X-Request-ID` is added here only at the httpx call level, not as FastAPI middleware).
- **Story 3.7** owns the RFC 7807 error envelope on the registry-api service side; this story only CONSUMES it.
- **Story 3.9** owns task-thread binding for outbound progress event routing.
- **Story 3.14** owns `/status`.
- **Story 3.8** owns Hypothesis fuzz coverage for command-injection in operator-supplied free text.
- Does NOT emit `task.created` audit event (registry-api owns this — FR26 / Story 2.9).
- Does NOT add per-user rate limiting (Story 3.6).
- Does NOT add the Console CLI surface (Story 4.2 — parity with FR12 comes later).

### Predicted file list

**New (3):**
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py`
- `services/telegram-gateway/src/telegram_gateway/test_task_command.py`

**Modified (4):**
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` — export `router`, update docstring.
- `services/telegram-gateway/src/telegram_gateway/app/config.py` — `registry_api_base_url` field.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — `httpx.AsyncClient` + `RegistryAPIClient` construction, `dp.workflow_data` injection, router registration.
- `.env.example` — `REGISTRY_API_BASE_URL=...` line.

### References

- `_bmad-output/planning-artifacts/epics.md:1021` — Story 3.3 spec.
- `_bmad-output/planning-artifacts/prd.md:812` (FR1), `:852` (FR28), `:905` (NFR-P2).
- `_bmad-output/planning-artifacts/architecture.md:228` (RFC 7807), `:231` (Bot→Registry HTTP/JSON contract), `:313` (endpoint conventions + `X-Request-ID`), `:374` (problem+json example).
- `services/registry-api/src/registry_api/routes/tasks.py` — `CreateTaskRequest` (title/repo/hint), `CreateTaskResponse` (task_id/event_id/created_at), idempotency headers.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — `AsyncExitStack`, dispatch task set, `app.state.*` assignment site.
- `services/telegram-gateway/src/telegram_gateway/app/config.py` — `TelegramSettings`, `HttpUrl` field pattern.
- `packages/events/src/events/ids.py` — `new_request_id()`.
- `_bmad-output/implementation-artifacts/3-1-aiogram-bootstrap-webhook.md` — H4 (long-lived client), M3 (fire-and-forget), M5 (HTML parse mode), lifespan wiring.
- `_bmad-output/implementation-artifacts/3-2-allowlist-middleware.md` — M2/M4 (`_safe_emit` shape), M6 (request_id correlation), L13 (public `TELEGRAM_GATEWAY_ACTOR`).
- `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-27.md` — AI #1 (trust-but-verify lint), AI #4 (uv sync flags), AI #5 (autouse re-register).

### Review Findings

Three-layer code review of commit `76795df`. After dedup: **5 High/Critical · 10 Med · 7 Low**. Per user directive ("fix all issues even minors") all are classified `[Patch]`.

**Critical / High severity**

- [x] [Review][Patch] H1 (Edge #1 ⚡CRITICAL): **FR28 idempotency silently broken** — `Idempotency-Key: telegram-{chat_id}-{message_id}` fails registry-api's `IdempotencyKeyMiddleware` UUIDv7 regex (Story 2.13), so middleware regenerates a fresh UUID per retry; every Telegram retry creates a new task. Spec line 269 ("opaque to registry-api") is factually wrong. **Fix:** derive a deterministic UUIDv7 (or UUIDv5 with fixed namespace) from `(chat_id, message_id)`. Update spec AC-7. The dev-pass test only verified the bot SENT the key — never that registry-api ACCEPTED it [task_command.py:_idempotency_key_from_message + spec AC-7]
- [x] [Review][Patch] H2 (Edge #2 CRITICAL): `KeyError` / `JSONDecodeError` / `ValidationError` from response body parsing escapes `handle_task`'s `httpx.HTTPError` / `httpx.HTTPStatusError` catches → user gets NO reply, M3 contract violated. Add an `except Exception` backstop in `handle_task` that replies with a generic error message + logs traceback [task_command.py:handle_task + registry_client.py:create_task]
- [x] [Review][Patch] H3 (Blind/Edge #4): Missing `parse_mode="HTML"` on success `message.reply(...)` → `<code>` tags render as literal `&lt;code&gt;` text or Telegram rejects message. Add `parse_mode="HTML"` explicitly OR rely on `DefaultBotProperties(parse_mode=ParseMode.HTML)` (Story 3.1 M5) — verify it's actually wired and add a test that asserts the rendered text contains `<code>` literal post-parse [task_command.py + lifespan.py]
- [x] [Review][Patch] H4 (Edge #3): `http_client.aclose` pushed last on AsyncExitStack → LIFO unwinds it FIRST while in-flight `/task` handlers still running → "client has been closed" RuntimeError. Reorder push so `http_client.aclose` runs AFTER `_drain_dispatch_tasks` and BEFORE `bot.session.close` / `writer.close` [lifespan.py]
- [x] [Review][Patch] H5 (Edge #4+#5): `_format_http_error` renders FastAPI's list-typed `detail` field as Python repr (`[{'loc': [...]}]`) → Telegram `BadRequest: can't parse entities`; also unescaped `detail`/`task_id_from_body` enable HTML injection from registry-api into operator's Telegram client. Add list→str coercion (extract first `msg` field) AND `html.escape(str(detail))` for all interpolated values [task_command.py:_format_http_error]

**Medium severity**

- [x] [Review][Patch] M1 (Blind): `RegistryAPIClient.__init__` `base_url` parameter is dead — actual routing uses `http_client.base_url`; divergence creates silent mis-routing footgun. **Drop `base_url` from constructor** OR enforce consistency with assertion [registry_client.py]
- [x] [Review][Patch] M2 (Blind): `_format_http_error` gives generic "Task rejected: HTTP 403" for auth errors. Add explicit 401/403 branch with "⚠️ Not authorized. Contact your administrator." [task_command.py:_format_http_error]
- [x] [Review][Patch] M3 (Blind): `httpx.TooManyRedirects` is `HTTPError` not `HTTPStatusError` → falls into generic network bucket with confusing user message. Catch explicitly with misconfiguration-specific message [task_command.py:handle_task]
- [x] [Review][Patch] M4 (Blind): p95 percentile-formula off for non-100 n: `int(0.95 * n) - 1` is wrong for n=99 (returns 93rd not 94th). Use `math.ceil(0.95 * n) - 1` or `statistics.quantiles` [test_task_command.py:test_task_handler_latency_under_p95_budget]
- [x] [Review][Patch] M5 (Blind): Latency test sequential `for i in range(100)` with 200ms `asyncio.sleep` mock → ≥20s wall-clock; p95 < 1.0s assertion can never legitimately fail given exact-200ms mock. Tighten threshold to `< 0.25s` OR document why 1.0s is meaningful [test_task_command.py]
- [x] [Review][Patch] M6 (Blind): `_make_registry_client` test helper leaks unclosed `httpx.AsyncClient` per call → `ResourceWarning`. Make it a `pytest.fixture` with `async with` teardown OR explicit `aclose()` in finally [test_task_command.py]
- [x] [Review][Patch] M7 (Blind): No top-level `except Exception` backstop in `handle_task` (also covered by H2; fold). [task_command.py:handle_task]
- [x] [Review][Patch] M8 (Edge #6): No test asserts `idempotency_status="applied"` is the default when `X-Idempotency-Status` header is absent; no test asserts `"(retry deduped)"` is ABSENT in the success path [test_task_command.py]
- [x] [Review][Patch] M9 (Edge #7): No test for `message.from_user is None` defensive branch (`"unknown"` actor id path) [test_task_command.py]
- [x] [Review][Patch] M10 (Edge #8 + Edge #9): `_make_registry_client` sync/async transport asymmetry with `# type: ignore[misc]`; AND `/task\nDescription` (newline, no space) silently swallows description. Use `raw_text.split(None, 1)` for any-whitespace splitting; make both transport paths async [task_command.py + test_task_command.py]

**Low severity**

- [x] [Review][Patch] L1 (Blind): `__all__` in `task_command.py` exports private helpers `_idempotency_key_from_message` / `_format_http_error` (underscore convention violation) [task_command.py]
- [x] [Review][Patch] L2 (Blind): module docstring coverage list references `test_format_http_error_409` that doesn't exist (renamed to `_with_task_id` / `_no_body_task_id`) [test_task_command.py module docstring]
- [x] [Review][Patch] L3 (Blind): `_FAKE_CREATED_AT = "2024-01-01T00:00:00Z"` (string) relies on Pydantic implicit string→datetime coercion. Use `datetime(2024, 1, 1, tzinfo=timezone.utc)` explicitly [test_task_command.py]
- [x] [Review][Patch] L4 (Blind): Idempotency-key format lacks service namespace — future Slack gateway with same numeric IDs would collide. Document namespacing assumption OR prefix with `tg:` (folds with H1's deterministic UUID fix — namespace becomes part of the UUIDv5 namespace UUID) [task_command.py:_idempotency_key_from_message]
- [x] [Review][Patch] L5 (Edge #10): No test for `_format_http_error` with empty (zero-byte) body on 409 path [test_task_command.py]
- [x] [Review][Patch] L6 (Edge #11): Negative `chat_id` (Telegram supergroups start at -100…) produces double-hyphen `telegram--1001234-567` — undocumented (becomes moot under H1 fix; document the namespace UUID seed shape) [task_command.py:_idempotency_key_from_message]
- [x] [Review][Patch] L7 (Auditor): description-extraction split-on-space differs from spec pseudocode `removeprefix("/task")`; impl is strictly better for `/task@botname` cases but no test covers the bot-mention form. Add `test_task_handler_handles_bot_mention` [test_task_command.py]

**Dismissed (none)** — all findings are actionable in this fix pass.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6 (executor agent)

### Debug Log References

- **Router singleton issue**: `router = Router()` at module level caused `RuntimeError: Router is already attached` when multiple lifespan test fixtures each created a new `Dispatcher` and called `dp.include_router(task_router)`. Fixed by replacing the module-level singleton with a `make_task_router()` factory function called once per lifespan. The factory creates a fresh `Router()` per dispatcher instance.
- **mypy default type**: `registry_api_base_url: HttpUrl = Field(default="http://registry-api:8080", ...)` caused `Incompatible types in assignment` because mypy sees the string literal as `str`, not `HttpUrl`. Fixed by using `HttpUrl("http://registry-api:8080")` as the default value.

### Completion Notes List

- Task 1: `RegistryAPIClient` + `CreateTaskResponseLocal` in `handlers/registry_client.py`; local model redefinition (no cross-service import); `idempotency_status` field derived from `X-Idempotency-Status` header.
- Task 2: `TelegramSettings.registry_api_base_url` field added; `lifespan.py` wired with `httpx.AsyncClient` + `RegistryAPIClient` in `AsyncExitStack`; `dp.workflow_data` injection; `.env.example` updated.
- Task 3: `/task` handler in `handlers/task_command.py`; `_idempotency_key_from_message` + `_format_http_error` helpers; `make_task_router()` factory (not module-level singleton) to avoid aiogram "already attached" error across test lifespans.
- Task 4: `handlers/__init__.py` exports `make_task_router`; docstring updated.
- Task 5: 20 tests in `test_task_command.py` (19 non-slow + 1 `@pytest.mark.slow` NFR-P2 latency); all RegistryAPIClient + handler paths covered using `httpx.MockTransport`.
- Task 6: `just lint` 8/8, `just test` 701 (+20), `just check-gates-self-test` 3/3, `just bootstrap-verify` clean.
- Review-fix H1: `_idempotency_key_from_message` replaced with UUIDv5→UUIDv7 reshape; `_TELEGRAM_NAMESPACE_UUID` hardcoded; `_UUIDV7_BARE_RE` exported for test assertions; AC-7 + Dev Notes "Idempotency key strategy" updated.
- Review-fix H2: `registry_client.create_task` wraps body parsing in `try/except (JSONDecodeError, KeyError, ValidationError)`; `handle_task` adds top-level `except Exception` backstop replying `"⚠️ Internal error. Logs captured."`.
- Review-fix H3: Verified `DefaultBotProperties(parse_mode=ParseMode.HTML)` is wired in `lifespan.py:199-204`; no per-call explicit kwarg needed; `test_task_handler_replies_with_task_id` asserts `<code>` in reply text.
- Review-fix H4: `lifespan.py` teardown order corrected — `http_client.aclose` pushed before `_drain_dispatch_tasks` and `flush_pending_emissions`; new LIFO order: flush → drain → http_client.aclose → bot.session.close → writer.close.
- Review-fix H5: `_format_http_error` extracts list `detail[*].msg` fields; all interpolated values wrapped in `html.escape()`; new tests for list detail, HTML escaping, and task_id escaping.
- Review-fix M1: Dropped `base_url` parameter from `RegistryAPIClient.__init__`; updated lifespan caller and all test construction sites.
- Review-fix M2: Added 401/403 branch in `_format_http_error`; tests pin both codes.
- Review-fix M3: `httpx.TooManyRedirects` caught before generic `httpx.HTTPError`; test pins.
- Review-fix M4: `int(0.95 * n) - 1` → `math.ceil(0.95 * n) - 1` in latency test.
- Review-fix M5: Latency test threshold tightened from `< 1.0 s` to `< 0.25 s`; docstring explains why.
- Review-fix M6: Added `registry_client_fixture` async pytest fixture with `async with` teardown; `_make_registry_client` kept for backward-compat with existing tests; all transport functions made `async def`.
- Review-fix M7: Folded with H2.
- Review-fix M8: `test_registry_client_default_idempotency_status_is_applied` added; `test_task_handler_replies_with_task_id` asserts `"(retry deduped)" not in reply`.
- Review-fix M9: `test_task_handler_uses_unknown_actor_when_from_user_is_none` added.
- Review-fix M10: `raw_text.split(None, 1)` replaces `raw_text.split(" ", 1)`; `test_task_handler_handles_newline_separator` added; all transports made async.
- Review-fix L1: Removed private helpers from `__all__` in `task_command.py`.
- Review-fix L2: Module docstring in `test_task_command.py` updated with correct test names.
- Review-fix L3: `_FAKE_CREATED_AT` changed to `datetime(2024, 1, 1, tzinfo=UTC)`.
- Review-fix L4/L6: Documented in `_idempotency_key_from_message` docstring; namespace UUID encodes service discriminator; negative chat_id handled safely.
- Review-fix L5: `test_format_http_error_409_empty_body` added.
- Review-fix L7: `test_task_handler_handles_bot_mention` added; aiogram filter behavior documented.
- Final gates: `just test` 719 passed (+18 net new), `just lint` 8/8, `just check-gates-self-test` 3/3, `just bootstrap-verify` clean.

### File List

**New (3):**
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py`
- `services/telegram-gateway/src/telegram_gateway/test_task_command.py`

**Modified (4):**
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py`
- `services/telegram-gateway/src/telegram_gateway/app/config.py`
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py`
- `.env.example`

**Review-fix modified (4):**
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` — H1 UUID reshape, H2 backstop, H5 escaping, M1 base_url drop, M2 401/403, M3 TooManyRedirects, M10 split(None), L1 __all__, L4/L6 docs
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — H2 body-parse wrap, M1 base_url drop
- `services/telegram-gateway/src/telegram_gateway/test_task_command.py` — all new tests, M4/M5 latency fix, M6 fixture, L2/L3 docstring fixes
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — H4 teardown reorder, M1 base_url drop from RegistryAPIClient call

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-04-27 | 1.0 | Story 3.3 implemented — `/task` command (Bootstrap Minimum #1): `RegistryAPIClient`, `httpx.AsyncClient` lifespan wiring, `make_task_router()` factory, 20 tests (+20 vs baseline). | claude-sonnet-4-6 (executor agent) |
| 2026-04-27 | 1.1 | Review-fix pass: 5 High, 10 Med, 7 Low addressed; +18 tests (719 total); FR28 idempotency UUIDv5→UUIDv7 reshape fix (H1), M3-contract backstop (H2), HTML-escape RFC 7807 detail (H5), lifespan teardown reorder (H4), list-detail extraction (H5), 401/403 branch (M2), TooManyRedirects catch (M3), math.ceil p95 fix (M4), threshold tightened to 0.25 s (M5), async fixture teardown (M6), split(None) whitespace handling (M10). | claude-sonnet-4-6 (executor agent) |
