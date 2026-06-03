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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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

# G-SEC-2 (D1) — explicit child-env allowlist for the spawned ``claude``
# subprocess. This MUST stay an explicit allowlist: NEVER forward the whole
# parent environment here — copying every parent var leaked operator /
# platform secrets (OPERATOR_HMAC_KEY, LITESTREAM_*, TELEGRAM_*, AWS_*,
# OPENAI_API_KEY, registry DB creds) into the agent subprocess (reverted
# twice; see ``mcp_clients._ENV_ALLOWLIST`` for the sibling discipline).
#
# Contents are FUNCTIONAL-only:
#   - PATH / HOME / USER — process basics the ``claude`` binary needs.
#   - LANG / LC_ALL / LC_CTYPE — locale (avoids Unicode/encoding surprises).
#   - TMPDIR / TMP / TEMP — temp-dir resolution for the agent's file ops.
#   - SSL_CERT_FILE / SSL_CERT_DIR / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE —
#     TLS / CA bundles for custom-CA deployments.
#   - GITHUB_TOKEN — retained because the ``claude`` subprocess performs
#     ``git push`` (see ``worker_wrapper/main.py:476``).
#     TODO(G-SEC-2 follow-up): migrate to a scoped git credential helper so
#     the raw PAT need not enter the agent env at all.
#
# ANTHROPIC_API_KEY is INTENTIONALLY NOT in this allowlist — it is re-injected
# from settings in ``_spawn`` (see the ``anthropic_api_key`` overlay), not
# passed through from the parent env.
_CHILD_ENV_ALLOWLIST: frozenset[str] = frozenset(
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
        # git push (main.py:476) — TODO(G-SEC-2 follow-up): scoped cred helper.
        "GITHUB_TOKEN",
    }
)

# G-SEC-2 (D1) — prefix allowlist: task/trace vars (``OMB_*``) + Claude CLI
# config (``CLAUDE_*``). NEVER name a secret with these prefixes — anything
# matching is forwarded to the agent subprocess verbatim.
_CHILD_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "CLAUDE_")


