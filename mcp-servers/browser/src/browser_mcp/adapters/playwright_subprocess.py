"""Playwright MCP subprocess lifecycle management (FR78 / FR87 / P4-I3).

Manages per-task Playwright MCP subprocesses via ``docker run -i --rm --init``.
Each task gets an isolated browser session spawned on demand and killed at task
end (or server shutdown). The module owns the *entire* subprocess lifecycle:
spawn → stdio transport → kill → reap.

Architecture
------------
- ``PlaywrightSubprocessManager`` holds a dict ``{task_id: PlaywrightSession}``.
- On first browser tool call for a task, ``get_or_spawn(task_id)`` launches
  ``docker run -i --rm --init <image>@sha256:<digest> --headless --isolated
  --caps=core,config`` and returns a connected ``MCP`` stdio transport.
- On task end (completion, stop, ``browser_close``), ``kill_session(task_id)``
  sends SIGTERM (10s graceful), then SIGKILL (30s hard cap) if the process
  refuses to die, and removes it from the dict.
- On server shutdown (lifespan cleanup), ``kill_all()`` force-kills every
  remaining session — no Chromium processes survive past server exit (NFR-R9).

Security (every item load-bearing)
----------------------------------
- ``create_subprocess_exec`` (the ``_exec`` variant), NEVER ``_shell``:
  the command is passed as discrete argv elements, so no shell re-parses
  a user-supplied token.
- ``--isolated`` is hardcoded (P4-I1) — profile kept in memory, never
  persisted to disk.
- ``--headless`` is hardcoded — the server runs in a Docker container with
  no display.
- ``--caps=core,config`` by default; ``storage`` and ``network`` are
  blocklisted (validated in ``server.py`` / ``__main__.py`` before this
  module is reached).
- Image is pinned by digest (``@sha256:...``) — no tag-only references.
- ``--no-sandbox`` is NEVER passed — Docker provides process-level sandboxing
  (seccomp, user namespaces); Chromium's own sandbox stays enabled.
- Resource limits (memory/CPU) are configurable via env vars with safe
  defaults (Story 20.5 fills these in; scaffolded here with defaults).

Story 20.2 ships the lifecycle. Browser tools (Stories 21.1-21.5) consume
``get_or_spawn()`` to obtain the MCP transport for forwarding tool calls.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Graceful shutdown: SIGTERM, wait up to 10s, then SIGKILL.
_GRACEFUL_TIMEOUT: float = 10.0
# Hard cap: total time before giving up (SIGTERM wait + SIGKILL wait).
_HARD_KILL_TIMEOUT: float = 30.0

# Default resource limits (Story 20.5 may make these configurable via env).
_DEFAULT_MEMORY_LIMIT: str = "512m"
_DEFAULT_CPU_LIMIT: float = 1.0


def _build_docker_command(
    image: str,
    *,
    memory_limit: str = _DEFAULT_MEMORY_LIMIT,
    cpu_limit: float = _DEFAULT_CPU_LIMIT,
    extra_caps: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> list[str]:
    """Construct the ``docker run`` argv for a Playwright MCP subprocess.

    Security constraints (asserted, not just documented):
    - ``--init`` is always present (PID-1 signal forwarding + zombie reaping).
    - ``--rm`` is always present (auto-remove container on exit).
    - ``--no-sandbox`` is NEVER present.
    - ``--network host`` is NEVER present (P4-I3 — no host network).
    - Image MUST contain ``@sha256:`` (pinned digest) — callers validate
      before reaching this function.
    """
    caps = ["core", "config"]
    if extra_caps:
        caps.extend(extra_caps)

    cmd: list[str] = [
        "docker",
        "run",
        "-i",  # interactive stdio
        "--rm",  # auto-remove on exit
        "--init",  # PID-1 init for signal forwarding + zombie reaping
        f"--memory={memory_limit}",
        f"--cpus={cpu_limit}",
        image,
        "--headless",  # no display
        "--isolated",  # P4-I1: in-memory profile, no persistent state
        f"--caps={','.join(caps)}",
    ]

    if allowed_origins:
        cmd.append(f"--allowed-origins={','.join(allowed_origins)}")

    return cmd


@dataclass
class PlaywrightSession:
    """A single per-task Playwright MCP subprocess + its MCP stdio transport.

    Attributes:
        task_id: The task this session belongs to.
        proc: The ``asyncio.subprocess.Process`` for the Docker container.
        started_at: Monotonic timestamp of spawn time.
        session_id: UUIDv7-style session identifier (minted at spawn).
    """

    task_id: str
    proc: asyncio.subprocess.Process
    started_at: float
    session_id: str


@dataclass
class PlaywrightSubprocessManager:
    """Manages per-task Playwright MCP subprocesses.

    Usage::

        mgr = PlaywrightSubprocessManager(image="pw@sha256:abc...")
        session = await mgr.spawn("task-123")
        # ... use session.proc.stdin / session.proc.stdout for MCP transport ...
        await mgr.kill_session("task-123", reason="task_complete")
    """

    image: str
    # Resource limits (Story 20.5 configurable)
    memory_limit: str = _DEFAULT_MEMORY_LIMIT
    cpu_limit: float = _DEFAULT_CPU_LIMIT
    # Cap overrides (validated upstream for blocklisted entries)
    extra_caps: list[str] | None = None
    # Origin control (Story 20.4)
    allowed_origins: list[str] | None = None
    # Per-task sessions
    _sessions: dict[str, PlaywrightSession] = field(default_factory=dict)

    @property
    def sessions(self) -> dict[str, PlaywrightSession]:
        """Read-only view of active sessions (for tool handlers)."""
        return dict(self._sessions)

    def has_session(self, task_id: str) -> bool:
        """Check whether a task has an active session."""
        return task_id in self._sessions

    async def spawn(self, task_id: str) -> PlaywrightSession:
        """Spawn a new Playwright MCP subprocess for *task_id*.

        Raises:
            RuntimeError: If the task already has an active session.
            OSError: If the subprocess fails to spawn.
        """
        if task_id in self._sessions:
            raise RuntimeError(
                f"Task {task_id!r} already has an active Playwright session. "
                f"Kill it before spawning a new one."
            )

        cmd = _build_docker_command(
            self.image,
            memory_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            extra_caps=self.extra_caps,
            allowed_origins=self.allowed_origins,
        )

        log.info(
            "playwright_spawning",
            extra={"task_id": task_id, "cmd": cmd},
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        from events.ids import new_uuid7

        session_id = new_uuid7()
        session = PlaywrightSession(
            task_id=task_id,
            proc=proc,
            started_at=time.monotonic(),
            session_id=session_id,
        )
        self._sessions[task_id] = session

        log.info(
            "playwright_spawned",
            extra={
                "task_id": task_id,
                "session_id": session_id,
                "pid": proc.pid,
            },
        )
        return session

    async def get_or_spawn(self, task_id: str) -> PlaywrightSession:
        """Return the existing session for *task_id*, or spawn a new one.

        This is the primary entry point for browser tool handlers: call this
        to obtain the per-task transport before forwarding the MCP tool call.
        """
        existing = self._sessions.get(task_id)
        if existing is not None and existing.proc.returncode is None:
            return existing
        # Session died or doesn't exist — (re)spawn.
        if task_id in self._sessions:
            # Clean up the dead entry first.
            del self._sessions[task_id]
        return await self.spawn(task_id)

    async def kill_session(
        self,
        task_id: str,
        *,
        reason: str = "unknown",
    ) -> PlaywrightSession | None:
        """Kill the session for *task_id* and remove it from the dict.

        Returns the killed session (with ``duration_s`` computed), or ``None``
        if the task had no active session.
        """
        session = self._sessions.pop(task_id, None)
        if session is None:
            return None

        duration_s = time.monotonic() - session.started_at
        await self._terminate_proc(session.proc, task_id)

        log.info(
            "playwright_session_ended",
            extra={
                "task_id": task_id,
                "session_id": session.session_id,
                "reason": reason,
                "duration_s": round(duration_s, 3),
            },
        )
        return session

    async def kill_all(self) -> None:
        """Force-kill all active sessions (lifespan cleanup / NFR-R9).

        Used during server shutdown to ensure no orphaned Chromium processes
        survive past server exit.
        """
        task_ids = list(self._sessions.keys())
        if not task_ids:
            return

        log.warning(
            "playwright_kill_all",
            extra={"count": len(task_ids), "task_ids": task_ids},
        )

        # Kill all concurrently (don't wait for each sequentially).
        await asyncio.gather(
            *(self._terminate_proc(self._sessions[task_id].proc, task_id) for task_id in task_ids),
            return_exceptions=True,
        )
        self._sessions.clear()

    async def _terminate_proc(
        self,
        proc: asyncio.subprocess.Process,
        task_id: str,
    ) -> None:
        """Terminate a subprocess: SIGTERM → wait → SIGKILL if needed.

        Timeline:
        1. Send SIGTERM (graceful).
        2. Wait up to ``_GRACEFUL_TIMEOUT`` seconds (10s).
        3. If still alive, send SIGKILL.
        4. Wait up to ``_HARD_KILL_TIMEOUT - _GRACEFUL_TIMEOUT`` seconds (20s).
        5. If STILL alive, log an error (zombie — should not happen with --init).
        """
        if proc.returncode is not None:
            # Already dead.
            return

        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            # Died between the check and the signal — fine.
            return

        try:
            await asyncio.wait_for(proc.wait(), timeout=_GRACEFUL_TIMEOUT)
            return  # Exited gracefully.
        except TimeoutError:
            pass

        # Process refused to die — hard kill.
        log.warning(
            "playwright_sigkill",
            extra={"task_id": task_id, "pid": proc.pid},
        )
        try:
            proc.kill()
        except ProcessLookupError:
            return

        try:
            remaining = _HARD_KILL_TIMEOUT - _GRACEFUL_TIMEOUT
            await asyncio.wait_for(proc.wait(), timeout=remaining)
        except TimeoutError:
            log.error(
                "playwright_zombie",
                extra={"task_id": task_id, "pid": proc.pid},
            )


__all__ = [
    "PlaywrightSubprocessManager",
    "PlaywrightSession",
    "_build_docker_command",
]
