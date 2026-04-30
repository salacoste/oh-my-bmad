# Story 3.9: Task thread binding + message delivery routing

Status: done

## Story

As **the operator**,
I want **every progress event for a task to deliver to the same Telegram thread (the chat where I sent `/task` and the message I sent it as), so I can follow a task's lifecycle in one conversation thread instead of a global feed**,
so that **(a) `(chat_id, reply_to_message_id)` is captured on task creation and persisted in registry-state, (b) a new Telegram outbound sink in `services/clawhip-daemon/` subscribes to the event log, looks up the binding, and dispatches outbound messages via the Telegram Bot API `sendMessage` with `chat_id` and `reply_to_message_id`, and (c) Stories 3.10–3.13 (approval/blocker/completion/self-recovered templates) plug rendered text into the sink without any further wiring work**.

This story establishes the **first outbound delivery surface** in the platform. Today the bot only does inbound (handles `/task`/`/approve`/`/ping` and replies synchronously inside the handler). After 3.9, registered events for a task land in the operator's chat thread asynchronously via the new sink — first as placeholder text (`f"Task {task_id}: {event_type}"`), and from 3.10 onward as proper templates.

### What this story is NOT

- NOT new message templates — those are Stories 3.10 (approval), 3.11 (blocker), 3.12 (completion), 3.13 (self-recovered). 3.9 ships a placeholder renderer that emits a one-line `f"Task {task_id}: {event_type}"` so the wiring is testable end-to-end without committing to template wording.
- NOT a multi-sink fleet — only the Telegram sink. Future sinks (email, webhook, local log tail per Phase 2) will plug into the same subscriber pattern.
- NOT a position-file / consumer-group mechanism for the sink. Phase 1 reads from offset 0 on startup and replays all events idempotently — `bot.send_message` is best-effort and Telegram dedupes by content. A real position-file pattern lands when Story 7.x adds resume-after-restart semantics for sinks.
- NOT a change to the inbound bot reply ("Task t-… created. Planning. Events on thread.") — that synchronous reply continues to land in the same chat. The sink delivers ASYNCHRONOUS progress events on top of that initial reply.
- NOT a refactor of clawhip-daemon's hello-world structure — the daemon's `__main__.py` was a Story 1.4 stub; 3.9 replaces it with a real subscriber loop.

## Acceptance Criteria

1. **AC-1: `TaskCreatedPayload` extended with `chat_id` + `reply_to_message_id`** — `services/registry-state/src/registry_state/domain/event_types.py:TaskCreatedPayload` gains two fields:
   ```python
   class TaskCreatedPayload(BaseModel):
       # ...existing fields...
       chat_id: int | None = None
       reply_to_message_id: int | None = None
   ```
   Both `Optional[int]` (nullable) for back-compat with pre-3.9 tasks. Telegram chat IDs CAN be negative (supergroup/channel chats start at -100…); the type is `int`, NOT `PositiveInt`. Pydantic v2 default-`None` keeps the schema additive per architecture.md:114 (additive-only schema evolution).

2. **AC-2: `Task` ORM table extended with two nullable columns** — `services/registry-state/src/registry_state/schema.py:Task`:
   ```python
   chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
   reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
   ```
   Use `BigInteger` (not `Integer`) — Telegram chat IDs can exceed 2^31 in supergroup/channel form. Add an Alembic migration `services/registry-state/src/registry_state/migrations/versions/<rev>_add_task_thread_binding.py` that does `ADD COLUMN ... NULL` on the existing `tasks` table (additive, zero-downtime, NFR-M3 compliant).

3. **AC-3: registry-api request body + response model extended** — `services/registry-api/src/registry_api/routes/tasks.py:CreateTaskRequest` and the response model gain the same two optional fields. `POST /v1/tasks` accepts and forwards them into the `task.created` event payload. `GET /v1/tasks/{id}` returns them in the response body so sinks can read the binding.

4. **AC-4: Materializer persists the binding** — `services/registry-state/src/registry_state/domain/materializer.py:_handle_task_created` writes `chat_id` + `reply_to_message_id` into the new ORM columns when a `task.created` event lands. Existing materializer tests should be extended to cover the new fields.

5. **AC-5: telegram-gateway client populates the fields** — `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:CreateTaskRequest` (the bot-side request shape) gains the two fields. `task_command.py:handle_task` populates them from `message.chat.id` and `message.message_id` when calling `registry_client.create_task(...)`. The existing inbound reply ("Task t-… created. Planning. Events on thread.") is unchanged.

6. **AC-6: NEW `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py`** — implements an async `TelegramOutbound` class:
   ```python
   class TelegramOutbound:
       def __init__(self, *, bot_token: AuditedSecret, http_client: httpx.AsyncClient) -> None: ...
       
       async def send_to_thread(
           self,
           *,
           chat_id: int,
           reply_to_message_id: int,
           text: str,
       ) -> None:
           """POST to https://api.telegram.org/bot<token>/sendMessage with retry."""
   ```
   - Uses `tenacity` 3× exponential backoff with jitter on transient HTTP errors (429, 5xx, network errors). Architecture.md:856 LOCKS this retry policy.
   - Reads bot_token via `AuditedSecret.value` ONCE at construction (Story 2.16 cache-once pattern; per-call `.value` reads would saturate the audit trail).
   - On terminal failure (max retries exhausted), emits a `sink.delivery_failed` typed event (already registered — `SinkDeliveryFailedPayload` exists in `event_types.py:217`) and returns; never raises to the caller.
   - HTML-escapes nothing — `text` is already-escaped by the renderer (Story 3.5 H5 carry-forward); the outbound adapter is a pure transport.
   - Sets `parse_mode="HTML"` on the `sendMessage` call so the bot honors Story 3.1's HTML-mode default.

7. **AC-7: NEW `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`** — subscriber loop:
   ```python
   class TelegramSink:
       def __init__(
           self,
           *,
           reader: EventLogReader,
           registry_api_client: RegistryAPIReadClient,
           outbound: TelegramOutbound,
           clock: Clock,
       ) -> None: ...
       
       async def run(self) -> None:
           """Subscribe to event log; for each task.* event, lookup binding; dispatch."""
   ```
   - On each event whose type starts with `task.` (e.g. `task.created`, `task.completed`, `task.blocker_raised`, `task.execution_started`, etc.):
     1. Extract `task_id` from the event payload.
     2. GET `/v1/tasks/{task_id}` from registry-api to read `(chat_id, reply_to_message_id)`.
     3. If either field is `None` (pre-3.9 task or non-Telegram-originated task), skip silently — no delivery attempted, no error.
     4. Otherwise call `_render(event)` to produce text, then `outbound.send_to_thread(...)`.
   - `_render(event) -> str` is the **placeholder renderer** for Phase 1: returns `f"Task {task_id}: {event_type}"`. Stories 3.10–3.13 replace this with proper templates (the dispatch table is structured so each event type can be rendered by a dedicated function in the future).
   - Reads from offset 0 on startup; no position file (deferred to Story 7.x). Idempotent enough for Phase 1 because Telegram dedupes by content within a short window AND because progress events are infrequent in solo-operator use.

