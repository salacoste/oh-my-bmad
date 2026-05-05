# Story 4.3: `approve`, `reject`, `stop`, `retry`, `ping`, `agent` commands

Status: review

## Story

As **the operator at the Mac**,
I want the operator-decision commands + health/ownership commands available from the CLI,
so that **full surface parity with Telegram holds**.

This story implements the remaining six console-cli commands. Four decision commands (`approve`, `reject`, `stop`, `retry`) call `POST /v1/tasks/{id}/decisions` via a new `submit_decision()` method on `RegistryAPIClient`. The `ping` command calls `GET /v1/health`. The `agent` command calls `GET /v1/tasks/{id}` (read-only) and renders Phase 1 static runtime info.

## Acceptance Criteria

1. **AC-1: `approve` command submits decision** — Running `oh-my-bmad-cli approve t-0001` calls `POST /v1/tasks/t-0001/decisions {"action":"approve"}` and prints confirmation (e.g. `Approved t-0001 (d-...).`).

2. **AC-2: `reject` command submits with reason** — Running `oh-my-bmad-cli reject t-0001 "wrong branch"` calls `POST /v1/tasks/t-0001/decisions {"action":"reject","hint":"wrong branch"}` and prints rejection confirmation.

3. **AC-3: `stop` command submits stop decision** — Running `oh-my-bmad-cli stop t-0001` calls `POST /v1/tasks/t-0001/decisions {"action":"stop"}` and prints stop confirmation.

4. **AC-4: `retry` command submits with hint** — Running `oh-my-bmad-cli retry t-0001 --hint "rate limit must be per-user"` calls `POST /v1/tasks/t-0001/decisions {"action":"retry","hint":"rate limit must be per-user"}` and prints retry confirmation.

5. **AC-5: `ping` command shows health** — Running `oh-my-bmad-cli ping` calls `GET /v1/health` and prints a one-line health summary within 2 s (registry status, worker status, clawhip queue depth, version).

6. **AC-6: `agent` command shows runtime info** — Running `oh-my-bmad-cli agent t-0001` calls `GET /v1/tasks/t-0001` to verify task exists, then prints Phase 1 static runtime info (`runtime=claude-code`).

7. **AC-7: `RegistryAPIClient` extended** — Two new methods: `submit_decision()` and `get_platform_health()`. Two new local response models: `DecisionResponseLocal` and `HealthResponseLocal` (frozen Pydantic models, same pattern as Story 4.2). `submit_decision()` validates task_id against `TASK_ID_PATTERN` before HTTP call.

8. **AC-8: Error rendering** — All commands handle ConnectError, HTTPStatusError (with RFC 7807 parsing via `parse_error_detail`), RegistryResponseError, and ValueError (from task_id validation). Errors go to stderr. 404 → task/endpoint-specific messages.

9. **AC-9: Import-graph rules pass** — `scripts/check_imports.py` passes. Console-cli imports from `packages/` only. No cross-service imports. Response models are local redefinitions.

10. **AC-10: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

11. **AC-11: Tests for all six commands** — Tests use mocked HTTP responses. Test submit_decision success for all 4 actions, get_platform_health success, agent success, 404s, network errors, invalid task_id. At least 15 new tests.

12. **AC-12: `just test` no regressions** — Existing test count (1209 passed) unchanged. New tests increase the count.

13. **AC-13: Atomic commit** — title: `feat(console-cli): implement approve, reject, stop, retry, ping, agent commands · E4`

## Tasks / Subtasks

- [x] **Task 1: Extend `RegistryAPIClient`** (AC: #7)
  - [x] Add `DecisionResponseLocal` frozen Pydantic model — fields: `task_id`, `decision_id`, `action` (Literal), `decided_at`, `idempotency_status` (Literal)
  - [x] Add `HealthResponseLocal` frozen Pydantic model — fields: `registry_status`, `worker_status`, `clawhip_queue_depth`, `version` (str fields with bounds, `extra="ignore"`)
  - [x] Implement `submit_decision()` — POST /v1/tasks/{task_id}/decisions with Idempotency-Key, X-Actor-Id headers; validates task_id before HTTP call; omits `hint` key when None; parses idempotency_status from body then header
  - [x] Implement `get_platform_health()` — GET /v1/health with X-Request-ID header; no Idempotency-Key (GET is idempotent); uses `model_validate` for body parsing
  - [x] Follow exact same patterns as telegram-gateway's `RegistryAPIClient` (body parsing inside try/except, RegistryResponseError for malformed bodies)

- [x] **Task 2: Implement `approve` command** (AC: #1, #8)
  - [x] Rewrite `commands/approve.py` — Typer argument: `task_id` (required)
  - [x] Validate task_id with TASK_ID_PATTERN locally (pre-HTTP call)
  - [x] Generate idempotency key via `events.new_idempotency_key()` and request_id via `events.new_request_id()`
  - [x] Call `client.submit_decision(action="approve", ...)`, render: `Approved {task_id} ({decision_id}).`
  - [x] Handle errors: ConnectError, HTTPStatusError, RegistryResponseError, ValueError

- [x] **Task 3: Implement `reject` command** (AC: #2, #8)
  - [x] Rewrite `commands/reject.py` — Typer arguments: `task_id` (required), `reason` (required)
  - [x] Call `client.submit_decision(action="reject", hint=reason, ...)`, render: `Rejected {task_id} ({decision_id}): {reason}`
  - [x] Same error handling pattern

- [x] **Task 4: Implement `stop` command** (AC: #3, #8)
  - [x] Rewrite `commands/stop.py` — Typer argument: `task_id` (required)
  - [x] Call `client.submit_decision(action="stop", ...)`, render: `Stopped {task_id} ({decision_id}).`
  - [x] Same error handling pattern

- [x] **Task 5: Implement `retry` command** (AC: #4, #8)
  - [x] Rewrite `commands/retry.py` — Typer arguments: `task_id` (required), `--hint` (optional)
  - [x] Call `client.submit_decision(action="retry", hint=hint, ...)`, render: `Retrying {task_id} ({decision_id}).`
  - [x] Same error handling pattern

- [x] **Task 6: Implement `ping` command** (AC: #5, #8)
  - [x] Rewrite `commands/ping.py` — no arguments
  - [x] Call `client.get_platform_health()`, render: `pong · registry: {status} · worker: {status} · clawhip: {depth} events · version: {version}`
  - [x] Handle ConnectError, HTTPStatusError, RegistryResponseError

- [x] **Task 7: Implement `agent` command** (AC: #6, #8)
  - [x] Rewrite `commands/agent.py` — Typer argument: `task_id` (required)
  - [x] Call `client.get_task(task_id=task_id)` to verify task exists
  - [x] Render Phase 1 static response: `Task {task_id}: runtime=claude-code`
  - [x] Handle errors (404 → "Task {task_id} not found.")

- [x] **Task 8: Write tests** (AC: #11)
  - [x] Extend existing `test_main.py` stub tests for new real commands
  - [x] Create `src/console_cli/test_decision_commands.py` — test submit_decision client method + approve/reject/stop/retry command tests
  - [x] Create `src/console_cli/test_ping_command.py` — test get_platform_health client method + ping command tests
  - [x] Create `src/console_cli/test_agent_command.py` — test agent command (get_task for verification + static rendering)
  - [x] Use `unittest.mock.AsyncMock` to mock httpx responses (same pattern as Story 4.2)

- [x] **Task 9: Verification + commit** (AC: #9, #10, #12, #13)
  - [x] `scripts/check_imports.py` — passes
  - [x] `just lint` 9/9 green
  - [x] `just test` — no regressions, new tests counted
  - [x] Version bump to `0.4.0` in `__init__.py` and `pyproject.toml`
  - [x] Atomic commit

## Dev Notes

### What already exists

| File | Current state | What to change |
|---|---|---|
| `services/console-cli/src/console_cli/commands/approve.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/reject.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/stop.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/retry.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/ping.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/agent.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Has `create_task`, `get_task`, `get_logs_digest` | Add `submit_decision` + `get_platform_health` + 2 response models |
| `services/console-cli/src/console_cli/app/main.py` | All 10 commands registered | No changes needed |
| `services/console-cli/src/console_cli/app/runner.py` | `run_async()` helper | No changes needed |
| `services/console-cli/src/console_cli/app/config.py` | `ConsoleSettings` | No changes needed |

### Registry-API endpoints for Story 4.3

**POST /v1/tasks/{task_id}/decisions** — Submit Decision
- **NOT YET IMPLEMENTED** server-side (Story 6.4 owns it). Live calls return 404.
- Request body: `{"action": "approve|reject|stop|retry", "hint": "..."?}`
- Headers: `Idempotency-Key` (required), `X-Actor-Id` (Phase 1: "console"), `X-Request-ID` (optional)
- Expected response shape (forward-compatible): `{"task_id", "decision_id", "action", "decided_at", "idempotency_status"}`

**GET /v1/health** — Platform Health
- **NOT YET IMPLEMENTED** server-side (no story owner yet). Live calls return 404.
- No request body, no Idempotency-Key header
- Expected response shape (forward-compatible): `{"registry_status", "worker_status", "clawhip_queue_depth", "version"}`
- Tests must mock the transport layer so they work today.

### Key patterns from Story 4.2

1. **Error rendering pattern** — all commands follow the same try/except structure:
```python
try:
    result = run_async(client.method(...))
    print(f"Success: {result}")
except httpx.ConnectError:
    print("Error: Could not reach registry-api. Is docker compose up?", file=sys.stderr)
    raise SystemExit(1) from None
except httpx.HTTPStatusError as exc:
    # 404-specific message OR generic parse_error_detail
    print(f"Error: {parse_error_detail(exc)}", file=sys.stderr)
    raise SystemExit(1) from None
except RegistryResponseError as exc:
    print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
    raise SystemExit(1) from None
except ValueError as exc:
    print(f"Error: {exc}", file=sys.stderr)
    raise SystemExit(1) from None
```

2. **Local response models** — frozen Pydantic models mirroring the wire shape. NOT imported from other services.

3. **Per-invocation AsyncClient** — `async with httpx.AsyncClient(base_url=..., timeout=_DEFAULT_TIMEOUT)` inside each method. No connection pool (CLI is short-lived).

4. **TASK_ID_PATTERN** — defined locally in `registry_api_client.py`, imported by command files.

5. **`parse_error_detail()`** — shared utility in `registry_api_client.py`, extracts RFC 7807 detail.

6. **`events` imports** — `from events import new_idempotency_key, new_request_id` for idempotency and request correlation.

### Telegram-gateway reference implementations

The telegram-gateway already implements all 6 commands. Key differences from console-cli:

| Aspect | Telegram-Gateway | Console-CLI |
|---|---|---|
| Actor identity | Telegram user id via `X-Actor-Id` | Hardcoded `"console"` via `X-Actor-Id` |
| Idempotency key | Deterministic from `(chat_id, message_id)` | Fresh UUIDv7 via `events.new_idempotency_key()` |
| Error handling | Try to safe_reply (never raises) | stderr + `raise SystemExit(1) from None` |
| Response rendering | Telegram HTML with emojis | Plain text terminal output |
| task_id extraction | `extract_task_id_from_message()` helper | Typer `Argument(...)` + TASK_ID_PATTERN validation |
| Logging | structlog per handler | Minimal (print to stdout/stderr) |

**Decision commands** (approve/reject/stop/retry) — all call `submit_decision()` with different `action` values. The only variation: `reject` passes `reason` as `hint`, `retry` passes optional `--hint`.

**ping** — calls `get_platform_health()`, no idempotency key (GET is idempotent). Console-CLI: same, just terminal output instead of Telegram reply.

**agent** — calls `get_task()` to verify task exists (read-only), then renders Phase 1 static `runtime=claude-code` info. No `submit_decision()` call.

### DecisionResponseLocal model

Mirror telegram-gateway's `DecisionResponseLocal` exactly:

```python
class DecisionResponseLocal(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    decision_id: str  # "d-<uuidv7>" per FR7 audit trail
    action: Literal["approve", "reject", "stop", "retry"]
    decided_at: datetime
    idempotency_status: Literal["applied", "replayed"] = "applied"
```

### HealthResponseLocal model

Mirror telegram-gateway's `HealthResponseLocal` exactly (str fields, not Literal — server contract not finalized):

```python
class HealthResponseLocal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    registry_status: str = Field(min_length=1, max_length=64)
    worker_status: str = Field(min_length=1, max_length=64)
    clawhip_queue_depth: int = Field(ge=0, le=1_000_000)
    version: str = Field(min_length=1, max_length=200)
```

### submit_decision() implementation

Follow telegram-gateway's pattern:

```python
async def submit_decision(
    self,
    *,
    task_id: str,
    action: Literal["approve", "reject", "stop", "retry"],
    idempotency_key: str,
    actor_id: str = "console",
    request_id: str | None = None,
    hint: str | None = None,
) -> DecisionResponseLocal:
    if not TASK_ID_PATTERN.match(task_id):
        raise ValueError(f"Invalid task_id: {task_id!r}")

    headers = {"Idempotency-Key": idempotency_key, "X-Actor-Id": actor_id}
    if request_id is not None:
        headers["X-Request-ID"] = request_id

    body: dict[str, str] = {"action": action}
    if hint is not None:
        body["hint"] = hint

    async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
        response = await client.post(f"/v1/tasks/{task_id}/decisions", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    try:
        raw_status = data.get("idempotency_status") or "applied"
        idempotency_status = "replayed" if raw_status == "replayed" else "applied"
        return DecisionResponseLocal(
            task_id=data["task_id"],
            decision_id=data["decision_id"],
            action=data["action"],
            decided_at=data["decided_at"],
            idempotency_status=idempotency_status,
        )
    except (json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
        raise RegistryResponseError(f"malformed body: {exc}") from exc
```

### get_platform_health() implementation

```python
async def get_platform_health(
    self,
    *,
    request_id: str | None = None,
) -> HealthResponseLocal:
    headers: dict[str, str] = {}
    if request_id is not None:
        headers["X-Request-ID"] = request_id

    async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
        response = await client.get("/v1/health", headers=headers)
        response.raise_for_status()
        data = response.json()

    try:
        return HealthResponseLocal.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise RegistryResponseError(f"malformed body: {exc}") from exc
```

### Decision command rendering

All 4 decision commands follow the same pattern — only the action and output message differ:

- **approve**: `Approved {task_id} ({decision_id}).`
- **reject**: `Rejected {task_id} ({decision_id}): {reason}`
- **stop**: `Stopped {task_id} ({decision_id}).`
- **retry**: `Retrying {task_id} ({decision_id}).`

### ping rendering

```
pong · registry: healthy · worker: idle · clawhip: 0 events · version: v0.1.0
```

### agent rendering

Phase 1 static (same as telegram-gateway):
```
Task {task_id}: runtime=claude-code
```

### Import-graph rules (CRITICAL)

Console-cli MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `console_cli` own modules — ALLOWED
- Import from `services/telegram-gateway/` — **FORBIDDEN**
- Response models are LOCAL redefinitions, not imports from telegram-gateway or registry-api

### Testing pattern

Follow Story 4.2's test patterns:
- Use `unittest.mock.AsyncMock` to mock `httpx.AsyncClient` methods
- Test success paths, error paths (ConnectError, HTTPStatusError for 404/422/500, RegistryResponseError, ValueError)
- Use `typer.testing.CliRunner` for integration-level command tests
- Use `unittest.mock.patch` to inject the mock client
- Test `submit_decision()` and `get_platform_health()` client methods directly with mocked transport
- Test that `hint` is omitted from POST body when None (reject/retry edge case)

### Previous story learnings (Story 4.2)

- `raise_for_status()` and `response.json()` must be INSIDE the `async with` block — stream may be closed outside
- `_DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)` — prevents indefinite hangs
- All error messages go to `sys.stderr`, not stdout
- `raise SystemExit(1) from None` per B904 lint rule
- `parse_error_detail()` is shared in `registry_api_client.py` — no duplication
- PEP 695 type param syntax in `runner.py`: `def run_async[T](coro: Awaitable[T]) -> T`
- `just lint` 9/9 is the gatekeeper. Run early and often.
- `just test` = PR gate. Test count was 1209.

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.4.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Modified — add submit_decision, get_platform_health, DecisionResponseLocal, HealthResponseLocal |
| `services/console-cli/src/console_cli/commands/approve.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/reject.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/stop.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/retry.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/ping.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/agent.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/test_decision_commands.py` | New — submit_decision + approve/reject/stop/retry tests |
| `services/console-cli/src/console_cli/test_ping_command.py` | New — get_platform_health + ping command tests |
| `services/console-cli/src/console_cli/test_agent_command.py` | New — agent command tests |
| `services/console-cli/src/console_cli/test_main.py` | Modified — update stub tests for now-real commands |
| `_bmad-output/implementation-artifacts/4-3-decision-and-health-commands.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1352-1367 — Story 4.3 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 659-669 — console-cli directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 231 — cross-service contract is HTTP/JSON]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — submit_decision + get_platform_health pattern + local models]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` — approve handler pattern]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py` — ping handler pattern]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/agent_command.py` — agent handler pattern (read-only, Phase 1 static)]
- [Source: `_bmad-output/implementation-artifacts/4-2-task-status-logs-commands.md` — Story 4.2 patterns and learnings]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None — straight implementation, no debug cycles.

### Completion Notes List

- Task 1: `RegistryAPIClient` extended with `submit_decision()` and `get_platform_health()`. Two new frozen Pydantic models: `DecisionResponseLocal` (task_id, decision_id, action Literal, decided_at, idempotency_status) and `HealthResponseLocal` (registry_status, worker_status, clawhip_queue_depth, version with `extra="ignore"`). `submit_decision()` validates task_id before HTTP call, omits `hint` key when None, parses idempotency_status. `get_platform_health()` uses no Idempotency-Key header (GET is idempotent). Also fixed `parse_error_detail()` mypy strict issue (no-any-return).
- Task 2: `commands/approve.py` rewritten — Typer argument `task_id`, generates idempotency key + request_id, calls `submit_decision(action="approve")`, renders `Approved {task_id} ({decision_id}).`
- Task 3: `commands/reject.py` rewritten — Typer arguments `task_id` + `reason`, passes reason as `hint`, renders `Rejected {task_id} ({decision_id}): {reason}`
- Task 4: `commands/stop.py` rewritten — Typer argument `task_id`, calls `submit_decision(action="stop")`, renders `Stopped {task_id} ({decision_id}).`
- Task 5: `commands/retry.py` rewritten — Typer argument `task_id` + `--hint` option, calls `submit_decision(action="retry", hint=hint)`, renders `Retrying {task_id} ({decision_id}).`
- Task 6: `commands/ping.py` rewritten — no arguments, calls `get_platform_health()`, renders `pong · registry: {status} · worker: {status} · clawhip: {depth} events · version: {version}`
- Task 7: `commands/agent.py` rewritten — Typer argument `task_id`, calls `get_task()` to verify existence, renders Phase 1 static `Task {task_id}: runtime=claude-code`. 404 → `Task {task_id} not found.`
- Task 8: 27 new tests across 3 files: `test_decision_commands.py` (14: 7 submit_decision client + 7 command tests), `test_ping_command.py` (8: 5 get_platform_health client + 3 ping command), `test_agent_command.py` (5: agent command tests). Updated `test_main.py` — REQUIRES_ARGS now includes approve/reject/stop/retry/agent; REAL_NO_ARGS is ping/events. Extracted helper functions (`_mock_200`, `_mock_error`, `_CONNECT_ERROR`) to keep lines under 100 chars.
- Task 9: `check_imports.py` passes. `just lint` 9/9 green. `just test` 1236 passed (was 1209, +27). Version bumped to 0.4.0. No regressions.

### File List

