"""GET /v1/tasks/{task_id}/events and /transitions route handlers
(Story 7.5 / FR6, Story 35.6 / FR108).

Returns a JSON array of raw typed event envelopes for debugging and the
``oh-my-bmad-cli events --follow`` live-tail. Supports ``since`` cursor
and ``limit`` pagination.

Wire contract: bare JSON array ``[...]`` (NOT wrapped in an object).
The CLI client wraps the array client-side into
``TaskEventsResponseLocal(events=data)``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Event,
)
from sqlalchemy import select

from registry_api.routes.tasks import _TASK_ID_PATTERN

_log = logging.getLogger("registry_api.routes.events")

router = APIRouter()


def _row_to_envelope(row: Event) -> dict[str, Any]:
    """Map an Event ORM row to an envelope dict matching the wire contract."""
    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {"_raw": row.payload_json}

    return {
        "event_id": row.id,
        "schema_version": row.schema_version,
        "type": row.type,
        "emitted_at": row.emitted_at.isoformat(),
        "emitted_at_monotonic_ns": row.emitted_at_monotonic_ns,
        "actor": {"kind": row.actor_kind, "id": row.actor_id},
        "session_id": row.session_id,
        "payload": payload,
        "parent_event_id": row.parent_event_id,
        "trace_id": row.trace_id,  # Story 9.7: ORM column wired (AC3/AC5)
        "request_id": row.request_id,
    }


@router.get(
    "/tasks/{task_id}/events",
    status_code=200,
)
async def get_task_events(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
    since: datetime | None = Query(None),  # noqa: B008 — FastAPI requires Query() in arg defaults
    after: int | None = Query(
        None,
        ge=0,
        description="Cursor: emitted_at_monotonic_ns of last event from previous page",
    ),  # noqa: B008
    limit: int = Query(100, ge=1, le=1000),  # noqa: B008
) -> list[dict[str, Any]]:
    """GET /v1/tasks/{task_id}/events — raw typed event stream (FR6).

    Returns a JSON array of event envelope objects ordered by
    ``emitted_at`` ascending. Supports ``since`` (ISO 8601 timestamp
    filter, inclusive ``>=``), ``after`` (monotonic_ns cursor, strict
    ``>``), and ``limit`` for the CLI follow-mode polling loop.

    Note: ``since`` uses wall-clock time while ``after`` uses monotonic
    nanoseconds. The two clocks may diverge under NTP corrections. When
    combining both, results must satisfy both predicates — prefer using
    only ``after`` for precise pagination to avoid clock-skew edge cases.
    """
    session_maker = request.app.state.session_maker
    _log.debug("events query task_id=%s since=%s after=%s limit=%s", task_id, since, after, limit)

    stmt = (
        select(Event)
        .where(Event.task_id == task_id)
        .order_by(Event.emitted_at.asc(), Event.emitted_at_monotonic_ns.asc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(Event.emitted_at >= since)
    if after is not None:
        stmt = stmt.where(Event.emitted_at_monotonic_ns > after)

    async with session_maker() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [_row_to_envelope(row) for row in rows]


# ---------------------------------------------------------------------------
# Story 35.6 / FR108: audit trail query endpoint
# ---------------------------------------------------------------------------


def _row_to_transition(row: Event) -> dict[str, Any]:
    """Map a ``task.state_transition`` Event row to a transition dict."""
    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}

    return {
        "event_id": row.id,
        "from_state": payload.get("from_state", ""),
        "to_state": payload.get("to_state", ""),
        "trigger_event": payload.get("trigger_event", ""),
        "worker_id": payload.get("worker_id", ""),
        "timestamp": payload.get("timestamp", row.emitted_at.isoformat()),
        "emitted_at": row.emitted_at.isoformat(),
        "emitted_at_monotonic_ns": row.emitted_at_monotonic_ns,
        "trace_id": row.trace_id,
    }


@router.get(
    "/tasks/{task_id}/transitions",
    status_code=200,
)
async def get_task_transitions(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
    after: int | None = Query(
        None,
        ge=0,
        description="Cursor: emitted_at_monotonic_ns of last transition from previous page",
    ),  # noqa: B008
    limit: int = Query(100, ge=1, le=1000),  # noqa: B008
) -> list[dict[str, Any]]:
    """GET /v1/tasks/{task_id}/transitions — ordered state transition history (FR108).

    Returns a JSON array of state transition records for the task, ordered by
    ``emitted_at`` ascending. Each record contains ``from_state``, ``to_state``,
    ``trigger_event``, ``worker_id``, and ``timestamp`` extracted from the
    ``task.state_transition`` audit event payload.

    Supports ``after`` (monotonic_ns cursor, strict ``>``) and ``limit`` for
    pagination, matching the ``/events`` endpoint pattern.
    """
    session_maker = request.app.state.session_maker
    _log.debug("transitions query task_id=%s after=%s limit=%s", task_id, after, limit)

    stmt = (
        select(Event)
        .where(Event.task_id == task_id, Event.type == "task.state_transition")
        .order_by(Event.emitted_at.asc(), Event.emitted_at_monotonic_ns.asc())
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(Event.emitted_at_monotonic_ns > after)

    async with session_maker() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [_row_to_transition(row) for row in rows]


__all__ = ["router"]
