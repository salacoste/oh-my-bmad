"""Claude Code CLI subprocess supervision + event extraction (Story 5.4).

Spawns ``claude`` as a subprocess with ``--output-format stream-json``, reads
structured JSON-lines from stdout, and extracts typed events from tool_use
content blocks and reasoning breadcrumbs from thinking/text blocks.
No regex-based stdout text parsing — only structured JSON deserialization
(NFR-O1).

This is an adapter module — it manages an external process boundary.  Direct
integration with the session lifecycle arrives in Story 5.12.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.reasoning import (
    ReasoningBreadcrumb,
    extract_reasoning_from_content,
)

# Graceful shutdown: wait this many seconds after SIGTERM before SIGKILL.
_GRACE_PERIOD_S: float = 5.0

# Only stream-json is supported — the runner relies on JSON-lines output.
_SUPPORTED_OUTPUT_FORMATS: frozenset[str] = frozenset({"stream-json"})

# Test-runner command patterns for event classification.
_TEST_PATTERN: re.Pattern[str] = re.compile(
    r"\b(pytest|npm test|cargo test|go test|just test|make test|jest|mocha)\b",
)
_COMMIT_PATTERN: re.Pattern[str] = re.compile(r"^\s*git\s+commit\b")
_GIT_PUSH_PATTERN: re.Pattern[str] = re.compile(r"^\s*git\s+push\b")

# Maximum prompt length included in spawn log (prevents sensitive data leaks).
_LOG_PROMPT_PREVIEW_LEN: int = 80


@dataclass
class ExtractedEvent:
    """A typed event extracted from a Claude Code tool_use block."""

    event_type: str  # "file.edited", "test.run", "commit.created"
    tool_name: str  # Original tool: "Write", "Edit", "Bash"
    tool_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeCodeResult:
    """Structured result from a Claude Code subprocess run."""

    exit_code: int = -1
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    error: str | None = None
    events: list[ExtractedEvent] = field(default_factory=list)
    reasoning: list[ReasoningBreadcrumb] = field(default_factory=list)
    stderr: str = ""


class ClaudeCodeRunner:
    """Supervises a ``claude`` subprocess and extracts typed events.

    Usage::

        runner = ClaudeCodeRunner(settings)
        result = await runner.run("Implement feature X", Path("/worktree"))
        for event in result.events:
            print(event.event_type, event.tool_name)
    """

    def __init__(self, settings: WorkerSettings) -> None:
        if settings.claude_output_format not in _SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(
                f"Unsupported claude_output_format: "
                f"{settings.claude_output_format!r}. "
                f"Supported: {_SUPPORTED_OUTPUT_FORMATS}",
            )
        self._settings = settings
        self._events: list[ExtractedEvent] = []
        self._reasoning: list[ReasoningBreadcrumb] = []
        self._session_id: str = ""
        self._result_msg: dict[str, Any] = {}
        self._process: asyncio.subprocess.Process | None = None

    def _build_args(self, prompt: str) -> list[str]:
        """Build CLI arguments for the ``claude`` subprocess.

        Story 9.6 / FR59: propagates trace_id via ``--trace-id`` CLI flag so
        any nested MCP tool calls Claude Code makes inherit the operator's
        causal correlation token. Dual-mechanism: ``_spawn`` ALSO sets
        ``OMB_TRACE_ID`` in the subprocess env as a fallback for Claude Code
        builds that do not yet recognise the flag (most CLIs tolerate unknown
        trailing flags as no-ops).
        """
        args = [
            "-p",
            prompt,
            "--output-format",
            self._settings.claude_output_format,
        ]
        if self._settings.claude_max_turns > 0:
            args.extend(["--max-turns", str(self._settings.claude_max_turns)])
        # Story 9.6 / FR59 — trace_id propagation to Claude Code subprocess.
        args.extend(["--trace-id", self._settings.resolve_trace_id()])
        return args

    async def _spawn(
        self,
        prompt: str,
        worktree_path: Path,
    ) -> asyncio.subprocess.Process:
        """Spawn the ``claude`` subprocess with correct args and env."""
        args = self._build_args(prompt)
        env = dict(os.environ)
        if self._settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self._settings.anthropic_api_key
        # Story 9.6 / FR59 — also propagate trace_id via OMB_TRACE_ID env var
        # (belt-and-braces alongside the ``--trace-id`` CLI flag). Claude Code
        # may consume either surface; the env-var path is safe today because
        # unknown env vars are ignored.
        env["OMB_TRACE_ID"] = self._settings.resolve_trace_id()
        log = structlog.get_logger(__name__)
        preview = prompt[:_LOG_PROMPT_PREVIEW_LEN]
        if len(prompt) > _LOG_PROMPT_PREVIEW_LEN:
            preview += "..."
        log.info(
            "claude_code_spawning",
            command=self._settings.claude_command,
            prompt_preview=preview,
            cwd=str(worktree_path),
        )
        return await asyncio.create_subprocess_exec(
            self._settings.claude_command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(worktree_path),
            env=env,
        )

    async def _read_stream(self, process: asyncio.subprocess.Process) -> None:
        """Read JSON-lines from stdout, dispatch each to ``_handle_message``."""
        if process.stdout is None:
            return
        log = structlog.get_logger(__name__)
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("claude_code_malformed_json", line=line[:200])
                continue
            await self._handle_message(msg)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> str:
        """Read all of stderr into a string.  Safe to call concurrently."""
        if process.stderr is None:
            return ""
        data = await process.stderr.read()
        return data.decode("utf-8", errors="replace").strip()

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch on ``msg["type"]``: system, assistant, user, result."""
        msg_type = msg.get("type")
        if msg_type == "system":
            self._session_id = msg.get("session_id", "")
        elif msg_type == "assistant":
            self._extract_events(msg)
        elif msg_type == "result":
            self._result_msg = msg

    def _extract_events(self, msg: dict[str, Any]) -> None:
        """Scan ``tool_use`` content blocks, map to event types.

        Also extracts reasoning breadcrumbs from ``thinking`` and ``text``
        blocks via the domain reasoning module (Story 5.5).
        """
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            event = self._classify_tool_use(tool_name, tool_input)
            if event is not None:
                self._events.append(event)
        # Extract reasoning breadcrumbs (Story 5.5).
        breadcrumbs = extract_reasoning_from_content(content, self._session_id)
        self._reasoning.extend(breadcrumbs)

    @staticmethod
    def _classify_tool_use(
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ExtractedEvent | None:
        """Map a tool_use block to a typed event."""
        if tool_name in ("Write", "Edit"):
            return ExtractedEvent(
                event_type="file.edited",
                tool_name=tool_name,
                tool_input=tool_input,
            )
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if isinstance(command, str):
                if _COMMIT_PATTERN.match(command):
                    return ExtractedEvent(
                        event_type="commit.created",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
                if _GIT_PUSH_PATTERN.match(command):
                    return ExtractedEvent(
                        event_type="git.push",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
                if _TEST_PATTERN.search(command):
                    return ExtractedEvent(
                        event_type="test.run",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
        return None

    async def _shutdown_process(self, process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait -> kill."""
        log = structlog.get_logger(__name__)
        if process.returncode is not None:
            return
        log.info("claude_code_terminating", pid=process.pid)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_GRACE_PERIOD_S)
        except TimeoutError:
            log.warning("claude_code_kill_after_timeout", pid=process.pid)
            process.kill()
            await process.wait()

    def _build_result(self, exit_code: int, stderr: str) -> ClaudeCodeResult:
        """Build a ``ClaudeCodeResult`` from accumulated state."""
        result = ClaudeCodeResult(
            exit_code=exit_code,
            session_id=self._session_id,
            events=list(self._events),
            reasoning=list(self._reasoning),
            stderr=stderr,
        )
        # Extract metadata from the final "result" message.
        # Use ``or`` coalescing to handle JSON ``null`` values safely.
        if self._result_msg:
            try:
                result.cost_usd = float(self._result_msg.get("cost_usd") or 0.0)
                result.duration_ms = int(self._result_msg.get("duration_ms") or 0)
                result.num_turns = int(self._result_msg.get("num_turns") or 0)
            except (ValueError, TypeError):
                log = structlog.get_logger(__name__)
                log.warning("claude_code_malformed_result_fields")
            subtype = self._result_msg.get("subtype", "")
            if subtype == "error_max_turns":
                result.error = "Max turns reached"
            elif self._result_msg.get("is_error"):
                result_text = self._result_msg.get("result", "")
                result.error = str(result_text)[:500] if result_text else "Unknown error"
        return result

    async def _run_with_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> ClaudeCodeResult:
        """Stream stdout + drain stderr concurrently, then build result."""
        log = structlog.get_logger(__name__)
        log.info("claude_code_started", pid=process.pid)

        stderr_task = asyncio.create_task(self._drain_stderr(process))
        try:
            await asyncio.wait_for(
                self._read_stream(process),
                timeout=self._settings.claude_timeout_s,
            )
        except TimeoutError:
            log.error(
                "claude_code_timeout",
                timeout=self._settings.claude_timeout_s,
            )
            stderr_task.cancel()
            with contextlib_suppress():
                await stderr_task
            await self._shutdown_process(process)
            # Drain stderr from the killed process for diagnostics.
            stderr = ""
            with contextlib_suppress():
                stderr = await self._drain_stderr(process)
            return ClaudeCodeResult(
                exit_code=-1,
                session_id=self._session_id,
                error=f"Timed out after {self._settings.claude_timeout_s}s",
                events=list(self._events),
                reasoning=list(self._reasoning),
                stderr=stderr,
            )

        # Normal completion — collect stderr (may already be done).
        try:
            stderr = await stderr_task
        except Exception:
            stderr = ""

        await process.wait()
        exit_code = process.returncode if process.returncode is not None else -1

        result = self._build_result(exit_code, stderr)

        if exit_code != 0 and not result.error:
            result.error = f"Process exited with code {exit_code}"
            if stderr:
                result.error += f": {stderr[:500]}"
            log.error(
                "claude_code_nonzero_exit",
                exit_code=exit_code,
                stderr=stderr[:500],
            )

        log.info(
            "claude_code_completed",
            session_id=result.session_id,
            exit_code=result.exit_code,
            events=len(result.events),
            error=result.error,
        )
        return result

    async def run(self, prompt: str, worktree_path: Path) -> ClaudeCodeResult:
        """Run ``claude`` with the given prompt and return a structured result."""
        self._events = []
        self._reasoning = []
        self._session_id = ""
        self._result_msg = {}

        # Catch spawn failures (missing binary, bad worktree path).
        try:
            process = await self._spawn(prompt, worktree_path)
        except OSError as exc:
            return ClaudeCodeResult(
                exit_code=-1,
                error=f"Failed to spawn claude: {exc}",
            )

        self._process = process
        try:
            return await self._run_with_process(process)
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit — clean up.
            await self._shutdown_process(process)
            self._process = None
            raise

    async def cancel(self) -> None:
        """Cancel a running subprocess (forward SIGTERM)."""
        if self._process is not None:
            await self._shutdown_process(self._process)
            self._process = None


def contextlib_suppress() -> Any:
    """Return ``contextlib.suppress(Exception)`` — avoids module-level import."""
    import contextlib

    return contextlib.suppress(Exception)


__all__ = [
    "ClaudeCodeResult",
    "ClaudeCodeRunner",
    "ExtractedEvent",
    "ReasoningBreadcrumb",
]
