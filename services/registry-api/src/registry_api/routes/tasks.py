"""POST /v1/tasks + GET /v1/tasks/{task_id} route handlers (Story 2.9 AC-2, AC-3).

Pydantic models:
  - ``CreateTaskRequest``:  request body for POST /v1/tasks.
  - ``CreateTaskResponse``: 201 response body for POST /v1/tasks.
  - ``ActorOut``:           nested actor shape in TaskResponse.
  - ``LastEventOut``:       nested last-event shape in TaskResponse.
  - ``TaskResponse``:       200 response body for GET /v1/tasks/{task_id}.

Behavior:
  POST: generate IDs, emit task.created envelope via EventLogWriter, return 201.
  GET:  query read-only SQLite via app.state.engine, return 200 or 404.

Actor identity is hardcoded ``("operator", "http-api")`` for Phase 1.
Real auth replaces this in Story 6.1+.

``next_commands`` lookup: minimal Phase 1 table. Full lifecycle logic lands
in Stories 5.x (worker lifecycle) and 6.x (approval gate).
"""

from __future__ import annotations

import logging
from datetime import datetime

from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_task_id
from fastapi import APIRouter, Path, Request, Response
from fastapi.exceptions import HTTPException
from idempotency import IdempotencyCacheStore
from pydantic import BaseModel, ConfigDict, Field
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed per AC-16
    TaskCreatedPayload,
)
from registry_state.schema import Event, Task  # noqa: IMP001 — services→services allowed per AC-16
from sqlalchemy import select

log = logging.getLogger("registry_api.routes.tasks")

# UUIDv7 task-id pattern: t- prefix + standard UUIDv7 hex shape
_TASK_ID_PATTERN = r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

# Phase 1 next-commands lookup. Full lifecycle logic in Stories 5.x / 6.x.
_NEXT_COMMANDS: dict[str, list[str]] = {
    "pending": ["stop"],
    "planning": ["stop"],
    "plan_ready": ["approve", "reject", "stop"],
    "executing": ["stop"],
    "completed": [],
    "failed": [],
    "stopped": [],
    "blocked": ["retry", "stop"],
}


def _next_commands_for(status: str) -> list[str]:
    """Return available commands for *status* per the Phase 1 lookup table.

    Unknown statuses produce an empty list and a warning log so an unexpected
    status string surfaces in operator dashboards instead of silently
    suppressing the workflow advance.
    """
    if status not in _NEXT_COMMANDS:
        log.warning(
            "unknown task status; returning empty next_commands",
            extra={"status": status},
        )
    return list(_NEXT_COMMANDS.get(status, []))


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """Request body for POST /v1/tasks.

    ``extra="forbid"`` rejects unknown fields with 422 (Pydantic v2 default
    maps to RequestValidationError → 422 via our handler).
    ``strict=True`` prevents silent type coercion (e.g. int title → str).
    ``frozen=True`` prevents mutation after construction.

    F7: ``title`` is bounded ``[1, 512]`` chars — empty titles are rejected
    at the API boundary (422) instead of producing meaningless task rows.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    repo: str | None = Field(default=None, max_length=2048)
    hint: str | None = Field(default=None, max_length=4096)


class CreateTaskResponse(BaseModel):
    """201 Created response body for POST /v1/tasks."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    event_id: str
    created_at: datetime


class ActorOut(BaseModel):
    """Nested actor shape in TaskResponse."""

    model_config = ConfigDict(frozen=True)

    kind: str
    id: str


