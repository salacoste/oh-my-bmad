"""Story 11.3 / FR63 — operator approval-inbox endpoints.

Two routes for the operator-facing approval-inbox UX:

* ``POST /v1/approvals/inbox`` — emit ``approval.inbox_opened`` to the event
  log. Used by the telegram-gateway ``/approvals`` command handler to
  record that the operator opened (or re-opened) a pinned Forum-Topic
  inbox in their chat. Idempotency-Key dedup via the standard
  ``IdempotencyCacheStore`` pattern (same as POST /v1/tasks).

* ``GET /v1/approvals/inbox/{operator_chat_id}`` — return the materialized
  ``ApprovalInbox`` row for *operator_chat_id*, or 404 if none exists.
  Used by clawhip-daemon (outbound delivery) to determine whether to
  route ``task.approval_requested`` events into the pinned thread
  (instead of the originating task thread).

D5 outcome (Story 11.3): clawhip-daemon reads via this HTTP endpoint —
no direct ``registry_state`` import, mirroring the existing
``GET /v1/tasks/{id}`` binding-lookup pattern (Story 3.6/3.9). The
endpoint reads through the same read-only SQLAlchemy session_maker
exposed on ``app.state`` for other GET routes.

FR26 single-writer compliance: telegram-gateway calls POST →
registry-api appends ``approval.inbox_opened`` to JSONL → registry-state
materializer UPSERTs the ``approval_inbox`` row. telegram-gateway never
writes SQLite directly.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Literal

import structlog
from events import ApprovalInboxOpenedPayload
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_uuid7
from fastapi import APIRouter, HTTPException, Path, Request, Response
from idempotency import IdempotencyCacheStore
from pydantic import BaseModel, ConfigDict, Field
from registry_state.schema import ApprovalInbox  # noqa: IMP001 services→services per AC-16
from sqlalchemy import select

from registry_api.routes.tasks import ResponseSlot, ResponseSlotCache

log = structlog.get_logger("registry_api.routes.approvals")

IdempotencyStatus = Literal["applied", "replayed"]


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class OpenInboxRequest(BaseModel):
    """Request body for ``POST /v1/approvals/inbox`` (Story 11.3 AC1).

    Only carries the data the operator's ``/approvals`` handler obtains
    from aiogram's ``create_forum_topic`` response — the chat id and
    the new thread id. ``opened_at`` and ``opened_by_actor_id`` are
    minted server-side from the request clock + middleware-provided
    actor identity, so the wire payload remains minimal and the
    server-side timestamps stay authoritative (matches the
    ``POST /v1/tasks`` convention).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # Telegram chat_id is int64; supergroups have negative ids.
    operator_chat_id: int = Field(ge=-(2**63), le=(2**63) - 1)
    # Telegram thread_id is positive int64 (Forum-Topic message_thread_id).
    inbox_thread_id: int = Field(ge=1, le=(2**63) - 1)


class OpenInboxResponse(BaseModel):
    """Response body for ``POST /v1/approvals/inbox`` (Story 11.3 AC1)."""

    model_config = ConfigDict(frozen=True)

    operator_chat_id: int
    inbox_thread_id: int
    opened_at: datetime
    event_id: str
    idempotency_status: IdempotencyStatus = "applied"


