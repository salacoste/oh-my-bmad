"""Shared subprocess lifecycle methods for runtime adapters.

Extracts the common subprocess-management, env-building, shutdown,
and result-construction patterns from Claude/Codex/Gemini runners.

Subclasses must provide their own ``_build_spawn_args``, ``_classify_tool_use``,
and other runner-specific methods; this base class supplies only the lifecycle
machinery that is identical across adapters.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import TYPE_CHECKING, Any

import structlog

from worker_wrapper.domain.runtime_adapter import HealthCheckResult

if TYPE_CHECKING:
    from worker_wrapper.adapters.claude_code_runner import TerminationResult

# Maximum prompt length included in spawn log (prevents sensitive data leaks).
_LOG_PROMPT_PREVIEW_LEN: int = 80


def contextlib_suppress() -> Any:
    """Return ``contextlib.suppress(Exception)`` — avoids module-level import."""
    import contextlib

    return contextlib.suppress(Exception)


class BaseRunner:
    """Shared lifecycle methods for runtime adapters.

    Provides concrete implementations of subprocess shutdown, stderr draining,
    health checking, and graceful termination. Subclasses inherit these and
    supply only the runner-specific logic (argument building, output parsing,
    event classification, result construction).
    """

    # -- subclass must override --------------------------------------------------

    @property
    def runtime_name(self) -> str:
        """Runtime identifier — subclasses must override."""
        raise NotImplementedError

    # -- shared health check -----------------------------------------------------

    def _health_check_command(self) -> str:
        """Return the binary command to probe. Subclasses must override."""
        raise NotImplementedError

    async def health_check(self) -> HealthCheckResult:
        """Probe CLI binary availability (FR95).

        Checks:
        1. Binary installed via ``shutil.which``.
        2. Version via ``<binary> --version`` (best-effort parse).
        API key validity is NOT probed here (lazy, cached by caller).
        """
        cmd = self._health_check_command()
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

    # -- shared stderr drain -----------------------------------------------------

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> str:
        """Read all of stderr into a string.  Safe to call concurrently."""
        if process.stderr is None:
            return ""
        data = await process.stderr.read()
        return data.decode("utf-8", errors="replace").strip()

    # -- shared shutdown ---------------------------------------------------------

    async def _shutdown_process(self, process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait -> kill."""
        log = structlog.get_logger(__name__)
        if process.returncode is not None:
            return
        log.info(f"{self.runtime_name}_terminating", pid=process.pid)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            log.warning(f"{self.runtime_name}_kill_after_timeout", pid=process.pid)
            process.kill()
            await process.wait()

    # -- shared cancel -----------------------------------------------------------

    async def cancel(self) -> None:
        """Cancel a running subprocess (forward SIGTERM)."""
        if self._process is not None:
            await self._shutdown_process(self._process)
            self._process = None

    # -- shared terminate_with_grace ---------------------------------------------

    async def terminate_with_grace(
        self,
        *,
        grace_period_s: float = 5.0,
    ) -> TerminationResult:
        """Terminate with SIGTERM -> wait -> SIGKILL escalation.

        Implements the P5-I3 contract for the budget supervisor.
        """
        from worker_wrapper.adapters.claude_code_runner import TerminationResult

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
            f"{self.runtime_name}_terminate_with_grace",
            pid=process.pid,
            grace_period_s=grace_period_s,
        )
        try:
            process.terminate()
        except ProcessLookupError:
            log.info(
                f"{self.runtime_name}_terminate_already_exited",
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
                f"{self.runtime_name}_sigterm_succeeded",
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
                f"{self.runtime_name}_sigkill_escalation",
                pid=process.pid,
                grace_period_s=grace_period_s,
            )
            try:
                process.kill()
            except ProcessLookupError:
                log.info(
                    f"{self.runtime_name}_sigkill_target_already_exited",
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
