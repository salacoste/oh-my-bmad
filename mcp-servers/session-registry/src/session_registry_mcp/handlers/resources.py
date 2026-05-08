"""Read-only MCP resource handlers — active sessions, session detail, heartbeats (Story 5.9).

Each handler queries the materialized SQLite state via SQLAlchemy async ORM.
Resources return JSON text; missing session returns ``""``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from registry_state.schema import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    Session,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def _session_to_dict(session: Session) -> dict[str, object]:
    """Serialize a Session ORM instance to a JSON-safe dict."""
    return {
        "id": session.id,
        "task_id": session.task_id,
        "worker_kind": session.worker_kind,
        "worktree_path": session.worktree_path,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "last_heartbeat_at": (
            session.last_heartbeat_at.isoformat() if session.last_heartbeat_at else None
        ),
    }


def register_resources(
    mcp: FastMCP,
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: str,
) -> None:
    """Register 3 read-only MCP resources on *mcp*."""

    @mcp.resource("session://active")
    async def session_active() -> str:
        """Return active sessions ordered by started_at desc as JSON."""
        async with session_maker() as session:
            result = await session.execute(
                select(Session).where(Session.status == "active").order_by(desc(Session.started_at))
            )
            sessions = list(result.scalars().all())
        return json.dumps([_session_to_dict(s) for s in sessions])

    @mcp.resource("session://detail/{session_id}")
    async def session_detail(session_id: str) -> str:
        """Return a single session by ID, or ``""`` if not found."""
        async with session_maker() as session:
            result = await session.execute(select(Session).where(Session.id == session_id))
            s = result.scalar_one_or_none()
        if s is None:
            return ""
        return json.dumps(_session_to_dict(s))

    @mcp.resource("session://heartbeats")
    async def session_heartbeats() -> str:
        """Return sessions with heartbeat timestamps, newest first."""
        async with session_maker() as session:
            result = await session.execute(
                select(Session)
                .where(Session.last_heartbeat_at.isnot(None))
                .order_by(desc(Session.last_heartbeat_at))
            )
            sessions = list(result.scalars().all())
        return json.dumps([_session_to_dict(s) for s in sessions])
