# Story 3.5: /ping command (Bootstrap Minimum #3 — closes Bootstrap Milestone)

Status: ready-for-dev

## Story

As **the operator (FR17, NFR-O4)**,
I want **to send `/ping` from Telegram and have the bot call `GET /v1/health` on the registry-api and reply within 2 s with a one-line platform health summary: registry status, worker status, clawhip queue depth, and platform version**,
so that **I can check the stack is alive from my phone without SSHing into any service, the `<2 s` end-to-end health-check budget (NFR-O4) is contractually verified before Stories 3.14–3.16 build further diagnostic commands, and the Bootstrap Milestone for Phase 1's operator-control-plane MVP is closed: an operator can now submit tasks, approve them, and check platform health entirely from Telegram**.

This is the third and final Bootstrap Minimum command. It **extends** `RegistryAPIClient` with a new `get_platform_health()` method and adds a new `ping_command.py` handler module, following the factory-router and module-level-handler pattern established by Stories 3.3 and 3.4.

The registry-api endpoint `GET /v1/health` is **not yet implemented server-side**; Story 3.5 ships the bot-side handler only, with httpx-mocked tests that are runnable today and forward-compatible with the eventual server-side implementation. See Dev Notes for the endpoint-owner gap.

### Bootstrap Milestone Close-out

After Story 3.5 ships and passes review, Phase 1's operator-control-plane MVP is achieved:

- **Story 3.3** — operator can submit tasks via `/task`
- **Story 3.4** — operator can approve tasks via `/approve`
- **Story 3.5** — operator can check platform health via `/ping`

Stories 3.6–3.20 add hardening (middleware stack, command-injection fuzz, message templates, audit events). Stories 4–7 add Console parity, autonomous execution, approval gates, and reconnaissance/recovery UX. The Phase 1 MVP claim per architecture.md hinges on Story 3.5 closing this milestone. **Flip the Bootstrap Milestone tracker to `closed` after this story is `done`.**

## Acceptance Criteria