def _build_child_env() -> dict[str, str]:
    """Return the explicit child-env for the spawned ``claude`` subprocess.

    G-SEC-2 (D1): only parent-env vars matching ``_CHILD_ENV_ALLOWLIST`` or
    starting with one of ``_CHILD_ENV_PREFIXES`` are forwarded; everything
    else (operator / platform secrets) is dropped. The ANTHROPIC_API_KEY and
    OMB_TRACE_ID overlays are applied by the caller (``_spawn``).
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k in _CHILD_ENV_ALLOWLIST or k.startswith(_CHILD_ENV_PREFIXES)
    }


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


@dataclass(frozen=True)
class TerminationResult:
    """Outcome of a graceful subprocess termination (Story 12.1 AC2).

    Returned by :meth:`ClaudeCodeRunner.terminate_with_grace`. Fields:

    - ``method``: which signal-path the termination took.

      * ``"noop"``  — no live subprocess (e.g. already exited / never spawned).
      * ``"sigterm"`` — subprocess exited within ``grace_period_s`` after SIGTERM.
      * ``"sigkill"`` — grace window elapsed; the runner reached the SIGKILL
        escalation branch. May reflect either an actual SIGKILL delivery
        (``escalation_landed=True``) OR a target that died in the race
        window between grace-timeout and our ``process.kill()`` call
        (``escalation_landed=False``). NFR-R8 dashboards classifying
        "escalations" should filter on ``method=="sigkill" AND
        escalation_landed`` to avoid double-counting the race-window case.

    - ``elapsed_s``: wall-clock duration of ``terminate_with_grace`` (via
      :func:`time.monotonic`). For ``"noop"``, ~0.0.
    - ``exit_code``: subprocess returncode (``None`` only on ``"noop"`` when
      no process ever ran).
    - ``escalation_landed`` (PP34): ``True`` iff a SIGKILL was actually
      delivered. ``False`` for ``"noop"`` / ``"sigterm"`` paths AND for the
      ``method=="sigkill"`` race-window case (target died via SIGTERM after
      the grace timeout fired but before our kill() landed — the runner
      observed ProcessLookupError on kill()). Defaulting to ``False`` keeps
      the field optional for existing callers.

    PP18 — public dataclass; the leading-underscore name (``_TerminationResult``)
    was a Story 12.1 pass-1 review finding (underscore-public-export
    contradiction). Renamed; ``_TerminationResult`` retained as a
    backwards-compat alias for the single in-tree importer (the integration
    test imports it via ``from worker_wrapper.adapters.claude_code_runner
    import _TerminationResult``).
    """

    method: Literal["noop", "sigterm", "sigkill"]
    elapsed_s: float
    exit_code: int | None
    escalation_landed: bool = False


# PP18 — backwards-compat alias; existing callers (integration test) keep
# importing the underscore-prefixed name. PP39 — alias retained at module
# scope but DROPPED from ``__all__`` to discourage new use. New callers
# MUST import :class:`TerminationResult` directly. The alias is deprecated
# and may be removed in a future story.
_TerminationResult = TerminationResult


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

        Story 9.6 / FR59: propagates trace_id to Claude Code via two surfaces:

        1. ``--trace-id <value>`` CLI flag — appended only when the
           ``emit_trace_id_flag`` setting is enabled (review pass-1 H2
           default OFF). The default-off gate prevents subprocess spawn
           failures on Claude Code builds that reject unknown flags. Flip the
           gate ON once upstream Claude Code consumes the flag.
        2. ``OMB_TRACE_ID`` env var — always set by ``_spawn``; safe today
           because unknown env vars are silently dropped by the child.

        Either surface is sufficient for downstream consumption.
        """
        args = [
            "-p",
            prompt,
            "--output-format",
            self._settings.claude_output_format,
        ]
        if self._settings.claude_max_turns > 0:
            args.extend(["--max-turns", str(self._settings.claude_max_turns)])
        # Story 9.6 / FR59 review pass-1 H2 — flag gated; env var path
        # (see ``_spawn``) is the non-breaking default surface.
        if self._settings.emit_trace_id_flag:
            args.extend(["--trace-id", self._settings.resolve_trace_id()])
        return args

    async def _spawn(
        self,
        prompt: str,
        worktree_path: Path,
    ) -> asyncio.subprocess.Process:
        """Spawn the ``claude`` subprocess with correct args and env."""
        args = self._build_args(prompt)
        # G-SEC-2 (D1): explicit child-env allowlist (NOT a full parent-env copy).
        env = _build_child_env()
        if self._settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self._settings.anthropic_api_key
        # Story 9.6 / FR59 review pass-1 L2 — OMB_TRACE_ID env var is the
        # always-on companion to the ``emit_trace_id_flag``-gated CLI
        # flag. Claude Code is expected to consume this; if it does not, the
        # env var is unused by the child (no error).
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

    async def terminate_with_grace(
        self,
        *,
        grace_period_s: float = 5.0,
    ) -> TerminationResult:
        """Terminate the subprocess with SIGTERM → wait → SIGKILL escalation.

        Story 12.1 AC2 — public termination callback used by
        :func:`worker_wrapper.domain.budget_supervisor.watch_for_budget_exceeded`
        when a ``task.budget_exceeded`` event arrives during task execution.

        Semantics:

        1. If no live subprocess is attached (``self._process is None`` or it
           has already exited), return immediately with ``method="noop"``.
        2. Send SIGTERM via :meth:`asyncio.subprocess.Process.terminate`.
        3. Wait up to ``grace_period_s`` for the subprocess to exit.
        4. On grace timeout: escalate to SIGKILL via
           :meth:`asyncio.subprocess.Process.kill`, then wait uncapped (the
           kernel guarantees SIGKILL delivery is O(1)).

        Wall-clock duration is measured via :func:`time.monotonic` (no clock
        injection on this adapter today; the budget supervisor measures its
        own latencies via an injected ``Clock`` for testability).

        Args:
            grace_period_s: Seconds to wait for SIGTERM-driven exit before
                escalating to SIGKILL. Default 5.0 per NFR-R8.

        Returns:
            :class:`TerminationResult` describing the method used, elapsed
            wall-clock seconds, and the subprocess exit code (if any).
        """
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
            "claude_code_terminate_with_grace",
            pid=process.pid,
            grace_period_s=grace_period_s,
        )
        # PP5 — TOCTOU race: subprocess may die between the ``returncode``
        # check above and the ``terminate()`` call below. Absence of the
        # process IS the desired post-condition; swallow and short-circuit.
        try:
            process.terminate()
        except ProcessLookupError:
            log.info(
                "claude_code_terminate_already_exited",
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
                "claude_code_sigterm_succeeded",
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
                "claude_code_sigkill_escalation",
                pid=process.pid,
                grace_period_s=grace_period_s,
            )
            # PP5 — second TOCTOU window: subprocess may die during the grace
            # period (cooperative SIGTERM finally landed, race lost to our
            # wait_for timeout). The reap below covers both branches.
            # PP34 — when this happens, classify as ``method="sigkill"``
            # (we DID hit the grace timeout — the escalation branch was
            # entered) but flag ``escalation_landed=False`` so dashboards
            # can distinguish actual deliveries from race-window cases.
            # The prior code mis-classified the race-window case as
            # ``"sigterm"``, undercounting escalation counters.
            try:
                process.kill()
            except ProcessLookupError:
                log.info(
                    "claude_code_sigkill_target_already_exited",
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
    "ClaudeCodeResult",
    "ClaudeCodeRunner",
    "ExtractedEvent",
    "ReasoningBreadcrumb",
    # PP18 — public name. PP39 — the underscore alias ``_TerminationResult``
    # is kept at module scope above for backwards-compat with the integration
    # test but is INTENTIONALLY OMITTED from ``__all__`` — exporting a
    # leading-underscore name undermines the rename. Deprecated; new callers
    # must use ``TerminationResult``.
    "TerminationResult",
]