class LastEventOut(BaseModel):
    """Nested last-event shape in TaskResponse."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    emitted_at: datetime


class TaskResponse(BaseModel):
    """200 OK response body for GET /v1/tasks/{task_id}."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    status: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    actor: ActorOut
    last_event: LastEventOut | None
    next_commands: list[str]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/tasks",
    status_code=201,
    description=(
        "Create a task by appending a `task.created` envelope to the event log. "
        "Returns 201 immediately after durable append; the materializer "
        "(registry-state subscriber) applies the event to SQLite asynchronously. "
        "The `Location` response header points to the GET endpoint for the new task — "
        "clients SHOULD poll it with exponential backoff because GET may return 404 "
        "for ~100–200ms after this 201 response (eventual consistency). "
        "Idempotency-Key dedup is enforced at the route level (Story 2.13): "
        "duplicate same-key submissions return 201 with byte-identical body and "
        "`X-Idempotency-Status: replayed`; first attempts return 201 with "
        "`X-Idempotency-Status: applied`. Errors during the first attempt are "
        "NOT cached — subsequent same-key submissions will retry the factory "
        "until one succeeds."
    ),
    responses={
        201: {
            "description": "Task created (or replayed from idempotency cache).",
            "model": CreateTaskResponse,
            "headers": {
                "Location": {"schema": {"type": "string"}},
                "X-Idempotency-Status": {
                    "schema": {"type": "string", "enum": ["applied", "replayed"]},
                    "description": (
                        "`applied` — first call with this key; factory ran. "
                        "`replayed` — cache hit; factory NOT run; cached body returned."
                    ),
                },
                "Idempotency-Key": {"schema": {"type": "string"}},
            },
        },
    },
)
async def post_tasks(
    body: CreateTaskRequest,
    request: Request,
) -> Response:
    """Create a task by emitting ``task.created`` to the JSONL event log.

    EVENTUAL CONSISTENCY: Returns 201 immediately after the event is durably
    appended to the log. The materializer (separate process — registry-state
    subscriber) applies the event to SQLite asynchronously, typically within
    100-200ms. Clients querying GET /v1/tasks/{task_id} immediately after
    receiving 201 may see a 404 until the materializer catches up. Use the
    Location header to retry with exponential backoff.

    The Location header points to the GET endpoint for the new task. Clients
    SHOULD poll Location until 200 returns (or 5+ seconds, then surface the
    error to the operator).

    Phase 1 actor identity is read from ``request.state.actor_id`` (set by
    ``ActorIdMiddleware`` on every request — currently hardcoded to ``"http-api"``;
    real auth lands in Story 6.1+).

    Idempotency semantics (Story 2.13 — FR28 / NFR-R4):
      - First call with key K: factory runs, emits ``task.created``, returns
        201 with body B and ``X-Idempotency-Status: applied``. The cache stores
        ``(K → result_event_id)`` durably (SQLite, 7-day TTL); body B is
        captured in an in-process side-channel keyed by K so replays return
        byte-identical bytes.
      - Concurrent same-K calls: per-key asyncio.Lock in
        ``IdempotencyCacheStore.get_or_run`` serializes them; the factory
        runs EXACTLY ONCE; losers receive 201 with the SAME body B and
        ``X-Idempotency-Status: replayed``.
      - Subsequent same-K calls (post-completion): cache hit → 201 with
        body B and ``X-Idempotency-Status: replayed``; no event re-emitted.
      - Errors during first attempt: NOT cached; subsequent same-K
        submissions retry the factory.

    Architecture line 318 references 409 for "idempotency collision returning
    prior result"; Story 2.13 returns 201 instead, matching the NFR-R4 spec
    literally ("all 100 responses 201") and simplifying client logic — the
    ``X-Idempotency-Status: replayed`` header conveys the dedup signal that
    a 409 would otherwise carry.

    Route-level wiring (vs middleware-level): keeps the dedup tied to the
    single endpoint that needs it; non-mutating endpoints (GET) carry no
    ``X-Idempotency-Status`` header. A future story can hoist this into a
    generic middleware once additional mutating endpoints land.
    """
    app = request.app
    clock = app.state.clock
    writer = app.state.writer
    idempotency_cache: IdempotencyCacheStore = app.state.idempotency_cache
    response_body_cache: dict[str, bytes] = app.state.idempotency_response_cache

    idempotency_key: str = request.state.idempotency_key
    request_id: str = request.state.request_id

    # Closure flag — distinguishes cache-miss (factory ran) from cache-hit
    # (factory skipped). ``IdempotencyCacheStore.get_or_run`` returns
    # ``(CacheHit, was_run: bool)``; we ALSO use this flag to know whether
    # to populate the response-body side-channel cache.
    factory_called: bool = False
    captured_body: dict[str, bytes] = {}

    async def _factory() -> str:
        nonlocal factory_called
        factory_called = True

        task_id = new_task_id(clock=clock)
        event_id = new_event_id(clock=clock)

        payload = TaskCreatedPayload(
            task_id=task_id,
            title=body.title,
            repo=body.repo,
            hint=body.hint,
        )

        # Phase 1: actor_id flows from middleware. TODO(Story 6.1+): real auth.
        actor = Actor(kind="operator", id=request.state.actor_id)

        envelope = EventEnvelope.create(
            event_id=event_id,
            type="task.created",
            schema_version="1.0.0",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=actor,
            payload=payload,
            request_id=request_id,
            parent_event_id=None,
        )

        await writer.append(envelope)

        # Build the 201 body NOW so we can both (a) cache it for byte-identical
        # replays and (b) hand it back to the outer scope via captured_body.
        response_model = CreateTaskResponse(
            task_id=task_id,
            event_id=event_id,
            created_at=envelope.emitted_at,
        )
        body_bytes = response_model.model_dump_json().encode("utf-8")
        captured_body["bytes"] = body_bytes
        captured_body["task_id"] = task_id.encode("utf-8")
        return event_id

    cache_hit, was_run = await idempotency_cache.get_or_run(
        idempotency_key,
        request_id=request_id,
        factory=_factory,
    )

    if was_run:
        assert factory_called, "get_or_run reported was_run=True but factory_called is False"
        body_bytes = captured_body["bytes"]
        task_id_bytes = captured_body["task_id"]
        # Persist body in the side-channel for replay paths (incl. waiters
        # that lost the per-key lock race and concurrent post-completion
        # callers from later in the TTL window).
        response_body_cache[idempotency_key] = body_bytes
        response_body_cache[idempotency_key + ":task_id"] = task_id_bytes
        status_value = "applied"
    else:
        # Cache hit. Use the side-channel body if present (typical case
        # within the same process); otherwise rebuild a minimal body from
        # the cached event_id (post-restart, body-cache empty — see Dev
        # Notes for the trade-off).
        cached = response_body_cache.get(idempotency_key)
        if cached is None:
            # Post-restart fallback: rebuild a minimal body. The task_id
            # cannot be recovered without a JSONL replay, so this branch
            # is intentionally conservative — it returns the result_event_id
            # only, with task_id="" (clients should re-fetch via the original
            # task's GET endpoint they already polled). Story 3.6 may add a
            # JSONL-backed body cache; out of scope for 2.13.
            fallback = CreateTaskResponse(
                task_id="",
                event_id=cache_hit.result_event_id,
                created_at=cache_hit.created_at,
            )
            body_bytes = fallback.model_dump_json().encode("utf-8")
            task_id_bytes = b""
        else:
            body_bytes = cached
            task_id_bytes = response_body_cache.get(idempotency_key + ":task_id", b"")
        status_value = "replayed"

    task_id_str = task_id_bytes.decode("utf-8")
    headers = {
        "X-Idempotency-Status": status_value,
        "Location": f"/v1/tasks/{task_id_str}",
    }
    return Response(
        content=body_bytes,
        status_code=201,
        media_type="application/json",
        headers=headers,
    )


