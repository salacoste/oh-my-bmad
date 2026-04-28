# Story 3.2: Telegram allowlist middleware + rejection event

Status: review

## Story

As **the operator (FR11, NFR-S4)**,
I want **every inbound Telegram update intercepted by an aiogram outer middleware that checks the sender's user id against `TG_ALLOWLIST_USER_IDS`; non-allowlisted senders receive no response and the rejection is recorded as a typed `telegram.rejected` event with `{user_id, reason}` payload**,
so that **FR11 (allowlist enforcement) and NFR-S4 (no-response semantics + audit trail) are satisfied at the ingress before any handler runs, the audit surface (FR42 / NFR-S3) gains the first operator-facing event type, and Stories 3.3–3.5 (Bootstrap Minimum commands) execute under the contract that only allowlisted users reach handler dispatch**.

## Acceptance Criteria

1. **AC-1: New event type `telegram.rejected`** — extend `services/registry-state/src/registry_state/domain/event_types.py`:
   ```python
   class TelegramRejectedPayload(BaseModel):
       """Payload for `telegram.rejected` (FR11 / NFR-S4 audit trail).

       Emitted when an inbound Telegram update fails the allowlist check.
       PII surface is intentionally minimal: user_id + structured reason
       only. No message content, no username, no chat metadata.
       """
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       user_id: int = Field(ge=1)
       reason: Literal["not_in_allowlist", "no_from_user"] = "not_in_allowlist"
   ```
   Register under BOTH versions per Story 2.14 additive-version rule:
   `register("telegram.rejected", "1.0.0", TelegramRejectedPayload)` and
   `register("telegram.rejected", "1.0.1", TelegramRejectedPayload)`.
   Add `TelegramRejectedPayload` to `__all__`. `EVENT_TYPES` count grows 13 → 14.

2. **AC-2: `TG_ALLOWLIST_USER_IDS` config field** — extend `TelegramSettings` in `services/telegram-gateway/src/telegram_gateway/app/config.py`:
   ```python
   tg_allowlist_user_ids: frozenset[int] = Field(
       default_factory=frozenset,
       validation_alias="TG_ALLOWLIST_USER_IDS",
       description=(
           "JSON list of allowed Telegram user ids. Empty default = "
           "closed-by-default (rejects every inbound update). FR11."
       ),
   )
   ```
   `pydantic-settings` natively parses `[12345, 67890]` JSON-list syntax for `frozenset[int]`. Reject negative or zero ids via field validator (Telegram user ids are positive). Document closed-by-default in field docstring.

3. **AC-3: `AllowlistMiddleware(BaseMiddleware)`** — new file `services/telegram-gateway/src/telegram_gateway/app/middleware.py`:
   ```python
   class AllowlistMiddleware(BaseMiddleware):
       def __init__(
           self,
           *,
           allowlist: frozenset[int],
           emit: Callable[[EventEnvelope], Awaitable[None]],
           actor: Actor,
           clock: Clock,
       ) -> None: ...

       async def __call__(self, handler, event, data) -> Any:
           user_id = self._extract_user_id(event)
           if user_id is None:
               # See AC-7 — no `from_user` → reject defensively.
               await self._emit_rejection(user_id=0, reason="no_from_user")
               return None
           if user_id in self._allowlist:
               return await handler(event, data)
           await self._emit_rejection(user_id=user_id, reason="not_in_allowlist")
           return None
   ```
   Returning `None` short-circuits dispatch (handler never runs). Emission uses `EventEnvelope.create(type="telegram.rejected", schema_version="1.0.0", payload=TelegramRejectedPayload(...), actor=actor, event_id=new_event_id(clock=clock), emitted_at=clock.now_utc(), emitted_at_monotonic_ns=clock.monotonic_ns(), request_id=new_request_id(clock=clock))` — same shape as Story 2.16's `AuditedSecret._build_envelope`. Wrap `emit` in a `_safe_emit` helper that catches + logs but never raises (fire-and-forget; Story 2.16 H4 / Story 3.1 M3 pattern).

4. **AC-4: Outer middleware registration in lifespan** — `services/telegram-gateway/src/telegram_gateway/app/lifespan.py`, after `Dispatcher()` construction and BEFORE `app.state.*` assignments:
   ```python
   dp.update.outer_middleware.register(AllowlistMiddleware(
       allowlist=audited.tg_allowlist_user_ids,
       emit=writer.append,
       actor=_TELEGRAM_GATEWAY_ACTOR,
       clock=clock,
   ))
   ```
   **Why `outer_middleware` not `middleware`:** aiogram v3 outer middleware fires BEFORE handler routing — even for updates with no registered handler. Inner middleware fires only AFTER routing finds a match. Allowlist must reject before routing so that non-allowlisted users with malformed / unhandled update types ALSO get rejected (defense-in-depth).

