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

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from registry_state.domain.task_fsm import TaskStateMachine

from events import Actor, new_event_id
from events.clock import Clock, SystemClock
from events.envelope import EventEnvelope
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from registry_state.adapters.event_log import EventLogWriter
from registry_state.domain.errors import InvalidStateTransition, MaterializerError
from registry_state.domain.event_types import (
    AgentReasoningBreadcrumbPayload,
    ApprovalGrantedPayload,
    ApprovalInboxOpenedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    FileEditedPayload,
    KeyRotatedPayload,
    LicenseOverridePayload,
    TaskApprovalRequestedPayload,
    TaskAssignedPayload,
    TaskAutoRetryPayload,
    TaskAutoStopPayload,
    TaskBlockerRaisedPayload,
    TaskBudgetExceededPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskLicenseFlaggedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskRetryRequestedPayload,
    TaskStateTransitionPayload,
    TaskStepCompletedPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
    Tier3ActionAttemptedPayload,
    Tier3ActionPerformedPayload,
)
from registry_state.schema import ApprovalInbox, KeyFingerprint, Task
from registry_state.schema import Session as SessionRow

_log = logging.getLogger("registry_state.domain.handlers")

_fsm = None  # Lazy singleton — avoids import-time instantiation overhead

# Phase 7 / Story 35.3: module-level audit writer, injected by subscriber startup.
# Set via ``_set_audit_writer()`` during ``run_subscriber`` init. When ``None``
# (standalone handler tests, materializer-only use), audit emission is a no-op
# so existing tests continue to pass without modification.
_audit_writer: EventLogWriter | None = None  # set by _set_audit_writer()


def _get_fsm() -> TaskStateMachine:
    """Lazy-initialised TaskStateMachine singleton (P6-I3)."""
    global _fsm
    if _fsm is None:
        from registry_state.domain.task_fsm import TaskStateMachine

        _fsm = TaskStateMachine()
    return _fsm


# ---------------------------------------------------------------------------
# Phase 7 / Story 35.3: audit event emission infrastructure
# ---------------------------------------------------------------------------


def _set_audit_writer(writer: EventLogWriter | None) -> None:
    """Inject (or clear) the audit-event writer.

    Called once during subscriber startup (``run_subscriber``) and cleared on
    shutdown.  Passing ``None`` disables audit emission — used by tests that
    exercise handlers without a live event log.
    """
    global _audit_writer
    _audit_writer = writer


def _get_audit_writer() -> EventLogWriter | None:
    """Return the current audit writer, or ``None`` if unset."""
    return _audit_writer


async def _emit_state_transition(
    *,
    task_id: str,
    from_state: str,
    to_state: str,
    trigger_event: str,
    worker_id: str,
    parent_envelope: EventEnvelope,
    clock: Clock,
) -> None:
    """Emit a ``task.state_transition`` audit event as a child of *parent_envelope*.

    Story 35.3 / FR108 / NFR-O16: every FSM state transition produces a
    ``task.state_transition`` event on the event spine, providing a full
    audit trail of task lifecycle changes.

    The child event's ``emitted_at_monotonic_ns`` is set to the parent's
    value + 1 to guarantee ordering within the same logical timestamp.

    When the audit writer is ``None`` (standalone tests, materializer-only
    use), this function is a silent no-op so existing callers are unaffected.

    Args:
        task_id: The task whose state changed.
        from_state: The FSM state before the transition.
        to_state: The FSM state after the transition.
        trigger_event: The event type that caused the transition
            (e.g. ``"task.planning.started"``).
        worker_id: The worker assigned to the task (empty string if none).
        parent_envelope: The envelope that triggered this transition.
            The audit event becomes a child of this envelope.
        clock: Clock used for timestamp and monotonic_ns generation.
    """
    writer = _get_audit_writer()
    if writer is None:
        return  # graceful degradation — no writer injected

    payload = TaskStateTransitionPayload(
        task_id=task_id,
        from_state=from_state,
        to_state=to_state,
        trigger_event=trigger_event,
        worker_id=worker_id,
        timestamp=parent_envelope.emitted_at.isoformat(),
    )
    audit_envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="task.state_transition",
        schema_version="1.1.0",
        emitted_at=parent_envelope.emitted_at,
        emitted_at_monotonic_ns=parent_envelope.emitted_at_monotonic_ns + 1,
        actor=Actor(kind="system", id="audit-trail"),
        payload=payload,
        trace_id=parent_envelope.trace_id,
        request_id=parent_envelope.request_id,
        parent_event_id=parent_envelope.event_id,
    )
    await writer.append(audit_envelope)


