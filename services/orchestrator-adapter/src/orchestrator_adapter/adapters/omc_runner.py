"""OMC subprocess supervisor — spawns ``node bridge/cli.cjs`` from ``upstream/omc/`` (Story 5.10).

Manages the OMC process lifecycle: spawn, stdin prompt, stdout capture,
graceful shutdown (SIGTERM → SIGKILL), and timeout enforcement.

Story 9.6 review pass-2 PH0 + pass-3 TH2: ``OMCRunner`` accepts the
trace_id PER-CALL (``run(prompt, *, trace_id=...)``) rather than
per-instance, so multiple tasks polled by a single ``adapter_loop`` each
carry their own correctly-scoped token.  When ``trace_id`` is non-None,
``_spawn`` exports ``OMB_TRACE_ID`` to the child env.  The OMC child
(and any worker-wrapper it spawns transitively) picks up the trace_id
through the ``WORKER_TRACE_ID`` / ``OMB_TRACE_ID`` ``AliasChoices``
declared on :class:`worker_wrapper.app.config.WorkerSettings`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

_GRACE_PERIOD_S: float = 5.0
_LOG_PROMPT_PREVIEW_LEN: int = 80

# G-SEC-2 (D4) — explicit child-env allowlist for the spawned OMC (Node)
# subprocess. This MUST stay an explicit allowlist: NEVER forward the whole
# parent environment here — copying every parent var leaked operator /
# platform secrets (OPERATOR_HMAC_KEY, LITESTREAM_*, TELEGRAM_*, AWS_*,
# OPENAI_API_KEY, registry DB creds) into the agent subprocess (reverted
# twice; see ``mcp_clients._ENV_ALLOWLIST`` for the sibling discipline).
#
# Contents are FUNCTIONAL-only:
#   - PATH / HOME / USER — process basics the ``node`` binary needs.
#   - LANG / LC_ALL / LC_CTYPE — locale.
#   - TMPDIR / TMP / TEMP — temp-dir resolution.
#   - SSL_CERT_FILE / SSL_CERT_DIR / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE —
#     TLS / CA bundles for custom-CA deployments.
#   - NODE_PATH — OMC is a Node app and needs Node module resolution.
#     NODE_OPTIONS is deliberately NOT forwarded: it accepts ``--require`` /
#     ``--inspect`` which would let a compromised parent env inject arbitrary
#     JS into the OMC child (security review G-SEC-2 MEDIUM). If a specific Node
#     flag is ever needed, set it explicitly in the launch argv, not via env.
#   - ANTHROPIC_API_KEY — passes THROUGH from the parent env here: unlike the
#     worker, ``OrchestratorSettings`` has NO ``anthropic_api_key`` field to
#     re-inject, so the allowlist is the only path for the agent's key.
#   - GITHUB_TOKEN — retained because the spawned agent performs ``git push``.
#     TODO(G-SEC-2 follow-up): migrate to a scoped git credential helper so
#     the raw PAT need not enter the agent env at all.
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
        # Node runtime (OMC is a Node app). NODE_OPTIONS intentionally omitted
        # (--require/--inspect injection vector — see comment above).
        "NODE_PATH",
        # ANTHROPIC passes through (no settings field to re-inject).
        "ANTHROPIC_API_KEY",
        # git push — TODO(G-SEC-2 follow-up): scoped cred helper.
        "GITHUB_TOKEN",
    }
)

# G-SEC-2 (D4) — prefix allowlist: task/trace vars (``OMB_*``) + Claude CLI
# config (``CLAUDE_*``). NEVER name a secret with these prefixes — anything
# matching is forwarded to the agent subprocess verbatim.
_CHILD_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "CLAUDE_")


def _build_child_env() -> dict[str, str]:
    """Return the explicit child-env for the spawned OMC (Node) subprocess.

    G-SEC-2 (D4): only parent-env vars matching ``_CHILD_ENV_ALLOWLIST`` or
    starting with one of ``_CHILD_ENV_PREFIXES`` are forwarded; everything
    else (operator / platform secrets) is dropped. The OMB_TRACE_ID overlay
    is applied by the caller (``_spawn``).
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k in _CHILD_ENV_ALLOWLIST or k.startswith(_CHILD_ENV_PREFIXES)
    }


@dataclass
class OMCResult:
    """Structured result from an OMC subprocess run."""

    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str | None = None


