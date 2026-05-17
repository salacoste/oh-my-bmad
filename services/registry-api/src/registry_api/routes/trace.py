"""GET /v1/trace/{trace_id} route handler (Story 9.7 / FR59a / AC8).

Returns every event in the causal chain for a given trace_id, ordered by
emitted_at_monotonic_ns ascending. Enables operator-facing /trace queries
from the console-cli and Telegram-gateway.

Architecture §"trace_id propagation wiring" §line-1169.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from events.envelope import is_valid_trace_id  # noqa: IMP001
from fastapi import APIRouter, HTTPException, Path, Request
from registry_state.schema import Event  # noqa: IMP001 — services→services allowed
from sqlalchemy import select

_log = logging.getLogger("registry_api.routes.trace")

router = APIRouter()


def _row_to_dict(row: Event) -> dict[str, Any]:
    """Map an Event ORM row to an envelope dict for /trace response."""
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
        "task_id": row.task_id,
        "session_id": row.session_id,
        "payload": payload,
        "parent_event_id": row.parent_event_id,
        "trace_id": row.trace_id,
        "request_id": row.request_id,
    }


@router.get(
    "/trace/{trace_id}",
    status_code=200,
)
async def get_trace(
    request: Request,
    trace_id: str = Path(..., description="Bare UUIDv7 or tg:<update_id>"),
) -> list[dict[str, Any]]:
    """GET /v1/trace/{trace_id} — all events in the causal chain (FR59a).

    Returns a JSON array of event envelope objects ordered by
    emitted_at_monotonic_ns ascending. The response may be empty when no
    events carry the given trace_id (e.g. a trace_id from before the 1.1.0
    schema bump, or an unknown trace_id).

    Validates trace_id shape per Story 9.1 contract (bare UUIDv7 or
    ``tg:<update_id>``) — returns 400 for malformed values.
    """
    if not is_valid_trace_id(trace_id):
        _log.warning("trace query rejected: invalid trace_id shape %r", trace_id)
        raise HTTPException(
            status_code=400,
            detail=f"invalid trace_id shape: {trace_id!r}. "
            "Must be a bare UUIDv7 or 'tg:<update_id>'.",
        )

    session_maker = request.app.state.session_maker
    _log.debug("trace query trace_id=%s", trace_id)

    stmt = (
        select(Event)
        .where(Event.trace_id == trace_id)
        .order_by(Event.emitted_at_monotonic_ns.asc())
    )

    async with session_maker() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [_row_to_dict(row) for row in rows]


__all__ = ["router"]