async def _audit_transition(
    session: AsyncSession,
    task_id: str,
    to_state: str,
    trigger_event: str,
    parent_envelope: EventEnvelope,
) -> None:
    """Emit a ``task.state_transition`` audit event for a state change.

    Convenience wrapper that reads ``from_state`` and ``worker_id`` from the
    current task row and delegates to :func:`_emit_state_transition`.
    No-op when the audit writer is not injected (tests, standalone use).
    """
    writer = _get_audit_writer()
    if writer is None:
        return
    result = await session.execute(select(Task.status, Task.worker_id).where(Task.id == task_id))
    row = result.one_or_none()
    if row is None:
        return  # task doesn't exist yet — _touch_task will raise
    from_state: str = row[0] or "unknown"
    worker_id: str = row[1] or ""
    await _emit_state_transition(
        task_id=task_id,
        from_state=from_state,
        to_state=to_state,
        trigger_event=trigger_event,
        worker_id=worker_id,
        parent_envelope=parent_envelope,
        clock=SystemClock(),
    )


async def _validate_fsm_transition(
    session: AsyncSession,
    task_id: str,
    target_status: str,
    event_type: str,
) -> bool:
    """Validate a status transition via the FSM (P6-I3).

    Fetches the current task status from the DB and calls
    ``TaskStateMachine.transition()``.  Returns ``True`` if the transition
    is valid, ``False`` if ``InvalidStateTransition`` is raised.

    The FSM is a guard, NOT a gate — callers MUST still apply the status
    change even when this returns ``False``.  Events from crash-recovery
    replay may arrive out-of-order; the subscriber must never crash due
    to an FSM violation.  A warning is logged on invalid transitions for
    operational visibility.
    """
    result = await session.execute(select(Task.status).where(Task.id == task_id))
    current_status = result.scalar_one_or_none()
    if current_status is None:
        # Task row doesn't exist — _touch_task will raise MaterializerError
        # which is the correct out-of-order replay guard. No FSM check needed.
        return True
    fsm = _get_fsm()
    try:
        fsm.transition(current_status, target_status)
    except InvalidStateTransition as exc:
        _log.warning(
            "FSM violation for task %s on event %s: %s (applying anyway — replay resilience)",
            task_id,
            event_type,
            exc,
        )
        return False
    return True


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


async def _task_id_for_session(
    session: AsyncSession,
    session_id: str,
) -> str | None:
    """Look up the ``task_id`` for a session row. Returns ``None`` if not found."""
    result = await session.execute(select(SessionRow.task_id).where(SessionRow.id == session_id))
    return result.scalar_one_or_none()