class OMCRunner:
    """Supervises an OMC CLI subprocess.

    Usage::

        runner = OMCRunner(omc_path=Path("upstream/omc"), timeout_s=120)
        result = await runner.run("Build the auth module")
        print(result.stdout)
    """

    def __init__(
        self,
        omc_path: Path,
        timeout_s: float = 120.0,
    ) -> None:
        # Story 9.6 review pass-3 TH2: ``trace_id`` lifted from ``__init__``
        # to a per-call kwarg on ``run()``.  Per-process trace_id semantics
        # were incorrect — distinct tasks polled by the same ``adapter_loop``
        # require their own trace_id passed PER ``runner.run(...)``.
        if not omc_path.is_dir():
            raise ValueError(f"omc_path does not exist: {omc_path}")
        cli = omc_path / "bridge" / "cli.cjs"
        if not cli.is_file():
            raise ValueError(f"OMC CLI not found at: {cli}")
        self._omc_path = omc_path
        self._cli = cli
        self._timeout_s = timeout_s
        self._process: asyncio.subprocess.Process | None = None

    async def _spawn(
        self, prompt: str, *, trace_id: str | None = None
    ) -> asyncio.subprocess.Process:
        """Spawn ``node bridge/cli.cjs launch`` with the prompt on stdin.

        Story 9.6 review pass-2 PH0 + pass-3 TH2: when ``trace_id`` is
        provided, export ``OMB_TRACE_ID=<trace_id>`` to the child env so
        downstream spawns (worker-wrapper et al.) resolve it via
        ``AliasChoices``.  The trace_id is now passed per-call (not stored
        on the runner) so each task carries its own correctly-scoped token.
        """
        log = structlog.get_logger(__name__)
        preview = prompt[:_LOG_PROMPT_PREVIEW_LEN]
        if len(prompt) > _LOG_PROMPT_PREVIEW_LEN:
            preview += "..."
        log.info("omc_spawning", prompt_preview=preview, cwd=str(self._omc_path))
        # Build env: G-SEC-2 (D4) explicit child-env allowlist (PATH /
        # NODE_PATH / ANTHROPIC_API_KEY etc.) — NOT a full parent-env copy —
        # then layer the trace_id when present.  Story 9.6 review pass-2 PH0 /
        # pass-3 TH2.
        env = _build_child_env()
        if trace_id is not None:
            env["OMB_TRACE_ID"] = trace_id
        return await asyncio.create_subprocess_exec(
            "node",
            str(self._cli),
            "launch",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._omc_path),
            env=env,
        )

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> str:
        """Read all of stderr into a string."""
        if process.stderr is None:
            return ""
        data = await process.stderr.read()
        return data.decode("utf-8", errors="replace").strip()

    async def _shutdown_process(self, process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait -> kill."""
        log = structlog.get_logger(__name__)
        if process.returncode is not None:
            return
        log.info("omc_terminating", pid=process.pid)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_GRACE_PERIOD_S)
        except TimeoutError:
            log.warning("omc_kill_after_timeout", pid=process.pid)
            process.kill()
            await process.wait()

    async def run(self, prompt: str, *, trace_id: str | None = None) -> OMCResult:
        """Run OMC with the given prompt and return a structured result.

        Story 9.6 review pass-3 TH2: ``trace_id`` is a per-call kwarg so
        each task spawned by ``adapter_loop`` carries its own scoped token.
        """
        log = structlog.get_logger(__name__)
        t0 = time.monotonic()

        try:
            process = await self._spawn(prompt, trace_id=trace_id)
        except OSError as exc:
            return OMCResult(exit_code=-1, error=f"Failed to spawn OMC: {exc}")

        self._process = process
        log.info("omc_started", pid=process.pid)

        try:
            # Write prompt to stdin, close it.
            if process.stdin is not None:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.write(b"\n")
                await process.stdin.drain()
                process.stdin.close()

            # Drain stderr concurrently to prevent deadlock.
            stderr_task = asyncio.create_task(self._drain_stderr(process))

            # Read stdout with timeout.
            try:
                if process.stdout is None:
                    stdout_bytes = b""
                else:
                    stdout_bytes = await asyncio.wait_for(
                        process.stdout.read(),
                        timeout=self._timeout_s,
                    )
                stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            except TimeoutError:
                log.error("omc_timeout", timeout=self._timeout_s)
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await stderr_task
                await self._shutdown_process(process)
                stderr = ""
                with contextlib.suppress(Exception):
                    stderr = await self._drain_stderr(process)
                duration_ms = int((time.monotonic() - t0) * 1000)
                return OMCResult(
                    exit_code=-1,
                    stderr=stderr,
                    duration_ms=duration_ms,
                    error=f"Timed out after {self._timeout_s}s",
                )

            # Collect stderr.
            try:
                stderr = await stderr_task
            except Exception:
                stderr = ""

            await process.wait()
            exit_code = process.returncode if process.returncode is not None else -1
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = OMCResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
            )

            if exit_code != 0 and not result.error:
                result.error = f"Process exited with code {exit_code}"
                if stderr:
                    result.error += f": {stderr[:500]}"

            log.info(
                "omc_completed",
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                error=result.error,
            )
            self._process = None
            return result

        except BaseException:
            await self._shutdown_process(process)
            self._process = None
            raise

    async def cancel(self) -> None:
        """Cancel a running subprocess."""
        if self._process is not None:
            await self._shutdown_process(self._process)
            self._process = None