5. **AC-5: No-response semantics — webhook returns 200 unconditionally** — Story 3.1's webhook handler already returns `Response(200)` BEFORE `feed_webhook_update` (fire-and-forget dispatch via `asyncio.create_task`, per Story 3.1 M3). The middleware suppresses handler invocation but never sends a Telegram reply for rejected users. Telegram observes a successful 200 ACK and never receives an outbound `sendMessage`. **Decision:** this story does NOT modify `webhook.py` — the existing fire-and-forget contract already satisfies "non-allowlisted senders receive no response" (NFR-S4). Test `test_rejected_user_receives_no_outbound_message` pins this by asserting the bot's `send_message`-style methods are never invoked for rejected updates.

6. **AC-6: Closed-by-default + startup warning** — empty `frozenset()` rejects every user id including the operator's own. Add startup warning in `lifespan.py` immediately after `from_env`:
   ```python
   if not audited.tg_allowlist_user_ids:
       logging.warning(
           "TG_ALLOWLIST_USER_IDS is empty — rejecting all inbound "
           "updates. Set the env-var to a non-empty list."
       )
   ```
   Operator-onboarding mode is "I forgot to add my user id"; the warning surfaces it on first boot. Test `test_empty_allowlist_logs_startup_warning` pins via `caplog`.

7. **AC-7: Defensive rejection for events without `from_user`** — **Decision:** REJECT defensively. Some `Update` variants (`my_chat_member`, `chat_member`, `chat_join_request`, `poll`, `poll_answer`) lack `from_user` at the top-level event, but the platform's threat model is "no command runs unless the operator's user-id is matched." Emitting `telegram.rejected` with `user_id=0, reason="no_from_user"` keeps the audit trail honest about events the gateway dropped. The `user_id=0` sentinel is documented in `TelegramRejectedPayload` (AC-1 widens `reason` to `Literal["not_in_allowlist", "no_from_user"]` and `user_id: int = Field(ge=0)` — 0 is the sentinel; operator ids stay `≥1` per Telegram contract). Test `test_event_without_from_user_rejected_with_sentinel` pins this.

