"""Session lifecycle — start / heartbeat / finish (Story 5.2).

Coordinates session state with two MCP servers:

* **clawhip-bridge** — typed event emission (``emit_event`` tool).
* **session-registry** — session state RPC (``session.register``,
  ``session.heartbeat``, ``session.close``).  Currently a stub; calls
  are best-effort.

All MCP tool calls are best-effort: failure logs a warning but does not
crash the worker.
"""

from __future__ import annotations

import asyncio

import structlog
from events.payloads import (
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionStartedPayload,
)
from mcp import ClientSession

from worker_wrapper.adapters.mcp_clients import MCPClientGroup
from worker_wrapper.app.config import WorkerSettings

_MCP_CALL_TIMEOUT: float = 10.0
_CLAMP_FLOOR: float = 1.0


def _clamp_timeout(interval_s: float) -> float:
    """Clamp MCP call timeout to at most half the heartbeat interval, minimum 1s."""
    return max(_CLAMP_FLOOR, min(_MCP_CALL_TIMEOUT, interval_s * 0.5))


async def _call_tool_best_effort(
    session: ClientSession | None,
    tool_name: str,
    arguments: dict[str, object],
    *,
    label: str,
    timeout: float = _MCP_CALL_TIMEOUT,
) -> None:
    """Call an MCP tool; log and swallow any ``Exception``.

    ``BaseException`` subclasses (``CancelledError``, ``KeyboardInterrupt``,
    ``SystemExit``) propagate — they must not be silently swallowed.
    """
    log = structlog.get_logger(__name__)
    if session is None:
        log.warning("mcp_tool_skipped_no_session", label=label, tool=tool_name)
        return
    try:
        await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments),
            timeout=timeout,
        )
    except TimeoutError:
        log.error(
            "mcp_tool_call_timeout",
            label=label,
            tool=tool_name,
            timeout=timeout,
            _hint="session may be in inconsistent state — MCP stdio may be corrupted",
        )
    except Exception:
        log.warning("mcp_tool_call_failed", label=label, tool=tool_name, exc_info=True)


async def start_session(
    clients: MCPClientGroup,
    settings: WorkerSettings,
) -> tuple[str, str]:
    """Emit ``session.started`` and call ``session.register`` (AC-1, AC-5).

    Returns ``(session_id, worker_id)`` for use by the heartbeat loop and
    finish_session.

    Note: a SIGTERM during this function may produce a zero-heartbeat
    lifecycle (``session.started`` emitted but no heartbeats).  The
    ``stop_event`` in ``__main__`` is checked immediately after this
    returns, so the window is the duration of two MCP calls.
    """
    log = structlog.get_logger(__name__)
    session_id = settings.resolve_session_id()
    worker_id = settings.resolve_worker_id()
    task_id = settings.resolve_task_id()

    started = SessionStartedPayload(
        session_id=session_id,
        worker_id=worker_id,
        task_id=task_id,
    )

    # Register with session-registry BEFORE emitting the event so that
    # the registry is aware of the session when the event arrives.
    reg_args: dict[str, object] = {
        "session_id": session_id,
        "worker_id": worker_id,
    }
    if started.task_id is not None:
        reg_args["task_id"] = started.task_id
    await _call_tool_best_effort(
        clients.session_registry,
        "session.register",
        reg_args,
        label="session_register",
    )

    await _call_tool_best_effort(
        clients.clawhip_bridge,
        "emit_event",
        {"type": "session.started", "payload": started.model_dump()},
        label="emit_session_started",
    )

    log.info(
        "session_started",
        session_id=session_id,
        worker_id=worker_id,
        task_id=started.task_id,
    )
    return session_id, worker_id


async def heartbeat_loop(
    clients: MCPClientGroup,
    settings: WorkerSettings,
    session_id: str,
    stop_event: asyncio.Event,
) -> None:
    """Periodic ``session.heartbeat`` event + MCP tool call (AC-2, AC-5).

    Exits when ``stop_event`` is set.
    """
    log = structlog.get_logger(__name__)
    log.info("heartbeat_loop_started", interval_s=settings.heartbeat_interval_s)
    mcp_timeout = _clamp_timeout(settings.heartbeat_interval_s)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.heartbeat_interval_s,
            )
            return  # stop_event was set
        except TimeoutError:
            pass  # interval elapsed — emit heartbeat

        hb = SessionHeartbeatPayload(session_id=session_id)
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {"type": "session.heartbeat", "payload": hb.model_dump()},
            label="emit_session_heartbeat",
            timeout=mcp_timeout,
        )
        await _call_tool_best_effort(
            clients.session_registry,
            "session.heartbeat",
            {"session_id": session_id},
            label="session_heartbeat_mcp",
            timeout=mcp_timeout,
        )
        log.debug("heartbeat_emitted", session_id=session_id)

    log.info("heartbeat_loop_stopped", session_id=session_id)


async def finish_session(
    clients: MCPClientGroup,
    session_id: str,
    worker_id: str,
) -> None:
    """Emit ``session.finished`` and call ``session.close`` (AC-3, AC-5)."""
    log = structlog.get_logger(__name__)

    fin = SessionFinishedPayload(session_id=session_id)
    await _call_tool_best_effort(
        clients.clawhip_bridge,
        "emit_event",
        {"type": "session.finished", "payload": fin.model_dump()},
        label="emit_session_finished",
    )
    await _call_tool_best_effort(
        clients.session_registry,
        "session.close",
        {"session_id": session_id, "worker_id": worker_id},
        label="session_close",
    )

    log.info("session_finished", session_id=session_id, worker_id=worker_id)