async def _close_active_session_for_task(
    session: AsyncSession,
    task_id: str,
    ended_at: datetime,
) -> None:
    """Close ALL active/idle sessions for *task_id* (Story 7.5.2 / FR27).

    Uses a bulk UPDATE to close every session matching
    ``task_id`` + ``status IN ("active", "idle", "reconnecting")`` in one statement.
    No-op if no matching sessions exist (replay safety).
    """
    stmt = (
        update(SessionRow)
        .where(
            SessionRow.task_id == task_id,
            SessionRow.status.in_(["active", "idle", "reconnecting"]),
        )
        .values(status="closed", ended_at=ended_at, worktree_path=None)
    )
    await session.execute(stmt)


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

    Story 12.4 AC4: persist ``budget_token_limit`` + ``budget_action`` from
    the additive 1.2.0 payload (per-task budget policy, FR68a). Pre-12.4
    payloads omit both → they default to ``None`` (NULL = "inherit the .env
    default"), the correct back-compat behaviour.
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
            hint=payload.hint,
            budget_token_limit=payload.budget_token_limit,
            budget_action=payload.budget_action,
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
    await _validate_fsm_transition(session, payload.task_id, "planning", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "planning", envelope.type, envelope)
    await _touch_task(session, payload.task_id, envelope, {"status": "planning", "hint": None})


async def handle_task_plan_ready(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"plan_ready"`` and store plan step metadata.

    Story 7.1: persists ``total_steps`` from ``payload.estimated_steps`` and
    resets ``current_step`` to 0 so the GET handler can report progress.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskPlanReadyPayload)
    assert isinstance(payload, TaskPlanReadyPayload)
    await _validate_fsm_transition(session, payload.task_id, "plan_ready", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "plan_ready", envelope.type, envelope)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        {
            "status": "plan_ready",
            "total_steps": payload.estimated_steps,
            "current_step": 0,
        },
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
    await _validate_fsm_transition(session, payload.task_id, "executing", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "executing", envelope.type, envelope)
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
    """Transition task to ``blocked`` and persist blocker reason (FR27 / Story 7.7).

    The worker-wrapper retains the worktree lock through the blocked window.
    The orchestrator surfaces ``blocked`` status in ``/status`` via the session
    query, which checks for active sessions with ``worktree_path``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskBlockerRaisedPayload)
    assert isinstance(payload, TaskBlockerRaisedPayload)
    if len(payload.reason) > 64:
        _log.warning(
            "blocker_reason truncated from %d to 64 chars for task %s",
            len(payload.reason),
            payload.task_id,
        )
    await _validate_fsm_transition(session, payload.task_id, "blocked", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "blocked", envelope.type, envelope)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        {
            "status": "blocked",
            "blocker_reason": payload.reason[:64],
        },
    )


async def handle_task_summary_emitted(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update last_event_id + updated_at for ``task.summary_emitted``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskSummaryEmittedPayload)
    assert isinstance(payload, TaskSummaryEmittedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_assigned(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Stamp ``worker_id`` on the task row for ``task.assigned`` (Story 32.3).

    Metadata-only handler — does NOT change task status. The FSM state stays
    unchanged (typically ``pending``); only the ``worker_id`` column is updated
    to record which worker claimed this task.

    ADR-0019 D2: worker_id is stamped on claimed tasks for observability
    (metrics, audit trails, crash detection).
    """
    payload = _hydrate(envelope.payload, TaskAssignedPayload)
    assert isinstance(payload, TaskAssignedPayload)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        {"worker_id": payload.worker_id},
    )


async def handle_task_state_transition(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Materialize ``task.state_transition`` audit event (Story 35.5 / FR108).

    Metadata-only handler — refreshes ``last_event_id`` and ``updated_at`` on
    the task row but does NOT change task status. The transition already
    happened in the triggering handler (e.g. ``handle_task_planning_started``);
    this handler exists solely so the audit event is queryable via the events
    table and the materializer cursor advances past it.
    """
    payload = _hydrate(envelope.payload, TaskStateTransitionPayload)
    assert isinstance(payload, TaskStateTransitionPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_approval_requested(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update last_event_id + updated_at for ``task.approval_requested``.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskApprovalRequestedPayload)
    assert isinstance(payload, TaskApprovalRequestedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_completed(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Set task status to ``"completed"`` and close ALL active/idle sessions.

    ``"completed"`` is the terminal status — no further lifecycle transitions
    are expected. Sessions are closed to release worktree locks in the
    materialized state.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskCompletedPayload)
    assert isinstance(payload, TaskCompletedPayload)
    await _validate_fsm_transition(session, payload.task_id, "completed", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "completed", envelope.type, envelope)
    await _touch_task(session, payload.task_id, envelope, {"status": "completed"})
    await _close_active_session_for_task(session, payload.task_id, envelope.emitted_at)


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
    """Set task status to ``"stopped"`` and close ALL active/idle sessions (FR27 / Story 7.7).

    Stop is a terminal state — no FSM coupling needed. The operator's stop
    decision is the final word. The worktree lock is released by closing the
    session row.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskStopRequestedPayload)
    assert isinstance(payload, TaskStopRequestedPayload)
    await _validate_fsm_transition(session, payload.task_id, "stopped", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "stopped", envelope.type, envelope)
    await _touch_task(session, payload.task_id, envelope, {"status": "stopped"})
    await _close_active_session_for_task(session, payload.task_id, envelope.emitted_at)


async def handle_task_retry_requested(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Persist the retry hint and transition task to ``pending`` (FR7 / Story 7.6).

    The orchestrator-adapter picks up ``pending`` tasks for re-planning.
    The hint is injected into the next planning prompt via the task-registry
    MCP resource.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskRetryRequestedPayload)
    assert isinstance(payload, TaskRetryRequestedPayload)
    await _validate_fsm_transition(session, payload.task_id, "pending", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "pending", envelope.type, envelope)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        extra_values={
            "status": "pending",
            "hint": payload.hint,
            "blocker_reason": None,
            "retry_count": 0,  # manual retry resets the counter
        },
    )


# ---------------------------------------------------------------------------
# Story 38.4 — automated recovery handlers
# ---------------------------------------------------------------------------


async def handle_task_auto_retry(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Automated recovery: transition task back to ``pending`` (Story 38.4).

    Mirrors ``handle_task_retry_requested`` but without an operator hint —
    the recovery loop requeues the task for fresh planning. The FSM
    ``failed`` → ``pending`` transition is validated.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskAutoRetryPayload)
    assert isinstance(payload, TaskAutoRetryPayload)
    await _validate_fsm_transition(session, payload.task_id, "pending", envelope.type)
    await _audit_transition(session, payload.task_id, "pending", envelope.type, envelope)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        extra_values={
            "status": "pending",
            "blocker_reason": None,
            "retry_count": payload.retry_count,
        },
    )


async def handle_task_auto_stop(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Automated recovery: transition task to ``stopped`` (Story 38.4).

    Mirrors ``handle_task_stop_requested`` — stop is terminal, closes
    active sessions. Triggered when retry budget is exhausted.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskAutoStopPayload)
    assert isinstance(payload, TaskAutoStopPayload)
    await _validate_fsm_transition(session, payload.task_id, "stopped", envelope.type)
    await _audit_transition(session, payload.task_id, "stopped", envelope.type, envelope)
    await _touch_task(session, payload.task_id, envelope, {"status": "stopped"})
    await _close_active_session_for_task(session, payload.task_id, envelope.emitted_at)


# ---------------------------------------------------------------------------
# Story 6.6 — Tier-3 audit event materializer handlers (AC-1 through AC-3).
# ``tier3.action_attempted`` was defined in Story 6.2 and emitted in Story 6.5;
# this story adds the materializer handler belatedly.
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


async def handle_task_license_flagged(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``updated_at`` + ``last_event_id`` for ``task.license_flagged``.

    Does NOT change task status — the flag is an observation that the
    approval gate consults before allowing an approve action.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskLicenseFlaggedPayload)
    assert isinstance(payload, TaskLicenseFlaggedPayload)
    await _touch_task(session, payload.task_id, envelope)


async def handle_task_budget_exceeded(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Transition task to ``blocked`` with ``blocker_reason="budget_exceeded"``.

    Story 6.11 AC-1 / FR44: when the per-task token budget is exceeded the
    task must halt and wait for the operator to approve a budget extension.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskBudgetExceededPayload)
    assert isinstance(payload, TaskBudgetExceededPayload)
    await _validate_fsm_transition(session, payload.task_id, "blocked", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "blocked", envelope.type, envelope)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        {
            "status": "blocked",
            "blocker_reason": "budget_exceeded",
        },
    )