1. **AC-1: `HealthResponseLocal` model in `registry_client.py`** — add to `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:
   ```python
   class HealthResponseLocal(BaseModel):
       """Local mirror of registry-api's eventual GET /v1/health response.

       FR17 fields: registry status, worker status, clawhip queue depth, platform version.
       Forward-compatible shape pinned by 3.5's mocked tests; alignment with the
       eventual server-side endpoint owner (TBD — gap in current epic plan; see Dev Notes).

       TODO(story-TBD): verify field names match the server-side GET /v1/health response
       when that endpoint lands. Most likely owner: Story 6.x middleware stack or a new
       platform-observability story between Epics 5 and 7.
       """
       model_config = ConfigDict(frozen=True)
       registry_status: Literal["healthy", "degraded", "unhealthy"]
       worker_status: Literal["idle", "busy", "unhealthy"]
       clawhip_queue_depth: int = Field(ge=0)
       version: str  # e.g., "v1.2.3"
   ```

2. **AC-2: `RegistryAPIClient.get_platform_health(...)` method** — extend `RegistryAPIClient` in `registry_client.py`:
   ```python
   async def get_platform_health(
       self,
       *,
       request_id: str | None = None,
   ) -> HealthResponseLocal:
       """GET /v1/health and return a typed local response model.

       No request body. No Idempotency-Key header — GET is idempotent by HTTP
       semantics (RFC 7231 §4.2.2); Telegram retries safely re-fetch the health
       summary without duplication concerns. This is the FIRST handler in the bot
       that omits an idempotency key; document the reason explicitly.

       Args:
           request_id: UUIDv7 request correlation id. Forwarded as X-Request-ID.

       Returns:
           HealthResponseLocal on HTTP 2xx.

       Raises:
           RegistryResponseError: On 2xx with malformed/unexpected body (Story 3.4 H1).
           httpx.HTTPStatusError: On non-2xx responses.
           httpx.HTTPError:       On network / timeout errors.

       Note:
           GET /v1/health does NOT exist server-side yet. No story owner has been
           assigned (gap in epic plan). Until then a live call returns 404.
           Tests mock the transport layer and are runnable today.
       """
   ```
   On 2xx, parses JSON into `HealthResponseLocal`. Raises `RegistryResponseError` on body-parse failure (same `try/except (JSONDecodeError, KeyError, ValidationError, ValueError)` pattern as `create_task` and `submit_decision`). Adds `X-Request-ID` header when `request_id` is not `None`. Does NOT send `Idempotency-Key` (GET is idempotent).

3. **AC-3: `ping_command.py` handler** — new file `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py`:
   ```python
   def make_ping_router() -> Router:
       """Factory — creates a fresh Router per dispatcher instance.

       Same judgment call as make_task_router() (Story 3.3) and make_approve_router()
       (Story 3.4): avoids 'Router already attached' RuntimeError across test lifespans.
       """

   async def handle_ping(
       message: Message,
       registry_client: RegistryAPIClient,
   ) -> None:
       """Module-level handler registered inside make_ping_router().

       Module-level (not a closure inside the factory) — matches Story 3.4 M6 pattern.
       _safe_reply wraps every reply (Story 3.1 M3 fire-and-forget contract).
       """
   ```
   Module docstring documents: no audit-event emission, no idempotency key, Bootstrap Milestone #3 callout, registry-api endpoint not yet implemented.

4. **AC-4: Success reply text format** — exact template (no HTML tags in the message body; `parse_mode="HTML"` is set globally via `DefaultBotProperties`):
   ```
   pong · registry: <status> · worker: <status> · clawhip: <N> events queued · version: <vX.Y.Z>
   ```
   - All interpolated values wrapped in `html.escape()`. The version string MUST be escaped defensively even though `vX.Y.Z` is the normal contract — operator env-var injection could produce strings with `<` (e.g., `v1.0.0-<branch>`).
   - When `registry_status == "unhealthy"`: prefix the entire reply with `⚠️ ` (one space separator):
     ```
     ⚠️ pong · registry: unhealthy · worker: <status> · clawhip: <N> events queued · version: <vX.Y.Z>
     ```
   - `"degraded"` does not prefix the emoji; only `"unhealthy"` does.
   - Literal separator is ` · ` (space, middle-dot U+00B7, space).

5. **AC-5: No idempotency key** — `get_platform_health()` sends NO `Idempotency-Key` header. This is the first command in the bot that omits idempotency. Reason: GET is idempotent by HTTP semantics (RFC 7231 §4.2.2); repeated calls safely re-fetch data without side effects. Document in `ping_command.py` module docstring and in `get_platform_health()` docstring. A test `test_ping_handler_does_not_send_idempotency_key` asserts the absence of the header.

6. **AC-6: Network-error reply** — when the registry is unreachable (`httpx.TimeoutException`, `httpx.ConnectError`, or other non-status `httpx.HTTPError`), the bot replies:
   ```
   ⚠️ Registry unreachable. Try again in a moment.
   ```
   This reply is distinct from the success path's `pong` prefix and the HTTP-error path's `_format_http_error` output.

7. **AC-7: HTTP-error reply** — when registry-api returns a non-2xx status, the bot calls `_format_http_error(exc)` from `_errors.py` (Story 3.4 M4 promotion). Same 4xx/5xx branch logic as `handle_task` and `handle_approve`.

8. **AC-8: Backstop** — a top-level `except Exception` catches any unexpected error and replies `"⚠️ Internal error. Logs captured."` (Story 3.3 H2 carry-forward). Handler always returns normally — never propagates (Story 3.1 M3 fire-and-forget contract).

9. **AC-9: Latency budget** — NFR-O4 requires the full round-trip `<2 s`. The bot-side handler budget is `p95 < 0.5 s` (4× headroom). The latency test uses 100 invocations with a 100 ms mocked registry response and asserts `p95 < 0.2 s` (`@pytest.mark.slow`). Uses `math.ceil(0.95 * n) - 1` percentile index formula (Story 3.4 M4 carry-forward).

10. **AC-10: Co-located tests (≥13)** — `services/telegram-gateway/src/telegram_gateway/test_ping_command.py`:
    - `test_ping_handler_replies_with_health_summary` — happy path (`registry_status="healthy"`, `worker_status="idle"`, `clawhip_queue_depth=3`, `version="v1.2.3"`); assert reply equals the exact template from AC-4.
    - `test_ping_handler_health_unhealthy_prefixes_warning_emoji` — `registry_status="unhealthy"`; assert reply starts with `"⚠️ pong"`.
    - `test_ping_handler_health_degraded_no_warning_emoji` — `registry_status="degraded"`; assert reply starts with `"pong"` (no emoji prefix).
    - `test_ping_handler_propagates_request_id` — assert `X-Request-ID` header in outbound GET request is a bare UUIDv7.
    - `test_ping_handler_does_not_send_idempotency_key` — assert `Idempotency-Key` header is ABSENT from the outbound GET request.
    - `test_ping_handler_5xx_replies_retry_message` — mock 500; assert reply starts with `"⚠️ Registry unavailable: HTTP 500"` (via `_format_http_error`).
    - `test_ping_handler_4xx_replies_error_message` — mock 404; assert `_format_http_error` output rendered.
    - `test_ping_handler_timeout_replies_unreachable` — mock `httpx.ReadTimeout`; assert reply equals `"⚠️ Registry unreachable. Try again in a moment."`.
    - `test_ping_handler_replies_with_html_escaped_version` — `version="v1.0.0-<branch>"`; assert reply contains `v1.0.0-&lt;branch&gt;` not raw `<branch>`.
    - `test_ping_handler_unexpected_exception_replies_internal_error` — mock `get_platform_health` raises `RuntimeError`; assert reply contains `"Internal error"` (H2 backstop).
    - `test_ping_handler_swallows_reply_failure` — `message.reply` raises `TelegramError`; assert handler returns normally without raising (M3 contract).
    - `test_ping_handler_latency_under_p95_budget` — `@pytest.mark.slow`; 100 invocations, 100 ms mocked response; `p95 < 0.2 s`.
    - `test_get_platform_health_parses_minimal_response` — direct call to `RegistryAPIClient.get_platform_health()`; mock returns minimal valid JSON; assert `HealthResponseLocal` fields match.
    - `test_get_platform_health_raises_registry_response_error_on_malformed_body` — mock returns `{}` (missing required fields); assert `RegistryResponseError` raised.
    Target: ≥13 tests (exceeds minimum of 10).

11. **AC-11: Architectural gates green** — all four gates pass:
    - `check_imports`: `ping_command.py` imports only from `telegram_gateway.handlers.*` and stdlib. No `registry_api.*` cross-service import.
    - `check_event_registry`: vacuously green — no new event types. Bot does not emit events.
    - `check_single_writer`: vacuously green — telegram-gateway writes nothing to SQLite.
    - `secret-hygiene-precommit`: clean — health status strings and version strings are non-secret.

12. **AC-12: Scope boundary** — files modifiable in this story:
    - **New (2):** `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py`, `services/telegram-gateway/src/telegram_gateway/test_ping_command.py`
    - **Modified (3):** `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (add `HealthResponseLocal` + `get_platform_health`), `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` (re-export `HealthResponseLocal` and `make_ping_router`), `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (router registration)
    - **Not modifiable:** `.env.example` (no new env-vars), `handlers/_keys.py`, `handlers/task_command.py`, `handlers/approve_command.py`, any `registry-api` source, story file (once authored), sprint-status.yaml (update separately).

13. **AC-13: No new env-vars** — `/ping` reuses `REGISTRY_API_BASE_URL` from Story 3.3. `.env.example` requires no changes.

14. **AC-14: Regression and atomic commit** — `just test` count grows by ≥13 (target ~765 excluding `@pytest.mark.slow`). `just lint` 8/8 green. **Independently re-verify** before flipping `ready-for-dev → done` (Epic-2-retro AI #1 principle — has caught issues 7 times this session). `just bootstrap-verify` shows no version churn. Single atomic commit titled exactly:
    ```
    feat(telegram-gateway): story 3.5 — /ping command (Bootstrap Minimum #3, closes Milestone) · FR17 NFR-O4
    ```

15. **AC-15: Bootstrap Milestone status** — after `just test` and `just lint` are green and the story is flipped to `done`, update `sprint-status.yaml` to mark the Bootstrap Milestone (Epic 3 Bootstrap Minimum Subset) as `closed`. Add a comment noting the date. This is a documentation-only change, no code.

## Tasks / Subtasks

- [ ] **Task 1: `HealthResponseLocal` model + `get_platform_health()` in `registry_client.py`** (AC: #1, #2)
  - [ ] Add `HealthResponseLocal` Pydantic model with `registry_status`, `worker_status`, `clawhip_queue_depth`, `version` fields and `ConfigDict(frozen=True)`.
  - [ ] Add `get_platform_health(*, request_id)` async method: GET `/v1/health`, no request body, no `Idempotency-Key`, optional `X-Request-ID`.
  - [ ] Apply same H1 body-parse error-wrapping as `create_task` / `submit_decision`: `try/except (JSONDecodeError, KeyError, ValidationError, ValueError)` → re-raise as `RegistryResponseError`.
  - [ ] Export `HealthResponseLocal` in `__all__`.
  - [ ] Verify `just lint` green on the modified file.

- [ ] **Task 2: `ping_command.py` handler** (AC: #3, #4, #5, #6, #7, #8)
  - [ ] New file `handlers/ping_command.py` with `make_ping_router()` factory and module-level `handle_ping` function.
  - [ ] Module docstring: no-audit-event rule, no-idempotency-key rationale, Bootstrap Milestone #3 callout, registry-api endpoint-not-yet-implemented gap note.
  - [ ] Reply format per AC-4: `html.escape` on all interpolated values; `⚠️ ` prefix only for `"unhealthy"`.
  - [ ] Error branches: `_format_http_error` for HTTP-status errors; `"⚠️ Registry unreachable. Try again in a moment."` for network errors; `"⚠️ Internal error. Logs captured."` backstop.
  - [ ] `_safe_reply` wrapper on every reply (Story 3.1 M3 pattern from 3.4).
  - [ ] Run `just lint` on new file.

- [ ] **Task 3: Lifespan wiring** (AC: #9 implicit, #12)
  - [ ] Import `make_ping_router` in `lifespan.py`.
  - [ ] `dp.include_router(make_ping_router())` after existing router inclusions.
  - [ ] No new `dp.workflow_data` keys — `registry_client` already injected by Story 3.3.

- [ ] **Task 4: Co-located tests** (AC: #10, #11)
  - [ ] `test_ping_command.py` with ≥13 tests per AC-10 breakdown.
  - [ ] All tests use `respx` or `httpx.MockTransport` for registry transport mocking — same pattern as `test_approve_command.py`.
  - [ ] `test_ping_handler_latency_under_p95_budget` marked `@pytest.mark.slow`.
  - [ ] Run `just test` and confirm ≥13 new tests pass.

- [ ] **Task 5: Regression verification + commit** (AC: #14, #15)
  - [ ] `just test` — confirm count ≥765 (target), all green.
  - [ ] `just lint` — confirm 8/8 green.
  - [ ] `just bootstrap-verify` — confirm no version churn.
  - [ ] Flip story status `ready-for-dev → done` in sprint-status.yaml and add Bootstrap Milestone closed comment.
  - [ ] Atomic commit with exact title from AC-14.

## Dev Notes

### Quoted Requirements

> **FR17** (`prd.md:831`): "Operator can issue a health-check command (`/ping`) that returns registry status, worker status, event-bus queue depth, and platform version."

> **NFR-O4** (`prd.md:935`): "Health check command (`/ping`) returns registry status, worker status, event-bus queue depth, and platform version in a single response within 2 s. (FR17.)"

### Registry-API Endpoint Not Yet Implemented — Owner Gap

`GET /v1/health` (the **platform-wide health aggregator**) is NOT implemented server-side. This endpoint is **distinct** from the telegram-gateway's own `GET /v1/health` container-health probe (shipped in Story 3.1). The two endpoints share a path but live on different hosts:

| Endpoint | Host | Status | Owner |
|---|---|---|---|
| `GET /v1/health` (container health) | `telegram-gateway:8080` | shipped Story 3.1 | Story 3.1 |
| `GET /v1/health` (platform aggregator) | `registry-api:8080` | **NOT IMPLEMENTED** | **TBD** |

**Do NOT modify** `services/telegram-gateway/src/telegram_gateway/app/webhook.py` or the gateway's own health probe. Story 3.5 calls `registry_client._http_client.get("/v1/health")` which targets `REGISTRY_API_BASE_URL` (the registry-api host), not the gateway itself.

**Gap note for next epic retrospective**: No story currently owns the server-side platform-health-aggregator endpoint. Candidate owners (in priority order):

1. **New platform-observability story** between Epics 5 and 7 — cleanest fit; the aggregator needs Worker and clawhip metrics that only exist after Epic 5.
2. **Extension of Story 3.6** (middleware stack) — possible if the health aggregator is treated as part of the cross-cutting concerns layer, but 3.6 is scoped to middleware wiring, not new endpoints.
3. **Story 4.x** — console-CLI parity (Story 4.3 `decision-and-health-commands`) expects `/ping` parity; the server-side endpoint must exist before Story 4.3 can be validated end-to-end.

**Recommendation**: assign ownership at the Epic 3 retrospective. Block Story 4.3 on the server-side endpoint landing.

### Architecture Reference

- `architecture.md:231` — Bot → Registry-API transport boundary: HTTP/JSON only; no shared Python objects across services.
- `architecture.md:313` — `X-Request-ID` correlation header; UUIDv7; generated at the bot handler entry point.

### Why No Idempotency Key

GET is idempotent by HTTP semantics (RFC 7231 §4.2.2). A `/ping` invocation fetches a read-only snapshot; repeating it does not create a second resource. The `Idempotency-Key` header is meaningful only for state-mutating operations. This is the first handler in the bot that omits the header; document the rationale in `get_platform_health()` and `ping_command.py` module docstrings so future contributors understand it was a deliberate choice, not an oversight.

### Previous-Story Intelligence (carry-forward)

All carry-forward items from Story 3.4's review-fix pass apply here:

- **H1 (`RegistryResponseError`)**: body-parse failures raise `RegistryResponseError` (subclass of `httpx.HTTPError`) so the existing `except httpx.HTTPError` branch catches it, but handlers can specialize with a specific `except RegistryResponseError` branch first.
- **H2 (backstop)**: top-level `except Exception` backstop replies `"⚠️ Internal error. Logs captured."` — never propagate.
- **H5 (HTML escape)**: `html.escape()` on ALL operator-supplied or externally sourced strings in reply text.
- **M3 (`TooManyRedirects`)**: subclass of `httpx.HTTPError`; already caught by the generic network-error branch.
- **M3 (`_safe_reply`)**: wrap every `await message.reply(...)` call so a `TelegramAPIError` on reply does not surface to the webhook.
- **M4 (`_format_http_error`)**: promoted to `_errors.py` in Story 3.4; import from there.
- **M6 (module-level handler)**: `handle_ping` is defined at module level, not as a closure inside `make_ping_router`. Closures cause `ImportError` / `AttributeError` in pytest's function-collection phase with aiogram 3.x.

Story 3.3 idempotency reshape (H1 UUIDv5→v7) is irrelevant to this story — no key is sent.

Story 3.2 allowlist runs first; non-allowlisted users never reach `/ping`.

Story 3.1 `_TELEGRAM_GATEWAY_ACTOR` and lifespan `registry_client` injection are already in place; no new `dp.workflow_data` keys needed.

### What This Story Does NOT Do

- Does NOT implement `GET /v1/health` server-side on registry-api (gap; needs owner story — see above).
- Does NOT add new Telegram commands beyond `/ping`.
- Does NOT add the FastAPI middleware stack (Story 3.6).
- Does NOT add Console-CLI `/ping` parity (Story 4.3 owns that).
- Does NOT emit any audit event from the bot. If the server-side health endpoint emits an event, that is registry-api's concern.
- Does NOT add new Docker services or env-vars.

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` | Add `HealthResponseLocal`, `get_platform_health()`, update `__all__` |
| `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py` | New — handler + factory |
| `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` | Re-export `HealthResponseLocal`, `make_ping_router` |
| `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` | Register ping router |
| `services/telegram-gateway/src/telegram_gateway/test_ping_command.py` | New — ≥13 tests |
| `_bmad-output/implementation-artifacts/3-5-ping-command.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | `3-5-ping-command: backlog → ready-for-dev` |

### References

- `prd.md:831` — FR17
- `prd.md:935` — NFR-O4
- `architecture.md:231` — cross-service HTTP/JSON transport boundary
- `architecture.md:313` — `X-Request-ID` correlation header
- RFC 7231 §4.2.2 — HTTP idempotent methods (GET)
- Story 3.1 — lifespan, dispatcher, `_TELEGRAM_GATEWAY_ACTOR`
- Story 3.2 — allowlist (runs before all command handlers)
- Story 3.3 — `RegistryAPIClient`, `make_task_router` factory pattern, `_safe_reply`, `_idempotency_key_from_message`
- Story 3.4 — `_keys.py`, `_errors.py` (`_format_http_error`), `RegistryResponseError`, module-level handler, `DecisionResponseLocal`
- Story 3.6 — FastAPI middleware stack (depends on `/v1/health`? check during 3.6 authoring)
- Story 4.3 — console-CLI `decision-and-health-commands` (parity surface; blocked on server-side endpoint)
- Epic-2-retro AI #1 — independent gate verify before flipping done

## Dev Agent Record

### Agent Notes

_(empty — to be filled by implementing agent)_

### Deferred Items

_(empty — to be filled by implementing agent)_

### Change Log

_(empty — to be filled by implementing agent)_

### File List

_(empty — to be filled by implementing agent)_
