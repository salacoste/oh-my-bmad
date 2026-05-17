"""worker-wrapper entry point — structlog wiring + MCP client startup (Story 5.1)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

import structlog
from secret_hygiene.sanitizer import redact_secrets

from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.app.config import WorkerSettings, _safe_trace_preview
from worker_wrapper.app.main import finish_session, heartbeat_loop, start_session

_SERVICE = "worker-wrapper"

_STRUCTLOG_CONFIGURED: bool = False


def _configure_structlog() -> None:
    """Wire structlog + bridge stdlib logging (idempotent)."""
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return

    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _STRUCTLOG_CONFIGURED = True


async def _run() -> None:
    log = structlog.get_logger(__name__)

    settings = WorkerSettings()
    ready = Path(
        settings.ready_file_path or f"/tmp/worker-wrapper-ready-{os.getpid()}",
    )
    clients = MCPClientGroup(settings)

    async with clients:
        try:
            results = await verify_connectivity(clients)
            # Partial failure is intentional graceful degradation during
            # the stub phase (task-registry/session-registry are stubs
            # until Stories 5.8/5.9 land).  Clawhip-bridge is the only
            # real server today.
            if not results or not all(results.values()):
                log.error("connectivity_check_failed_partial", results=results)

            # Register signal handlers BEFORE session lifecycle so that
            # SIGTERM during startup sets stop_event and the subsequent
            # stop_event.wait() returns immediately, allowing graceful
            # finish_session instead of a dangling session.started event.
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _handle_stop(signum: int) -> None:
                log.info("stopping", signal=signum)
                stop_event.set()

            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, _handle_stop, sig)
            except (NotImplementedError, RuntimeError):
                # Windows does not implement add_signal_handler; signals
                # are still delivered but the loop will exit via
                # KeyboardInterrupt.  Same fallback as registry-state
                # and clawhip-daemon.
                pass

            # Story 5.2 — session lifecycle (AC-1, AC-2, AC-3, AC-5, AC-6).
            session_id, worker_id = await start_session(clients, settings)

            ready.touch()
            # Story 9.6 review pass-2 PM11 — surface the flag state + resolved
            # trace_id (8-char preview only) so operators can confirm at boot
            # whether ``--trace-id`` is being passed to Claude Code and what
            # the worker's effective trace_id is.
            resolved_tid = settings.resolve_trace_id()
            log.info(
                "ready",
                service=_SERVICE,
                session_id=session_id,
                worker_id=worker_id,
                trace_id_emit_flag=settings.emit_trace_id_flag,
                trace_id_preview=_safe_trace_preview(resolved_tid),  # pass-3 TM2
            )

            heartbeat_task = asyncio.create_task(
                heartbeat_loop(clients, settings, session_id, stop_event),
            )
            await stop_event.wait()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

            # Story 9.6 review pass-1 M5 / L8: finish_session resolves the
            # trace_id from ``settings`` internally — uniform API with
            # ``start_session`` / ``heartbeat_loop`` (no kwarg pollution).
            await finish_session(
                clients,
                settings,
                session_id,
                worker_id,
                worktree_path=settings.worktree_path,
            )
        finally:
            ready.unlink(missing_ok=True)


def main() -> None:
    _configure_structlog()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