@router.get(
    "/tasks/{task_id}",
    status_code=200,
    response_model=TaskResponse,
)
async def get_task_by_id(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
) -> TaskResponse:
    """GET /v1/tasks/{task_id} — return full materialized state from read-only SQLite.

    Returns 200 with ``TaskResponse`` on success, 404 (problem+json) if the
    task does not exist. The engine is read-only (Story 2.3 ``create_engine``
    with ``read_only=True``) — write attempts raise ``OperationalError``.

    F10: ``session_maker`` is constructed once on ``app.state`` during lifespan
    startup and reused per-request — avoids the per-request allocation of an
    ``async_sessionmaker`` on the hot read path.
    """
    session_maker = request.app.state.session_maker

    async with session_maker() as session:
        task_result = await session.execute(select(Task).where(Task.id == task_id))
        task = task_result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        last_event: LastEventOut | None = None
        if task.last_event_id is not None:
            event_result = await session.execute(
                select(Event).where(Event.id == task.last_event_id)
            )
            event_row = event_result.scalar_one_or_none()
            if event_row is not None:
                last_event = LastEventOut(
                    id=event_row.id,
                    type=event_row.type,
                    emitted_at=event_row.emitted_at,
                )

    return TaskResponse(
        task_id=task.id,
        status=task.status,
        title=task.title,
        created_at=task.created_at,
        updated_at=task.updated_at,
        actor=ActorOut(kind=task.actor_kind, id=task.actor_id),
        last_event=last_event,
        next_commands=_next_commands_for(task.status),
    )


__all__ = [
    "ActorOut",
    "CreateTaskRequest",
    "CreateTaskResponse",
    "LastEventOut",
    "TaskResponse",
    "router",
]
