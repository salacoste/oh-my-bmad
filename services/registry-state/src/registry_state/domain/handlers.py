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
from datetime import datetime
from typing import cast

from events.envelope import EventEnvelope
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (
    AgentReasoningBreadcrumbPayload,
    ApprovalGrantedPayload,
    ApprovalInboxOpenedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    FileEditedPayload,
    LicenseOverridePayload,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskBudgetExceededPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskLicenseFlaggedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskRetryRequestedPayload,
    TaskStepCompletedPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
    Tier3ActionAttemptedPayload,
    Tier3ActionPerformedPayload,
)
from registry_state.schema import ApprovalInbox, Task
from registry_state.schema import Session as SessionRow

_log = logging.getLogger("registry_state.domain.handlers")


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
    await _touch_task(session, payload.task_id, envelope, {"status": "planning", "hint": None})


async def handle_task_plan_ready(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Update task status to ``"plan_ready"`` and store plan step metadata.

    Story 7.1: persists ``total_steps`` from ``payload.estimated_steps`` and
    resets ``current_step`` to 0 so the GET handler can report progress.

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, TaskPlanReadyPayload)
    assert isinstance(payload, TaskPlanReadyPayload)
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
    await _touch_task(
        session,
        payload.task_id,
        envelope,
        extra_values={
            "status": "pending",
            "hint": payload.hint,
            "blocker_reason": None,
        },
    )


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

    Raises ``MaterializerError`` if the task row does not exist.
    """
    payload = _hydrate(envelope.payload, BudgetOverridePayload)
    assert isinstance(payload, BudgetOverridePayload)
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
            last_event_id=envelope.event_id,
            updated_at=envelope.emitted_at,
        )
    )
    result = cast(CursorResult[tuple[()]], await session.execute(stmt))
    if result.rowcount == 0:
        # Task is not in budget-blocked state — fall back to touch-only
        # (replay safety: re-running the same override is a no-op).
        await _touch_task(session, payload.task_id, envelope)


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
    # Story 6.10 — task.license_flagged handler (FR40).
    materializer.register_handler("task.license_flagged", handle_task_license_flagged)
    # Story 6.11 — budget-exceeded enforcement (FR44).
    materializer.register_handler("task.budget_exceeded", handle_task_budget_exceeded)
    materializer.register_handler("tier3.budget_override", handle_tier3_budget_override)
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


__all__ = [
    "handle_agent_reasoning_breadcrumb",
    "handle_approval_granted",
    "handle_approval_inbox_opened",
    "handle_approval_rejected",
    "handle_file_edited",
    "handle_task_approval_requested",
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
    "handle_tier3_action_attempted",
    "handle_tier3_action_performed",
    "handle_tier3_budget_override",
    "handle_tier3_license_override",
    "register_default_handlers",
]
