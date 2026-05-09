# Story 4.5: Error rendering (RFC 7807 to text)

Status: review

## Story

As the operator,
I want RFC 7807 error responses rendered as readable console text with exit codes,
so that scripting against the CLI is possible.

## Acceptance Criteria

1. **AC-1: Structured error rendering** — When the API returns a non-2xx response with an RFC 7807 body, the CLI prints `Error: <title> -- <detail>` to stderr, followed by one context line per extension field (e.g., `  task_id: t-...`).

2. **AC-2: Exit code mapping** — The CLI exits with a code derived from the HTTP status: `2` for 422 (validation), `4` for 404 (not found), `5` for 409 (conflict). All other HTTP errors exit with `1`. Network errors (`ConnectError`, `TimeoutException`) also exit with `1`.

3. **AC-3: stdout is empty on error** — When any error occurs, stdout contains no output. All error text goes to stderr.

4. **AC-4: Centralized `render_http_error`** — A shared function `render_http_error(exc: httpx.HTTPStatusError) -> NoReturn` in a new module `adapters/error_renderer.py` handles RFC 7807 parsing, formatting, and exit code mapping. All commands use it instead of inline `parse_error_detail` + `raise SystemExit`.

5. **AC-5: All commands refactored** — Every command file (`task`, `status`, `logs`, `approve`, `reject`, `stop`, `retry`, `ping`, `agent`, `events`) calls `render_http_error` for `HTTPStatusError`. The existing `parse_error_detail()` in `registry_api_client.py` is retained for backward compatibility but commands no longer call it directly.

6. **AC-6: Import-graph rules pass** — `scripts/check_imports.py` passes. `error_renderer.py` imports only from `httpx`, `sys`, and `console_cli` own modules.

7. **AC-7: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

8. **AC-8: Tests** — New tests for `render_http_error`: RFC 7807 body with title+detail+extensions, plain-text body, each exit code mapping (422→2, 404→4, 409→5, 500→1). Updated command tests assert correct exit codes per error type. At least 8 new tests.

9. **AC-9: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

10. **AC-10: Atomic commit** — title: `feat(console-cli): implement RFC 7807 error rendering with exit codes · E4`

## Tasks / Subtasks

