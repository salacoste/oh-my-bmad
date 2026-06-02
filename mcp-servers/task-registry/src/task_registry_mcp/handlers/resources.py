"""Read-only MCP resource handlers — task list, detail, approval queue, blockers (Story 5.8).

Each handler queries the materialized SQLite state via SQLAlchemy async ORM.
Resources return JSON text; missing task returns ``""``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from registry_state.schema import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    Event,
    Task,
)
from sqlalchemy import desc, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def _task_to_dict(task: Task) -> dict[str, object]:
    """Serialize a Task ORM instance to a JSON-safe dict."""
    return {
        "id": task.id,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "actor_kind": task.actor_kind,
        "actor_id": task.actor_id,
        "title": task.title,
        "last_event_id": task.last_event_id,
        "blocker_reason": task.blocker_reason,
        "current_step": task.current_step,
        "total_steps": task.total_steps,
        "last_agent_action": task.last_agent_action,
        "hint": task.hint,
        # Story 12.4: per-task budget policy (FR68a). NULL → orchestrator-adapter
        # falls back to OMB_DEFAULT_TASK_BUDGET_TOKENS / legacy default.
        "budget_token_limit": task.budget_token_limit,
        "budget_action": task.budget_action,
    }


def register_resources(
    mcp: FastMCP,
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: str,
) -> None:
    """Register 4 read-only MCP resources on *mcp*."""

    # ------------------------------------------------------------------
    # Resource: task/list
    # ------------------------------------------------------------------

    @mcp.resource("task://list")
    async def task_list() -> str:
        """Return all tasks ordered by updated_at desc as JSON."""
        async with session_maker() as session:
            result = await session.execute(select(Task).order_by(desc(Task.updated_at)))
            tasks = list(result.scalars().all())
        return json.dumps([_task_to_dict(t) for t in tasks])

    # ------------------------------------------------------------------
    # Resource: task/detail/{task_id}
    # ------------------------------------------------------------------

    @mcp.resource("task://detail/{task_id}")
    async def task_detail(task_id: str) -> str:
        """Return a single task by ID, or ``""`` if not found."""
        async with session_maker() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
        if task is None:
            return ""
        return json.dumps(_task_to_dict(task))

    # ------------------------------------------------------------------
    # Resource: task/approval-queue
    # ------------------------------------------------------------------

    @mcp.resource("task://approval-queue")
    async def approval_queue() -> str:
        """Return tasks with task.approval_requested events as JSON."""
        async with session_maker() as session:
            subq = (
                select(distinct(Event.task_id))
                .where(Event.type == "task.approval_requested")
                .where(Event.task_id.isnot(None))
            )
            result = await session.execute(
                select(Task).where(Task.id.in_(subq)).order_by(Task.updated_at.asc())
            )
            tasks = list(result.scalars().all())
        return json.dumps([_task_to_dict(t) for t in tasks])

    # ------------------------------------------------------------------
    # Resource: task/blockers
    # ------------------------------------------------------------------

    @mcp.resource("task://blockers")
    async def blockers() -> str:
        """Return tasks with task.blocker_raised events as JSON."""
        async with session_maker() as session:
            subq = (
                select(distinct(Event.task_id))
                .where(Event.type == "task.blocker_raised")
                .where(Event.task_id.isnot(None))
            )
            result = await session.execute(
                select(Task).where(Task.id.in_(subq)).order_by(desc(Task.updated_at))
            )
            tasks = list(result.scalars().all())
        return json.dumps([_task_to_dict(t) for t in tasks])