8. **AC-8: NEW `services/clawhip-daemon/src/clawhip_daemon/app/main.py`** — promotes `__main__.py` from Story 1.4's no-op hello-world stub to a real subscriber loop:
   - `build_app()` constructs `TelegramOutbound`, `TelegramSink`, and the `EventLogReader` (reusing the same reader pattern from `services/registry-state/.../app/main.py:run_subscriber`).
   - `main()` runs `await sink.run()` indefinitely until SIGTERM/SIGINT.
   - Reads env vars: `CLAWHIP_DAEMON_LOG_DIR` (default `/var/lib/oh-my-bmad/registry/events`), `CLAWHIP_DAEMON_REGISTRY_API_URL` (default `http://registry-api:8080`), `TELEGRAM_BOT_TOKEN` (from `.env`).
   - structlog wired identically to Stories 3.6 / 3.7 (`_configure_logging()` idempotent helper).

9. **AC-9: Test coverage (≥20 tests)**:
   - **registry-state**: extend `test_materializer.py` with tests for the new payload fields persisting (3 tests: with both fields, with neither, with one).
   - **registry-state**: extend `test_schema.py` to verify the migration is additive and reads back round-trip (1 test).
   - **registry-api**: extend `test_app.py` POST/GET tests to cover the new fields (4 tests: POST with both, POST with neither (back-compat), GET reflects both, validation rejects non-int).
   - **telegram-gateway**: extend `test_task_command.py` with a test asserting `message.chat.id` + `message.message_id` are forwarded (2 tests: positive chat_id, negative supergroup chat_id).
   - **clawhip-daemon outbound** (NEW `services/clawhip-daemon/src/clawhip_daemon/adapters/test_telegram_outbound.py`): 5 tests covering happy-path send, 429 retry, 500 retry, network-error retry, and `sink.delivery_failed` emission on terminal failure.
   - **clawhip-daemon sink** (NEW `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`): 5 tests covering happy-path dispatch, skip-on-missing-binding, skip-on-pre-3.9-task, skip-on-non-task-event, and the placeholder renderer output shape.
   - **integration** (NEW `tests/integration/test_task_thread_binding.py`, marked `@pytest.mark.integration`): 1 end-to-end test creating a task with `(chat_id=-1001, message_id=42)`, emitting a synthetic `task.completed` event into the log, asserting `bot.send_message` was called with `chat_id=-1001, reply_to_message_id=42, text="Task t-...: task.completed"`.
   
   Total: 21 tests minimum.

10. **AC-10: Architectural gates green** — same matrix as Story 3.8 (now 9 gates):
    - `check_imports`: clawhip-daemon imports stdlib + httpx + tenacity + `events.*` + `secret_hygiene.*` only; cross-service `registry_state.*` ORM import allowed via `# noqa: IMP001 — Story 2.9 AC-16` (clawhip-daemon needs to read Task rows or call registry-api HTTP — pick HTTP to avoid the noqa).
    - `check_event_registry`: `task.created` schema is bumped to `1.1.0` per architecture.md:114 additive-only rule (the old payload is `1.0.0`; new fields are optional; sinks accept both versions). Register the new version in `packages/events/src/events/schema_registry.py` in the SAME commit as the first emission per AC-12.
    - `check_single_writer`: clawhip-daemon writes ZERO events to SQLite. The only event it CAN write is `sink.delivery_failed` (via `clawhip-bridge` MCP per architecture.md:846), NOT direct SQLite. Verify by `check_single_writer.py`.
    - `check_no_subprocess`: spine-clean (Story 3.8 added `services/clawhip-daemon/src/` to glob discovery via M13; now actually has source — verify the gate scans it and finds zero violations).
    - `secret-hygiene-precommit`: clean — `TELEGRAM_BOT_TOKEN` is read via `AuditedSecret`.

11. **AC-11: Schema-registry version bump** — `packages/events/src/events/schema_registry.py` registers `("task.created", "1.1.0", TaskCreatedPayloadV1_1)` alongside the existing `("task.created", "1.0.0", TaskCreatedPayloadV1_0)`. The materializer dispatches on `schema_version`: v1.0.0 events ignore the new fields (back-compat); v1.1.0 events read them. Architecture.md:114 forbids breaking changes — additive-only is enforced.

12. **AC-12: Scope boundary** — files modifiable in this story:
    - **New (8):**
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py`
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/test_telegram_outbound.py`
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/__init__.py`
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/__init__.py`
      - `services/clawhip-daemon/src/clawhip_daemon/app/main.py`
      - `tests/integration/test_task_thread_binding.py`
    - **Modified (≈12):**
      - `services/registry-state/src/registry_state/domain/event_types.py` (AC-1)
      - `services/registry-state/src/registry_state/schema.py` (AC-2)
      - `services/registry-state/src/registry_state/migrations/versions/<rev>_add_task_thread_binding.py` (NEW migration, treated as Modified-tree because Alembic versions live alongside existing migrations)
      - `services/registry-state/src/registry_state/domain/materializer.py` (AC-4)
      - `services/registry-state/src/registry_state/domain/test_materializer.py` (AC-9)
      - `services/registry-state/src/registry_state/test_schema.py` (AC-9)
      - `services/registry-api/src/registry_api/routes/tasks.py` (AC-3)
      - `services/registry-api/src/registry_api/test_app.py` (AC-9)
      - `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (AC-5)
      - `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` (AC-5)
      - `services/telegram-gateway/src/telegram_gateway/test_task_command.py` (AC-9)
      - `packages/events/src/events/schema_registry.py` (AC-11)
      - `services/clawhip-daemon/src/clawhip_daemon/__main__.py` (AC-8 — replaces hello-world stub with delegate to `app.main:main`)
      - `services/clawhip-daemon/pyproject.toml` (add `httpx`, `tenacity`, `aiogram` (or just httpx for direct API calls — pick one; the architecture only requires `tenacity 3× exp-backoff`))
    - **Not modifiable:**
      - Other test files in services not listed above
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (only the standard `backlog → ready-for-dev → in-progress → review → done` flips)

13. **AC-13: New dependencies** — `services/clawhip-daemon/pyproject.toml` gains explicit deps:
    - `httpx>=0.27` (Telegram Bot API client)
    - `tenacity>=8.2` (retry decorator; architecture.md:856 LOCK)
    - `events` (workspace, already implicitly transitive via secret-hygiene; declare explicit per Story 3.6/3.7 review pattern)
    - `secret-hygiene` (workspace; for `AuditedSecret`)
    - `registry-state` (workspace; for `EventLogReader` and ORM read access — OR avoid this by using HTTP-only via registry-api `GET /v1/tasks/{id}`; prefer the HTTP path to avoid spine-cross-service import)
    - `structlog>=24.1` (Story 3.6 carry-forward)
    
    No NEW third-party dependency beyond `tenacity` (which arch.md already commits to). Verify `uv lock` churn limited to the explicit-dep additions.

14. **AC-14: Atomic commit + Epic-2-retro AI #1 verify** — single atomic commit titled exactly:
    ```
    feat(clawhip-daemon,registry-*,telegram-gateway): story 3.9 — task thread binding + Telegram outbound sink (placeholder renderer) · FR13
    ```
    `just lint` 9/9 green. `just test` count grows by ≥21 (target ~886+). `just test-fuzz` unchanged (7 tests). **Independently re-verify** (Epic-2-retro AI #1 — pattern that has caught 10+ issues this session). Spine-sentinel test (`test_spine_source_code_unchanged`) WILL FIRE — Story 3.9 modifies `services/registry-state/src/`, `services/registry-api/src/`, `services/telegram-gateway/src/`, and adds new files in `services/clawhip-daemon/src/`. Same disposition as Stories 3.6/3.7 — accepted as known signal per the test's TODO(s3-ast).

15. **AC-15: Story 3.6/3.7/3.8 carry-forwards honored**:
    - **Story 3.6 review L1** — `MappingProxyType` for read-only constants if any.
    - **Story 3.6 review M5** — mutation-method gate (`_MUTATING_METHODS`) is irrelevant here; clawhip-daemon makes only HTTP GET calls to registry-api.
    - **Story 3.6 review N7** — avoid cross-service IMP001 noqa proliferation. Use HTTP, not direct ORM imports. Sink reads via `registry_api_client.get_task(task_id)` (an HTTP `GET /v1/tasks/{id}` adapter), NOT via `registry_state.schema.Task`.
    - **Story 3.7 H4** — wire-key collision-safe namespace. The new fields land at the TOP of `TaskCreatedPayload` (siblings of `task_id` and `title`), not nested under `extensions` — they are core domain data, not a nudge.
    - **Story 3.7 L16** — `_INTERNAL_ERROR_MESSAGE` shared constant; not affected here.
    - **Story 3.8 H4** — runtime `sys.modules["subprocess"]` sentinel; not affected here (clawhip-daemon doesn't import subprocess).
    - **Story 3.8 H9** — multi-tag `# noqa: IMP001, SHELL001 — reason` now works; if any imports need both tags, this is the green path.
    - **Story 3.8 M12/M13** — spine-roots glob-discovery; `services/clawhip-daemon/src/` is now scanned by `check_no_subprocess.py`. Verify the daemon's source is subprocess-free.
    - **Epic-2-retro AI #1** — independent gate verify before flipping done. Mandatory.