- [x] **Task 1: Create `error_renderer.py`** (AC: #1, #2, #4, #6)
  - [x] Create `services/console-cli/src/console_cli/adapters/error_renderer.py`
  - [x] Define exit code constants: `EXIT_VALIDATION = 2`, `EXIT_NOT_FOUND = 4`, `EXIT_CONFLICT = 5`, `EXIT_ERROR = 1`
  - [x] Implement `exit_code_for_status(status_code: int) -> int` — maps HTTP status to exit code
  - [x] Implement `render_http_error(exc: httpx.HTTPStatusError) -> NoReturn` — parses RFC 7807 body, formats error message, prints to stderr, raises `SystemExit(code)`
  - [x] RFC 7807 parsing: extract `title`, `detail`, `extensions` from response JSON
  - [x] Format: `Error: <title> -- <detail>` then one indented line per extension: `  key: value`
  - [x] Fallback: if body is not RFC 7807 (no `title`/`detail`), use `response.text` as detail with title `"HTTP <status>"`
  - [x] Module imports only: `sys`, `httpx`, `typing.NoReturn`

- [x] **Task 2: Refactor all commands to use `render_http_error`** (AC: #3, #5)
  - [x] In each command file, replace the `except httpx.HTTPStatusError` block with a single call to `render_http_error(exc)`
  - [x] Remove direct calls to `parse_error_detail()` from command files (keep in `registry_api_client.py` for now)
  - [x] Commands to update: `task.py`, `status.py`, `logs.py`, `approve.py`, `reject.py`, `stop.py`, `retry.py`, `ping.py`, `agent.py`, `events.py`
  - [x] Verify `stdout` is empty on error paths (no `print()` to stdout before error in try block for commands that don't have a successful print before the error)

- [x] **Task 3: Write tests** (AC: #8)
  - [x] Create `services/console-cli/src/console_cli/test_error_renderer.py`
  - [x] Test `exit_code_for_status`: 422→2, 404→4, 409→5, 400→1, 500→1, 503→1
  - [x] Test `render_http_error` with full RFC 7807 body (title, detail, extensions) — verifies formatted output + exit code
  - [x] Test `render_http_error` with plain-text body — verifies fallback formatting
  - [x] Test `render_http_error` with non-JSON body — verifies fallback to response.text
  - [x] Test that stdout is empty on error (capture stdout separately)
  - [x] Update existing command tests: assert specific exit codes (4 for 404, 2 for 422, etc.) instead of `!= 0`

- [x] **Task 4: Verification + commit** (AC: #6, #7, #9, #10)
  - [x] `scripts/check_imports.py` passes
  - [x] `just lint` 9/9 green
  - [x] `just test` — no regressions, new tests counted
  - [x] Version bump to `0.6.0` in `__init__.py` and `pyproject.toml`
  - [x] Atomic commit

## Dev Notes

### What already exists

| File | Current state | What to change |
|---|---|---|
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Has `parse_error_detail()` — extracts only `detail` from RFC 7807 | Retain (used internally by `RegistryAPIClient`); commands stop calling it directly |
| All 10 command files in `commands/` | Inline `except HTTPStatusError` blocks with `parse_error_detail()` + `raise SystemExit(1)` | Replace with single `render_http_error(exc)` call |
| `services/console-cli/src/console_cli/adapters/error_renderer.py` | Does not exist | New file — centralized error rendering |

### RFC 7807 response shape (from architecture.md)

```json
{
  "type": "/errors/<slug>",
  "title": "<short human-readable>",
  "status": 409,
  "detail": "<longer explanation>",
  "instance": "/v1/tasks",
  "extensions": {
    "task_id": "t-0192a1b5-...",
    "idempotency_key": "..."
  }
}
```

`extensions` holds platform-specific fields; never flatten into top level.

### Exit code mapping

```
HTTP 422 (Unprocessable Entity) → exit 2  (validation error)
HTTP 404 (Not Found)            → exit 4  (resource not found)
HTTP 409 (Conflict)             → exit 5  (idempotency collision, state conflict)
HTTP 4xx (other)                → exit 1  (generic client error)
HTTP 5xx                        → exit 1  (server error)
ConnectError / TimeoutException → exit 1  (network error)
```

This mapping enables shell scripting:
```bash
oh-my-bmad-cli status t-xxx
case $? in
  0) echo "success" ;;
  2) echo "validation error" ;;
  4) echo "not found" ;;
  5) echo "conflict" ;;
  *) echo "other error" ;;
esac
```

### Error rendering format

Full RFC 7807 body:
```
Error: Duplicate idempotency key -- A decision with this idempotency key was already recorded.
  task_id: t-0192a1b5-...
  idempotency_key: ...
```

Fallback (non-RFC 7807 body, e.g., plain text or HTML):
```
Error: HTTP 500 -- Internal Server Error
```

### `error_renderer.py` implementation sketch

```python
"""Centralized RFC 7807 error rendering for console-cli commands."""

from __future__ import annotations

import sys

import httpx

EXIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5


def exit_code_for_status(status_code: int) -> int:
    return {
        422: EXIT_VALIDATION,
        404: EXIT_NOT_FOUND,
        409: EXIT_CONFLICT,
    }.get(status_code, EXIT_ERROR)


def render_http_error(exc: httpx.HTTPStatusError) -> None:
    """Render RFC 7807 error to stderr and exit with mapped code."""
    code = exit_code_for_status(exc.response.status_code)
    title: str | None = None
    detail: str | None = None
    extensions: dict[str, object] | None = None

    try:
        body: dict[str, object] = exc.response.json()
        if isinstance(body.get("title"), str):
            title = body["title"]
        if isinstance(body.get("detail"), str):
            detail = body["detail"]
        if isinstance(body.get("extensions"), dict):
            extensions = body["extensions"]
    except Exception:
        pass

    if title is None and detail is None:
        title = f"HTTP {exc.response.status_code}"
        detail = exc.response.text[:200] or exc.response.reason_phrase

    parts = [f"Error: {title or f'HTTP {exc.response.status_code}'}"]
    if detail:
        parts[0] += f" -- {detail}"
    if extensions:
        for key, value in extensions.items():
            parts.append(f"  {key}: {value}")

    print("\n".join(parts), file=sys.stderr)
    raise SystemExit(code) from None
```

### Command refactoring pattern

Before (current pattern in every command):
```python
except httpx.HTTPStatusError as exc:
    if exc.response.status_code == 404:
        print(f"Task {task_id} not found.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"Error: {parse_error_detail(exc)}", file=sys.stderr)
    raise SystemExit(1) from None
```

After:
```python
except httpx.HTTPStatusError as exc:
    render_http_error(exc)
```

The 404-specific messages (`"Task {task_id} not found."`) will be replaced by the RFC 7807 rendering. If the API returns a 404 with an RFC 7807 body containing `title: "Not Found"` and `detail: "task t-xxx not found"`, the output will be `Error: Not Found -- task t-xxx not found` — which is better for scripting (machine-parseable exit code + consistent format).

**Important:** Commands that have special 404 messages (approve, reject, stop, retry, agent, events) lose their custom per-command 404 text. This is intentional — the AC requires consistent RFC 7807 rendering, not per-command custom messages. The API will provide meaningful `title` and `detail` in its 404 response.

### stdout cleanliness

The AC requires stdout to be empty on error. Currently, commands like `task.py` call `print(f"Task {result.task_id} created.")` AFTER the `try/except` in the success path. If the try block succeeds, print runs; if it fails, the except handler fires and no stdout print occurs. This is already correct for most commands.

**Exception:** `events.py` with `--follow` prints events via `print(json.dumps(...))` as they arrive. If a subsequent poll fails, events already printed to stdout remain. This is acceptable — the AC refers to the error response itself not producing stdout, not retracting previously streamed data.

### Key patterns from Stories 4.2–4.4

1. **Error rendering pattern** — all commands follow the same try/except structure with ConnectError, TimeoutException, HTTPStatusError, RegistryResponseError, ValueError. `render_http_error` replaces only the HTTPStatusError handler.
2. **Local response models** — frozen Pydantic models, NOT imported from other services.
3. **`parse_error_detail()`** — shared utility in `registry_api_client.py`. Retain it there; it's still used internally. Commands now use `render_http_error` instead.
4. **`raise SystemExit(n) from None`** — per B904 lint rule. `render_http_error` follows this.
5. **All error messages to `sys.stderr`** — never stdout. `render_http_error` prints to stderr.
6. **`just lint` 9/9 is the gatekeeper** — run early and often.
7. **Events command special case** — `events.py` has TWO error handling blocks (follow and non-follow). Both need refactoring. The follow path catches `HTTPStatusError` and needs `render_http_error`.
8. **Story 4.4 review fix** — `_poll_events` bypasses `RegistryAPIClient` and catches `HTTPStatusError` directly. It also needs `render_http_error`.

### Import-graph rules (CRITICAL)

Console-cli MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `console_cli` own modules — ALLOWED
- Import from `services/telegram-gateway/` — **FORBIDDEN**
- `error_renderer.py` imports only `sys`, `httpx` — zero package imports needed

### Testing pattern

- Test `exit_code_for_status` directly (simple unit test)
- Test `render_http_error` by constructing `httpx.HTTPStatusError` with mock `httpx.Response` objects
- Use `capsys` fixture to verify stderr output and stdout emptiness
- For command integration tests: verify that 404 responses produce exit code 4, 422 produces exit code 2
- Follow the same `AsyncMock` + `patch` pattern as Stories 4.2–4.4

### Deferred from Story 4.4 review

Two items deferred from Story 4.4 review that are in scope for this story:
1. `status`/`task`/`logs` commands missing `TimeoutException` handler — fix as part of this refactor
2. `task.py` does not validate empty title — fix as part of this refactor (add validation before HTTP call)

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.6.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/error_renderer.py` | New — centralized RFC 7807 error rendering |
| `services/console-cli/src/console_cli/commands/task.py` | Modified — use render_http_error, add TimeoutException, add title validation |
| `services/console-cli/src/console_cli/commands/status.py` | Modified — use render_http_error, add TimeoutException |
| `services/console-cli/src/console_cli/commands/logs.py` | Modified — use render_http_error, add TimeoutException |
| `services/console-cli/src/console_cli/commands/approve.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/reject.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/stop.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/retry.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/ping.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/agent.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/events.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/test_error_renderer.py` | New — render_http_error + exit code tests |
| `services/console-cli/src/console_cli/test_decision_commands.py` | Modified — assert specific exit codes |
| `services/console-cli/src/console_cli/test_events_command.py` | Modified — assert specific exit codes |
| `services/console-cli/src/console_cli/test_main.py` | Modified — if needed |
| `_bmad-output/implementation-artifacts/4-5-error-rendering-cli.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines ~1387-1395 — Story 4.5 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 226-228 — RFC 7807 error envelope decision]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 366-382 — RFC 7807 format pattern with extensions]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 421-426 — Error handling process patterns]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 312-318 — HTTP status code conventions]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 658-669 — console-cli directory structure]
- [Source: `_bmad-output/implementation-artifacts/4-4-events-follow-live-tail.md` — Story 4.4 patterns and deferred items]
- [Source: `_bmad-output/implementation-artifacts/4-3-decision-and-health-commands.md` — Story 4.3 patterns]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None — straight implementation, no debug cycles.

