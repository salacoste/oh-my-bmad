"""Gemini CLI subprocess supervision + event extraction (Phase 6 / FR107).

Spawns ``gemini`` CLI as a subprocess with ``--json`` flag, reads structured
JSONL from stdout, and extracts typed events from response messages.
No regex-based stdout text parsing — only structured JSON deserialization
(NFR-O1 / P6-I2).

This adapter mirrors :mod:`worker_wrapper.adapters.codex_runner` and
satisfies the :class:`worker_wrapper.domain.runtime_adapter.RuntimeAdapter`
protocol (ADR-0015 D1).

Credential isolation (P6-I5): ``GEMINI_API_KEY`` is injected from settings,
NOT from the parent env. ``_GEMINI_ENV_ALLOWLIST`` explicitly excludes
``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, and ``GITHUB_TOKEN``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.runtime_adapter import HealthCheckResult

if TYPE_CHECKING:
    from worker_wrapper.adapters.claude_code_runner import TerminationResult

# Graceful shutdown: wait this many seconds after SIGTERM before SIGKILL.
_GRACE_PERIOD_S: float = 5.0

# Maximum prompt length included in spawn log (prevents sensitive data leaks).
_LOG_PROMPT_PREVIEW_LEN: int = 80

# P6-I5 — explicit child-env allowlist for the spawned ``gemini`` subprocess.
# This MUST stay an explicit allowlist: NEVER forward the whole parent
# environment here.  Mirrors ``codex_runner._CODEX_ENV_ALLOWLIST``
# but uses ``GEMINI_`` prefix instead of ``CODEX_``.
#
# ANTHROPIC_API_KEY is INTENTIONALLY NOT in this allowlist — P6-I5 credential
# isolation: Claude credentials must never reach the Gemini subprocess.
# OPENAI_API_KEY is INTENTIONALLY NOT in this allowlist — same discipline.
# GITHUB_TOKEN is INTENTIONALLY NOT in this allowlist — G-SEC-2 discipline.
#
# GEMINI_API_KEY is INTENTIONALLY NOT in this allowlist — it is re-injected
# from settings in ``_spawn`` (see the ``google_api_key`` overlay).
_GEMINI_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Process basics
        "PATH",
        "HOME",
        "USER",
        # Locale
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Temp directories
        "TMPDIR",
        "TMP",
        "TEMP",
        # TLS / CA bundles (custom-CA deployments)
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)

# P6-I5 — prefix allowlist: task/trace vars (``OMB_*``) + Gemini config
# (``GEMINI_*``). NEVER name a secret with these prefixes.
_GEMINI_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "GEMINI_")

# P6-I5 — denylist: vars that match the GEMINI_ prefix but must NOT be
# forwarded from parent env.  They are re-injected from settings in _spawn.
_GEMINI_ENV_DENYLIST: frozenset[str] = frozenset({"GEMINI_API_KEY"})


def _build_child_env() -> dict[str, str]:
    """Return the explicit child-env for the spawned ``gemini`` subprocess.

    P6-I5: only parent-env vars matching ``_GEMINI_ENV_ALLOWLIST`` or
    starting with one of ``_GEMINI_ENV_PREFIXES`` are forwarded; everything
    else (operator / platform secrets, ANTHROPIC_API_KEY, OPENAI_API_KEY)
    is dropped. The GEMINI_API_KEY and OMB_TRACE_ID overlays are applied by
    the caller (``_spawn``).
    """
    return {
        k: v
        for k, v in os.environ.items()
        if (k in _GEMINI_ENV_ALLOWLIST or k.startswith(_GEMINI_ENV_PREFIXES))
        and k not in _GEMINI_ENV_DENYLIST
    }


@dataclass
class ExtractedEvent:
    """A typed event extracted from a Gemini tool_use block.

    Parallel to :class:`codex_runner.ExtractedEvent`.
    """

    event_type: str  # "file.edited", "test.run", "commit.created", "git.push"
    tool_name: str  # Original Gemini tool name
    tool_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeminiResult:
    """Structured result from a Gemini subprocess run.

    Parallel to :class:`codex_runner.CodexResult`.
    """

    exit_code: int = -1
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    error: str | None = None
    events: list[ExtractedEvent] = field(default_factory=list)
    stderr: str = ""
    # Token usage from Gemini response ``usageMetadata`` fields.
    input_tokens: int = 0
    output_tokens: int = 0


# Exit code mapping per architecture amendment.
#   0   → completed (success)
#   1   → failed (task error)
#   2   → failed (invalid arguments / configuration)
#   130 → cancelled (SIGTERM received)
#   137 → cancelled (SIGKILL received)
#   -1  → timeout (adapter-level)


class GeminiRunner:
    """Supervises a ``gemini`` subprocess and extracts typed events.

    Satisfies the :class:`RuntimeAdapter` protocol (FR107 / ADR-0015).

    Usage::

        runner = GeminiRunner(settings)
        result = await runner.run("Implement feature X", Path("/worktree"))
        for event in result.events:
            print(event.event_type, event.tool_name)
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._events: list[ExtractedEvent] = []
        self._session_id: str = ""
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._num_turns: int = 0
        self._process: asyncio.subprocess.Process | None = None

    # -- RuntimeAdapter protocol (FR107 / ADR-0015) --

    @property
    def runtime_name(self) -> str:
        """Runtime identifier: ``"gemini"``."""
        return "gemini"

    async def health_check(self) -> HealthCheckResult:
        """Probe Gemini CLI binary availability (NFR-M12 / Story 33.5).

        Checks:
        1. Binary installed via ``shutil.which``.
        2. Version via ``gemini --version`` (best-effort parse).
        API key validity is NOT probed here (lazy, cached by caller).
        """
        cmd = self._settings.gemini_command
        if not cmd:
            return HealthCheckResult(installed=False, version="")
        installed = shutil.which(cmd) is not None
        version = ""
        if installed:
            try:
                proc = await asyncio.create_subprocess_exec(
                    cmd,
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                version = stdout.decode("utf-8", errors="replace").strip()
            except (OSError, TimeoutError):
                pass
        return HealthCheckResult(installed=installed, version=version)

    def _build_args(self, prompt: str) -> list[str]:
        """Build CLI arguments for ``gemini`` subprocess."""
        args = [
            "run",
            "--json",
            prompt,
        ]
        return args

    async def _spawn(
        self,
        prompt: str,
        worktree_path: Path,
    ) -> asyncio.subprocess.Process:
        """Spawn the ``gemini`` subprocess with correct args and env."""
        args = self._build_args(prompt)
        # P6-I5: explicit child-env allowlist (NOT a full parent-env copy).
        env = _build_child_env()
        # Inject GEMINI_API_KEY from settings, NOT from parent env.
        if self._settings.google_api_key:
            env["GEMINI_API_KEY"] = self._settings.google_api_key
        # Trace ID propagation (P5-I2 / NFR-O7).
        env["OMB_TRACE_ID"] = self._settings.resolve_trace_id()
        log = structlog.get_logger(__name__)
        preview = prompt[:_LOG_PROMPT_PREVIEW_LEN]
        if len(prompt) > _LOG_PROMPT_PREVIEW_LEN:
            preview += "..."
        log.info(
            "gemini_spawning",
            command=self._settings.gemini_command,
            prompt_preview=preview,
            cwd=str(worktree_path),
        )
        return await asyncio.create_subprocess_exec(
            self._settings.gemini_command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(worktree_path),
            env=env,
        )

    async def _read_stream(self, process: asyncio.subprocess.Process) -> None:
        """Read JSONL from stdout, dispatch each line to ``_handle_message``."""
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
                log.warning("gemini_malformed_json", line=line[:200])
                continue
            self._handle_message(msg)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch on message type from Gemini JSONL output.

        Gemini ``--json`` produces messages with types like:
        - ``session.created`` — session metadata
        - ``turn.completed`` — per-turn completion with tool calls + usage
        - ``message`` — text output
        """
        msg_type = msg.get("type")
        if msg_type == "session.created":
            self._session_id = msg.get("session_id", "")
        elif msg_type == "turn.completed":
            self._num_turns += 1
            self._extract_usage(msg)
            self._extract_events(msg)

    def _extract_usage(self, msg: dict[str, Any]) -> None:
        """Extract token usage from a ``turn.completed`` message.

        Gemini uses ``usageMetadata`` for token counts (matching the Gemini
        API convention). Also supports ``usage`` dict for parity with Codex.
        """
        # Prefer usageMetadata (Gemini convention)
        usage_meta = msg.get("usageMetadata", {})
        if isinstance(usage_meta, dict):
            self._input_tokens += int(usage_meta.get("promptTokenCount", 0) or 0)
            self._output_tokens += int(usage_meta.get("candidatesTokenCount", 0) or 0)
            return
        # Fallback to usage dict (Codex convention)
        usage = msg.get("usage", {})
        if isinstance(usage, dict):
            self._input_tokens += int(usage.get("input_tokens", 0) or 0)
            self._output_tokens += int(usage.get("output_tokens", 0) or 0)

    def _extract_events(self, msg: dict[str, Any]) -> None:
        """Extract typed events from tool calls in a ``turn.completed`` message."""
        tool_calls = msg.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            return
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            tool_name = call.get("name", "")
            tool_input = call.get("input", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            event = self._classify_tool_use(tool_name, tool_input)
            if event is not None:
                self._events.append(event)

    @staticmethod
    def _classify_tool_use(
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ExtractedEvent | None:
        """Map a Gemini tool call to a typed event.

        Best-effort mapping (PRD D5): Gemini tool names are mapped to
        ExtractedEvent types where a clear mapping exists. Unmapped tools
        return None (captured as ``runtime.tool_executed`` in the task driver
        if needed, but not here — this keeps the adapter focused).
        """
        name_lower = tool_name.lower()
        if name_lower in ("write", "edit", "create_file", "apply_edit"):
            return ExtractedEvent(
                event_type="file.edited",
                tool_name=tool_name,
                tool_input=tool_input,
            )
        if name_lower in ("bash", "shell", "run_command"):
            command = tool_input.get("command", "")
            if isinstance(command, str):
                if "git push" in command:
                    return ExtractedEvent(
                        event_type="git.push",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
                if "git commit" in command or "git add" in command:
                    return ExtractedEvent(
                        event_type="commit.created",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
                # Test execution detection
                test_keywords = (
                    "pytest",
                    "npm test",
                    "cargo test",
                    "go test",
                    "just test",
                    "make test",
                    "jest",
                    "mocha",
                )
                if any(kw in command for kw in test_keywords):
                    return ExtractedEvent(
                        event_type="test.run",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
        return None

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> str:
        """Read all of stderr into a string.  Safe to call concurrently."""
        if process.stderr is None:
            return ""
        data = await process.stderr.read()
        return data.decode("utf-8", errors="replace").strip()

    async def _shutdown_process(self, process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait -> kill."""
        log = structlog.get_logger(__name__)
        if process.returncode is not None:
            return
        log.info("gemini_terminating", pid=process.pid)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_GRACE_PERIOD_S)
        except TimeoutError:
            log.warning("gemini_kill_after_timeout", pid=process.pid)
            process.kill()
            await process.wait()

    def _build_result(self, exit_code: int, stderr: str) -> GeminiResult:
        """Build a ``GeminiResult`` from accumulated state."""
        result = GeminiResult(
            exit_code=exit_code,
            session_id=self._session_id,
            events=list(self._events),
            stderr=stderr,
            num_turns=self._num_turns,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
        # Map exit codes to error messages.
        if exit_code == 1:
            result.error = stderr[:500] if stderr else "Task error"
        elif exit_code == 2:
            result.error = "Invalid arguments or configuration"
        elif exit_code == -1:
            result.error = "Timed out"
        elif exit_code not in (0, 130, 137):
            result.error = f"Unexpected exit code {exit_code}"
            if stderr:
                result.error += f": {stderr[:500]}"
        return result

    async def run(self, prompt: str, worktree_path: Path) -> GeminiResult:
        """Run ``gemini`` with the given prompt and return a structured result."""
        self._events = []
        self._session_id = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._num_turns = 0

        # Catch spawn failures (missing binary, bad worktree path).
        try:
            process = await self._spawn(prompt, worktree_path)
        except OSError as exc:
            return GeminiResult(
                exit_code=-1,
                error=f"Failed to spawn gemini: {exc}",
            )

        self._process = process
        try:
            return await self._run_with_process(process)
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit — clean up.
            await self._shutdown_process(process)
            self._process = None
            raise

    async def _run_with_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> GeminiResult:
        """Stream stdout + drain stderr concurrently, then build result."""
        log = structlog.get_logger(__name__)
        log.info("gemini_started", pid=process.pid)

        stderr_task = asyncio.create_task(self._drain_stderr(process))
        try:
            await asyncio.wait_for(
                self._read_stream(process),
                timeout=self._settings.gemini_timeout_s,
            )
        except TimeoutError:
            log.error(
                "gemini_timeout",
                timeout=self._settings.gemini_timeout_s,
            )
            stderr_task.cancel()
            with contextlib_suppress():
                await stderr_task
            await self._shutdown_process(process)
            stderr = ""
            with contextlib_suppress():
                stderr = await self._drain_stderr(process)
            return GeminiResult(
                exit_code=-1,
                session_id=self._session_id,
                error=f"Timed out after {self._settings.gemini_timeout_s}s",
                events=list(self._events),
                stderr=stderr,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )

        # Normal completion — collect stderr.
        try:
            stderr = await stderr_task
        except Exception:
            stderr = ""

        await process.wait()
        exit_code = process.returncode if process.returncode is not None else -1

        result = self._build_result(exit_code, stderr)

        log.info(
            "gemini_completed",
            session_id=result.session_id,
            exit_code=result.exit_code,
            events=len(result.events),
            error=result.error,
        )
        return result

    async def cancel(self) -> None:
        """Cancel a running subprocess (forward SIGTERM)."""
        if self._process is not None:
            await self._shutdown_process(self._process)
            self._process = None

    async def terminate_with_grace(
        self,
        *,
        grace_period_s: float = 5.0,
    ) -> TerminationResult:
        """Terminate with SIGTERM → wait → SIGKILL escalation (P5-I3).

        Mirrors ``ClaudeCodeRunner.terminate_with_grace`` semantics exactly.
        """
        from worker_wrapper.adapters.claude_code_runner import (
            TerminationResult,
        )

        log = structlog.get_logger(__name__)
        start = time.monotonic()
        process = self._process
        if process is None or process.returncode is not None:
            elapsed = time.monotonic() - start
            return TerminationResult(
                method="noop",
                elapsed_s=elapsed,
                exit_code=process.returncode if process is not None else None,
            )

        log.info(
            "gemini_terminate_with_grace",
            pid=process.pid,
            grace_period_s=grace_period_s,
        )
        try:
            process.terminate()
        except ProcessLookupError:
            log.info(
                "gemini_terminate_already_exited",
                pid=process.pid,
                exit_code=process.returncode,
            )
            return TerminationResult(
                method="noop",
                elapsed_s=time.monotonic() - start,
                exit_code=process.returncode,
            )
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_period_s)
            elapsed = time.monotonic() - start
            log.info(
                "gemini_sigterm_succeeded",
                pid=process.pid,
                elapsed_s=elapsed,
                exit_code=process.returncode,
            )
            return TerminationResult(
                method="sigterm",
                elapsed_s=elapsed,
                exit_code=process.returncode,
            )
        except TimeoutError:
            log.warning(
                "gemini_sigkill_escalation",
                pid=process.pid,
                grace_period_s=grace_period_s,
            )
            try:
                process.kill()
            except ProcessLookupError:
                log.info(
                    "gemini_sigkill_target_already_exited",
                    pid=process.pid,
                )
                await process.wait()
                return TerminationResult(
                    method="sigkill",
                    elapsed_s=time.monotonic() - start,
                    exit_code=process.returncode,
                    escalation_landed=False,
                )
            await process.wait()
            elapsed = time.monotonic() - start
            return TerminationResult(
                method="sigkill",
                elapsed_s=elapsed,
                exit_code=process.returncode,
                escalation_landed=True,
            )


def contextlib_suppress() -> Any:
    """Return ``contextlib.suppress(Exception)`` — avoids module-level import."""
    import contextlib

    return contextlib.suppress(Exception)


__all__ = [
    "ExtractedEvent",
    "GeminiResult",
    "GeminiRunner",
]