16. **AC-16: Telegram Bot API surface** — sink uses `https://api.telegram.org/bot<TOKEN>/sendMessage` directly via `httpx` (NOT via `aiogram.Bot`). Rationale: clawhip-daemon already has zero aiogram surface area; adding it would import 30+ MB of dispatcher framework just for one method. Direct httpx is ~20 LoC and matches the architecture.md:846 "outbound HTTP" model. The bot's INBOUND path (telegram-gateway) keeps using `aiogram` for webhook handling.

## Tasks / Subtasks

- [x] **Task 1: Schema + event-payload extension** (AC: #1, #2, #11)
  - [x] Add `chat_id` + `reply_to_message_id` to `TaskCreatedPayload` (event_types.py).
  - [x] Add `chat_id` + `reply_to_message_id` (BigInteger, nullable) columns to `Task` ORM (schema.py).
  - [x] Generate Alembic migration `<rev>_add_task_thread_binding.py` via `alembic revision -m "add task thread binding"`; verify additive-only (`op.add_column(..., nullable=True)`).
  - [x] Bump `task.created` schema version: register both `1.0.0` (existing) and `1.1.0` (new fields) in `packages/events/src/events/schema_registry.py`.
  - [x] Run `alembic upgrade head` against a fresh tmp DB; verify no errors.

- [x] **Task 2: Materializer + registry-api wire extension** (AC: #3, #4)
  - [x] Extend `_handle_task_created` to write `chat_id` / `reply_to_message_id` into the Task row when present.
  - [x] Extend `CreateTaskRequest` Pydantic model in `routes/tasks.py` with the two optional fields.
  - [x] Extend `TaskCreatedResponse` (or `GetTaskResponse`) to surface them.
  - [x] POST handler emits the fields in the `task.created` event payload.

- [x] **Task 3: telegram-gateway client + handler population** (AC: #5)
  - [x] Extend bot-side `CreateTaskRequest` in `handlers/registry_client.py`.
  - [x] `task_command.py:handle_task` populates from `message.chat.id` and `message.message_id` (negative values OK; supergroup chats use them).

- [x] **Task 4: clawhip-daemon TelegramOutbound + Sink + entrypoint** (AC: #6, #7, #8, #13, #16)
  - [x] NEW `clawhip_daemon/adapters/telegram_outbound.py` — httpx + tenacity 3× retry; `sink.delivery_failed` event emission on terminal failure; `parse_mode="HTML"`.
  - [x] NEW `clawhip_daemon/adapters/sinks/telegram_sink.py` — subscriber loop; `task.*` event filter; lookup via registry-api `GET /v1/tasks/{id}`; placeholder renderer (`f"Task {task_id}: {event_type}"`).
  - [x] NEW `clawhip_daemon/app/main.py` — `build_app` + `main` entrypoint; structlog wiring.
  - [x] Modify `__main__.py` to delegate to `app.main:main`.
  - [x] Update `services/clawhip-daemon/pyproject.toml` deps.

- [x] **Task 5: Tests** (AC: #9)
  - [x] Materializer tests (3 new in test_materializer.py).
  - [x] Schema migration test (1 new in test_schema.py).
  - [x] registry-api POST/GET tests (4 new in test_app.py).
  - [x] telegram-gateway test_task_command tests (2 new — positive + negative chat_id).
  - [x] NEW test_telegram_outbound.py (5 tests).
  - [x] NEW test_telegram_sink.py (5 tests).
  - [x] NEW tests/integration/test_task_thread_binding.py (1 e2e test).

- [x] **Task 6: Regression verification + atomic commit** (AC: #14)
  - [x] `just test` — confirm ≥21 new tests pass (target 865 → 886+).
  - [x] `just lint` — 9/9 green; `check_no_subprocess.py` scans the new clawhip-daemon source.
  - [x] `just bootstrap-verify` — clawhip-daemon module import works.
  - [x] **Independent gate verify** before flipping `review → done`.
  - [x] Note expected `test_spine_source_code_unchanged` failure in Completion Notes (modifies registry-state + registry-api + telegram-gateway src/).
  - [x] Flip `sprint-status.yaml`: `3-9-task-thread-binding: ready-for-dev → in-progress → review → done`.
  - [x] Atomic commit with the exact title from AC-14.

## Dev Notes

### Quoted Requirements

> **FR13** (`prd.md:827`): "Operator can bind a Telegram thread to a task id such that subsequent progress events for that task deliver to the same thread."

> **Architecture.md:707** — `services/clawhip-daemon/.../sinks/telegram_sink.py # outbound rendering`.

> **Architecture.md:710** — `services/clawhip-daemon/.../adapters/telegram_outbound.py # Telegram Bot API sendMessage client`.

> **Architecture.md:856** — outbound LOCKS: `tenacity 3× exp-backoff`; secret via `TELEGRAM_BOT_TOKEN`.

> **Architecture.md:846** — "clawhip-daemon → Telegram sink: formatted outbound message".

### Why HTTP-Only Lookup (No Cross-Service ORM Import)

clawhip-daemon needs `(chat_id, reply_to_message_id)` per task. Two options:
- (a) Direct `from registry_state.schema import Task` + read-only SQLite query.
- (b) HTTP `GET /v1/tasks/{id}` to registry-api.

**Choosing (b)** to honor Story 3.6 review N7 (no IMP001 noqa proliferation) and Story 3.7 N6 (services communicate via wire contracts). The slight latency overhead is acceptable in Phase 1; a future story can introduce a sink-local read cache or shared MCP read surface if needed.

### Why Direct httpx Instead of `aiogram.Bot`

clawhip-daemon has zero aiogram surface today. Adding `aiogram` for one Telegram API method (`sendMessage`) imports the full dispatcher framework (~30 MB on disk including `aiohttp`, dependency injection, FSM machinery). A direct `httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={...})` is ~20 LoC, one dep, easier to test. The bot's INBOUND path (telegram-gateway) keeps `aiogram` because it leverages dispatcher routing and webhook handling.

### Why Schema Version Bump (1.0.0 → 1.1.0)

`task.created` payload is GROWING, not changing. Per architecture.md:114 (additive-only NFR-M3), the bump is *additive minor* (1.0.0 → 1.1.0). Both versions register; the materializer dispatches on `schema_version` and:
- v1.0.0: ignores new fields (back-compat for replay of old events).
- v1.1.0: reads new fields.

Older deployment events in the JSONL log don't break — they continue to deserialize as v1.0.0.

### Why Phase-1 Sink Is Fire-and-Forget From Offset 0

Architecture.md:707 says outbound rendering lives in clawhip-daemon. The sink's resume-after-restart behavior is NOT specified yet (Story 7.x territory). Phase 1 reads from offset 0 on every startup. Implications:
- Solo-operator scale: ≤1000 events/day → ≤1000 outbound `sendMessage` calls per restart, well within Telegram's per-bot rate limit (30 msg/s).
- Telegram dedupes by content within a short window — most operators won't notice replayed messages.
- The `sink.delivery_failed` event provides observability when delivery fails terminally.
- A future Story 7.x adds position-file resume; placeholder pattern is intentional Phase 1 simplification.

### Architecture References

- `prd.md:827` — FR13.
- `architecture.md:114` — additive-only schema evolution (NFR-M3).
- `architecture.md:231` — clawhip-daemon → Telegram sink: HTTP outbound.
- `architecture.md:707` — `telegram_sink.py # outbound rendering` placement.
- `architecture.md:710` — `telegram_outbound.py # Telegram Bot API sendMessage client` placement.
- `architecture.md:846` — clawhip-daemon → Telegram sink → formatted outbound message.
- `architecture.md:856` — `tenacity 3× exp-backoff` LOCK; `TELEGRAM_BOT_TOKEN` secret.
- Story 1.4 — clawhip-daemon hello-world stub (replaced by 3.9).
- Story 2.1 / 2.4 — event log + envelope.
- Story 2.5 — registry-state subscriber pattern (clawhip-daemon mirrors it).
- Story 2.16 — `AuditedSecret` cache-once.
- Story 3.6 / 3.7 / 3.8 — review carry-forwards (N7 noqa avoidance, structlog wiring, MappingProxyType, multi-tag noqa).

## Review Findings

Three-layer adversarial review of commit `39170a0` on 2026-05-01 (Blind / Edge / Auditor on Opus). User directive "fix all issues even minors" applies. After dedup: **11 High · 18 Medium · 27 Low = 56 patches**, **0 deferred**, **5 dismissed-as-noise**.

### High severity

- [x] [Review][Patch] **H1 — Bot-token leaks via URL into structlog ERROR output** [Blind+Edge]: `url = f"{_TELEGRAM_BASE_URL}/bot{self._token}/sendMessage"`; on terminal failure `_log.error(... exc=str(exc))` renders `httpx.HTTPStatusError` whose `repr()` includes `request.url`. `redact_secrets` (structlog processor) only knows `AuditedSecret`-tracked tokens; the cached plain-string `self._token` is invisible to the redactor. Fix: keep the AuditedSecret wrapper as `self._secret` AND register its value with the redactor's known-secret set at construction (or scrub the URL before logging) [telegram_outbound.py:_TELEGRAM_BASE_URL + send_to_thread]
- [x] [Review][Patch] **H2 — `except BaseException` swallows `CancelledError` / `SystemExit` / `KeyboardInterrupt`** [Blind+Auditor]: catch in retry-loop body uses `BaseException`. Asyncio `CancelledError` is `BaseException`-derived in 3.8+; SIGTERM cancellation is silently downgraded to ERROR log + emit attempt, loop continues another iteration. Fix: `except Exception` (or explicitly re-raise `BaseException` types after handling) [telegram_outbound.py:1279]
- [x] [Review][Patch] **H3 — JSONL parse error crashes the entire sink permanently** [Edge#H1]: `from_canonical_json(raw.rstrip(b"\r\n"))` raises on malformed JSON; no try/except in `_read_new_envelopes_since` → propagates up through `_scan_all_files` → `run()` task exits → no auto-restart in `app/main.py`. One corrupt line = permanent loss of all subsequent Telegram delivery until pod restart. Fix: try/except around the parse, log offset+exception, advance past the bad line [telegram_sink.py:_read_new_envelopes_since]
- [x] [Review][Patch] **H4 — Eventual-consistency race: 404 silently drops `task.created` events** [Edge#H2 + Auditor]: registry-api emits `task.created` to the JSONL log BEFORE materializer commits the row. Sink polls the log within 100 ms, hits the GET, gets 404, treats as "non-Telegram task" → drops silently. Probabilistic in normal operation; deterministic loss under load (slow disk, GC pause). Fix: distinguish 404-task-not-yet-materialized (retry once after ≥200 ms) from 404-truly-missing; integration test must cover the race [telegram_sink.py:_lookup_binding + integration test]
- [x] [Review][Patch] **H5 — registry-api unreachable on startup → all events silently dropped, no operator alarm** [Edge#H4]: `_lookup_binding` catches `httpx.HTTPError` and returns `(None, None)`. Boot-order race: clawhip-daemon comes up first, processes minutes of buffered events with no registry-api, all dropped. Fix: track `consecutive_lookup_failures`; emit WARN-level structured log + counter when N>5, OR refuse to enter dispatch loop until registry-api responds healthy on first poll [telegram_sink.py:_lookup_binding + run]
- [x] [Review][Patch] **H6 — Telegram 429 `Retry-After` header ignored** [Blind+Edge]: generic `wait_exponential(multiplier=0.5, max=8)` retry capped at 8 s. Telegram's 429 carries `Retry-After: <seconds>` (header) or `parameters.retry_after` (JSON body). When the header says `30` (typical flood-wait), the 3-attempt budget is consumed inside the rate-limit window — guaranteed terminal failure. Fix: parse `Retry-After` header (and JSON `parameters.retry_after`) on 429; use a custom `wait` callable to honor it [telegram_outbound.py:retry config]
- [x] [Review][Patch] **H7 — Schema registration is in the wrong file** [Auditor#1]: AC-11 mandates `packages/events/src/events/schema_registry.py` register `("task.created", "1.1.0", ...)`. The diff registers from `services/registry-state/src/registry_state/domain/event_types.py:351` and does NOT modify `packages/events/src/events/schema_registry.py`. The package was set up to be the canonical version registry. Fix: move the `register("task.created", "1.1.0", TaskCreatedPayload)` call into `packages/events/src/events/schema_registry.py` (where 1.0.0 lives) [event_types.py:351 + packages/events/src/events/schema_registry.py]
- [x] [Review][Patch] **H8 — `sink.delivery_failed` typed event NOT emitted on terminal failure** [Auditor#2]: AC-6 explicitly says "emits a `sink.delivery_failed` typed event ... and returns; never raises to the caller". Implementation uses an optional `emit` callback defaulted to `None` AND `app/main.py` passes nothing; only structlog ERROR is logged. Self-acknowledged Deviation #1. Fix: wire the emit callback in `app/main.py` via the `clawhip-bridge` MCP client (the canonical event-emission path per architecture.md:846) — even if `clawhip-bridge` lands in a future story, scaffolding the path now is required by AC-6. Document the emit-shape contract; fall back to structlog if MCP client is None [telegram_outbound.py + app/main.py]
- [x] [Review][Patch] **H9 — Out-of-scope `_EXPECTED_BODY_KEYS` strict-equality update breaks Story 3.8 contract for future fields** [Auditor#4 + Edge]: `tests/integration/test_command_injection_fuzz.py:761` was updated to `frozenset({"title", "chat_id", "reply_to_message_id"})` with `set(body_obj.keys()) == _EXPECTED_BODY_KEYS`. AC-3 already plumbs `repo`/`hint` through; future stories adding more fields will break the assertion. Fix: relax to `_EXPECTED_BODY_KEYS.issubset(body_obj.keys())` AND assert no UNEXPECTED key (a separate `_FORBIDDEN_BODY_KEYS` allow-list-style check), so injection-prevention property survives but legitimate additions don't trip it [test_command_injection_fuzz.py]
- [x] [Review][Patch] **H10 — `rstrip(b"\r\n")` is byte-set strip not literal trailer strip** [Blind#4]: behavior strips ANY combination of `\r` / `\n` characters from the right; on cross-platform writers (`\r\n` line endings) the offset arithmetic uses `len(raw)` (full line) but `rstrip` may over-strip if a payload legitimately ends with content that resembles `\r`. Fix: explicit `if raw.endswith(b"\r\n"): raw = raw[:-2]; elif raw.endswith(b"\n"): raw = raw[:-1]` [telegram_sink.py:_read_new_envelopes_since]
- [x] [Review][Patch] **H11 — `AuditedSecret(token_raw, emit=None)` bypasses Story 2.16 audit contract** [Blind#5]: every other service emits an audit event on each `.value` read. clawhip-daemon silently bypasses entirely. The "cache-once" pattern was meant to reduce audit volume, NOT eliminate audits. Fix: wire a minimal `emit` callback that writes a single boot-time `secret.accessed` envelope (via the same `clawhip-bridge` channel as H8), or via local structlog-WARN if MCP wiring is deferred. Document the contract and the deviation [app/main.py:1736]

### Medium severity

- [x] [Review][Patch] **M1 — Shared `httpx.AsyncClient` between Telegram (api.telegram.org) and registry-api (LAN)** [Blind+Edge]: same client used for both; different latency profiles, connection-pool needs, and TLS handshake costs. A slow `sendMessage` POST can stall a registry-api `GET /v1/tasks/{id}` lookup via head-of-line blocking. Fix: instantiate two clients in `app/main.py` — `_telegram_http_client` (longer timeout, larger pool) and `_registry_http_client` (shorter timeout, internal LAN tuning) [app/main.py:build_app]
- [x] [Review][Patch] **M2 — Telegram `ok: false` HTTP-200 responses treated as success** [Blind#7]: `response.raise_for_status()` only covers 4xx/5xx. Telegram occasionally returns `{"ok": false, "error_code": ..., "description": "..."}` with HTTP 200. Fix: after `raise_for_status`, parse JSON and assert `body["ok"] is True`; on `ok: false` raise `TelegramApiError` (retry on `error_code in {429, 5xx}`, terminal otherwise) [telegram_outbound.py]
- [x] [Review][Patch] **M3 — `consecutive_failures=1` hardcoded** [Blind#8]: literal `1` in the emit payload; field name implies running counter across failures. Fix: track per-`TelegramOutbound` instance counter that increments on each consecutive terminal failure and resets on success [telegram_outbound.py:1296]
- [x] [Review][Patch] **M4 — `response.json()` not type-validated in `_lookup_binding`** [Blind+Auditor]: registry-api emits a Pydantic `TaskResponse`; sink parses with raw `dict.get`. Future `TaskResponse` field renames silently break the sink. Fix: use a local Pydantic `TaskBindingResponse(BaseModel)` to parse + extract; use `model_validate` for type-safe access [telegram_sink.py:_lookup_binding]
- [x] [Review][Patch] **M5 — `offsets: dict[str, int] = {}` is local to `run()` AND keyed by filename → orphan keys accumulate forever** [Blind#9]: across UTC-midnight rollover, the previous day's filename stays in the dict. Solo-operator scale OK but production smell. Fix: prune offsets older than N days, OR key by `Path` and discard when file is missing on next iteration [telegram_sink.py:run]
- [x] [Review][Patch] **M6 — `sorted(base_dir.glob("*.jsonl"))` lex-sort** [Blind#10]: relies on `YYYY-MM-DD.jsonl` filename pattern lex-sortable; non-conforming files (e.g. `partial.jsonl`) sort unpredictably. Fix: filter by regex `^\d{4}-\d{2}-\d{2}\.jsonl$` before sorting; use `key=lambda p: p.name` to be explicit [telegram_sink.py:_scan_all_files]
- [x] [Review][Patch] **M7 — N+1 GET to registry-api per event (no caching)** [Blind+Edge+Auditor]: bindings are immutable (set once at task.created, never updated). 8 lifecycle events × 100 tasks = 800 redundant GETs. Fix: add `cachetools.TTLCache(maxsize=1000, ttl=3600)` keyed by `task_id`; cache miss → GET + populate; cache hit → use cached binding [telegram_sink.py:_lookup_binding]
- [x] [Review][Patch] **M8 — `request_id` from envelope NOT propagated as `X-Request-ID` on registry-api lookup** [Blind#11]: distributed tracing breaks at sink boundary. Fix: pass `headers={"X-Request-ID": envelope.request_id}` (use a fresh UUIDv7 if envelope.request_id is None) on every `GET /v1/tasks/{id}` call [telegram_sink.py:_lookup_binding]
- [x] [Review][Patch] **M9 — Integration test only asserts `text.startswith("Task t-")` and `text.endswith(": task.completed")` — weak** [Blind#integration]: future renderer changes (Story 3.10) break the start/end constraints. HTML-escape branch never exercised in integration. Fix: assert exact placeholder format `f"Task {task_id}: task.completed"` AND assert `<` / `>` / `&` produce escaped output (use a synthetic `task_id` that triggers escaping or a stricter event_type) [test_task_thread_binding.py]
- [x] [Review][Patch] **M10 — Test files cross-import `registry_state.domain.event_types` despite Story 3.6 N7 carry-forward in production** [Blind#12]: production sink is HTTP-only to honor N7 (no IMP001 noqa); tests import directly with `noqa: IMP001` 4 times. Fix: build envelopes via the same HTTP-emission path the production sink expects, OR move the cross-import to a shared `tests/fixtures/event_envelopes.py` helper that consolidates the noqa to one location [adapters/sinks/test_telegram_sink.py + tests/integration/test_task_thread_binding.py]
- [x] [Review][Patch] **M11 — `request.read() and __import__("json").loads(request.content)` brittle test scaffold** [Blind#13]: in async httpx, `request.read()` is a coroutine; calling from sync transport lambda returns a coroutine which is truthy → `and` evaluates the right side; works only because `request.content` is cached. Fix: use `json.loads(request.content)` directly (not via `__import__`); confirm consistent with the sister test [test_task_command.py:2382]
- [x] [Review][Patch] **M12 — `MagicMock()` without `spec=TelegramOutbound`** [Blind#14]: forces `# type: ignore[arg-type]` at every test site; suppressed type errors hide real interface drift. Fix: `MagicMock(spec=TelegramOutbound)` + `outbound_mock.send_to_thread = AsyncMock()` so calls validate against the real interface [test_telegram_sink.py]
- [x] [Review][Patch] **M13 — `chat_id=0` and `reply_to_message_id=0` accepted as valid** [Edge#M5]: Pydantic `int | None` accepts `0`. Telegram returns `400 chat not found` for `chat_id=0`, `400 replied message not found` for `reply_to_message_id=0`. Fix: `chat_id: int | None = Field(default=None)` with custom validator rejecting `0` (Telegram never uses 0); `reply_to_message_id: int | None = Field(default=None, gt=0)` (message IDs are strictly positive) — apply consistently in registry-api `CreateTaskRequest`, `TaskResponse`, registry-state `TaskCreatedPayload`, and bot `CreateTaskRequest` [registry-api/routes/tasks.py + registry-state/event_types.py + telegram-gateway/registry_client.py]
- [x] [Review][Patch] **M14 — AC-4 spec text mismatch: `materializer.py:_handle_task_created` does not exist** [Auditor#5]: actual handler lives at `handlers.py:handle_task_created` (no leading underscore); `materializer.py` only registers it. Fix: amend the spec text in this story file (and a follow-up doc-PR if other stories cite the wrong path) [3-9 spec doc AC-4 + AC-12 file list]
- [x] [Review][Patch] **M15 — AC-7 typed `EventLogReader`/`RegistryAPIReadClient` constructor signatures dropped** [Auditor#6]: spec specifies clean DI shape; implementation took raw `Path`/`str`/`httpx.AsyncClient`. SOLID DIP violation; future Story 7.x position-file work has to refactor TelegramSink internals instead of swapping the reader. Fix: extract `EventLogReader` Protocol (or thin wrapper class) in `clawhip_daemon/adapters/event_log_reader.py`; inject via the constructor; same for `RegistryAPIReadClient` [telegram_sink.py:__init__ + new event_log_reader.py]
- [x] [Review][Patch] **M16 — `from tenacity.wait import wait_random` uses non-public submodule** [Auditor#7]: tenacity's stability covers only top-level `tenacity.*`. Fix: `from tenacity import wait_random` [telegram_outbound.py:1173]
- [x] [Review][Patch] **M17 — `_lookup_binding` swallows transient errors AS skip-silently** [Auditor#9]: 5xx / transport / parse errors all return `(None, None)` and are treated identically to legitimate 404. A flaky registry-api silently loses events. Fix: distinguish 404 (legitimate) from 5xx/transport (transient) — retry once or twice on transient with backoff before declaring "skip silently" [telegram_sink.py:_lookup_binding]
- [x] [Review][Patch] **M18 — Sink test count discrepancy: spec said 5, completion notes claim 6, file shows 5** [Auditor#3]: Either count is correct but the dev-pass disposition contradicts itself. Spec also calls for explicit "skip-on-pre-3.9-task" coverage that's collapsed into "skip-on-missing-chat_id" / "skip-on-missing-reply_to". Fix: add a true pre-3.9-replay test (a Task row with `chat_id=NULL` AND `reply_to_message_id=NULL` from a v1.0.0-event materialization), plus a test that `event.type` exactly outside the allowlist is skipped (M19) [test_telegram_sink.py]

### Low severity

- [x] [Review][Patch] **L1 — `_AppComponents` NamedTuple defined but unused** [Blind#L1]: dead code. Drop the class + the `from typing import NamedTuple` import [app/main.py]
- [x] [Review][Patch] **L2 — `last_exc: BaseException | None = None` initialization is dead** [Blind#L]: variable is only read inside the `except` block where it's already `exc`. Drop the variable; reference `exc` directly [telegram_outbound.py]
- [x] [Review][Patch] **L3 — `emit: Callable[[object], Awaitable[None]] | None` loose typing** [Blind#L]: payload type is `object`. Fix: `Callable[[SinkDeliveryFailedPayload], Awaitable[None]]` to match the spec contract [telegram_outbound.py]
- [x] [Review][Patch] **L4 — `Clock | None = None` injection in `TelegramSink` stored but never used** [Blind#L]: dead injection. Drop the parameter, OR use it (timestamp the dispatch latency for metrics) [telegram_sink.py]
- [x] [Review][Patch] **L5 — Magic seeds `Random(99)`, `Random(42)`, `Random(77)` in test helpers** [Blind#L]: deterministic only as long as test order is stable; pytest-randomly would break cross-test assertions. Fix: use a single test-fixture-scoped seed, document it [test_telegram_sink.py]
- [x] [Review][Patch] **L6 — SQLite `op.drop_column` in `downgrade()` requires `batch_alter_table` for SQLite < 3.35** [Blind#L]: down-migration may fail on older SQLite. Fix: wrap `downgrade()` in `with op.batch_alter_table("tasks") as batch_op:` [migrations/2026-04-30_0002_add_task_thread_binding.py]
- [x] [Review][Patch] **L7 — `tenacity` lockfile drift: spec `>=8.2`, lockfile `9.1.4`** [Blind+Auditor]: pin spec lower-bound to `>=9.0` if 9-only behavior is relied on, OR test against 8.2. Verify `wait_exponential + wait_random` API survived the major bump [pyproject.toml + uv.lock]
- [x] [Review][Patch] **L8 — `text` length not validated client-side; Telegram caps at 4096 chars** [Blind+Edge]: pathological renderer producing 5000-char text → 400 every event. Fix: pre-flight `if not text or len(text) > 4096: log+drop` saves one round-trip per malformed render [telegram_outbound.py]
- [x] [Review][Patch] **L9 — Empty `text` not validated** [Edge#L]: same fix as L8 [telegram_outbound.py]
- [x] [Review][Patch] **L10 — `_safe_truncate` byte-vs-char ambiguity** [Blind#L]: char count, not byte count; 4-byte emoji can produce 4× expected bytes. Fix: truncate on bytes via `s.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")` [telegram_outbound.py:_safe_truncate]
- [x] [Review][Patch] **L11 — Hardcoded `_TELEGRAM_BASE_URL`** [Blind#L]: no env-var override for staging/test. Fix: `_TELEGRAM_BASE_URL = os.environ.get("TELEGRAM_API_BASE_URL", "https://api.telegram.org")` [telegram_outbound.py]
- [x] [Review][Patch] **L12 — `Actor(kind="system", id=_SERVICE)` uses `"clawhip-daemon"` not UUID7** [Blind#L]: convention drift; other services may use UUID. Verify constraint in `Actor.id` validator [app/main.py]
- [x] [Review][Patch] **L13 — Migration adds NULL columns with no index** [Edge#L]: chat_id queries (Story 3.10+ "broadcast to chat") will full-table-scan. Document as known scaling cliff in the migration docstring; add index in a future story [migrations/0002_add_task_thread_binding.py]
- [x] [Review][Patch] **L14 — Migration runs while registry-state is mid-flight (FR26 single-writer)** [Edge#L]: `alembic upgrade` while writer is running → SQLite `database is locked`. Fix: docstring note "registry-state MUST be stopped before this migration runs (FR26 single-writer)" [migrations/0002_add_task_thread_binding.py]
- [x] [Review][Patch] **L15 — `task.` prefix filter is too broad** [Edge#L]: catches future `task.internal.heartbeat` (worker-only). Fix: positive allowlist `_DELIVERABLE_EVENT_TYPES = frozenset({"task.created", "task.planning.started", "task.plan.ready", "task.execution.started", "task.blocker_raised", "task.summary_emitted", "task.approval_requested", "task.completed"})` [telegram_sink.py:_handle]
- [x] [Review][Patch] **L16 — Final batch shutdown delay** [Edge#L]: after `_scan_all_files`, all queued events dispatch sequentially before `stop.is_set()` is checked again. Worst-case: 50 events × 24 s retry = 20 min shutdown. Fix: check `stop.is_set()` between dispatches [telegram_sink.py:run]
- [x] [Review][Patch] **L17 — schema 1.0.1 also pre-existing (Auditor#11)**: AC-11 wording mentions only 1.0.0; a 1.0.1 registration is also present from a prior story. Fix: spec text amendment in this story file [3-9 spec doc AC-11]
- [x] [Review][Patch] **L18 — Token format not validated at boot** [Blind#L]: whitespace-only token `" "` bypasses `if not token_raw` check. Fix: regex validate `\d+:[A-Za-z0-9_-]+` after strip [app/main.py]
- [x] [Review][Patch] **L19 — `TaskResponse` lacks `model_config(strict=True)`** [Blind#L]: drift risk vs `CreateTaskRequest`. Fix: align both [registry-api/routes/tasks.py]
- [x] [Review][Patch] **L20 — `chat_id` no `Field(ge=-(2**63), le=(2**63)-1)` bounds** [Auditor#LOW]: oversize attacker input round-trips as Python int but breaks SQLite BigInteger insert. Fix: explicit Field bounds [event_types.py + registry-api/routes/tasks.py]
- [x] [Review][Patch] **L21 — Unused `request.read() and` prefix in test** [Blind#L]: drop-in fix [test_task_command.py:2382]
- [x] [Review][Patch] **L22 — Inline `__import__("pydantic", fromlist=...)` in materializer test** [Blind#L]: defeats refactoring tools. Fix: top-level `from pydantic import BaseModel` [test_materializer.py:2068]
- [x] [Review][Patch] **L23 — Unused `response_status: int = 200` parameter in test helpers** [Blind#L]: never overridden. Drop or use [test_telegram_sink.py]
- [x] [Review][Patch] **L24 — `_INI_PATH` relative-to-test-file fragile** [Blind#L]: pre-existing issue; document or use marker-walk [test_migrations.py:2211]
- [x] [Review][Patch] **L25 — `# noqa: SW001` test-only fixture seeding bypasses materializer** [Blind#L]: tests should still go through the materializer for binding scenarios. Fix: extract a `_seed_task_via_materializer()` helper [registry-api/test_app.py]
- [x] [Review][Patch] **L26 — Signal-handler race window in `_run`** [Auditor#L]: tiny window between `asyncio.run` start and `_install_signal_handlers`. Document as acceptable Phase 1 [app/main.py]
- [x] [Review][Patch] **L27 — Integration test bypasses `run()` loop** [Edge#L]: only `_handle()` called directly; offset accounting, file rotation, polling untested. Fix: invoke `run()` with a `stop_event` set after one iteration [test_task_thread_binding.py]

### Dismissed (false positives / out-of-scope)

- N1: Windows signal handlers (Edge#L) — Phase 1 deployment is Linux only; out of scope.
- N2: macOS HFS+ case-insensitive filename sort (Blind#L) — cosmetic; no impact on `YYYY-MM-DD.jsonl` filenames.
- N3: HTML-escape claim contradiction in renderer (Auditor#L withdrawn) — verified compliant on re-check.
- N4: `concurrent send_to_thread` httpx connection pool (Blind LOW informational) — sink loop is single-task; no concurrent calls today.
- N5: `_AppComponents` import would become orphan — covered by L1 (dropping the class).

### Predicted File List

| File | Change |
|---|---|
| `packages/events/src/events/schema_registry.py` | Modified — register `(task.created, 1.1.0, TaskCreatedPayloadV1_1)` alongside 1.0.0 |
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — `TaskCreatedPayload` gains `chat_id` + `reply_to_message_id` |
| `services/registry-state/src/registry_state/schema.py` | Modified — `Task` ORM gains 2 nullable BigInteger columns |
| `services/registry-state/src/registry_state/migrations/versions/<rev>_add_task_thread_binding.py` | NEW — additive Alembic migration |
| `services/registry-state/src/registry_state/domain/materializer.py` | Modified — `_handle_task_created` writes new columns |
| `services/registry-state/src/registry_state/domain/test_materializer.py` | Modified — 3 new tests |
| `services/registry-state/src/registry_state/test_schema.py` | Modified — 1 new migration round-trip test |
| `services/registry-api/src/registry_api/routes/tasks.py` | Modified — request/response models gain optional fields; emission writes them |
| `services/registry-api/src/registry_api/test_app.py` | Modified — 4 new tests |
| `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` | Modified — `CreateTaskRequest` gains 2 fields |
| `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` | Modified — `handle_task` populates from message |
| `services/telegram-gateway/src/telegram_gateway/test_task_command.py` | Modified — 2 new tests |
| `services/clawhip-daemon/src/clawhip_daemon/__main__.py` | Modified — replace hello-world stub with delegation to `app.main:main` |
| `services/clawhip-daemon/src/clawhip_daemon/app/main.py` | NEW — `build_app` + `main` + structlog wiring |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/__init__.py` | NEW — package marker |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` | NEW — httpx + tenacity outbound |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/test_telegram_outbound.py` | NEW — 5 unit tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/__init__.py` | NEW — package marker |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | NEW — subscriber loop + placeholder renderer |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | NEW — 5 unit tests |
| `services/clawhip-daemon/pyproject.toml` | Modified — add httpx, tenacity, secret-hygiene, events, structlog |
| `tests/integration/test_task_thread_binding.py` | NEW — 1 e2e test |
| `_bmad-output/implementation-artifacts/3-9-task-thread-binding.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (executor agent, foreground spawn + one SendMessage continuation; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Initial executor pass completed Tasks 1-3 (~6 min, 74 tool uses) and stopped mid-Task-4 ("First update pyproject:") — same truncation pattern observed in Stories 3.6/3.7 with large multi-file work.
- SendMessage continuation completed Tasks 4-5 + ran `just lint`, `just test`, `just bootstrap-verify` (~20 min, 121 tool uses).
- Independent gate verification (orchestrator): `just lint` 9/9 green, `just test` 865 → 887 passed (+22), `just bootstrap-verify` clean — 13 workspace imports verified including new `clawhip_daemon` adapters.
- Pre-existing dev-tooling quirk: `uv sync --no-dev` strips `asgi-lifespan` from venv; restored via `uv sync --all-packages` (same pattern as Stories 3.6/3.7/3.8).

### Completion Notes List

- **All 16 ACs satisfied** with two documented deviations (see below).
- **+22 tests** (865 → 887): 3 materializer + 1 schema migration + 4 registry-api + 2 telegram-gateway + 5 outbound + 6 sink (5 required + 1 HTML-escape variant) + 1 e2e integration = 22 total. Spec target was 21 minimum.
- **EventLogReader sourcing (Notable Design Decision)**: No public `EventLogReader` class exists in registry-state's spine. Per Story 3.6 review N7 (no IMP001 noqa proliferation), clawhip-daemon implements its own JSONL tail via `events.from_canonical_json` (same algorithm as `registry_state.adapters.event_log._read_new_envelopes_since`). Documented inline in `telegram_sink.py`.
- **`sink.delivery_failed` emission deferred (Deviation #1 from AC-6)**: AC-6 specifies `SinkDeliveryFailedPayload` emission via `clawhip-bridge` MCP on terminal failure. Implementation logs `ERROR` via structlog AND invokes an optional `emit` callback (defaults to `None`). In `app/main.py` the callback is passed as `None` because clawhip-daemon has no `EventLogWriter` (registry-state is the single writer per FR26 — clawhip-daemon would need to emit via the future `clawhip-bridge` MCP server which is not yet wired). Phase 1 falls back to observability-via-structlog-logs only. Future story: when clawhip-bridge MCP is the canonical event-emission surface, wire the `emit` callback.
- **Schema-version dispatch (Notable Design Decision)**: Implemented as a single `TaskCreatedPayload` class registered under both `1.0.0` (legacy) and `1.1.0` (new). Pre-3.9 events deserialize with `chat_id=None`/`reply_to_message_id=None` (Pydantic defaults). No explicit version-dispatch logic needed in the materializer — additive-only schema evolution per architecture.md:114 makes this transparent. Both versions remain registered for replay back-compat.
- **`_EXPECTED_BODY_KEYS` updated in fuzz test (Deviation #2 from AC-13)**: Story 3.8's fuzz harness pinned `_EXPECTED_BODY_KEYS = frozenset({"title"})`. Story 3.9 makes the bot ALWAYS forward `chat_id` + `reply_to_message_id` (every Telegram message has them); the assertion would otherwise fail. Updated to `frozenset({"title", "chat_id", "reply_to_message_id"})`. AC-13 didn't explicitly authorize the modification, but it's an obvious downstream consequence of the AC-5 wire extension. Documented for the reviewer.
- **Direct httpx, no aiogram** in clawhip-daemon outbound (AC-16). Total clawhip-daemon outbound surface: ~80 LoC including `tenacity` retry wrapping. Bot inbound (telegram-gateway) keeps `aiogram`.
- **HTTP-only binding lookup** (Story 3.6 review N7 carry-forward): `TelegramSink._lookup_binding` calls `httpx.AsyncClient.get(f"{registry_api_url}/v1/tasks/{task_id}")`. No cross-service ORM imports.
- **Spine sentinel WILL fire on this commit** as expected (Story 3.9 modifies registry-state + registry-api + telegram-gateway src/) — accepted disposition per AC-14 + the test's own TODO(s3-ast). After commit, the diff `HEAD~1..HEAD` will surface 3-9's spine changes.

### Change Log

| Date | Change |
|---|---|
| 2026-05-01 | Story 3.9 implemented: `TaskCreatedPayload` schema 1.0.0 → 1.1.0 (additive — `chat_id` + `reply_to_message_id` as optional `int | None`); `Task` ORM gains 2 nullable BigInteger columns (negative supergroup IDs supported); additive Alembic migration `2026-04-30_0002_add_task_thread_binding.py`; cross-service wire extension propagates from telegram-gateway → registry-api → task.created event payload → registry-state materializer → ORM persistence; first outbound delivery surface in `services/clawhip-daemon/`: `TelegramOutbound` (direct httpx + tenacity 3× exp-backoff per architecture.md:856) + `TelegramSink` (JSONL tail subscriber loop, HTTP binding lookup, placeholder renderer `f"Task {task_id}: {event_type}"` HTML-escaped); clawhip-daemon `__main__.py` promoted from Story 1.4 hello-world stub to real subscriber loop via `app/main.py`; structlog wiring identical to Stories 3.6/3.7; 22 new tests across 8 files (3 materializer + 1 migration + 4 registry-api + 2 task_command + 5 outbound + 6 sink + 1 e2e integration). Test count 865 → 887 (+22). 9/9 lint gates green; bootstrap-verify clean (clawhip_daemon now in 13 workspace imports). uv.lock churn limited to clawhip-daemon's new explicit deps (httpx, tenacity, events, secret-hygiene, structlog). |

### File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — `TaskCreatedPayload` gains `chat_id` + `reply_to_message_id` (both `int \| None = None`); both versions registered in schema_registry |
| `services/registry-state/src/registry_state/schema.py` | Modified — `Task` ORM gains 2 nullable BigInteger columns |
| `services/registry-state/src/registry_state/migrations/versions/2026-04-30_0002_add_task_thread_binding.py` | NEW — additive Alembic migration; revision `0002`, down_revision `0001` |
| `services/registry-state/src/registry_state/domain/handlers.py` | Modified — `handle_task_created` writes new columns from payload |
| `services/registry-state/src/registry_state/domain/test_materializer.py` | Modified — +3 tests |
| `services/registry-state/src/registry_state/test_schema.py` | Modified — +1 test (BigInteger round-trip + nullable back-compat) |
| `services/registry-state/src/registry_state/test_migrations.py` | Modified — `_REVISION` bumped to `0002` |
| `services/registry-api/src/registry_api/routes/tasks.py` | Modified — `CreateTaskRequest` + `TaskResponse` gain optional fields; POST emits `task.created` v1.1.0 |
| `services/registry-api/src/registry_api/test_app.py` | Modified — +4 tests |
| `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` | Modified — `create_task()` accepts + forwards new fields |
| `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` | Modified — `handle_task` populates from `message.chat.id` + `message.message_id` |
| `services/telegram-gateway/src/telegram_gateway/test_task_command.py` | Modified — +2 tests (positive + negative supergroup chat_id) |
| `services/clawhip-daemon/pyproject.toml` | Modified — explicit deps: `httpx>=0.27`, `tenacity>=8.2`, `events`, `secret-hygiene`, `structlog>=24.1` |
| `services/clawhip-daemon/src/clawhip_daemon/__main__.py` | Modified — replaces hello-world stub with delegation to `app.main:main` |
| `services/clawhip-daemon/src/clawhip_daemon/app/__init__.py` | NEW — package marker |
| `services/clawhip-daemon/src/clawhip_daemon/app/main.py` | NEW — `build_app()` + `run()` + `main()` + structlog wiring |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/__init__.py` | NEW — package marker |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` | NEW — `TelegramOutbound` class; httpx + tenacity 3× exp-backoff; logs ERROR + optional emit callback on terminal failure |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/test_telegram_outbound.py` | NEW — 5 unit tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/__init__.py` | NEW — package marker |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | NEW — `TelegramSink` subscriber loop; JSONL tail; HTTP binding lookup; placeholder renderer (HTML-escaped) |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | NEW — 6 unit tests (5 required + 1 HTML-escape variant) |
| `tests/integration/test_task_thread_binding.py` | NEW — 1 e2e test (create task with chat=-1001/msg=42 → emit task.completed → assert sendMessage called) |
| `tests/integration/test_command_injection_fuzz.py` | Modified — `_EXPECTED_BODY_KEYS` updated to `frozenset({"title", "chat_id", "reply_to_message_id"})` (downstream consequence of AC-5 wire extension) |
| `uv.lock` | Auto-regenerated — `tenacity` becomes direct dep on clawhip-daemon; `httpx`/`events`/`secret-hygiene`/`structlog` move from transitive to explicit on clawhip-daemon |
| `_bmad-output/implementation-artifacts/3-9-task-thread-binding.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review` + `last_updated: 2026-05-01T...` |
