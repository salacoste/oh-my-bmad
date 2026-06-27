"""GET /v1/tasks/{task_id}/logs/digest route handler (Story 7.3 / FR5).

Returns an LLM-summarized digest of a task's recent events. Uses the
Anthropic API via ``adapters.llm_digest`` with graceful fallback to raw
event formatting when the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import anthropic
from fastapi import APIRouter, Path, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Event,
)
from sqlalchemy import select

from registry_api.adapters.llm_digest import EventRow, summarize_events
from registry_api.routes.tasks import _TASK_ID_PATTERN

_log = logging.getLogger("registry_api.routes.digest")

router = APIRouter()
_DIGEST_STREAM_ROUTE = "GET /v1/tasks/{task_id}/logs/digest/stream"
_DIGEST_STREAM_MEDIA_TYPE = "application/x-ndjson"
_DIGEST_STREAM_CHUNK_SIZE = 2_000
_DIGEST_STREAM_MAX_CHUNKS = 10
_DIGEST_STREAM_FORBIDDEN_PATTERNS = (
    re.compile(
        r"\b(?:payload_json|provider_internal|anthropic|openai|hrefs?|prompts?|urls?|source\s+tokens?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:event\s+payloads?|raw\s+events?|raw\s+logs?|provider\s+internals?|control\s+hints?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:https?|file)://", re.IGNORECASE),
    re.compile(r"(?<!\w)(?:retry|control)(?!\w)", re.IGNORECASE),
    re.compile(
        r"(?:(?<=\s)|^|[\"'`<({\[])(?:"
        r"~/|"
        r"\.{1,2}/|"
        r"/(?:users|private|tmp|home|var|etc|opt|usr|root|volumes|workspace|workspaces|mnt)/|"
        r"[a-z]:[\\/]"
        r")\S*",
        re.IGNORECASE,
    ),
)


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


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _digest_stream_state(digest_text: str) -> tuple[str, str, str]:
    """Return fail-closed stream text, display state, and authority state.

    The non-streaming digest route keeps its historical raw-event fallback.
    The stream contract is intentionally narrower: when summarization fell
    back to raw event formatting or successful model text contains over-broad
    values, stream only bounded degraded metadata text rather than excerpts.
    """
    text = digest_text.strip()
    if _digest_provider_unavailable(digest_text):
        return (
            "LLM unavailable — bounded digest stream summary unavailable.",
            "provider-unavailable",
            "non-authoritative",
        )
    if _digest_stream_contains_forbidden_marker(digest_text):
        return (
            "Digest stream summary suppressed by safety boundary.",
            "invalid",
            "non-authoritative",
        )
    return text, "healthy", "authoritative"


def _digest_provider_unavailable(digest_text: str) -> bool:
    text = digest_text.strip().lower()
    return text.startswith("(llm unavailable") or "raw event summary" in text


def _digest_stream_contains_forbidden_marker(digest_text: str) -> bool:
    return any(pattern.search(digest_text) for pattern in _DIGEST_STREAM_FORBIDDEN_PATTERNS)


def _chunk_digest(text: str) -> tuple[list[str], bool]:
    max_chars = _DIGEST_STREAM_CHUNK_SIZE * _DIGEST_STREAM_MAX_CHUNKS
    stream_truncated = len(text) > max_chars
    bounded = text[:max_chars]
    chunks = [
        bounded[index : index + _DIGEST_STREAM_CHUNK_SIZE]
        for index in range(0, len(bounded), _DIGEST_STREAM_CHUNK_SIZE)
    ]
    return chunks[:_DIGEST_STREAM_MAX_CHUNKS] or [
        "Digest stream summary unavailable."
    ], stream_truncated


def _ndjson_frame(frame: dict[str, Any]) -> bytes:
    return (json.dumps(frame, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


async def _digest_stream_frames(
    *,
    task_id: str,
    digest_text: str,
    truncated: bool,
    retrieved_at: str,
    request_id: str,
    trace_id: str | None,
) -> AsyncIterator[bytes]:
    safe_text, final_display_state, final_authority_state = _digest_stream_state(digest_text)
    chunks, stream_truncated = _chunk_digest(safe_text)
    base_frame: dict[str, Any] = {
        "task_id": task_id,
        "route": _DIGEST_STREAM_ROUTE,
        "retrieved_at": retrieved_at,
        "freshness_state": "fresh",
        "provenance": "registry-state digest stream",
        "request_id": request_id,
        "trace_id": trace_id,
        "correlation_id": request_id,
    }
    yield _ndjson_frame(
        {
            **base_frame,
            "type": "open",
            "sequence": 0,
            "display_state": "partial",
            "authority_state": "non-authoritative",
        }
    )
    for sequence, chunk in enumerate(chunks, start=1):
        yield _ndjson_frame(
            {
                **base_frame,
                "type": "chunk",
                "sequence": sequence,
                "display_state": "partial",
                "authority_state": "non-authoritative",
                "chunk": chunk,
            }
        )
    yield _ndjson_frame(
        {
            **base_frame,
            "type": "final",
            "sequence": len(chunks) + 1,
            "display_state": final_display_state,
            "authority_state": final_authority_state,
            "truncated": truncated or stream_truncated,
            "line_count": max(1, min(20, len(safe_text.splitlines()) or 1)),
            "chunk_count": len(chunks),
        }
    )


async def _load_digest(
    request: Request,
    task_id: str,
) -> tuple[str, bool]:
    session_maker = request.app.state.session_maker
    client: anthropic.AsyncAnthropic | None = getattr(request.app.state, "anthropic_client", None)

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

    events = [
        EventRow(
            type=row.type,
            emitted_at_iso=row.emitted_at.isoformat(),
            payload_json=row.payload_json,
        )
        for row in event_rows
    ]

    digest_text, truncated = await summarize_events(events, client=client)
    lines = digest_text.splitlines()
    if len(lines) > 20:
        digest_text = "\n".join(lines[:20])
        truncated = True
    return digest_text, truncated


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
    digest_text, truncated = await _load_digest(request, task_id)
    line_count = len(digest_text.splitlines()[:20]) or 1

    return LogsDigestResponse(
        task_id=task_id,
        digest=digest_text,
        truncated=truncated,
        line_count=line_count,
    )


@router.get(
    "/tasks/{task_id}/logs/digest/stream",
    status_code=200,
)
async def get_logs_digest_stream(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
) -> StreamingResponse:
    """GET /v1/tasks/{task_id}/logs/digest/stream — bounded NDJSON stream."""
    if request.url.query:
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks/{task_id}/logs/digest/stream does not accept query selectors",
        )

    if await request.body():
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks/{task_id}/logs/digest/stream does not accept a request body",
        )

    digest_text, truncated = await _load_digest(request, task_id)
    clock = request.app.state.clock
    request_id: str = request.state.request_id
    trace_id: str | None = getattr(request.state, "trace_id", None)
    return StreamingResponse(
        _digest_stream_frames(
            task_id=task_id,
            digest_text=digest_text,
            truncated=truncated,
            retrieved_at=_utc_z(clock.now()),
            request_id=request_id,
            trace_id=trace_id,
        ),
        media_type=_DIGEST_STREAM_MEDIA_TYPE,
    )


__all__ = ["LogsDigestResponse", "router"]
