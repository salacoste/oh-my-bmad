"""State-transition handlers for the 4 task event types (Story 2.5, AC-4).

Each handler mutates the ``tasks`` (and optionally ``sessions``) table
based on the event's payload. Handlers are registered with the Materializer
and invoked ONLY when the event row was newly inserted (``rowcount == 1``).

Idempotency contract:
- ``handle_task_created`` uses ``ON CONFLICT DO UPDATE`` to make re-runs safe:
  the status stays ``pending`` (UPDATE doesn't change it); ``last_event_id``
  and ``updated_at`` are refreshed.
- Update handlers (planning_started, plan_ready, execution_started) raise
  ``MaterializerError`` when the task row is missing — this is an out-of-order
  replay guard. Production replay processes events in ``emitted_at_monotonic_ns``
  order, so the Task row always exists by the time these handlers fire.

Session rename note: ``Session`` in schema.py is the ORM model for the
``sessions`` table. Imported here as ``SessionRow`` to avoid ambiguity with
``sqlalchemy.ext.asyncio.AsyncSession`` which is also used in handler
signatures.
"""

from __future__ import annotations

from typing import cast

from events.envelope import EventEnvelope
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
)
from registry_state.schema import Session as SessionRow
from registry_state.schema import Task


def _hydrate(payload: dict[str, object] | BaseModel, model: type[BaseModel]) -> BaseModel:
    """Hydrate *payload* into *model*, handling both BaseModel and dict inputs.

    When an envelope was constructed via ``EventEnvelope.create()``, the
    payload is already the concrete model instance. After disk replay via
    ``from_canonical_json``, the payload arrives as a ``dict`` — we
    ``model_validate`` it back into the typed model.
    """
    if isinstance(payload, model):
        return payload
    if isinstance(payload, BaseModel):
        return model.model_validate(payload.model_dump())
    return model.model_validate(dict(payload))


async def handle_task_created(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Upsert a Task row with ``status="pending"``.

    Uses ``ON CONFLICT DO UPDATE`` so re-running the handler with the same
    envelope (e.g. bug-fix replay) produces the same final row state: status
    stays ``pending``; ``last_event_id`` and ``updated_at`` are refreshed.
    """
    payload = _hydrate(envelope.payload, TaskCreatedPayload)
    assert isinstance(payload, TaskCreatedPayload)
    stmt = (
        sqlite_insert(Task)
        .values(
            id=payload.task_id,
            status="pending",
            created_at=envelope.emitted_at,
            updated_at=envelope.emitted_at,
            actor_kind=envelope.actor.kind,
            actor_id=envelope.actor.id,
            title=payload.title,
            last_event_id=envelope.event_id,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_=dict(
                last_event_id=envelope.event_id,
                updated_at=envelope.emitted_at,
            ),
        )
    )
    await session.execute(stmt)


async def handle_task_planning_started(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"planning"``.

    Raises ``MaterializerError`` if the task row does not exist (out-of-order
    replay: ``task.planning.started`` before ``task.created``).
    """
    payload = _hydrate(envelope.payload, TaskPlanningStartedPayload)
    assert isinstance(payload, TaskPlanningStartedPayload)
    stmt = (
        update(Task)
        .where(Task.id == payload.task_id)
        .values(
            status="planning",
            last_event_id=envelope.event_id,
            updated_at=envelope.emitted_at,
        )
    )
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount == 0:
        raise MaterializerError(
            event_id=envelope.event_id,
            event_type=envelope.type,
            reason=f"task {payload.task_id!r} not found — out-of-order replay?",
        )


async def handle_task_plan_ready(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"plan_ready"``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskPlanReadyPayload)
    assert isinstance(payload, TaskPlanReadyPayload)
    stmt = (
        update(Task)
        .where(Task.id == payload.task_id)
        .values(
            status="plan_ready",
            last_event_id=envelope.event_id,
            updated_at=envelope.emitted_at,
        )
    )
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount == 0:
        raise MaterializerError(
            event_id=envelope.event_id,
            event_type=envelope.type,
            reason=f"task {payload.task_id!r} not found — out-of-order replay?",
        )


async def handle_task_execution_started(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"executing"`` and insert a Session row.

    The session row uses ``worker_kind="unknown"`` as a placeholder — later
    stories refine session rows with worker-specific events. AC-6 of this
    story does NOT require rich session-row population — just existence.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskExecutionStartedPayload)
    assert isinstance(payload, TaskExecutionStartedPayload)
    stmt = (
        update(Task)
        .where(Task.id == payload.task_id)
        .values(
            status="executing",
            last_event_id=envelope.event_id,
            updated_at=envelope.emitted_at,
        )
    )
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount == 0:
        raise MaterializerError(
            event_id=envelope.event_id,
            event_type=envelope.type,
            reason=f"task {payload.task_id!r} not found — out-of-order replay?",
        )
    session_stmt = (
        sqlite_insert(SessionRow)
        .values(
            id=payload.session_id,
            task_id=payload.task_id,
            worker_kind="unknown",
            status="active",
            started_at=envelope.emitted_at,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(session_stmt)


def register_default_handlers(materializer: object) -> None:
    """Register all 4 task-event handlers onto *materializer*.

    Accepts ``object`` to avoid a circular import with ``materializer.py``
    at the type level; the runtime type is ``Materializer``.  Callers
    (``app/main.py``) pass a live ``Materializer`` instance.
    """
    from registry_state.domain.materializer import Materializer

    assert isinstance(materializer, Materializer)
    materializer.register_handler("task.created", handle_task_created)
    materializer.register_handler("task.planning.started", handle_task_planning_started)
    materializer.register_handler("task.plan.ready", handle_task_plan_ready)
    materializer.register_handler("task.execution.started", handle_task_execution_started)


__all__ = [
    "handle_task_created",
    "handle_task_execution_started",
    "handle_task_plan_ready",
    "handle_task_planning_started",
    "register_default_handlers",
]