### Completion Notes List

- Task 1: Created `adapters/error_renderer.py` with `exit_code_for_status()` (maps 422→2, 404→4, 409→5, others→1) and `render_http_error()` (parses RFC 7807 body for title/detail/extensions, formats as `Error: <title> -- <detail>` with indented extension lines, falls back to `HTTP <status> -- <text>` for non-RFC-7807 responses). All output to stderr, raises `SystemExit(code)`.
- Task 2: Refactored all 10 command files to use `render_http_error(exc)` replacing inline `parse_error_detail()` + `raise SystemExit(1)`. Added `TimeoutException` handler to `task.py`, `status.py`, `logs.py` (deferred from Story 4.4 review). Added empty-title validation to `task.py`. Removed `parse_error_detail` import from all command files (retained in `registry_api_client.py`). Events command both follow and non-follow paths refactored.
- Task 3: 13 new tests in `test_error_renderer.py`: 6 exit code mapping tests + 7 render tests (full RFC 7807, no extensions, title only, plain text fallback, non-JSON fallback, stderr output verification, stdout empty check). Updated `test_status_command.py` and `test_logs_command.py` 404 tests to assert `exit_code == 4` instead of `== 1`.
- Task 4: `check_imports.py` passes. `just lint` 9/9 green. `just test` 1268 passed (was 1255, +13). Version bumped to 0.6.0. No regressions.

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — version bump 0.6.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/adapters/error_renderer.py` | New — centralized RFC 7807 error rendering |
| `services/console-cli/src/console_cli/commands/task.py` | Modified — use render_http_error, add TimeoutException, add title validation |
| `services/console-cli/src/console_cli/commands/status.py` | Modified — use render_http_error, add TimeoutException |
| `services/console-cli/src/console_cli/commands/logs.py` | Modified — use render_http_error, add TimeoutException |
| `services/console-cli/src/console_cli/commands/approve.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/reject.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/stop.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/retry.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/ping.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/agent.py` | Modified — use render_http_error |
| `services/console-cli/src/console_cli/commands/events.py` | Modified — use render_http_error (both follow + non-follow) |
| `services/console-cli/src/console_cli/test_error_renderer.py` | New — 13 tests for error rendering |
| `services/console-cli/src/console_cli/test_status_command.py` | Modified — 404 test asserts exit code 4 |
| `services/console-cli/src/console_cli/test_logs_command.py` | Modified — 404 test asserts exit code 4 |
| `_bmad-output/implementation-artifacts/4-5-error-rendering-cli.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |
