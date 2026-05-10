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
    ApprovalGrantedPayload,
    ApprovalRejectedPayload,
    LicenseOverridePayload,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskRetryRequestedPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
    Tier3ActionAttemptedPayload,
    Tier3ActionPerformedPayload,
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


async def _touch_task(
    session: AsyncSession,
    task_id: str,
    envelope: EventEnvelope,
    extra_values: dict[str, object] | None = None,
) -> None:
    """UPDATE tasks SET last_event_id/updated_at (+ *extra_values*) WHERE id = *task_id*.

    Raises ``MaterializerError`` if the task row does not exist (out-of-order
    replay guard).  ``rowcount != 1`` covers the missing-row case (0), the
    dialect-cannot-determine case (-1), and the "should never happen with a
    PK WHERE" case (>1).  Single-row UPDATE on aiosqlite normally returns
    0 or 1; this is defence in depth.
    """
    values: dict[str, object] = {
        "last_event_id": envelope.event_id,
        "updated_at": envelope.emitted_at,
    }
    if extra_values:
        values.update(extra_values)
    stmt = update(Task).where(Task.id == task_id).values(**values)
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount != 1:
        raise MaterializerError(
            event_id=envelope.event_id,
            event_type=envelope.type,
            reason=f"task {task_id!r} not found — out-of-order replay?",
        )


# ---------------------------------------------------------------------------
# Task lifecycle handlers
# ---------------------------------------------------------------------------


