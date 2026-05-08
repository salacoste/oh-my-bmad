"""Bounded-write MCP tool handlers — session register, heartbeat, close (Story 5.9).

Phase 1: validated stubs. They check tier, validate session/task exists, validate
parameters, and return ``{"ok": true}``. Actual persistence routes through the
event spine via clawhip-bridge — deferred to Story 5.12 integration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from registry_state.schema import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    Session,
    Task,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def _check_tier(actor_kind: str, tool_name: str) -> bool:
    """NO-OP capability-tier gate (Phase 1 placeholder).

    Story 6.1-6.3 replaces this with real Tier 0/1/2/3 enforcement.
    """
    log.debug(
        "tier-check (no-op): actor_kind=%s tool=%s — full enforcement in Stories 6.1-6.3",
        actor_kind,
        tool_name,
    )
    return True


async def _validate_task_exists(
    session_maker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> bool:
    """Return True if task exists in the materialized state."""
    async with session_maker() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none() is not None


async def _validate_session_exists(
    session_maker: async_sessionmaker[AsyncSession],
    session_id: str,
) -> bool:
    """Return True if session exists in the materialized state."""
    async with session_maker() as session:
        result = await session.execute(select(Session).where(Session.id == session_id))
        return result.scalar_one_or_none() is not None


def register_tools(
    mcp: FastMCP,
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: str,
    actor_id: str,
) -> None:
    """Register 3 bounded-write MCP tools on *mcp*."""

    @mcp.tool()
    async def session_register(
        task_id: str,
        worker_kind: str,
        worktree_path: str,
    ) -> dict[str, object]:
        """Register a new session (Tier-1 bounded write, Phase 1 stub)."""
        if not _check_tier(actor_kind, "session.register"):
            raise PermissionError(f"actor_kind={actor_kind!r} not authorized for session.register")
        if not task_id or not worker_kind:
            return {"ok": False, "error": "task_id and worker_kind are required"}
        exists = await _validate_task_exists(session_maker, task_id)
        if not exists:
            return {"ok": False, "error": f"task {task_id!r} not found"}
        log.info(
            "session.register: task_id=%s worker_kind=%s actor=%s (stub)",
            task_id,
            worker_kind,
            actor_id,
        )
        return {"ok": True}

    @mcp.tool()
    async def session_heartbeat(session_id: str) -> dict[str, object]:
        """Update session heartbeat timestamp (Tier-1 bounded write, Phase 1 stub)."""
        if not _check_tier(actor_kind, "session.heartbeat"):
            raise PermissionError(f"actor_kind={actor_kind!r} not authorized for session.heartbeat")
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        exists = await _validate_session_exists(session_maker, session_id)
        if not exists:
            return {"ok": False, "error": f"session {session_id!r} not found"}
        log.info(
            "session.heartbeat: session_id=%s actor=%s (stub)",
            session_id,
            actor_id,
        )
        return {"ok": True}

    @mcp.tool()
    async def session_close(session_id: str) -> dict[str, object]:
        """Close a session (Tier-1 bounded write, Phase 1 stub)."""
        if not _check_tier(actor_kind, "session.close"):
            raise PermissionError(f"actor_kind={actor_kind!r} not authorized for session.close")
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        exists = await _validate_session_exists(session_maker, session_id)
        if not exists:
            return {"ok": False, "error": f"session {session_id!r} not found"}
        log.info(
            "session.close: session_id=%s actor=%s (stub)",
            session_id,
            actor_id,
        )
        return {"ok": True}