async def handle_tier3_budget_override(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Resume a budget-blocked task after operator override.

    Story 6.11 AC-2: the operator's ``--override budget`` approval emits a
    ``tier3.budget_override`` audit event; this handler transitions the task
    back to ``executing`` so the worker can continue.

    Only transitions tasks currently in ``blocked`` with
    ``blocker_reason="budget_exceeded"``. Other states are left unchanged,
    making this handler replay-safe.

    Story 12.3c AC4 (FR68 cross-restart durability): the override's
    ``new_limit`` is ALSO persisted to ``Task.budget_token_limit`` on BOTH the
    primary blocked→executing transition AND the replay-safe touch fallback.
    registry-state is the SOLE ``state.sqlite3`` writer (FR26), so this is the
    single durable persistence point for the raised ceiling. On an
    orchestrator-adapter restart mid-task, ``_resolve_budget_limit`` reloads
    this raised value (highest precedence) instead of the original limit. The
    same handler is registered for the ``budget.override`` @1.1.0 alias so the
    persistence applies regardless of which event name carries the override.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, BudgetOverridePayload)
    assert isinstance(payload, BudgetOverridePayload)
    await _validate_fsm_transition(session, payload.task_id, "executing", envelope.type)
    # FSM-validated (P6-I3)
    await _audit_transition(session, payload.task_id, "executing", envelope.type, envelope)
    stmt = (
        update(Task)
        .where(
            Task.id == payload.task_id,
            Task.status == "blocked",
            Task.blocker_reason == "budget_exceeded",
        )
        .values(
            status="executing",
            blocker_reason=None,
            budget_token_limit=payload.new_limit,
            last_event_id=envelope.event_id,
            updated_at=envelope.emitted_at,
        )
    )
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount == 0:
        # Task is not in budget-blocked state — fall back to touch-only
        # (replay safety: re-running the same override is a no-op). Still
        # persist the raised ceiling (Story 12.3c AC4) so a restart picks it
        # up even when the FSM transition already happened.
        await _touch_task(
            session,
            payload.task_id,
            envelope,
            {"budget_token_limit": payload.new_limit},
        )


# ---------------------------------------------------------------------------
# Story 7.1 — Reconstituted-state handlers (step progress, agent actions)
# ---------------------------------------------------------------------------


async def handle_task_step_completed(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``current_step`` on the task row for ``task.step.completed``.

    Story 7.1: persists the step number from the payload so the GET handler
    can report ``current_step / total_steps`` progress.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskStepCompletedPayload)
    assert isinstance(payload, TaskStepCompletedPayload)
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        {
            "current_step": payload.step,
        },
    )


