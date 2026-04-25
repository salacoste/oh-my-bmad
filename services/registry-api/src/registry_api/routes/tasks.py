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
    response_model=CreateTaskResponse,
    description=(
        "Create a task by appending a `task.created` envelope to the event log. "
        "Returns 201 immediately after durable append; the materializer "
        "(registry-state subscriber) applies the event to SQLite asynchronously. "
        "The `Location` response header points to the GET endpoint for the new task — "
        "clients SHOULD poll it with exponential backoff because GET may return 404 "
        "for ~100–200ms after this 201 response (eventual consistency). "
        "Note: the `Idempotency-Key` request header is read but dedup logic is "
        "not yet enforced — pending Story 3.6 (FastAPI middleware stack). "
        "Response headers `Idempotency-Key` (echo) and `X-Idempotency-Status: "
        "not-enforced` indicate Phase 1 status."
    ),
)
async def post_tasks(
    body: CreateTaskRequest,
    request: Request,
    response: Response,
) -> CreateTaskResponse:
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
    """
    app = request.app
    clock = app.state.clock
    writer = app.state.writer

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
        request_id=request.state.request_id,
        parent_event_id=None,
    )

    await writer.append(envelope)

    response.headers["Location"] = f"/v1/tasks/{task_id}"
    return CreateTaskResponse(
        task_id=task_id,
        event_id=event_id,
        created_at=envelope.emitted_at,
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
