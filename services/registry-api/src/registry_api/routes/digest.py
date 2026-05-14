"""GET /v1/tasks/{task_id}/logs/digest route handler (Story 7.3 / FR5).

Returns an LLM-summarized digest of a task's recent events. Uses the
Anthropic API via ``adapters.llm_digest`` with graceful fallback to raw
event formatting when the LLM is unavailable.
"""

from __future__ import annotations

import logging

import anthropic
from fastapi import APIRouter, Path, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Event,
)
from sqlalchemy import select

from registry_api.adapters.llm_digest import EventRow, summarize_events
from registry_api.routes.tasks import _TASK_ID_PATTERN

_log = logging.getLogger("registry_api.routes.digest")

router = APIRouter()


class LogsDigestResponse(BaseModel):
    """200 OK response body for GET /v1/tasks/{task_id}/logs/digest.

    Wire contract must match ``LogsDigestResponseLocal`` in
    ``telegram_gateway/handlers/registry_client.py``:
    ``task_id``, ``digest``, ``truncated``, ``line_count``.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    digest: str = Field(min_length=1, max_length=20_000)
    truncated: bool = False
    line_count: int = Field(ge=1, le=20)


@router.get(
    "/tasks/{task_id}/logs/digest",
    status_code=200,
    response_model=LogsDigestResponse,
)
async def get_logs_digest(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
) -> LogsDigestResponse:
    """GET /v1/tasks/{task_id}/logs/digest — LLM-summarized event digest (FR5).

    Queries the task's recent events from the read-only SQLite store,
    passes them to the LLM digest adapter, and returns a concise summary.

    Returns 404 if the task has no events. The endpoint gracefully degrades
    to a raw-event summary if the Anthropic API is unavailable.
    """
    session_maker = request.app.state.session_maker
    client: anthropic.AsyncAnthropic | None = getattr(
        request.app.state, "anthropic_client", None
    )

    async with session_maker() as session:
        result = await session.execute(
            select(Event)
            .where(Event.task_id == task_id)
            .order_by(Event.emitted_at.desc())
            .limit(55)
        )
        event_rows = result.scalars().all()

    if not event_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No events found for task {task_id}",
        )

    # Map ORM rows to lightweight EventRow dataclasses.
    events = [
        EventRow(
            type=row.type,
            emitted_at_iso=row.emitted_at.isoformat(),
            payload_json=row.payload_json,
        )
        for row in event_rows
    ]

    digest_text, truncated = await summarize_events(events, client=client)

    # Enforce the wire-contract 20-line cap. LLM output and the fallback
    # digest may exceed 20 lines; truncate to satisfy LogsDigestResponse.
    lines = digest_text.splitlines()
    if len(lines) > 20:
        digest_text = "\n".join(lines[:20])
        truncated = True
    line_count = len(lines[:20]) or 1

    return LogsDigestResponse(
        task_id=task_id,
        digest=digest_text,
        truncated=truncated,
        line_count=line_count,
    )


__all__ = ["LogsDigestResponse", "router"]