async def handle_file_edited(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update ``last_agent_action`` on the task row for ``file.edited``.

    Story 7.1: records a human-readable summary like ``"Edit path/to/file.py"``
    so the status response can show what the agent last did. The payload has
    ``session_id`` (not ``task_id``), so we resolve via the sessions table.

    No-op if the session row is missing (out-of-order replay safety).
    """
    payload = _hydrate(envelope.payload, FileEditedPayload)
    assert isinstance(payload, FileEditedPayload)
    task_id = await _task_id_for_session(session, payload.session_id)
    if task_id is None:
        _log.warning("session_id %s not found; skipping file.edited event", payload.session_id)
        return
    action = f"{payload.tool_name} {payload.file_path}"
    await _touch_task(
        session,
        task_id,
        envelope,
        {
            "last_agent_action": action[:2000],
        },
    )


async def handle_agent_reasoning_breadcrumb(
    session: AsyncSession,
    envelope: EventEnvelope,
) -> None:
    """Update ``last_agent_action`` for ``agent.reasoning.*`` breadcrumbs.

    Story 7.1: records the breadcrumb text (truncated) as the last agent
    action, but only when not suppressed (secret-hygiene redaction). The
    payload has ``session_id`` (not ``task_id``), resolved via sessions table.

    No-op if suppressed or the session row is missing.
    """
    payload = _hydrate(envelope.payload, AgentReasoningBreadcrumbPayload)
    assert isinstance(payload, AgentReasoningBreadcrumbPayload)
    if payload.suppressed:
        return
    task_id = await _task_id_for_session(session, payload.session_id)
    if task_id is None:
        _log.warning(
            "session_id %s not found; skipping %s event",
            payload.session_id,
            envelope.type,
        )
        return
    await _touch_task(
        session,
        task_id,
        envelope,
        {
            "last_agent_action": payload.text[:2000],
        },
    )


# ---------------------------------------------------------------------------
# Story 11.3 — approval.inbox_opened handler (FR63)
# ---------------------------------------------------------------------------


async def handle_approval_inbox_opened(session: AsyncSession, envelope: EventEnvelope) -> None:
    """UPSERT a row in ``approval_inbox`` for ``approval.inbox_opened`` events.

    Story 11.3 AC2 — operator opened (or re-opened) a pinned Forum-Topic
    inbox via the ``/approvals`` Telegram command. The materialized row
    drives outbound routing in clawhip-daemon: subsequent
    ``task.approval_requested`` events for tasks owned by this operator
    chat are delivered into ``inbox_thread_id`` instead of the
    originating task thread (FR63).

    UPSERT semantics keyed on ``operator_chat_id`` (the PK): re-running
    ``/approvals`` after archiving the previous inbox replaces the row
    in place with the new ``inbox_thread_id`` + refreshed
    ``opened_at`` / ``opened_by_actor_id``. Idempotent replay of the
    same envelope is a no-op (same values written).

    No FK touch and no Task row update — this event is operator-session-
    scoped, not task-scoped. The materializer's ``_extract_ids`` returns
    ``(None, None)`` for ``approval.inbox_opened`` (the ``approval.``
    prefix uses ``task_id`` extraction logic, but the payload has no
    ``task_id`` field — :meth:`dict.get` returns ``None`` and the event
    row's ``task_id`` column is NULL).
    """
    payload = _hydrate(envelope.payload, ApprovalInboxOpenedPayload)
    assert isinstance(payload, ApprovalInboxOpenedPayload)
    stmt = (
        sqlite_insert(ApprovalInbox)
        .values(
            operator_chat_id=payload.operator_chat_id,
            inbox_thread_id=payload.inbox_thread_id,
            opened_at=payload.opened_at,
            opened_by_actor_id=payload.opened_by_actor_id,
        )
        .on_conflict_do_update(
            index_elements=["operator_chat_id"],
            set_=dict(
                inbox_thread_id=payload.inbox_thread_id,
                opened_at=payload.opened_at,
                opened_by_actor_id=payload.opened_by_actor_id,
            ),
        )
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Story 11.5 — key.rotated handler (FR65a)
# ---------------------------------------------------------------------------


async def handle_key_rotated(session: AsyncSession, envelope: EventEnvelope) -> None:
    """UPSERT the singleton ``key_fingerprint`` row on ``key.rotated`` events.

    Story 11.5 AC3 — registry-api's rotation detector emits ``key.rotated``
    exactly once per actual rotation (Story 11.5 AC4); this handler
    materializes the event into the ``key_fingerprint`` singleton row keyed
    on ``id="current"``. After this row is committed, a subsequent registry-
    api restart will read the same fingerprint via the rotation detector
    and short-circuit the emit path (no-op).

    Idempotent replay: re-running the same envelope yields the same final
    row state (fingerprint, rotated_at, rotated_by_actor_id all overwritten
    with the payload's values).

    No FK touch and no Task row update — this event is operator-scoped
    (NOT task-scoped). The materializer's ``_extract_ids`` returns
    ``(None, None)`` for ``key.rotated`` (the type does not start with
    ``"task."`` / ``"approval."`` / ``"tier3."``, so the existing
    extraction logic already produces NULL for both columns; no change
    needed to ``_extract_ids`` for Story 11.5).
    """
    payload = _hydrate(envelope.payload, KeyRotatedPayload)
    assert isinstance(payload, KeyRotatedPayload)
    stmt = (
        sqlite_insert(KeyFingerprint)
        .values(
            id="current",
            fingerprint=payload.new_key_fingerprint,
            rotated_at=payload.rotated_at,
            rotated_by_actor_id=payload.actor_id,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_=dict(
                fingerprint=payload.new_key_fingerprint,
                rotated_at=payload.rotated_at,
                rotated_by_actor_id=payload.actor_id,
            ),
        )
    )
    await session.execute(stmt)


class MaterializerProtocol(Protocol):
    """Story 11.3 review P28: structural type for the materializer-registration surface.

    ``register_default_handlers`` previously accepted ``object`` to avoid a
    circular import; the Protocol expresses the actual structural shape
    (a single ``register_handler`` method) without re-introducing the
    cycle. The runtime ``isinstance(Materializer)`` check is preserved as
    the production safety net under ``python -O``.
    """

    def register_handler(
        self,
        event_type: str,
        handler: Callable[[AsyncSession, EventEnvelope], Awaitable[None]],
    ) -> None: ...


def register_default_handlers(materializer: MaterializerProtocol) -> None:
    """Register all built-in task-event handlers onto *materializer*.

    Accepts :class:`MaterializerProtocol` (Story 11.3 review P28) to keep
    the static interface as narrow as the function actually uses, while
    avoiding a circular import with ``materializer.py``. The runtime type
    is still ``Materializer``; the ``isinstance`` check below defends
    against duck-typed callers under ``python -O``.

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
    materializer.register_handler("task.assigned", handle_task_assigned)
    materializer.register_handler("task.state_transition", handle_task_state_transition)
    materializer.register_handler("task.completed", handle_task_completed)
    # Story 6.5 — 4 decision audit event handlers.
    materializer.register_handler("approval.granted", handle_approval_granted)
    materializer.register_handler("approval.rejected", handle_approval_rejected)
    materializer.register_handler("task.stop_requested", handle_task_stop_requested)
    materializer.register_handler("task.retry_requested", handle_task_retry_requested)
    # Story 38.4 — automated recovery handlers (NFR-R5 recovery action).
    materializer.register_handler("task.auto_retry", handle_task_auto_retry)
    materializer.register_handler("task.auto_stop", handle_task_auto_stop)
    # Story 6.6 — 3 tier-3 audit event handlers.
    materializer.register_handler("tier3.action_attempted", handle_tier3_action_attempted)
    materializer.register_handler("tier3.action_performed", handle_tier3_action_performed)
    materializer.register_handler("tier3.license_override", handle_tier3_license_override)
    # Story 6.10 — task.license_flagged handler (FR40).
    materializer.register_handler("task.license_flagged", handle_task_license_flagged)
    # Story 6.11 — budget-exceeded enforcement (FR44).
    materializer.register_handler("task.budget_exceeded", handle_task_budget_exceeded)
    materializer.register_handler("tier3.budget_override", handle_tier3_budget_override)
    # Story 12.3c (FR68) — the Epic-12 namespace alias ``budget.override``
    # (event_types.py:315) carries the SAME BudgetOverridePayload. Register it
    # to the same handler so the AC4 new_limit persistence applies regardless of
    # which event name a future emitter uses (the live decisions.py route still
    # emits tier3.budget_override for FR44 back-compat).
    materializer.register_handler("budget.override", handle_tier3_budget_override)
    # Story 7.1 — reconstituted-state handlers (step progress, agent actions).
    materializer.register_handler("task.step.completed", handle_task_step_completed)
    materializer.register_handler("file.edited", handle_file_edited)
    materializer.register_handler(
        "agent.reasoning.plan_drafted",
        handle_agent_reasoning_breadcrumb,
    )
    materializer.register_handler(
        "agent.reasoning.tool_call_rationale",
        handle_agent_reasoning_breadcrumb,
    )
    materializer.register_handler(
        "agent.reasoning.step_summary",
        handle_agent_reasoning_breadcrumb,
    )
    # Story 11.3 — approval.inbox_opened handler (FR63).
    materializer.register_handler("approval.inbox_opened", handle_approval_inbox_opened)
    # Story 11.5 — key.rotated handler (FR65a).
    materializer.register_handler("key.rotated", handle_key_rotated)


__all__ = [
    "_audit_transition",
    "_emit_state_transition",
    "_get_audit_writer",
    "_set_audit_writer",
    "handle_agent_reasoning_breadcrumb",
    "handle_approval_granted",
    "handle_approval_inbox_opened",
    "handle_approval_rejected",
    "handle_file_edited",
    "handle_key_rotated",
    "handle_task_approval_requested",
    "handle_task_assigned",
    "handle_task_auto_retry",
    "handle_task_auto_stop",
    "handle_task_blocker_raised",
    "handle_task_budget_exceeded",
    "handle_task_completed",
    "handle_task_created",
    "handle_task_execution_started",
    "handle_task_license_flagged",
    "handle_task_plan_ready",
    "handle_task_planning_started",
    "handle_task_retry_requested",
    "handle_task_step_completed",
    "handle_task_stop_requested",
    "handle_task_summary_emitted",
    "handle_task_state_transition",
    "handle_tier3_action_attempted",
    "handle_tier3_action_performed",
    "handle_tier3_budget_override",
    "handle_tier3_license_override",
    "register_default_handlers",
]