class InboxStateResponse(BaseModel):
    """Response body for ``GET /v1/approvals/inbox/{operator_chat_id}``.

    Mirrors the columns of :class:`registry_state.schema.ApprovalInbox`
    so clawhip-daemon's HTTP read client can map the response into a
    routing decision directly. Returns 404 (problem+json) when no row
    exists for *operator_chat_id* — telegram-gateway's ``/approvals``
    handler uses the 404 as the "no inbox open yet" signal.
    """

    model_config = ConfigDict(frozen=True)

    operator_chat_id: int
    inbox_thread_id: int
    opened_at: datetime
    opened_by_actor_id: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/approvals/inbox",
    status_code=201,
    description=(
        "Emit ``approval.inbox_opened`` to the event log. Used by the "
        "telegram-gateway ``/approvals`` command handler when the operator "
        "creates a pinned Forum-Topic inbox. Returns 201 immediately after "
        "durable append; the materializer (registry-state subscriber) UPSERTs "
        "the ``approval_inbox`` row asynchronously. Idempotency-Key dedup via "
        "the standard ``IdempotencyCacheStore`` pattern — duplicate "
        "submissions return 201 with the cached body and "
        "``X-Idempotency-Status: replayed``."
    ),
)
async def post_open_inbox(
    body: OpenInboxRequest,
    request: Request,
) -> Response:
    """POST /v1/approvals/inbox — record the operator's new pinned inbox.

    Phase 1 actor identity is read from ``request.state.actor_id`` (set by
    ``ActorIdMiddleware``); ``opened_at`` is minted from ``app.state.clock``
    so the server-side timestamp is authoritative (matches the
    ``POST /v1/tasks`` convention). The materializer UPSERTs the
    ``approval_inbox`` row; the materialization lag is typically <200 ms.
    """
    app = request.app
    clock = app.state.clock
    writer = app.state.writer
    idempotency_cache: IdempotencyCacheStore = app.state.idempotency_cache
    response_body_cache: ResponseSlotCache = app.state.idempotency_response_cache

    idempotency_key: str = request.state.idempotency_key
    request_id: str = request.state.request_id

    trace_id_val = getattr(request.state, "trace_id", None)
    if trace_id_val is None:
        # Story 11.3 review P22: downgrade to WARNING — standalone trace_id
        # minting is a recovery path, not a hard error. Tests that call the
        # endpoint without the middleware should not log noise at ERROR.
        log.warning("TraceIdMiddleware missing from stack — minting standalone trace_id")
        trace_id: str = new_uuid7(clock=clock)
    else:
        trace_id = trace_id_val
    actor_id: str = getattr(request.state, "actor_id", "http-api")

    # Story 11.3 PP8 (pass-2): the dual encoding below is deliberate —
    # ``ResponseSlotCache`` uses tuple keys (asyncio-safe, in-memory only);
    # ``IdempotencyCacheStore`` (persistent SQLite) uses string keys for
    # SQLite-compatibility. Both encodings derive from the same
    # ``(actor_id, idempotency_key)`` tuple and stay in lockstep.
    cache_key = (actor_id, idempotency_key)
    factory_called: bool = False
    captured: dict[str, str | int | datetime] = {}

    async def _factory() -> str:
        nonlocal factory_called
        # Story 11.3 review P24: defensive guard — factory must only run
        # once per cache key. If it fires twice, the cache invariant is
        # broken (concurrent loser callers visible mid-population).
        # Story 11.3 PP20: wrap the bare ``RuntimeError`` in an
        # ``HTTPException(503)`` so it does not propagate as a generic
        # 500 (no handler) and so the client sees a Retry-After hint
        # matching the P14 outer-guard hygiene.
        if factory_called:
            raise HTTPException(
                status_code=503,
                detail=(
                    "approval-inbox idempotency factory called twice — "
                    "cache invariant violated; please retry."
                ),
                headers={"Retry-After": "5"},
            )
        factory_called = True

        event_id = new_event_id(clock=clock)
        # Story 11.3 review P21: ``opened_at`` is captured here under the
        # idempotency-cache per-key lock. It reflects factory-exec time,
        # NOT request-arrival time. Under cache contention these may differ
        # by single-digit milliseconds — acceptable for ``opened_at``
        # semantics (audit-grade "when did the operator open the inbox").
        opened_at = clock.now()

        payload = ApprovalInboxOpenedPayload(
            operator_chat_id=body.operator_chat_id,
            inbox_thread_id=body.inbox_thread_id,
            opened_at=opened_at,
            opened_by_actor_id=actor_id,
        )

        actor = Actor(kind="operator", id=actor_id)

        envelope = EventEnvelope.create(
            event_id=event_id,
            type="approval.inbox_opened",
            schema_version="1.1.0",
            emitted_at=opened_at,
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=actor,
            payload=payload,
            request_id=request_id,
            trace_id=trace_id,
            parent_event_id=None,
        )
        await writer.append(envelope)

        response_model = OpenInboxResponse(
            operator_chat_id=body.operator_chat_id,
            inbox_thread_id=body.inbox_thread_id,
            opened_at=opened_at,
            event_id=event_id,
            idempotency_status="applied",
        )
        # Story 11.3 review P23: ms-precision serialization is enforced by
        # EventEnvelope's pydantic serializer (Story 2.1 convention); the
        # response body inherits it via ``OpenInboxResponse.opened_at``
        # which is ``AwareDatetime``.
        body_bytes = response_model.model_dump_json().encode("utf-8")

        # Same pattern as POST /v1/tasks — store the bytes in the
        # response-slot cache under the per-key asyncio.Lock so loser
        # callers cannot observe a half-populated cache.
        response_body_cache[cache_key] = ResponseSlot(
            body=body_bytes,
            task_id=b"",  # not a task-scoped operation; sentinel empty bytes.
        )
        captured["event_id"] = event_id
        captured["opened_at"] = opened_at
        return event_id

    cache_hit, was_run = await idempotency_cache.get_or_run(
        f"{actor_id}\x00{idempotency_key}",
        request_id=request_id,
        factory=_factory,
    )

    if was_run:
        if not factory_called:
            # Story 11.3 PP10 (pass-2): differentiate from the post-restart
            # 410 below. 410 Gone is reserved for "the resource genuinely
            # vanished" (cache row exists, in-memory slot lost on restart);
            # 503 Service Unavailable + Retry-After is the right code for
            # an invariant violation (server-side bug) because the client
            # should retry the SAME key after the server recovers.
            log.error(
                "approval-inbox idempotency cache invariant violated",
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            raise HTTPException(
                status_code=503,
                detail=("Idempotency cache invariant violated; please retry."),
                headers={"Retry-After": "5"},
            )
        slot = response_body_cache[cache_key]
        body_bytes = slot.body
        status_value: IdempotencyStatus = "applied"
    else:
        slot_or_none = response_body_cache.get(cache_key)
        if slot_or_none is None:
            # Story 11.3 review P4 + P6: post-restart replay cannot recover
            # the original ``opened_at`` / ``operator_chat_id`` /
            # ``inbox_thread_id`` (the side-channel response cache was lost
            # on restart, and the IdempotencyCacheStore row only carries
            # ``result_event_id`` / ``created_at`` where ``created_at`` is
            # the cache-row insertion time, NOT the original event
            # ``opened_at``). Returning request-body values mixed with the
            # cached event_id would silently misreport the event timestamp.
            # 410 Gone is the honest response: the original result is no
            # longer recoverable; the client should retry with a new key.
            raise HTTPException(
                status_code=410,
                detail=(
                    "Idempotency-Key replay after restart: original response "
                    "not recoverable — please retry with a new key."
                ),
            )
        # Story 11.3 review P13 + PP9 (pass-2): reconstruct the cached
        # body via the typed ``OpenInboxResponse`` pydantic model so the
        # response bytes go through the SAME serializer as the
        # initial-write path. This eliminates any drift between
        # stdlib-``json.dumps`` and pydantic v2's model_dump_json (e.g.
        # ms-precision timestamps, NaN handling, key ordering).
        try:
            cached_payload = _json.loads(slot_or_none.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Cache corruption — keep original bytes; header still says replayed.
            body_bytes = slot_or_none.body
        else:
            replayed_response = OpenInboxResponse(
                **{**cached_payload, "idempotency_status": "replayed"}
            )
            body_bytes = replayed_response.model_dump_json().encode("utf-8")
        status_value = "replayed"

    headers: dict[str, str] = {"X-Idempotency-Status": status_value}
    return Response(
        content=body_bytes,
        status_code=201,
        media_type="application/json",
        headers=headers,
    )


@router.get(
    "/approvals/inbox/{operator_chat_id}",
    status_code=200,
    response_model=InboxStateResponse,
    description=(
        "Return the materialized ``approval_inbox`` row for "
        "*operator_chat_id*, or 404 if none exists. Read-only — consumed by "
        "clawhip-daemon's outbound-delivery sink (route ``task.approval_requested`` "
        "to the pinned thread) and by the telegram-gateway ``/approvals`` "
        "handler (detect whether the operator already has an inbox open)."
    ),
)
async def get_inbox_by_chat_id(
    request: Request,
    operator_chat_id: int = Path(..., ge=-(2**63), le=(2**63) - 1),
) -> InboxStateResponse:
    """GET /v1/approvals/inbox/{operator_chat_id} — return the row or 404."""
    session_maker = request.app.state.session_maker
    async with session_maker() as session:
        result = await session.execute(
            select(ApprovalInbox).where(ApprovalInbox.operator_chat_id == operator_chat_id)
        )
        row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No approval inbox open for operator_chat_id={operator_chat_id}",
        )

    return InboxStateResponse(
        operator_chat_id=row.operator_chat_id,
        inbox_thread_id=row.inbox_thread_id,
        opened_at=row.opened_at,
        opened_by_actor_id=row.opened_by_actor_id,
    )


__all__ = [
    "InboxStateResponse",
    "OpenInboxRequest",
    "OpenInboxResponse",
    "router",
]
