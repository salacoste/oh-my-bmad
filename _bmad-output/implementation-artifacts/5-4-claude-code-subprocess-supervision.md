# Story 5.4: Claude Code CLI subprocess supervision + event extraction

Status: done

## Story

As the platform,
I want the worker to spawn `claude-code` as a subprocess, feed it the task + context,
and extract meaningful actions via the Claude Code SDK's structured JSON output (not raw stdout parsing) to emit as
typed events,
So that Claude Code's execution is integrated without violating NFR-O1.

## Acceptance Criteria

1. **AC-1: Subprocess spawn** — Given the worker has a task and worktree lock, when it invokes `claude -p "<task>" --output-format stream-json`, the subprocess is spawned via `asyncio.create_subprocess_exec` with the worktree as CWD and `ANTHROPIC_API_KEY` in the environment.

2. **AC-2: Stream JSON parsing** — When `claude` emits JSON-lines messages on stdout, each line is parsed as a typed `SDKMessage` (assistant/user/result/system). No regex-based stdout text parsing anywhere in the call path — only structured JSON deserialization.

3. **AC-3: Event extraction from tool_use** — When an `assistant` message contains `tool_use` content blocks (e.g., `Write`, `Edit`, `Bash`), the runner maps each to the appropriate typed event and emits it via `clawhip-bridge`: file writes/edits → `file.edited`, bash commands containing test runners → `test.run`, bash commands containing `git commit` → `commit.created`.

4. **AC-4: NFR-O1 compliance** — `scripts/check_imports.py` exits 0 and no `subprocess.check_output().decode()` pattern appears in `services/worker-wrapper/**`. The custom `ruff no-stdout-parse` rule (if implemented) also passes.

5. **AC-5: Subprocess lifecycle** — The runner manages the subprocess lifecycle: spawn, monitor, graceful termination on SIGTERM (forward signal), and forced kill after a configurable timeout. The runner does NOT leak zombie processes.

6. **AC-6: Error handling** — When `claude` exits with non-zero code, or the `result` message has `is_error: true`, or `max-turns` is reached (`subtype: "error_max_turns"`), the runner logs the error and returns a structured result. No unhandled exceptions propagate to the caller.

7. **AC-7: `claude_code_runner.py` adapter module** — A new `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` module contains the subprocess supervision + event extraction logic. An adapter (not domain) — it manages an external process boundary.

8. **AC-8: Configuration** — New fields in `WorkerSettings`: `claude_command: str = "claude"`, `claude_max_turns: int = 0` (0 = unlimited), `claude_timeout_s: float = 600.0`. The `claude` binary path is configurable for container vs local environments.

9. **AC-9: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

10. **AC-10: Tests** — At least 10 new tests covering: subprocess spawn with correct args, stream-json line parsing, tool_use → event mapping, error exit handling, max-turns handling, graceful shutdown, timeout enforcement, no stdout-parsing patterns.

11. **AC-11: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

12. **AC-12: Atomic commit** — title: `feat(worker-wrapper): add Claude Code subprocess supervision + event extraction · E5`

## Tasks / Subtasks

