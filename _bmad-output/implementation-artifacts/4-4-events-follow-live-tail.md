# Story 4.4: `events --follow` live tail

Status: done

## Story

As the operator debugging locally,
I want `oh-my-bmad-cli events <task-id> --follow` to stream the raw typed event stream to my console in real time,
so that I can inspect platform behavior without leaving the terminal.

## Acceptance Criteria

1. **AC-1: `events` with `--follow` polls and prints events** — Running `oh-my-bmad-cli events t-0001 --follow` polls `GET /v1/tasks/t-0001/events?since=<last_ts>&limit=100` in a loop. Each new event prints as a single JSON line to stdout within 1 s of emission.

2. **AC-2: `events` without `--follow` does a single fetch** — Running `oh-my-bmad-cli events t-0001` fetches events once and prints them, then exits.

3. **AC-3: Ctrl+C exits cleanly** — Pressing Ctrl+C during `--follow` prints nothing extra and exits with code 0 (no traceback, no error message).

4. **AC-4: `RegistryAPIClient` extended** — New method `get_task_events(task_id, since, limit)` calling `GET /v1/tasks/{task_id}/events`. New response model `TaskEventsResponseLocal` wrapping a list of event dicts. `get_task_events()` validates task_id against `TASK_ID_PATTERN` before HTTP call.

5. **AC-5: Polling uses `since` cursor** — Each poll passes the `emitted_at` timestamp of the last-seen event as `since=<ts>`. On first poll, `since` is omitted (fetch from beginning). Subsequent polls only fetch events after the cursor.

6. **AC-6: Error rendering** — All errors handled: ConnectError, HTTPStatusError (with 404-specific "Task {task_id} not found." message), RegistryResponseError, ValueError. Errors go to stderr. `--follow` exits on error (does not retry).

7. **AC-7: Import-graph rules pass** — `scripts/check_imports.py` passes. Console-cli imports from `packages/` only. No cross-service imports.

8. **AC-8: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

9. **AC-9: Tests** — Tests use mocked HTTP responses. Test `get_task_events` success (returns events), empty result, 404, network error. Test `events` command with and without `--follow`. Test Ctrl+C graceful exit. At least 10 new tests.

10. **AC-10: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

11. **AC-11: Atomic commit** — title: `feat(console-cli): implement events --follow live tail command · E4`

## Tasks / Subtasks

