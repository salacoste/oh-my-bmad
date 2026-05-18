"""GET /v1/trace/{trace_id} route handler (Story 9.7 / FR59a / AC8).

Returns every event in the causal chain for a given trace_id, ordered by
emitted_at_monotonic_ns ascending. Enables operator-facing /trace queries
from the console-cli and Telegram-gateway.

Architecture §"trace_id propagation wiring" §line-1169.

Story 9.7 pass-1 patches applied:
  * PH-B5/E4 — LIMIT + after_event_id cursor pagination; X-Trace-Truncated
    header when result equals the limit.
  * PM-A10/E13 — extensions field added; datetime rendered with canonical
    ``Z`` suffix to match :func:`events.canonical.to_canonical_json`.
  * PM-B17 — structured ``trace_payload_json_corrupt`` log on JSON parse
    failure so operators have forensics to chase.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from events.envelope import is_valid_trace_id  # noqa: IMP001
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from registry_state.schema import Event  # noqa: IMP001 — services→services allowed
from sqlalchemy import select

_log = logging.getLogger("registry_api.routes.trace")

router = APIRouter()

# Story 9.7 pass-1 PH-B5/E4 pagination bounds.
_DEFAULT_LIMIT = 500
_MAX_LIMIT = 2000


def _row_to_dict(row: Event) -> dict[str, Any]:
    """Map an Event ORM row to an envelope dict for /trace response.

    PM-A10/E13: canonical ``Z``-suffixed datetime so the response shape
    matches what :func:`to_canonical_json` would emit.

    PM-B17: logs a structured ``trace_payload_json_corrupt`` event when
    payload_json is malformed instead of silently returning ``{"_raw": ...}``.
    Forensics: the row id is included so the operator can locate the row.

    Story 9.7 pass-2 TH-B6 (Q7 audit): the materializer stores PAYLOAD-ONLY
    in ``payload_json`` via :func:`_canonical_payload_json` (which returns
    ``env.payload.model_dump()`` serialised — NOT the full envelope). The
    inherited heuristic ``if "payload" in parsed_payload`` was therefore
    wrong: a legitimate payload that happens to have a ``"payload"`` key
    (e.g. ``secret.accessed`` payload with a nested ``payload`` summary)
    would be incorrectly unwrapped. Fix: return ``parsed_payload`` directly
    as the response's ``payload`` field. ``extensions`` is NOT stored on
    the Event row (no column for it), so the response field is always
    ``{}`` — operators needing the full canonical envelope should consume
    the JSONL log directly.
    """
    try:
        parsed_payload: dict[str, Any] = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning(
            "trace_payload_json_corrupt event_id=%s error_type=%s detail=%s",
            row.id,
            type(exc).__name__,
            exc,
        )
        parsed_payload = {"_raw": row.payload_json}

    # PM-A10/E13: canonical ``Z`` suffix matches to_canonical_json format
    # (strftime("%Y-%m-%dT%H:%M:%S.%fZ") yields microsecond precision +
    # explicit UTC ``Z`` — round-trippable through json+envelope parser).
    emitted_at_str = row.emitted_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    return {
        "event_id": row.id,
        "schema_version": row.schema_version,
        "type": row.type,
        "emitted_at": emitted_at_str,
        "emitted_at_monotonic_ns": row.emitted_at_monotonic_ns,
        "actor": {"kind": row.actor_kind, "id": row.actor_id},
        "task_id": row.task_id,
        "session_id": row.session_id,
        # TH-B6: materializer canonical shape is payload-only — no heuristic.
        "payload": parsed_payload,
        # ``extensions`` is not persisted on the Event row; always {} here.
        "extensions": {},
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
    response: Response,
    trace_id: str = Path(..., description="Bare UUIDv7 or tg:<update_id>"),
    limit: int = Query(
        _DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description="Max events returned (default 500, max 2000).",
    ),
    after_event_id: str | None = Query(
        None,
        description="Cursor: return events with id > after_event_id (string comparison).",
    ),
) -> list[dict[str, Any]]:
    """GET /v1/trace/{trace_id} — all events in the causal chain (FR59a).

    Returns a JSON array of event envelope objects ordered by
    emitted_at_monotonic_ns ascending. The response may be empty when no
    events carry the given trace_id (e.g. a trace_id from before the 1.1.0
    schema bump, or an unknown trace_id).

    Validates trace_id shape per Story 9.1 contract (bare UUIDv7 or
    ``tg:<update_id>``) — returns 400 for malformed values.

    Pagination (Story 9.7 pass-1 PH-B5/E4):
      * ``?limit=<N>`` — default 500, max 2000. Bounds the response size.
      * ``?after_event_id=<id>`` — cursor for paging through long chains.
        Returns rows with ``Event.id > after_event_id``.
      * ``X-Trace-Truncated: true`` header set when the result size hits
        the limit (signal to client there may be more rows).
    """
    if not is_valid_trace_id(trace_id):
        _log.warning("trace query rejected: invalid trace_id shape %r", trace_id)
        raise HTTPException(
            status_code=400,
            detail=f"invalid trace_id shape: {trace_id!r}. "
            "Must be a bare UUIDv7 or 'tg:<update_id>'.",
        )

    session_maker = request.app.state.session_maker
    _log.debug(
        "trace query trace_id=%s limit=%d after_event_id=%r",
        trace_id,
        limit,
        after_event_id,
    )

    stmt = select(Event).where(Event.trace_id == trace_id)
    if after_event_id is not None:
        stmt = stmt.where(Event.id > after_event_id)
    # Story 9.7 pass-2 TH-B4: LIMIT+1 pattern so we can DISTINGUISH "exactly
    # `limit` rows total" (no truncation) from "more rows exist" (truncated).
    # The prior ``len(rows) == limit`` heuristic produced false-positive
    # truncated headers when the result set happened to match the limit
    # exactly, causing clients to infinite-loop fetching empty pages.
    stmt = stmt.order_by(Event.emitted_at_monotonic_ns.asc()).limit(limit + 1)

    async with session_maker() as session:
        result = await session.execute(stmt)
        fetched = result.scalars().all()

    truncated = len(fetched) > limit
    rows = fetched[:limit]

    # TL-B14: surface whether ANY returned row was synthetically back-filled
    # (per Q1 Migrator decision). Operators consuming /trace then know
    # collision risk applies. Inspecting ``row.request_id`` vs ``row.trace_id``
    # is the cheapest signal: the back-fill rule sets ``trace_id = request_id``
    # (with ``e-`` prefix stripped), so equality (modulo prefix) is a strong
    # synthetic marker. False-positive cost is acceptable — non-synthetic
    # callers that intentionally pass trace_id=request_id also get the header,
    # which surfaces an interesting correlation rather than hiding it.
    has_synthetic = any(
        row.trace_id is not None
        and row.request_id is not None
        and (row.trace_id == row.request_id or row.trace_id == row.request_id.removeprefix("e-"))
        for row in rows
    )
    if has_synthetic:
        response.headers["X-Trace-Has-Synthetic"] = "true"

    if truncated:
        response.headers["X-Trace-Truncated"] = "true"

    return [_row_to_dict(row) for row in rows]


__all__ = ["router"]