async def handle_task_created(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Upsert a Task row with ``status="pending"``.

    Uses ``ON CONFLICT DO UPDATE`` so re-running the handler with the same
    envelope (e.g. bug-fix replay) produces the same final row state: status
    stays ``pending``; ``last_event_id`` and ``updated_at`` are refreshed.

    Story 3.9 AC-4: persist ``chat_id`` + ``reply_to_message_id`` from the
    payload when present (Telegram thread binding, FR13). Pre-3.9 v1.0.0
    payloads omit these fields → both default to ``None`` on the model
    so the SQL INSERT writes NULL, which is the correct back-compat
    behaviour. The ON CONFLICT branch refreshes the binding only when the
    new payload carries it (re-running the same envelope is a no-op).
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
            chat_id=payload.chat_id,
            reply_to_message_id=payload.reply_to_message_id,
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
    await _touch_task(session, payload.task_id, envelope, {"status": "planning"})


async def handle_task_plan_ready(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"plan_ready"``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskPlanReadyPayload)
    assert isinstance(payload, TaskPlanReadyPayload)
    await _touch_task(session, payload.task_id, envelope, {"status": "plan_ready"})


async def handle_task_execution_started(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"executing"`` and insert a Session row.

    The session row uses ``worker_kind="unknown"`` as a placeholder — later
    stories refine session rows with worker-specific events. AC-6 of this
    story does NOT require rich session-row population — just existence.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskExecutionStartedPayload)
    assert isinstance(payload, TaskExecutionStartedPayload)
    await _touch_task(session, payload.task_id, envelope, {"status": "executing"})
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


# ---------------------------------------------------------------------------
# Story 2.8 — Additional task-event handlers
# ---------------------------------------------------------------------------


async def handle_task_blocker_raised(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update last_event_id + updated_at for ``task.blocker_raised``.

    Raises ``MaterializerError`` if the task row does not exist (out-of-order
    replay). Status is intentionally NOT changed — lifecycle status transitions
    for blockers land in Stories 5.x / 6.x.
    """
    payload = _hydrate(envelope.payload, TaskBlockerRaisedPayload)
    assert isinstance(payload, TaskBlockerRaisedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_summary_emitted(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update last_event_id + updated_at for ``task.summary_emitted``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskSummaryEmittedPayload)
    assert isinstance(payload, TaskSummaryEmittedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_approval_requested(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update last_event_id + updated_at for ``task.approval_requested``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskApprovalRequestedPayload)
    assert isinstance(payload, TaskApprovalRequestedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_completed(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Set task status to ``"completed"`` for ``task.completed``.

    ``"completed"`` is the terminal status — no further lifecycle transitions
    are expected. Status changes for blockers / approvals / summaries land in
    Stories 5.x / 6.x; only the completion handler sets status here.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskCompletedPayload)
    assert isinstance(payload, TaskCompletedPayload)
    await _touch_task(session, payload.task_id, envelope, {"status": "completed"})


# ---------------------------------------------------------------------------
# Story 6.5 — Decision audit event handlers (AC-1 through AC-4)
# ---------------------------------------------------------------------------


async def handle_approval_granted(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``approval.granted``.

    Does NOT change task status — the worker lifecycle FSM (Story 6.7) owns
    the executing/planning transition. Premature status change would break
    the FSM contract.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, ApprovalGrantedPayload)
    assert isinstance(payload, ApprovalGrantedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_approval_rejected(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``approval.rejected``.

    Does NOT change task status — rejection is a decision, not a lifecycle
    transition. The task stays in its current status.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, ApprovalRejectedPayload)
    assert isinstance(payload, ApprovalRejectedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_stop_requested(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Set task status to ``"stopped"`` for ``task.stop_requested``.

    Stop is a terminal state — no FSM coupling needed. The operator's stop
    decision is the final word.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskStopRequestedPayload)
    assert isinstance(payload, TaskStopRequestedPayload)
    await _touch_task(session, payload.task_id, envelope, {"status": "stopped"})


async def handle_task_retry_requested(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``task.retry_requested``.

    Does NOT change task status — retry triggers re-planning via the worker
    lifecycle; the materializer does not own that transition.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskRetryRequestedPayload)
    assert isinstance(payload, TaskRetryRequestedPayload)
    await _touch_task(session, payload.task_id, envelope)


# ---------------------------------------------------------------------------
# Story 6.6 — Tier-3 audit event handlers (AC-1 through AC-3)
# ---------------------------------------------------------------------------


async def handle_tier3_action_attempted(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``tier3.action_attempted``.

    Does NOT change task status — the attempt is an audit fact, not a lifecycle
    transition.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, Tier3ActionAttemptedPayload)
    assert isinstance(payload, Tier3ActionAttemptedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_tier3_action_performed(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``tier3.action_performed``.

    Does NOT change task status — the performance is an audit fact; the worker
    lifecycle FSM owns downstream transitions.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, Tier3ActionPerformedPayload)
    assert isinstance(payload, Tier3ActionPerformedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_tier3_license_override(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``tier3.license_override``.

    Does NOT change task status — the override is an audit fact recorded
    alongside ``approval.granted``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, LicenseOverridePayload)
    assert isinstance(payload, LicenseOverridePayload)
    await _touch_task(session, payload.task_id, envelope)


def register_default_handlers(materializer: object) -> None:
    """Register all built-in task-event handlers onto *materializer*.

    Accepts ``object`` to avoid a circular import with ``materializer.py``
    at the type level; the runtime type is ``Materializer``.  Callers
    (``app/main.py``) pass a live ``Materializer`` instance.

    Raises:
        TypeError: If *materializer* is not a ``Materializer`` instance.
            This is a runtime check (not ``assert``) so the contract still
            fires under ``python -O`` where ``assert`` statements are
            stripped.  mypy enforces the static type via the call sites,
            but the runtime guard defends against duck-typed callers.
    """
    from registry_state.domain.materializer import Materializer

    if not isinstance(materializer, Materializer):
        raise TypeError(
            f"register_default_handlers expected Materializer, got {type(materializer).__name__}"
        )
    materializer.register_handler("task.created", handle_task_created)
    materializer.register_handler("task.planning.started", handle_task_planning_started)
    materializer.register_handler("task.plan.ready", handle_task_plan_ready)
    materializer.register_handler("task.execution.started", handle_task_execution_started)
    # Story 2.8 — 4 new handlers.
    materializer.register_handler("task.blocker_raised", handle_task_blocker_raised)
    materializer.register_handler("task.summary_emitted", handle_task_summary_emitted)
    materializer.register_handler("task.approval_requested", handle_task_approval_requested)
    materializer.register_handler("task.completed", handle_task_completed)
    # Story 6.5 — 4 decision audit event handlers.
    materializer.register_handler("approval.granted", handle_approval_granted)
    materializer.register_handler("approval.rejected", handle_approval_rejected)
    materializer.register_handler("task.stop_requested", handle_task_stop_requested)
    materializer.register_handler("task.retry_requested", handle_task_retry_requested)
    # Story 6.6 — 3 tier-3 audit event handlers.
    materializer.register_handler("tier3.action_attempted", handle_tier3_action_attempted)
    materializer.register_handler("tier3.action_performed", handle_tier3_action_performed)
    materializer.register_handler("tier3.license_override", handle_tier3_license_override)


__all__ = [
    "handle_approval_granted",
    "handle_approval_rejected",
    "handle_task_approval_requested",
    "handle_task_blocker_raised",
    "handle_task_completed",
    "handle_task_created",
    "handle_task_execution_started",
    "handle_task_plan_ready",
    "handle_task_planning_started",
    "handle_task_retry_requested",
    "handle_task_stop_requested",
    "handle_task_summary_emitted",
    "handle_tier3_action_attempted",
    "handle_tier3_action_performed",
    "handle_tier3_license_override",
    "register_default_handlers",
]