8. **AC-8: Co-located tests (≥8)** — `services/telegram-gateway/src/telegram_gateway/test_allowlist.py`:
   - `test_allowlisted_user_passes_through` — synthetic Update `from_user.id=12345`; allowlist=`{12345}`; assert handler invoked exactly once + zero envelopes emitted.
   - `test_non_allowlisted_user_rejected` — `from_user.id=67890`; allowlist=`{12345}`; assert handler NOT invoked + exactly one `telegram.rejected` envelope with `payload.user_id == 67890` and `payload.reason == "not_in_allowlist"`.
   - `test_empty_allowlist_rejects_everyone` — allowlist=`frozenset()`; `from_user.id=12345`; assert handler NOT invoked + envelope emitted.
   - `test_event_without_from_user_rejected_with_sentinel` — `Update` shape with no `from_user`; assert envelope emitted with `user_id=0, reason="no_from_user"`; handler NOT invoked.
   - `test_actor_identity_in_envelope` — assert `envelope.actor == _TELEGRAM_GATEWAY_ACTOR` (kind=`"system"`, id=`"telegram-gateway"`).
   - `test_envelope_validates_against_schema_registry` — uses an autouse `_re_register_telegram_rejected` fixture (mirror of Story 2.16's `_re_register_secret_accessed`); confirms registry round-trip.
   - `test_outer_middleware_runs_before_inner` — register an inner middleware that records invocation; non-allowlisted user → assert inner NOT invoked; allowlisted user → assert inner IS invoked exactly once.
   - `test_empty_allowlist_logs_startup_warning` — `caplog` capture during lifespan startup; assert WARNING fires when `TG_ALLOWLIST_USER_IDS=[]`; assert it does NOT fire when non-empty.
   - `test_rejected_user_receives_no_outbound_message` — pin AC-5 no-response contract via FakeBot.
   - `test_emit_failure_does_not_propagate` — monkeypatch `writer.append` to raise; assert middleware swallows (fire-and-forget contract); assert handler still NOT invoked for non-allowlisted user.
   - Target: 10 tests.

9. **AC-9: Latency** — middleware adds zero blocking I/O. Allowlist check is O(1) `frozenset` membership. Envelope construction + `_safe_emit` task scheduling is `<1ms` for in-process / mocked I/O. Test `test_middleware_p50_latency_under_1ms` pins this with `time.perf_counter()` over 100 invocations against a `FakeBot` + no-op handler. Mirrors Story 3.1 M4's pattern (in-process mocked path).

10. **AC-10: Architectural gates green**:
    - `check_imports`: `telegram-gateway` → `events` is the canonical edge already in place (Story 3.1 imports `EventEnvelope`). New import: `from registry_state.domain.event_types import TelegramRejectedPayload` is a `services → services` edge that triggers `IMP001`. Use `# noqa: IMP001 — telegram.rejected payload schema lives in registry-state per Story 2.14 additive-version rule; relocation to packages/events/ tracked in TODO(architecture)` (per Story 3.1's verifier-pass lesson — every `# noqa: IMP001` MUST have a non-empty reason after the tag, otherwise `check_imports` fails with `bare-noqa` violation).
    - `check_event_registry`: new `telegram.rejected` literal must be findable. Use `EventEnvelope.create(type="telegram.rejected", ...)` form (vacuously green per Story 2.16 / Story 2.10 patterns).
    - `check_single_writer`: telegram-gateway writes nothing to SQLite; emission flows through `writer.append`. Vacuously green.

11. **AC-11: `tests/_log_capture.py::ALLOWED_LOG_FIELDS` audit** — **Decision:** NO update needed. The middleware emits `user_id` and `reason` ONLY inside `EventEnvelope.payload`, which flows through `EventLogWriter` (NOT stdlib `logging` / structlog). The startup warning (AC-6) logs no structured fields. Verify with `rg 'logger\.(info|warning|error)' services/telegram-gateway/src/telegram_gateway/app/middleware.py` — should show no `extra={...}` calls. If a future review-fix pass adds structured fields, that pass owns the whitelist update.

12. **AC-12: Scope (files modifiable)**:
    - `services/telegram-gateway/src/telegram_gateway/app/middleware.py` (NEW)
    - `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` (NEW)
    - `services/telegram-gateway/src/telegram_gateway/conftest.py` (modified — add `_re_register_telegram_rejected` autouse)
    - `services/registry-state/src/registry_state/domain/event_types.py` (modified — `TelegramRejectedPayload` + 2 register calls + `__all__`)
    - `services/telegram-gateway/src/telegram_gateway/app/config.py` (modified — `tg_allowlist_user_ids` field)
    - `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` (modified — middleware registration + empty-allowlist warning)
    - `.env.example` (modified — `TG_ALLOWLIST_USER_IDS=[]` line)
    NO modifications to other services. NO version bumps (additive change within an already-bumped service).

13. **AC-13: `.env.example` addition**:
    ```
    # Allowed Telegram user ids — JSON list. Empty = reject all
    # (closed-by-default). Find your id by messaging @userinfobot on
    # Telegram. Multiple operators: [12345, 67890].
    # Consumed by: telegram-gateway. FR11 / NFR-S4.
    TG_ALLOWLIST_USER_IDS=[]
    ```

14. **AC-14: Regression + atomic commit** — `just test` count grows by ≥10 (target ~651). `just lint` 8/8 green; **independently re-verify** by running `just lint` against the merge SHA before flipping `review → done` (Epic-2-retro AI #1). `just check-gates-self-test` 3/3 green. `just bootstrap-verify` shows no version churn. Single atomic commit titled exactly:
    ```
    feat(telegram-gateway): story 3.2 — allowlist middleware + telegram.rejected · FR11 NFR-S4
    ```

## Tasks / Subtasks

- [x] **Task 1: Register `telegram.rejected` event type** (AC: #1)
  - [x] Add `TelegramRejectedPayload` to `services/registry-state/src/registry_state/domain/event_types.py`.
  - [x] Append two `register(...)` calls (`1.0.0`, `1.0.1`) per Story 2.14 additive-version rule.
  - [x] Update `__all__`. Confirm `EVENT_TYPES` 13 → 14 via spot check.

- [x] **Task 2: `TelegramSettings.tg_allowlist_user_ids` config field** (AC: #2, #13)
  - [x] Add `tg_allowlist_user_ids: frozenset[int]` field with `validation_alias`.
  - [x] Field validator rejects ids `≤ 0`.
  - [x] Append `TG_ALLOWLIST_USER_IDS=[]` line to `.env.example`.

- [x] **Task 3: `AllowlistMiddleware` implementation** (AC: #3, #5, #7, #9)
  - [x] New file `app/middleware.py`; subclass `aiogram.BaseMiddleware`.
  - [x] Extract user-id logic handles `Update` (top-level), `Message`, `CallbackQuery`; defensive `None` for missing `from_user`.
  - [x] `_safe_emit` helper wraps `emit` with try/except+log (Story 3.1 fire-and-forget pattern).
  - [x] Use `EventEnvelope.create(...)` form for registry-gate compatibility.
  - [x] `# noqa: IMP001 — <reason>` on the `registry_state.domain.event_types` import.

- [x] **Task 4: Lifespan wiring + empty-allowlist warning** (AC: #4, #6)
  - [x] Register middleware via `dp.update.outer_middleware.register(...)` after `Dispatcher()` construction.
  - [x] Add WARNING log when `tg_allowlist_user_ids` empty.
  - [x] Pass `_TELEGRAM_GATEWAY_ACTOR` (canonical constant from Story 3.1 L17).

- [x] **Task 5: Co-located tests + autouse fixture** (AC: #8, #10, #11)
  - [x] `test_allowlist.py` with ≥10 tests per AC-8 breakdown (12 tests delivered).
  - [x] Extend `conftest.py` with `_re_register_telegram_rejected` autouse fixture (idempotent; mirror Story 2.16 / 3.1).
  - [x] Reuse Story 3.1's `client_and_state` fixture for in-process integration tests.
  - [x] Verify `tests/_log_capture.py` whitelist needs NO update (AC-11 audit).

- [x] **Task 6: Gates + atomic commit** (AC: #10, #12, #14)
  - [x] `just check-gates-self-test` 3/3 green.
  - [x] `just lint` 8/8 green INDEPENDENTLY (Epic-2-retro AI #1).
  - [x] `just bootstrap-verify` no version churn.
  - [x] Single atomic commit with the AC-14 title.

## Dev Notes

### Architecture context

- **FR11** (`prd.md:825`): "Telegram Bot can authenticate incoming messages against an allowlist of Telegram user ids; non-allowlisted senders receive no response and are logged as rejected."
- **NFR-S4** (`prd.md:924`): "Allowlist enforcement: non-allowlisted Telegram user ids receive no response from the bot. The rejection itself is recorded as a typed event. (FR11.)"
- **FR42 / NFR-S3** — `telegram.rejected` is part of the queryable audit surface; registry-state will index it once Stories 5+ wire the events-list endpoint. This story doesn't add the query path, just the event type + emission site.
- **architecture.md:215**: middleware ordering note for the FastAPI middleware stack (request-id, idempotency, log-sanitizer, webhook rate limiter) — that's Story 3.6, NOT this story. The aiogram outer middleware here is independent of FastAPI's stack.

### Why outer_middleware not middleware (aiogram v3 distinction)

aiogram v3 has two middleware tiers on `Dispatcher`:
- `dp.update.outer_middleware` — runs BEFORE the dispatcher resolves a handler. Sees every update including types with no registered handler.
- `dp.update.middleware` (inner) — runs AFTER routing succeeds. Skipped entirely for unmatched update types.

The allowlist must run as outer middleware because:
1. Defense-in-depth: a non-allowlisted user sending a `chat_member` event (no command handler) should still be rejected + audited.
2. NFR-S4 wording is "non-allowlisted user ids receive no response" — independent of whether their update would have matched a handler.
3. Inner middleware would let unhandled-update-type traffic bypass the audit trail.

### No-response semantics

Story 3.1's webhook handler returns `200` BEFORE dispatch (fire-and-forget via `asyncio.create_task` per Story 3.1 M3). The middleware suppresses handler invocation but never sends an outbound Telegram message. Telegram observes a successful 200 ACK and never receives a `sendMessage` for rejected users. From Telegram's wire perspective, "no allowlist match" and "handler ran silently" are indistinguishable — exactly NFR-S4's "no response" wording.

### Closed-by-default

Empty `frozenset()` rejects every user id including the operator's own. Two reasons:
1. **Onboarding fail-loud**: an operator who copies `.env.example` verbatim and forgets to add their id sees no responses to `/ping` — and the startup WARNING line in the journal tells them why.
2. **Threat-model alignment**: missing config = strictest posture. Same shape as `bot_token` missing in Story 3.1 (`from_env` raises `ValidationError`).

### Defense-in-depth note

The allowlist middleware is the FIRST enforcement; capability tiers (Story 6) will additionally enforce per-action authorization on registry-api endpoints. Non-allowlisted users never reach registry-api in the happy path, but registry-api MUST NOT trust that — Story 6.3 (tier-enforcement HTTP middleware) is the second layer. This story does NOT touch registry-api.

### Schema-registry test-isolation pattern (Epic-2-retro AI #5)

Every test that depends on `telegram.rejected` registration MUST add a function-scoped autouse fixture that re-installs the registration with idempotency guards. Without it, `packages/events/src/events/test_envelope.py::_clean_registry` clears `REGISTRY` between cases and tests fail with `EventSchemaUnknown('telegram.rejected', '1.0.0')`. Copy the shape from `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py::_re_register_secret_accessed`. Co-located conftest only — `tests/conftest.py` is unreachable from `services/**` (Epic-2-retro AI #5).

### `# noqa: IMP001` reason-tag requirement

Story 3.1's verifier-pass added `# noqa: IMP001` on the `registry_state` import without a reason after the tag. Lint failed with `bare-noqa` (`check_imports` enforces a non-empty trailing comment). Format must be `# noqa: IMP001 — <reason>`. Apply the same shape on `app/middleware.py`'s import of `TelegramRejectedPayload`.

### What this story does NOT do

- Does NOT implement command handlers — `/task` (3.3), `/approve` (3.4), `/ping` (3.5), `/status` (3.14), `/logs` (3.15), `/stop` (3.16), `/reject` (3.17), `/retry` (3.18), `/agent` (3.19) all land later.
- Does NOT add the FastAPI middleware stack (request-id / idempotency / log-sanitizer / webhook rate limiter — Story 3.6).
- Does NOT add per-user rate limiting (architecture.md:215 — Story 3.6).
- Does NOT add Hypothesis fuzz coverage for command-injection in operator-supplied free text (Story 3.8).
- Does NOT call `registry-api` — Story 3.3 introduces the HTTP client.
- Does NOT modify `webhook.py` — the existing fire-and-forget 200-ACK contract from Story 3.1 already satisfies NFR-S4 no-response semantics.
- Does NOT add the events-list query path for `telegram.rejected` — registry-state indexes events but the read endpoint is later.

### Previous-story intelligence

- **Story 3.1**: ships `Dispatcher`, `app.state.{bot,dp,settings}` wiring, `_TELEGRAM_GATEWAY_ACTOR` canonical constant (Story 3.1 L17), fire-and-forget dispatch via `asyncio.create_task` (M3). Reuse all of these verbatim.
- **Story 2.16**: schema-registry test-isolation pattern (autouse re-register), `EventEnvelope.create()` form, fire-and-forget audit emission, `_safe_emit` helper shape.
- **Story 2.14**: additive-version rule for event-type registration (1.0.0 + 1.0.1 register calls).
- **Story 2.10**: `Actor.kind = Literal["operator", "orchestrator", "worker", "system", "clawhip"]`. This story uses `kind="system"` (gateway-internal emission, not operator-initiated).
- **Story 2.17**: `tests/_log_capture.py::ALLOWED_LOG_FIELDS` whitelist source-of-truth for structured-log fields. AC-11 audits — no update needed for this story.
- **Epic-2-retro AI #1** (trust-but-verify lint), **AI #4** (`uv sync --all-groups --all-packages`), **AI #5** (autouse re-register pattern) all apply directly.
- **Story 3.1 verifier-pass lesson**: every `# noqa: IMP001` MUST have a non-empty reason after the tag.

### File List (predicted)

**New (2):**
- `services/telegram-gateway/src/telegram_gateway/app/middleware.py`
- `services/telegram-gateway/src/telegram_gateway/test_allowlist.py`

**Modified (5):**
- `services/registry-state/src/registry_state/domain/event_types.py` — `TelegramRejectedPayload` + 2 register calls + `__all__`.
- `services/telegram-gateway/src/telegram_gateway/app/config.py` — `tg_allowlist_user_ids` field.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — middleware registration + empty-allowlist warning.
- `services/telegram-gateway/src/telegram_gateway/conftest.py` — `_re_register_telegram_rejected` autouse fixture.
- `.env.example` — `TG_ALLOWLIST_USER_IDS=[]` line.

### References

- `_bmad-output/planning-artifacts/epics.md:1004` — Story 3.2 spec.
- `_bmad-output/planning-artifacts/prd.md:825` (FR11), `:924` (NFR-S4), `:863` (FR42), `:923` (NFR-S3).
- `_bmad-output/planning-artifacts/architecture.md:215` (FastAPI middleware stack — for Story 3.6, scope-out reference).
- `_bmad-output/implementation-artifacts/3-1-aiogram-bootstrap-webhook.md` — lifespan + `Dispatcher` + `app.state.*` wiring; `_TELEGRAM_GATEWAY_ACTOR` constant; fire-and-forget dispatch (M3); `# noqa: IMP001 — <reason>` requirement (verifier-pass lesson).
- `_bmad-output/implementation-artifacts/2-16-secret-accessed-audit-events.md` — `EventEnvelope.create()` form, `_safe_emit` shape, autouse re-register pattern.
- `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-27.md` — AI #1 / #4 / #5.
- `services/registry-state/src/registry_state/domain/event_types.py` — schema registration pattern + additive-version rule (Story 2.14).
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — `Dispatcher()` construction site (middleware registers here).
- `services/telegram-gateway/src/telegram_gateway/test_webhook.py` — synthetic Update fixture pattern (base for allowlist tests).
- `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py::_re_register_secret_accessed` — autouse fixture shape.
- aiogram v3 docs — `BaseMiddleware`; `outer_middleware` vs `middleware` distinction.

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7[1m] (executor agent — opus run hit usage cap; sonnet 4-6 finalized)`

### Debug Log References

- **ruff F401**: `EventLogWriter` imported but unused in `test_allowlist.py` — removed unused import.
- **ruff E501**: Line too long in `test_allowlist.py:fake_send_message` definition — split to multi-line.
- **ruff format**: auto-formatted `test_allowlist.py` after manual edits.
- **test_empty_allowlist_logs_startup_warning ordering failure**: `caplog` captured zero records when running after `services/registry-state/src/registry_state/test_migrations.py`. Root cause: `alembic.command.upgrade()` calls `logging.config.fileConfig()` with `disable_existing_loggers=True` (the default), which sets `.disabled = True` on loggers not listed in `alembic.ini` including `telegram_gateway.lifespan`. Fix: add `logging.getLogger("telegram_gateway.lifespan").disabled = False` before `caplog.at_level()` in both caplog-based tests. Applied same fix to `test_non_empty_allowlist_does_not_log_warning` for symmetry.

### Completion Notes List

- **Task 1**: `TelegramRejectedPayload` added to `event_types.py` with `user_id: int = Field(ge=0)` (0 = sentinel), dual `register()` calls, `__all__` updated; EVENT_TYPES count grows 13→14.
- **Task 2**: `tg_allowlist_user_ids: frozenset[int]` field with `_validate_allowlist_positive` validator (rejects ≤0 ids); `.env.example` AC-13 block appended.
- **Task 3**: `app/middleware.py` — `AllowlistMiddleware(BaseMiddleware)` with `_extract_user_id`, `_emit_rejection`, `_safe_emit`; full noqa reason tag on cross-service import.
- **Task 4**: `lifespan.py` wires `dp.update.outer_middleware.register(AllowlistMiddleware(...))` after `Dispatcher()` + empty-allowlist WARNING.
- **Task 5**: 12 tests in `test_allowlist.py` (target was ≥10); `_re_register_telegram_rejected` autouse fixture added to `conftest.py`; AC-11 audit confirmed no `ALLOWED_LOG_FIELDS` update needed; test-ordering fix for `fileConfig` logger-disable interference.
- **Task 6**: lint 8/8, test 653 passed (+12), check-gates-self-test 3/3, bootstrap-verify clean; atomic commit with AC-14 title.

### File List

**New (2):**
- `services/telegram-gateway/src/telegram_gateway/app/middleware.py`
- `services/telegram-gateway/src/telegram_gateway/test_allowlist.py`

**Modified (5):**
- `services/registry-state/src/registry_state/domain/event_types.py` — `TelegramRejectedPayload` + 2 register calls + `__all__`.
- `services/telegram-gateway/src/telegram_gateway/app/config.py` — `tg_allowlist_user_ids` field + `_validate_allowlist_positive` validator.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — middleware registration + empty-allowlist WARNING.
- `services/telegram-gateway/src/telegram_gateway/conftest.py` — `_re_register_telegram_rejected` autouse fixture.
- `.env.example` — AC-13 `TG_ALLOWLIST_USER_IDS=[]` block.

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-04-27 | 1.0.0 | Story 3.2 implemented — allowlist middleware + telegram.rejected event · FR11 NFR-S4 | claude-opus-4-7[1m] / claude-sonnet-4-6 |