- [x] **Task 1: Extend `RegistryAPIClient`** (AC: #4)
  - [x] Add `TaskEventsResponseLocal` frozen Pydantic model — field: `events` (list of dicts with `extra="ignore"`)
  - [x] Implement `get_task_events()` — GET /v1/tasks/{task_id}/events with optional `since` and `limit` query params
  - [x] Validate task_id with TASK_ID_PATTERN before HTTP call
  - [x] Parse response as list of event envelope dicts (JSON passthrough — no per-event model validation for performance)
  - [x] Follow same error-handling pattern as other methods (RegistryResponseError for malformed body)

- [x] **Task 2: Implement `events` command** (AC: #1, #2, #3, #6)
  - [x] Rewrite `commands/events.py` — Typer argument: `task_id` (required), Typer option: `--follow` (boolean flag, default False)
  - [x] Without `--follow`: single `get_task_events()` call, print each event as JSON line, exit
  - [x] With `--follow`: enter polling loop — fetch events, print new ones, update cursor, sleep 0.5s, repeat
  - [x] Sleep interval of 0.5s ensures events appear within 1s of emission (AC-1)
  - [x] Handle KeyboardInterrupt to exit cleanly with code 0
  - [x] Handle errors: ConnectError, HTTPStatusError (404-specific), RegistryResponseError, ValueError

- [x] **Task 3: Implement async polling** (AC: #1, #5)
  - [x] The `--follow` loop runs inside an async coroutine called via `run_async()`
  - [x] Use `asyncio.sleep(0.5)` between polls (not `time.sleep`)
  - [x] On each iteration: call `get_task_events(since=last_emitted_at)`, print new events, update cursor
  - [x] Empty response → no output, just continue polling
  - [x] Use a single long-lived `AsyncClient` for the polling loop (not per-invocation) to avoid creating a new TCP connection every 0.5s

- [x] **Task 4: Write tests** (AC: #9)
  - [x] Create `src/console_cli/test_events_command.py`
  - [x] Test `get_task_events` client method: success with events, empty result, 404, network error, malformed body, invalid task_id
  - [x] Test `events` command without `--follow`: success, no events, 404, network error
  - [x] Test `events` command with `--follow`: prints initial events, polls and prints new events, stops on error
  - [x] Test Ctrl+C graceful exit during `--follow`
  - [x] Use `unittest.mock.AsyncMock` to mock httpx responses (same pattern as Stories 4.2/4.3)

- [x] **Task 5: Verification + commit** (AC: #7, #8, #10, #11)
  - [x] `scripts/check_imports.py` passes
  - [x] `just lint` 9/9 green
  - [x] `just test` — no regressions, new tests counted
  - [x] Version bump to `0.5.0` in `__init__.py` and `pyproject.toml`
  - [x] Atomic commit

## Dev Notes

### What already exists

| File | Current state | What to change |
|---|---|---|
| `services/console-cli/src/console_cli/commands/events.py` | Stub printing "Not yet implemented" | Replace with real implementation |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Has `create_task`, `get_task`, `get_logs_digest`, `submit_decision`, `get_platform_health` | Add `get_task_events` + `TaskEventsResponseLocal` |
| `services/console-cli/src/console_cli/app/main.py` | `events` command registered | No changes needed |
| `services/console-cli/src/console_cli/app/runner.py` | `run_async()` helper | No changes needed |
| `services/console-cli/src/console_cli/app/config.py` | `ConsoleSettings` | No changes needed |

### Backend endpoint (Story 7.5 — NOT YET IMPLEMENTED)

**GET /v1/tasks/{task_id}/events** — Raw Event Stream
- **NOT YET IMPLEMENTED** server-side (Story 7.5 owns it). Live calls return 404.
- Query params: `since` (ISO 8601 timestamp, optional), `limit` (integer, optional, default 100)
- Response: JSON array of event envelope objects, ordered by `emitted_at` ascending
- Tests must mock the transport layer so they work today.

### Key design decisions

1. **Polling, not SSE/WebSocket** — The architecture has no server-push mechanism. The CLI polls the GET endpoint in a loop. The 0.5s sleep interval meets the 1s AC requirement.

2. **Long-lived AsyncClient for `--follow`** — The existing pattern creates a new `AsyncClient` per method call via `async with`. For `--follow`, this would create a new TCP connection every 0.5s. Instead, create one `AsyncClient` at the start of the polling loop and reuse it.

3. **JSON passthrough for events** — Each event is printed as a raw JSON line. No need to validate each event against `EventEnvelope` (from `packages/events`) — that would be expensive and unnecessary. The `get_task_events()` method returns a list of dicts (raw JSON objects). The events endpoint is for debugging; the operator sees the raw data.

4. **`since` cursor** — The `emitted_at` field from the last-seen event becomes the `since` query param for the next poll. On first call, `since` is omitted (fetch all available events up to `limit`).

5. **Without `--follow`** — A single `get_task_events()` call with no `since` param, prints all returned events, exits. This is the "snapshot" mode for quick inspection.

### TaskEventsResponseLocal model

```python
class TaskEventsResponseLocal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    events: list[dict[str, object]] = Field(default_factory=list)
```

Minimal model — wraps the JSON array. Each event is a `dict[str, object]` (JSON passthrough). `extra="ignore"` for forward compatibility.

### get_task_events() implementation

```python
async def get_task_events(
    self,
    *,
    task_id: str,
    since: str | None = None,
    limit: int = 100,
    request_id: str | None = None,
) -> TaskEventsResponseLocal:
    if not TASK_ID_PATTERN.match(task_id):
        raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

    params: dict[str, str | int] = {"limit": limit}
    if since is not None:
        params["since"] = since

    headers: dict[str, str] = {}
    if request_id is not None:
        headers["X-Request-ID"] = request_id

    async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
        response = await client.get(
            f"/v1/tasks/{task_id}/events",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    try:
        return TaskEventsResponseLocal.model_validate({"events": data})
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise RegistryResponseError(f"malformed body: {exc}") from exc
```

### events command structure

```python
def events(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
    follow: bool = typer.Option(False, "--follow", help="Stream events in real time"),
) -> None:
    # validate task_id
    # if not follow: single fetch + print + exit
    # if follow: run_async(_poll_events(task_id))
```

The `--follow` polling coroutine:

```python
async def _poll_events(task_id: str) -> None:
    settings = ConsoleSettings()
    base_url = settings.registry_api_base_url
    since: str | None = None

    async with httpx.AsyncClient(base_url=base_url, timeout=_DEFAULT_TIMEOUT) as client:
        while True:
            response = await client.get(
                f"/v1/tasks/{task_id}/events",
                params={"since": since, "limit": 100} if since else {"limit": 100},
            )
            response.raise_for_status()
            events = response.json()
            for event in events:
                print(json.dumps(event, sort_keys=True))
                since = event.get("emitted_at")
            await asyncio.sleep(0.5)
```

Note: `_poll_events` uses its own `AsyncClient` directly (not going through `RegistryAPIClient`) because it needs to keep the client alive across multiple requests. The `RegistryAPIClient.get_task_events()` method is still available for the non-follow single-fetch path and for testing.

### Error handling for --follow

The `--follow` loop catches:
- `KeyboardInterrupt` → `sys.exit(0)` (clean exit, no traceback)
- `httpx.ConnectError` → stderr message, exit 1
- `httpx.HTTPStatusError` → 404-specific or generic, stderr, exit 1
- Other errors → stderr message, exit 1

These are caught OUTSIDE the async loop, wrapping the `run_async()` call:

```python
try:
    run_async(_poll_events(task_id))
except KeyboardInterrupt:
    pass  # clean exit
except httpx.ConnectError:
    print("Error: ...", file=sys.stderr)
    raise SystemExit(1) from None
# ... etc
```

### Testing patterns for --follow

Testing the `--follow` loop requires controlling the async sleep and mocking multiple sequential HTTP responses:

```python
async def _mock_events_sequence(responses):
    """Yield mock responses in sequence."""
    for resp in responses:
        yield resp
```

Use `AsyncMock(side_effect=[response1, response2, ...])` to return different responses on successive calls. Use `unittest.mock.patch("asyncio.sleep", new_callable=AsyncMock)` to prevent real sleeping. The test asserts that the correct number of events were printed and that the loop eventually stops (via an exception or a `StopAsyncIteration` sentinel).

### Key patterns from Story 4.3

1. **Error rendering pattern** — all commands follow the same try/except structure with ConnectError, TimeoutException, HTTPStatusError (404-specific), RegistryResponseError, ValueError.
2. **Local response models** — frozen Pydantic models, NOT imported from other services.
3. **TASK_ID_PATTERN** — defined locally in `registry_api_client.py`, imported by command files.
4. **`parse_error_detail()`** — shared utility for RFC 7807 error parsing.
5. **`events` imports** — `from events import new_request_id` for request correlation.
6. **`raise SystemExit(1) from None`** — per B904 lint rule.
7. **All error messages to `sys.stderr`** — never stdout.
8. **`just lint` 9/9 is the gatekeeper** — run early and often.

### Import-graph rules (CRITICAL)

Console-cli MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `console_cli` own modules — ALLOWED
- Import from `services/telegram-gateway/` — **FORBIDDEN**
- Response models are LOCAL redefinitions, not imports from telegram-gateway or registry-api

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.5.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Modified — add get_task_events, TaskEventsResponseLocal |
| `services/console-cli/src/console_cli/commands/events.py` | Rewritten — real implementation with --follow |
| `services/console-cli/src/console_cli/test_events_command.py` | New — get_task_events + events command tests |
| `_bmad-output/implementation-artifacts/4-4-events-follow-live-tail.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1374-1386 — Story 4.4 definition]
- [Source: `_bmad-output/planning-artifacts/epics.md` lines 2086-2098 — Story 7.5 GET /v1/tasks/{id}/events]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 658-669 — console-cli directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 231 — cross-service contract is HTTP/JSON]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 840-906 — event spine architecture]
- [Source: `packages/events/src/events/envelope.py` — EventEnvelope canonical shape]
- [Source: `_bmad-output/implementation-artifacts/4-3-decision-and-health-commands.md` — Story 4.3 patterns and learnings]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None — straight implementation, no debug cycles.

### Completion Notes List

- Task 1: `RegistryAPIClient` extended with `get_task_events()` and `TaskEventsResponseLocal`. New method validates task_id, supports optional `since` and `limit` query params, returns list of raw event dicts (JSON passthrough). Updated module docstring to "Stories 4.2–4.4".
- Task 2+3: `commands/events.py` rewritten — Typer argument `task_id` + `--follow` boolean option. Without `--follow`: single fetch via `RegistryAPIClient.get_task_events()`, prints each event as sorted JSON line. With `--follow`: long-lived `_poll_events()` coroutine using a single `AsyncClient` for the polling duration, `asyncio.sleep(0.5)` between polls, `since` cursor updated from last `emitted_at`. `KeyboardInterrupt` exits cleanly (exit 0). Full error handling: ConnectError, TimeoutException, HTTPStatusError (404-specific), RegistryResponseError, ValueError.
- Task 4: 15 new tests in `test_events_command.py`: 7 client method tests (success, empty, with since, invalid task_id, HTTP error, network error, malformed body) + 5 non-follow command tests (success, no events, invalid task_id, 404, network error) + 3 follow tests (initial events, Ctrl+C, cursor since param). Updated `test_main.py` — moved `events` from `REAL_NO_ARGS` to `REQUIRES_ARGS`.
- Task 5: `check_imports.py` passes. `just lint` 9/9 green. `just test` 1255 passed (was 1240, +15). Version bumped to 0.5.0. No regressions.

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.5.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Modified — add get_task_events, TaskEventsResponseLocal, update docstring |
| `services/console-cli/src/console_cli/commands/events.py` | Rewritten — real implementation with --follow |
| `services/console-cli/src/console_cli/test_events_command.py` | New — get_task_events + events command tests |
| `services/console-cli/src/console_cli/test_main.py` | Modified — events moved to REQUIRES_ARGS |
| `_bmad-output/implementation-artifacts/4-4-events-follow-live-tail.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### Review Findings

- [x] [Review][Patch] Add `flush=True` to print in `_poll_events` — events delayed when piped to other processes [`events.py:59`]
- [x] [Review][Patch] Generate per-iteration `request_id` in polling loop — single ID defeats per-request correlation [`events.py:48`]
- [x] [Review][Patch] Validate response is a list in `_poll_events` — non-list response crashes with `AttributeError` [`events.py:56`]
- [x] [Review][Patch] Move `import json` to top of test file — removed inline import in `test_get_task_events_malformed_body` [`test_events_command.py:6`]
- [x] [Review][Defer] `status`/`task`/`logs` commands missing `TimeoutException` handler — pre-existing (Story 4.2), deferred
- [x] [Review][Defer] `task.py` does not validate empty title — pre-existing (Story 4.2), deferred
