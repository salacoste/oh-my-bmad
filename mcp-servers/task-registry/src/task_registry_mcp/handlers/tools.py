"""Bounded-write MCP tool handlers — task add_note, attach_artifact, emit_event (Story 5.8).

Phase 1: validated stubs. They check tier, validate task_id exists, validate
parameters, and return ``{"ok": true}``. Actual persistence routes through the
event spine via clawhip-bridge — deferred to Story 5.12 integration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from registry_state.schema import Task  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
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
        "tier-check (no-op): actor_kind=%s tool=%s — full enforcement in Story 6.1",
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


def register_tools(
    mcp: FastMCP,
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: str,
    actor_id: str,
) -> None:
    """Register 3 bounded-write MCP tools on *mcp*."""

    # ------------------------------------------------------------------
    # Tool: task.add_note
    # ------------------------------------------------------------------

    @mcp.tool()
    async def task_add_note(task_id: str, note: str) -> dict[str, object]:
        """Add a note to a task (Tier-1 bounded write, Phase 1 stub).

        Validates tier and task existence. Actual persistence deferred to
        Story 5.12 integration with the event spine.
        """
        if not _check_tier(actor_kind, "task.add_note"):
            raise PermissionError(f"actor_kind={actor_kind!r} not authorized for task.add_note")
        if not task_id or not note:
            return {"ok": False, "error": "task_id and note are required"}
        exists = await _validate_task_exists(session_maker, task_id)
        if not exists:
            return {"ok": False, "error": f"task {task_id!r} not found"}
        log.info("task.add_note: task_id=%s actor=%s (stub)", task_id, actor_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Tool: task.attach_artifact
    # ------------------------------------------------------------------

    @mcp.tool()
    async def task_attach_artifact(
        task_id: str,
        artifact_url: str,
        artifact_type: str,
    ) -> dict[str, object]:
        """Attach an artifact to a task (Tier-1 bounded write, Phase 1 stub).

        Validates tier and task existence. Actual persistence deferred to
        Story 5.12 integration with the event spine.
        """
        if not _check_tier(actor_kind, "task.attach_artifact"):
            raise PermissionError(
                f"actor_kind={actor_kind!r} not authorized for task.attach_artifact"
            )
        if not task_id or not artifact_url or not artifact_type:
            return {"ok": False, "error": "task_id, artifact_url, and artifact_type are required"}
        exists = await _validate_task_exists(session_maker, task_id)
        if not exists:
            return {"ok": False, "error": f"task {task_id!r} not found"}
        log.info(
            "task.attach_artifact: task_id=%s type=%s (stub)",
            task_id,
            artifact_type,
        )
        return {"ok": True}

    # ------------------------------------------------------------------
    # Tool: task.emit_event
    # ------------------------------------------------------------------

    @mcp.tool()
    async def task_emit_event(
        task_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Emit a bounded task event (Tier-1 bounded write, Phase 1 stub).

        Validates tier, task existence, and required parameters. Actual
        event emission routes through the event spine via clawhip-bridge
        — deferred to Story 5.12 integration.
        """
        if not _check_tier(actor_kind, "task.emit_event"):
            raise PermissionError(f"actor_kind={actor_kind!r} not authorized for task.emit_event")
        if not task_id or not event_type:
            return {"ok": False, "error": "task_id and event_type are required"}
        exists = await _validate_task_exists(session_maker, task_id)
        if not exists:
            return {"ok": False, "error": f"task {task_id!r} not found"}
        log.info(
            "task.emit_event: task_id=%s type=%s actor=%s (stub)",
            task_id,
            event_type,
            actor_id,
        )
        return {"ok": True}