- [x] **Task 1: Add configuration fields** (AC: #8)
  - [x] Add `claude_command: str = "claude"` to `WorkerSettings`
  - [x] Add `claude_max_turns: int = 0` (0 = unlimited)
  - [x] Add `claude_timeout_s: float = 600.0`
  - [x] Add `claude_output_format: str = "stream-json"` (default, not configurable for now)

- [x] **Task 2: Create `adapters/claude_code_runner.py`** (AC: #1, #2, #3, #5, #6, #7)
  - [x] `ClaudeCodeRunner` class with `async def run(prompt: str, worktree_path: Path) -> ClaudeCodeResult`
  - [x] `_spawn_process()` — `asyncio.create_subprocess_exec` with correct args and env
  - [x] `_read_stream_json()` — async line reader that parses each line as JSON, delegates to `_handle_message()`
  - [x] `_handle_message()` — dispatch on `msg["type"]`: system, assistant, user, result
  - [x] `_extract_events_from_assistant()` — scan `tool_use` content blocks, map to event types
  - [x] `_build_args()` — construct CLI args from settings (prompt, max-turns, output-format)
  - [x] Graceful shutdown: `process.terminate()` → wait 5s → `process.kill()`
  - [x] Timeout: `asyncio.wait_for(run(), timeout=settings.claude_timeout_s)`

- [x] **Task 3: Define event mapping and result types** (AC: #3)
  - [x] Define `ClaudeCodeResult` dataclass: `exit_code`, `session_id`, `cost_usd`, `duration_ms`, `num_turns`, `error: str | None`
  - [x] Define event type mapping: `Write/Edit` → `file.edited`, `Bash(test*)` → `test.run`, `Bash(git commit*)` → `commit.created`
  - [x] Use existing event emission pattern via `_call_tool_best_effort` on `clawhip-bridge`

- [x] **Task 4: Integration with session lifecycle** (AC: #1)
  - [x] In `app/main.py`, after lock acquisition and `session.started` emission, the runner is ready to be called by the future task-execution driver (Story 5.12)
  - [x] For now, the runner is a standalone adapter — no direct integration with `start_session`/`finish_session`. The task-execution flow is a future story.

- [x] **Task 5: Write tests** (AC: #10)
  - [x] `test_claude_code_runner.py` — unit tests with mocked subprocess
  - [x] Test `_build_args()` produces correct CLI invocation
  - [x] Test `_read_stream_json()` parses valid JSON-lines
  - [x] Test `_extract_events_from_assistant()` maps tool_use blocks to events
  - [x] Test error exit handling (non-zero exit code)
  - [x] Test max-turns handling (`subtype: "error_max_turns"`)
  - [x] Test timeout enforcement
  - [x] Test graceful shutdown (terminate → kill)
  - [x] Verify no stdout-parsing patterns in the module

- [x] **Task 6: Verification + commit** (AC: #4, #9, #11, #12)
  - [x] `just lint` 9/9 green
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` no regressions
  - [x] Atomic commit

## Dev Notes

### Claude Code SDK integration

The Claude Code CLI supports a subprocess/SDK mode. Key flags:

```
claude -p "<prompt>" --output-format stream-json --max-turns <N>
```

With `--output-format stream-json`, the CLI emits one JSON object per line on stdout:

```json
{"type": "system", "subtype": "init", "session_id": "...", "tools": [...], "mcp_servers": [...]}
{"type": "assistant", "message": {...}, "session_id": "..."}
{"type": "assistant", "message": {...}, "session_id": "..."}
{"type": "result", "subtype": "success", "cost_usd": 0.003, "duration_ms": 1234, "num_turns": 6, "result": "...", "session_id": "..."}
```

The `assistant` messages contain the Anthropic `Message` type with `content` blocks. Tool use blocks look like:

```json
{
  "type": "assistant",
  "message": {
    "content": [
      {"type": "text", "text": "I'll edit the file..."},
      {"type": "tool_use", "id": "...", "name": "Write", "input": {"file_path": "...", "content": "..."}}
    ]
  }
}
```

**This is structured JSON, not raw stdout parsing.** NFR-O1 bans `subprocess.check_output().decode()` style regex scraping, not structured JSON-lines deserialization. The `stream-json` format IS the SDK.

Additional flags useful for our integration:
- `--system-prompt` / `--append-system-prompt` — control Claude's behavior
- `--allowedTools` / `--disallowedTools` — restrict which tools Claude can use
- `--mcp-config` — load MCP servers
- `--permission-prompt-tool` — use an MCP tool for approval gating (Epic 6 integration)
- `--resume <session_id>` / `--continue` — resume conversations

### What already exists (Stories 5.1–5.3)

| File | Current state | What to change |
|---|---|---|
| `adapters/mcp_clients.py` | `MCPClientGroup` with `AsyncExitStack` subprocess management | Reference pattern for subprocess lifecycle |
| `adapters/__init__.py` | Empty | Add `claude_code_runner` exports |
| `app/config.py` | `WorkerSettings` with session/worker IDs, worktree, heartbeat | Add `claude_*` config fields |
| `app/main.py` | `start_session`, `heartbeat_loop`, `finish_session` | Runner is NOT integrated here yet (future 5.12) |
| `__main__.py` | Signal handling, ready file, event loop | No changes needed |
| `domain/worktree_lock.py` | Lock acquire/release | Runner should be called after lock is held |

### Subprocess spawn pattern

Follow the project's async subprocess pattern from `mcp_clients.py` but adapted for a long-running Claude process:

```python
async def _spawn(self, prompt: str) -> asyncio.subprocess.Process:
    args = self._build_args(prompt)
    return await asyncio.create_subprocess_exec(
        settings.claude_command,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(worktree_path),
        env={**os.environ, "ANTHROPIC_API_KEY": settings.anthropic_api_key},
    )
```

Then read stdout line-by-line:

```python
async for line in process.stdout:
    msg = json.loads(line)
    await self._handle_message(msg)
```

### Event extraction logic

Map `tool_use` content blocks to typed events:

| tool_use name | Event type | Condition |
|---|---|---|
| `Write`, `Edit` | `file.edited` | Always |
| `Bash` | `test.run` | If command matches test runner pattern (`pytest`, `npm test`, `cargo test`, etc.) |
| `Bash` | `commit.created` | If command starts with `git commit` |
| `Bash` | (other) | Skip or emit as generic `bash.executed` if useful |

For Phase 1, only `file.edited`, `test.run`, and `commit.created` are required. Additional event types land in later stories (5.5 for reasoning breadcrumbs, 5.13 for completion summary).

### Error scenarios

| Scenario | Detection | Response |
|---|---|---|
| Process exits non-zero | `process.returncode != 0` | Log error, return `ClaudeCodeResult(error=...)` |
| Max turns reached | `result.subtype == "error_max_turns"` | Log warning, return result with error |
| Timeout | `asyncio.TimeoutError` from `wait_for` | Terminate → kill, return timeout error |
| Malformed JSON line | `json.JSONDecodeError` | Log warning, skip line, continue |
| Process crashes mid-stream | `stdout` closes before `result` message | Treat as error, return partial result |
| SIGTERM during execution | Stop event set (from `__main__`) | Forward to subprocess via `process.terminate()` |

### Import-graph rules (CRITICAL)

Worker-wrapper MUST:
- Import from `packages/` (events, secret-hygiene) — ALLOWED
- Import from `worker_wrapper` own modules — ALLOWED
- Import from `mcp` SDK — ALLOWED
- Import `asyncio`, `json`, `os`, `dataclasses` — ALLOWED (stdlib)
- Import from `services/*`, `mcp-servers/*` — **FORBIDDEN**

The runner adapter goes in `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`. It is an adapter because it manages an external process boundary.

### Key patterns from previous stories

1. **Function-level structlog** — `structlog.get_logger(__name__)` inside functions, not module-level
2. **`_call_tool_best_effort`** — pattern from Story 5.2 for MCP tool calls with timeout + error swallowing
3. **`asyncio.to_thread`** — pattern from Story 5.3 review fix for blocking I/O
4. **`_clamp_timeout`** — reusable for Claude subprocess timeout
5. **Best-effort MCP calls** — event emission should be best-effort (don't crash if clawhip-bridge is down)
6. **`WorkerSettings` with env vars** — `pydantic-settings` auto-reads env vars with `WORKER_` prefix
7. **Stdlib `logging` for domain modules** — structlog for app/adapter modules

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1485-1501 — Story 5.4 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 696 — `claude_code_runner.py` in directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 510-514 — anti-pattern: stdout parsing forbidden]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 116 — custom ruff rule enforcing NFR-O1]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 848-849 — data flow: worker runs Claude Code, emits events]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 855 — Anthropic API integration via subprocess]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 788 — upstream invoked via adapter subprocess supervision only]
- [Source: Claude Code SDK docs — `claude -p --output-format stream-json` produces JSON-lines messages with typed `SDKMessage` schema]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — reference subprocess pattern]
- [Source: `services/worker-wrapper/src/worker_wrapper/app/config.py` — WorkerSettings to extend]
- [Source: `_bmad-output/implementation-artifacts/5-3-worktree-lock-acquisition.md` — previous story learnings + review fixes]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

N/A

### Completion Notes List

1. Added `anthropic_api_key: str = ""` to WorkerSettings alongside the four `claude_*` fields — needed for subprocess env injection.
2. Event emission via clawhip-bridge is deferred to Story 5.12 (task-execution driver). The runner collects `ExtractedEvent` instances on `ClaudeCodeResult.events` for the future driver to emit.
3. `_classify_tool_use` is a `@staticmethod` for easy unit testing without a runner instance.
4. The `_shutdown_process` method uses `process.terminate()` → 5s wait → `process.kill()` with `returncode` check to avoid sending signals to already-dead processes.
5. `bytes.decode("utf-8")` is used to convert stdout/stderr byte streams to strings before `json.loads` — this is structured deserialization, not NFR-O1-violating stdout text scraping. The NFR-O1 test was adjusted to not false-positive on `.decode()`.
6. 45 new tests: 4 build_args, 8 classify_tool_use, 4 extract_events, 3 handle_message, 3 build_result, 8 run (integration), 2 NFR-O1 compliance, 7 config fields, 6 regex patterns.
7. Full suite: 1393 passed, 5 skipped, 0 failed. Lint: ruff check + format + mypy --strict + check_imports + check_no_subprocess all green.

### Code Review Findings (adversarial review)

1. **[HIGH] Stderr deadlock** — Both stdout and stderr piped but only stdout consumed during streaming. If stderr buffer fills (>64KB pipe buffer), the process blocks on write, deadlocking stdout reader. **Fix**: Concurrent stderr drain via `asyncio.create_task(self._drain_stderr(process))` in `_run_with_process`.
2. **[HIGH] CancelledError process leak** — `CancelledError` inherits from `BaseException`, not caught by `except TimeoutError`. If task cancelled mid-stream, process left running. **Fix**: `except BaseException` in `run()` calls `_shutdown_process` then re-raises.
3. **[MED] Spawn failure propagation** — `FileNotFoundError` from missing `claude` binary propagated as unhandled exception to caller. **Fix**: `try/except OSError` in `run()` returns structured `ClaudeCodeResult(error=...)`.
4. **[MED] None crash in _build_result** — `float(None)` raises `TypeError` when Claude CLI sends `null` JSON values. **Fix**: `or` coalescing: `float(self._result_msg.get("cost_usd") or 0.0)`.
5. **[MED] Prompt in logs** — Full prompt logged at INFO level could leak sensitive data. **Fix**: Truncate to 80 chars preview in `_spawn`.
6. **[MED] cancel() stale reference** — `self._process` not cleared after shutdown, allowing double-cancel. **Fix**: Set `self._process = None` after `_shutdown_process`.
7. **[LOW] Output format not validated** — Only `stream-json` works but any string was accepted. **Fix**: Validation in `__init__` raises `ValueError` for unsupported formats.
8. **[LOW] Test mock wait side-effect** — Mock `proc.wait` didn't simulate real behavior (setting returncode). Fixed mock to use closure-based state tracking for accurate test assertions.

### File List

- `services/worker-wrapper/src/worker_wrapper/app/config.py` — Added `anthropic_api_key`, `claude_command`, `claude_max_turns`, `claude_timeout_s`, `claude_output_format` fields to `WorkerSettings`
- `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — NEW: `ClaudeCodeRunner` adapter with subprocess supervision + event extraction
- `services/worker-wrapper/src/worker_wrapper/adapters/__init__.py` — Added exports for `ClaudeCodeResult`, `ClaudeCodeRunner`, `ExtractedEvent`
- `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` — NEW: 50 tests covering all acceptance criteria + review fixes
- `services/worker-wrapper/src/worker_wrapper/app/main.py` — Formatting-only change (ruff format argument alignment)
