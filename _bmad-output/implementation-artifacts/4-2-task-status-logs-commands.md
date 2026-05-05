# Story 4.2: `task`, `status`, `logs` commands

Status: review

## Story

As **the operator at the Mac**,
I want `oh-my-bmad-cli task`, `status`, `logs` to perform the same actions as their Telegram counterparts,
so that **desk-side workflows don't require switching to my phone**.

This story implements the three primary query/creation commands for the console CLI. Each command calls registry-api over HTTP and renders the response as terminal output. The `task` command creates a new task, `status` shows the current state of a task, and `logs` shows the LLM-digest output.

## Acceptance Criteria

1. **AC-1: `task` command creates a task** — Running `oh-my-bmad-cli task "add idempotency middleware" --repo gateway` calls `POST /v1/tasks` and prints `Task t-... created. Planning.`

2. **AC-2: `status` command shows task state** — Running `oh-my-bmad-cli status t-0001` prints the same one-message state reconstitution the Telegram `/status` renders (task_id, status, title, actor, last_event, next_commands).

3. **AC-3: `logs` command shows log digest** — Running `oh-my-bmad-cli logs t-0001` prints the LLM-digest output (or a clear message that the endpoint is not yet available if 404).

4. **AC-4: `RegistryAPIClient` fully implemented** — The placeholder from Story 4.1 is replaced with a real httpx-based client that supports `create_task()`, `get_task()`, and `get_logs_digest()`. Local response models mirror the telegram-gateway pattern (`*Local` frozen Pydantic models). `RegistryResponseError` for malformed 2xx bodies.

5. **AC-5: Async commands work via Typer** — Commands use `asyncio.run()` to bridge Typer's synchronous interface with the async httpx client. No lifespan/dependency-injection needed — CLI invocations are short-lived.

6. **AC-6: Error rendering** — HTTP errors (connection refused, 404, validation errors) render as human-readable stderr messages. RFC 7807 problem+json responses are parsed and displayed. No raw JSON dumps to stdout.

7. **AC-7: Import-graph rules pass** — `scripts/check_imports.py` passes. Console-cli imports from `packages/` only. No cross-service imports. Response models are local redefinitions (same pattern as telegram-gateway).

8. **AC-8: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

9. **AC-9: Tests for all three commands** — Tests use mocked HTTP responses (same pattern as telegram-gateway tests). Test create_task success, get_task success, get_task 404, get_logs_digest success, network error handling. At least 8 new tests.

10. **AC-10: `just test` no regressions** — Existing test count unchanged (1176 passed). New tests increase the count.

11. **AC-11: Atomic commit** — title: `feat(console-cli): implement task, status, logs commands · E4`

## Tasks / Subtasks

- [x] **Task 1: Implement `RegistryAPIClient`** (AC: #4, #7)
  - [x] Replace placeholder `adapters/registry_api_client.py` with full implementation
  - [x] Define local response models: `CreateTaskResponseLocal`, `TaskResponseLocal`, `LogsDigestResponseLocal`, `ActorLocal`, `LastEventLocal` (frozen Pydantic models, same pattern as telegram-gateway)
  - [x] Define `RegistryResponseError(httpx.HTTPError)` for malformed 2xx bodies
  - [x] Implement `create_task()` — POST /v1/tasks with Idempotency-Key and X-Request-ID headers
  - [x] Implement `get_task()` — GET /v1/tasks/{task_id} with task_id validation
  - [x] Implement `get_logs_digest()` — GET /v1/tasks/{task_id}/logs/digest with task_id validation
  - [x] Define `TASK_ID_PATTERN` locally (cannot import from telegram-gateway)
  - [x] Use `async with httpx.AsyncClient(...)` per command invocation (short-lived CLI, no connection pool needed)

- [x] **Task 2: Implement `task` command** (AC: #1, #5, #6)
  - [x] Update `commands/task.py` — add Typer arguments: `title` (required), `--repo` (optional), `--hint` (optional)
  - [x] Create `app/runner.py` — `run_async()` helper that bridges Typer sync → asyncio.run()
  - [x] Call `client.create_task()`, render success: `Task {task_id} created. Planning.`
  - [x] Handle errors: connection refused → stderr message, validation errors → stderr, other HTTP errors → stderr

- [x] **Task 3: Implement `status` command** (AC: #2, #5, #6)
  - [x] Update `commands/status.py` — add Typer argument: `task_id` (required)
  - [x] Call `client.get_task()`, render: task_id, status, title, actor, last_event, next_commands
  - [x] Handle 404 → `Task {task_id} not found`

- [x] **Task 4: Implement `logs` command** (AC: #3, #5, #6)
  - [x] Update `commands/logs.py` — add Typer argument: `task_id` (required)
  - [x] Call `client.get_logs_digest()`, render digest text
  - [x] Handle 404 (endpoint or task not found) → clear message

- [x] **Task 5: Write tests** (AC: #9)
  - [x] Create `src/console_cli/test_task_command.py` — test create_task success, network error, validation error
  - [x] Create `src/console_cli/test_status_command.py` — test get_task success, 404, network error
  - [x] Create `src/console_cli/test_logs_command.py` — test get_logs success, 404 (endpoint not deployed)
  - [x] Use `unittest.mock.AsyncMock` to mock httpx responses (same pattern as telegram-gateway handler tests)
  - [x] Test `RegistryAPIClient` methods directly with mocked transport

- [x] **Task 6: Verification + commit** (AC: #7, #8, #10, #11)
  - [x] `scripts/check_imports.py` — passes
  - [x] `just lint` 9/9 green
  - [x] `just test` — no regressions, new tests counted
  - [x] Version bump to `0.3.0` in `__init__.py` and `pyproject.toml`
  - [x] Atomic commit

## Dev Notes

### What already exists

| File | Current state | What to change |
|---|---|---|
| `services/console-cli/src/console_cli/commands/task.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/status.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/commands/logs.py` | Stub | Replace with real implementation |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Placeholder | Replace with full client |
| `services/console-cli/src/console_cli/app/config.py` | ConsoleSettings | No changes needed |
| `services/console-cli/src/console_cli/app/main.py` | Command registration | No changes needed |

### Registry-API endpoints (currently implemented)

**POST /v1/tasks** — Create Task
- Request body: `{"title": "...", "repo": "...", "hint": "..."}`
- Response 201: `{"task_id": "t-...", "event_id": "e-...", "created_at": "2026-..."}`
- Headers: `Idempotency-Key` (required), `X-Request-ID` (optional), `X-Actor-Id` (Phase 1: any string)
- Idempotency enforced with 7-day TTL

**GET /v1/tasks/{task_id}** — Get Task
- Response 200: `{"task_id", "status", "title", "created_at", "updated_at", "actor": {"kind", "id"}, "last_event": {"id", "type", "emitted_at"} | null, "next_commands": [...]}`
- task_id must match `^t-[uuidv7]$`
- 404 with RFC 7807 problem+json if not found

**GET /v1/tasks/{task_id}/logs/digest** — Log Digest
- **NOT YET IMPLEMENTED** server-side (Story 7.3 owns it). Live calls return 404.
- Response shape (forward-compatible): `{"task_id", "digest", "truncated", "line_count"}`
- Tests must mock the transport layer so they work today.

### Console-CLI vs Telegram-Gateway: key differences

| Aspect | Telegram-Gateway | Console-CLI |
|---|---|---|
| HTTP client lifecycle | Long-lived, lifespan-owned `AsyncClient` | Per-invocation `async with AsyncClient` (CLI is short-lived) |
| Idempotency key | Deterministic from `(chat_id, message_id)` | Fresh UUIDv7 per invocation via `events.ids.new_idempotency_key()` |
| Actor identity | Telegram user id via `X-Actor-Id` | Local OS user or hardcoded `"console"` |
| Response rendering | Telegram message with Markdown | Terminal stdout with plain text |
| chat_id / reply_to_message_id | Persisted for Telegram sink routing | Not applicable — omit from POST body |
| TASK_ID_PATTERN | Imported from `handlers/_keys.py` | Must be defined locally (cross-service import forbidden) |

### Local response models pattern

Follow telegram-gateway's `RegistryAPIClient` exactly — define `*Local` frozen Pydantic models that mirror the registry-api JSON wire shape:

```python
class CreateTaskResponseLocal(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    event_id: str
    created_at: datetime
    idempotency_status: Literal["applied", "replayed"] = "applied"
```

**CRITICAL:** Do NOT import response models from `services/registry-api/`. The cross-service contract is HTTP/JSON, not shared Python objects (architecture.md:231).

### Async bridge pattern

Typer commands are synchronous. Use a helper:

```python
# app/runner.py
import asyncio

def run_async(coro):
    asyncio.run(coro)
```

Each command creates a `RegistryAPIClient`, calls an async method via `run_async()`, and renders the result.

### Error rendering pattern

```python
try:
    result = run_async(client.create_task(...))
    print(f"Task {result.task_id} created. Planning.")
except httpx.ConnectError:
    print("Error: Could not reach registry-api. Is docker compose up?", file=sys.stderr)
    raise SystemExit(1)
except httpx.HTTPStatusError as exc:
    # Parse RFC 7807 problem+json
    detail = exc.response.json().get("detail", exc.response.text)
    print(f"Error: {detail}", file=sys.stderr)
    raise SystemExit(1)
```

### TASK_ID_PATTERN — define locally

Cannot import from `telegram_gateway.handlers._keys`. Define the same regex locally:

```python
import re
TASK_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
```

### Import-graph rules (CRITICAL)

Console-cli MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `console_cli` own modules — ALLOWED
- Import from `services/telegram-gateway/` — **FORBIDDEN**
- Response models are LOCAL redefinitions, not imports from registry-api

### Testing pattern

Follow the telegram-gateway handler test pattern:
- Use `unittest.mock.AsyncMock` to mock `httpx.AsyncClient` methods
- Test success paths, error paths (ConnectError, HTTPStatusError for 404/422/500)
- Use `typer.testing.CliRunner` for integration-level command tests
- Use `unittest.mock.patch` to inject the mock client

### Previous story learnings (Story 4.1)

- Entry point must target `main()`, not `app()` — structlog configuration must run
- Use `pytest.MonkeyPatch` fixture for env-var tests, not raw `os.environ`
- `RegistryAPIClient` must NOT have a default URL — callers pass from `ConsoleSettings`
- `events` command uses `app.command(name="events")` to avoid Typer collision
- `just lint` 9/9 is the gatekeeper. Run early and often.
- `just test` = PR gate. Test count was 1176.

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.3.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Rewritten — full client implementation |
| `services/console-cli/src/console_cli/app/runner.py` | New — async bridge helper |
| `services/console-cli/src/console_cli/commands/task.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/status.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/commands/logs.py` | Rewritten — real implementation |
| `services/console-cli/src/console_cli/test_task_command.py` | New — task command tests |
| `services/console-cli/src/console_cli/test_status_command.py` | New — status command tests |
| `services/console-cli/src/console_cli/test_logs_command.py` | New — logs command tests |
| `_bmad-output/implementation-artifacts/4-2-task-status-logs-commands.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1332-1351 — Story 4.2 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 659-669 — console-cli directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 231 — cross-service contract is HTTP/JSON]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — client pattern + local models]
- [Source: `services/registry-api/src/registry_api/routes/tasks.py` — endpoint specs + response models]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py` — TASK_ID_PATTERN regex]
- [Source: `_bmad-output/implementation-artifacts/4-1-typer-binary-scaffold.md` — Story 4.1 completion notes]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None — straight implementation, no debug cycles.

### Completion Notes List

- Task 1: `RegistryAPIClient` fully rewritten with `create_task()`, `get_task()`, `get_logs_digest()`. All 6 local response models defined (`CreateTaskResponseLocal`, `TaskResponseLocal`, `LogsDigestResponseLocal`, `ActorLocal`, `LastEventLocal`) as frozen Pydantic models. `RegistryResponseError(httpx.HTTPError)` for malformed 2xx bodies. `TASK_ID_PATTERN` defined locally (same regex as telegram-gateway `_keys.py`). Per-invocation `async with httpx.AsyncClient` pattern.
- Task 2: `commands/task.py` rewritten with Typer arguments (`title`, `--repo`, `--hint`). Uses `events.new_idempotency_key()` and `events.new_request_id()` from packages/events. `app/runner.py` created with `run_async()` async bridge. Error rendering: ConnectError → stderr, HTTPStatusError → RFC 7807 detail, RegistryResponseError → stderr.
- Task 3: `commands/status.py` rewritten with Typer argument `task_id`. Renders task_id, status, title, actor, last_event, next_commands. 404 → "Task X not found".
- Task 4: `commands/logs.py` rewritten with Typer argument `task_id`. Renders digest text + truncation notice. 404 → "Logs not available for task X".
- Task 5: 20 new tests across 3 files: `test_task_command.py` (8: 4 TASK_ID_PATTERN + 4 create_task), `test_status_command.py` (7: 4 get_task client + 3 status command), `test_logs_command.py` (8: 4 get_logs_digest client + 4 logs command). Updated `test_main.py` stub tests to reflect task/status/logs now require args (+13 → +33 total new/updated tests).
- Task 6: `check_imports.py` passes. `just lint` 9/9 green. `just test` 1209 passed (was 1176, +33). Version bumped to 0.3.0. No regressions.
- Note: `runner.py` uses `TypeVar` + `Awaitable[T]` (ruff UP047 auto-fixed to type param syntax, then manual fix for compatibility). All `raise SystemExit(1)` in except blocks use `from None` per B904.

### File List
