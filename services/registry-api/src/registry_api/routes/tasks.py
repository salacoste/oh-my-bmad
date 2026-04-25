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

from datetime import datetime

from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_task_id
from fastapi import APIRouter, Path, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ConfigDict
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed per AC-16
    TaskCreatedPayload,
)
from registry_state.schema import Event, Task  # noqa: IMP001 — services→services allowed per AC-16
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    """Return available commands for *status* per the Phase 1 lookup table."""
    return list(_NEXT_COMMANDS.get(status, []))


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """Request body for POST /v1/tasks.

    ``extra="forbid"`` rejects unknown fields with 422 (Pydantic v2 default
    maps to RequestValidationError → 400 via our handler).
    ``strict=True`` prevents silent type coercion (e.g. int title → str).
    ``frozen=True`` prevents mutation after construction.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    title: str
    repo: str | None = None
    hint: str | None = None


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


@router.post("/tasks", status_code=201, response_model=CreateTaskResponse)
async def post_tasks(body: CreateTaskRequest, request: Request) -> CreateTaskResponse:
    """POST /v1/tasks — create a task by emitting ``task.created`` to the event log.

    Phase 1 actor identity is hardcoded ``("operator", "http-api")``.
    Idempotency dedup is deferred to Story 3.6.
    """
    app = request.app
    clock = app.state.clock
    writer = app.state.writer

    task_id = new_task_id(clock=clock)
    event_id = new_event_id(clock=clock)

    payload = TaskCreatedPayload(task_id=task_id, title=body.title)

    # Phase 1: actor_id hardcoded to "http-api". TODO(Story 6.1+): real auth.
    actor = Actor(kind="operator", id=app.state.actor_id)

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
    """
    app = request.app
    engine = app.state.engine
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_maker() as session:
        task_result = await session.execute(select(Task).where(Task.id == task_id))
        task = task_result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

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
