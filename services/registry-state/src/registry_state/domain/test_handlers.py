"""Tests for registry_state.domain.handlers — Story 2.5 AC-13 (6 tests).

Each test opens a fresh in-memory SQLite DB and exercises handler functions
directly via a live AsyncSession. Local fixtures ``fixed_clock`` +
``seeded_uuid7`` are inlined per the Story 2.4 convention (no new conftest).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from random import Random

import pytest
import pytest_asyncio
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TickingClock,
    new_event_id,
    new_session_id,
    new_task_id,
    new_uuid7,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (
    AgentReasoningBreadcrumbPayload,
    ApprovalGrantedPayload,
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
from registry_state.domain.handlers import (
    _close_active_session_for_task,
    handle_agent_reasoning_breadcrumb,
    handle_approval_granted,
    handle_approval_rejected,
    handle_file_edited,
    handle_task_approval_requested,
    handle_task_blocker_raised,
    handle_task_budget_exceeded,
    handle_task_completed,
    handle_task_created,
    handle_task_execution_started,
    handle_task_license_flagged,
    handle_task_plan_ready,
    handle_task_planning_started,
    handle_task_retry_requested,
    handle_task_step_completed,
    handle_task_stop_requested,
    handle_task_summary_emitted,
    handle_tier3_action_attempted,
    handle_tier3_action_performed,
    handle_tier3_budget_override,
    handle_tier3_license_override,
)
from registry_state.schema import Base, Task
from registry_state.schema import Session as SessionRow

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-handlers")


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register the 8 task event types before each test in this file.

    ``test_event_log.py`` has an autouse ``_clean_registry`` fixture that calls
    ``unregister_all()`` at teardown.  When pytest runs the full suite, that
    teardown fires AFTER our tests collect but BEFORE they execute (depending on
    ordering), leaving the registry empty.  Re-registering here (idempotent per
    Story 2.1's register() contract) ensures a clean known state for every test
    in this file regardless of suite order.

    F19: a session-scoped variant was considered, but the cross-file
    ``_clean_registry`` teardown forces a per-test re-registration to remain
    safe; we keep this autouse fixture, but tighten the docstring to make the
    "fixture-vs-tests coupling" explicit.  The 4 Story 2.8 types live behind a
    clear comment so removal of any payload class produces a single localized
    failure rather than a cryptic registry KeyError.
    """
    from events.schema_registry import register as _reg

    # Pre-Story-2.8 lifecycle types
    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
    _reg("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
    _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    # Story 2.8 types — only required by the four `test_task_*` and the
    # `test_story28_*` tests below.  Listed separately so a future split into
    # per-class fixtures stays mechanical.
    _reg("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
    _reg("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
    _reg("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)
    # Story 6.5 — decision audit event types.
    _reg("approval.granted", "1.0.0", ApprovalGrantedPayload)
    _reg("approval.rejected", "1.0.0", ApprovalRejectedPayload)
    _reg("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
    _reg("task.retry_requested", "1.0.0", TaskRetryRequestedPayload)
    # Story 6.6 — tier-3 audit event types.
    _reg("tier3.action_attempted", "1.0.0", Tier3ActionAttemptedPayload)
    _reg("tier3.action_performed", "1.0.0", Tier3ActionPerformedPayload)
    _reg("tier3.license_override", "1.0.0", LicenseOverridePayload)
    # Story 6.10 — license flag event type.
    _reg("task.license_flagged", "1.0.0", TaskLicenseFlaggedPayload)
    # Story 6.11 — budget enforcement event types.
    _reg("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    _reg("tier3.budget_override", "1.0.0", BudgetOverridePayload)
    # Story 7.1 — reconstituted-state event types.
    _reg("task.step.completed", "1.0.0", TaskStepCompletedPayload)
    _reg("file.edited", "1.0.0", FileEditedPayload)
    _reg("agent.reasoning.plan_drafted", "1.0.0", AgentReasoningBreadcrumbPayload)
    _reg("agent.reasoning.tool_call_rationale", "1.0.0", AgentReasoningBreadcrumbPayload)
    _reg("agent.reasoning.step_summary", "1.0.0", AgentReasoningBreadcrumbPayload)


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with schema created; auto-commits on clean exit.

    NullPool means every connection is independent — we must create schema and
    run tests on the SAME connection.  We achieve this by using a
    ``StaticPool`` so all ``connect()`` calls return the same underlying
    sqlite3 connection, then creating tables once before handing off the
    session.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = get_session(eng)
    async with sm() as session, session.begin():
        yield session
    await eng.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_created_envelope(
    mono_ns: int = 1_000_000,
    seed: int = 42,
    title: str | None = "Test",
) -> EventEnvelope:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=tid, title=title),
        request_id=new_uuid7(clock=clk, rng=rng),
    )


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_task_created_inserts_task_row_with_pending_status(
    db_session: AsyncSession,
) -> None:
    """handle_task_created inserts a Task row with status='pending'."""
    env = _make_created_envelope()
    await handle_task_created(db_session, env)
    assert isinstance(env.payload, TaskCreatedPayload)
    task = await db_session.get(Task, env.payload.task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.actor_kind == "system"
    assert task.title == "Test"
    assert task.last_event_id == env.event_id


@pytest.mark.asyncio
async def test_task_created_is_idempotent(db_session: AsyncSession) -> None:
    """handle_task_created called twice produces the same row state (ON CONFLICT DO UPDATE)."""
    env = _make_created_envelope()
    await handle_task_created(db_session, env)
    await handle_task_created(db_session, env)  # second call is idempotent
    assert isinstance(env.payload, TaskCreatedPayload)
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM tasks WHERE id = :tid"),
        {"tid": env.payload.task_id},
    )
    count = result.scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_task_planning_started_updates_status(db_session: AsyncSession) -> None:
    """handle_task_planning_started transitions task status pending → planning."""
    env_created = _make_created_envelope(mono_ns=1_000_000)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(99)
    clk = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    env_planning = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.planning.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanningStartedPayload(task_id=task_id),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_planning_started(db_session, env_planning)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "planning"
    assert task.last_event_id == env_planning.event_id


@pytest.mark.asyncio
async def test_task_planning_started_clears_hint(db_session: AsyncSession) -> None:
    """handle_task_planning_started clears the hint field."""
    rng = Random(77)
    clk = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    env_created = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=new_task_id(clock=clk, rng=rng), hint="focus on X"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.hint == "focus on X"

    clk2 = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    env_planning = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng),
        schema_version="1.0.0",
        type="task.planning.started",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanningStartedPayload(task_id=task_id),
        request_id=new_uuid7(clock=clk2, rng=rng),
    )
    await handle_task_planning_started(db_session, env_planning)
    await db_session.refresh(task)
    assert task.hint is None


@pytest.mark.asyncio
async def test_task_plan_ready_updates_status(db_session: AsyncSession) -> None:
    """handle_task_plan_ready transitions task status → plan_ready."""
    env_created = _make_created_envelope(mono_ns=1_000_000)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(33)
    clk = FrozenClock(mono_ns=3_000_000, now=FROZEN_EPOCH)
    env_pr = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanReadyPayload(task_id=task_id, plan_summary="Step 1"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_plan_ready(db_session, env_pr)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "plan_ready"
    assert task.total_steps == 0  # estimated_steps defaults to 0
    assert task.current_step == 0


@pytest.mark.asyncio
async def test_task_execution_started_updates_status_and_inserts_session_row(
    db_session: AsyncSession,
) -> None:
    """handle_task_execution_started: status → executing + new session row inserted."""
    env_created = _make_created_envelope(mono_ns=1_000_000)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng4 = Random(4)
    clk4 = FrozenClock(mono_ns=4_000_000, now=FROZEN_EPOCH)
    sid = new_session_id(clock=clk4, rng=rng4)

    env_exec = EventEnvelope.create(
        event_id=new_event_id(clock=clk4, rng=rng4),
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clk4.now(),
        emitted_at_monotonic_ns=clk4.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskExecutionStartedPayload(task_id=task_id, session_id=sid),
        request_id=new_uuid7(clock=clk4, rng=rng4),
    )
    await handle_task_execution_started(db_session, env_exec)

    task = await db_session.get(Task, task_id)
    sess = await db_session.get(SessionRow, sid)
    assert task is not None
    assert task.status == "executing"
    assert sess is not None
    assert sess.worker_kind == "unknown"
    assert sess.status == "active"
    assert sess.task_id == task_id


@pytest.mark.asyncio
async def test_handler_on_missing_task_raises_materializer_error(
    db_session: AsyncSession,
) -> None:
    """Handlers raise MaterializerError when the task row doesn't exist."""
    rng = Random(99)
    clk = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    missing_task_id = new_task_id(clock=clk, rng=rng)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.planning.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanningStartedPayload(task_id=missing_task_id),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError) as exc_info:
        await handle_task_planning_started(db_session, env)
    assert exc_info.value.event_type == "task.planning.started"
    assert missing_task_id in exc_info.value.reason


@pytest.mark.asyncio
async def test_plan_ready_on_missing_task_raises_materializer_error(
    db_session: AsyncSession,
) -> None:
    """``handle_task_plan_ready`` raises MaterializerError when the task row is missing.

    Defends the ``rowcount != 1`` guard added to handlers (was ``rowcount == 0``).
    Re-asserts the contract for the ``task.plan.ready`` handler so a regression
    in the rowcount check is caught even if the planning-started variant is
    refactored away.
    """
    rng = Random(123)
    clk = FrozenClock(mono_ns=3_000_000, now=FROZEN_EPOCH)
    missing_task_id = new_task_id(clock=clk, rng=rng)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanReadyPayload(task_id=missing_task_id, plan_summary="x"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError) as exc_info:
        await handle_task_plan_ready(db_session, env)
    assert exc_info.value.event_type == "task.plan.ready"
    assert missing_task_id in exc_info.value.reason


@pytest.mark.asyncio
async def test_execution_started_on_missing_task_raises_materializer_error(
    db_session: AsyncSession,
) -> None:
    """``handle_task_execution_started`` raises MaterializerError when the task is missing."""
    rng = Random(321)
    clk = FrozenClock(mono_ns=4_000_000, now=FROZEN_EPOCH)
    missing_task_id = new_task_id(clock=clk, rng=rng)
    sid = new_session_id(clock=clk, rng=rng)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskExecutionStartedPayload(task_id=missing_task_id, session_id=sid),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError) as exc_info:
        await handle_task_execution_started(db_session, env)
    assert exc_info.value.event_type == "task.execution.started"
    assert missing_task_id in exc_info.value.reason


# ===========================================================================
# Story 2.8 — 4 new handler tests
# ===========================================================================


@pytest.mark.asyncio
async def test_task_blocker_raised_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_blocker_raised transitions to blocked and sets blocker_reason (Story 7.7)."""
    env_created = _make_created_envelope(mono_ns=1_000_000)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(200)
    clk = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    env_blocker = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBlockerRaisedPayload(task_id=task_id, reason="CI red"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_blocker_raised(db_session, env_blocker)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_blocker.event_id
    assert task.status == "blocked"
    assert task.blocker_reason == "CI red"


@pytest.mark.asyncio
async def test_task_blocker_raised_truncates_long_reason(db_session: AsyncSession) -> None:
    """handle_task_blocker_raised truncates blocker_reason to 64 chars."""
    env_created = _make_created_envelope(mono_ns=1_000_000)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    long_reason = "x" * 200
    rng = Random(88)
    clk = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    env_blocker = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBlockerRaisedPayload(task_id=task_id, reason=long_reason),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_blocker_raised(db_session, env_blocker)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "blocked"
    assert task.blocker_reason == "x" * 64


@pytest.mark.asyncio
async def test_task_summary_emitted_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_summary_emitted updates last_event_id + updated_at."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=55)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(201)
    clk = FrozenClock(mono_ns=6_000_000, now=FROZEN_EPOCH)
    env_summary = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.summary_emitted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskSummaryEmittedPayload(task_id=task_id, summary="step 1 done"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_summary_emitted(db_session, env_summary)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_summary.event_id


@pytest.mark.asyncio
async def test_task_approval_requested_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_approval_requested updates last_event_id + updated_at."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=66)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(202)
    clk = FrozenClock(mono_ns=7_000_000, now=FROZEN_EPOCH)
    env_approval = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.approval_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskApprovalRequestedPayload(
            task_id=task_id, action="deploy", justification="ready"
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_approval_requested(db_session, env_approval)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_approval.event_id


@pytest.mark.asyncio
async def test_task_completed_sets_status_completed(
    db_session: AsyncSession,
) -> None:
    """handle_task_completed sets status='completed' — the only status change in Story 2.8."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=77)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(203)
    clk = FrozenClock(mono_ns=8_000_000, now=FROZEN_EPOCH)
    env_completed = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(
            task_id=task_id, summary="all done", pr_url="https://github.com/x/1"
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_completed(db_session, env_completed)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "completed"
    assert task.last_event_id == env_completed.event_id


@pytest.mark.asyncio
async def test_story28_handlers_raise_materializer_error_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """All 4 Story 2.8 handlers raise MaterializerError when task is missing."""
    rng = Random(999)
    clk = FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH)
    missing_id = new_task_id(clock=clk, rng=rng)

    # blocker_raised
    env_b = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBlockerRaisedPayload(task_id=missing_id, reason="oops"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_blocker_raised(db_session, env_b)

    # summary_emitted
    env_s = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.summary_emitted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskSummaryEmittedPayload(task_id=missing_id, summary="x"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_summary_emitted(db_session, env_s)

    # approval_requested
    env_a = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.approval_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskApprovalRequestedPayload(task_id=missing_id, action="x", justification="y"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_approval_requested(db_session, env_a)

    # completed
    env_c = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(task_id=missing_id, summary="x"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_completed(db_session, env_c)


# ===========================================================================
# Story 6.5 — Decision audit event handler tests (AC-1 through AC-5)
# ===========================================================================


@pytest.mark.asyncio
async def test_approval_granted_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_approval_granted updates last_event_id + updated_at; status unchanged (AC-1)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=101)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(301)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env_grant = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.granted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalGrantedPayload(
            task_id=task_id,
            decision_id="d-aaa",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_approval_granted(db_session, env_grant)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_grant.event_id
    assert task.updated_at == env_grant.emitted_at
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_approval_rejected_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_approval_rejected updates last_event_id + updated_at; status unchanged (AC-2)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=102)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(302)
    clk = FrozenClock(mono_ns=11_000_000, now=FROZEN_EPOCH)
    env_reject = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.rejected",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalRejectedPayload(
            task_id=task_id,
            decision_id="d-bbb",
            actor_id="op-1",
            reason="bad plan",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_approval_rejected(db_session, env_reject)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_reject.event_id
    assert task.updated_at == env_reject.emitted_at
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_task_stop_requested_sets_status_stopped(
    db_session: AsyncSession,
) -> None:
    """handle_task_stop_requested sets status='stopped' (AC-3)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=103)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(303)
    clk = FrozenClock(mono_ns=12_000_000, now=FROZEN_EPOCH)
    env_stop = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(
            task_id=task_id,
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_stop_requested(db_session, env_stop)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "stopped"
    assert task.last_event_id == env_stop.event_id
    assert task.updated_at == env_stop.emitted_at


@pytest.mark.asyncio
async def test_task_stop_requested_closes_active_session(
    db_session: AsyncSession,
) -> None:
    """handle_task_stop_requested closes the active session (Story 7.7 AC-2)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=501)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # Seed an active session with worktree_path.
    rng_sid = Random(502)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    session_row = SessionRow(
        id=new_session_id(clock=clk_sid, rng=rng_sid),
        task_id=task_id,
        worker_kind="claude-code",
        status="active",
        started_at=FROZEN_EPOCH,
        worktree_path="/tmp/worktree-abc",
    )
    db_session.add(session_row)
    await db_session.flush()

    rng = Random(503)
    clk = FrozenClock(mono_ns=12_000_000, now=FROZEN_EPOCH)
    env_stop = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(task_id=task_id, actor_id="op-1"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_stop_requested(db_session, env_stop)
    await db_session.refresh(session_row)
    assert session_row.status == "closed"
    assert session_row.worktree_path is None
    assert session_row.ended_at == env_stop.emitted_at


@pytest.mark.asyncio
async def test_task_completed_closes_active_session(
    db_session: AsyncSession,
) -> None:
    """handle_task_completed closes the active session (Story 7.7 data hygiene)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=504)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng_sid = Random(505)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    session_row = SessionRow(
        id=new_session_id(clock=clk_sid, rng=rng_sid),
        task_id=task_id,
        worker_kind="claude-code",
        status="active",
        started_at=FROZEN_EPOCH,
        worktree_path="/tmp/worktree-def",
    )
    db_session.add(session_row)
    await db_session.flush()

    rng = Random(506)
    clk = FrozenClock(mono_ns=14_000_000, now=FROZEN_EPOCH)
    env_complete = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(task_id=task_id, summary="done"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_completed(db_session, env_complete)
    await db_session.refresh(session_row)
    assert session_row.status == "closed"
    assert session_row.worktree_path is None


@pytest.mark.asyncio
async def test_task_retry_requested_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_retry_requested transitions to pending and persists hint (Story 7.6 AC-1)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=104)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(304)
    clk = FrozenClock(mono_ns=13_000_000, now=FROZEN_EPOCH)
    env_retry = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-ccc",
            actor_id="op-1",
            hint="focus on X",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_retry_requested(db_session, env_retry)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_retry.event_id
    assert task.updated_at == env_retry.emitted_at
    assert task.status == "pending"
    assert task.hint == "focus on X"


@pytest.mark.asyncio
async def test_task_retry_without_hint_clears_existing_hint(
    db_session: AsyncSession,
) -> None:
    """Retrying without hint clears a previously persisted hint (Story 7.6 AC-3)."""
    rng = Random(401)
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=401)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # First retry with hint.
    clk2 = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    env_retry1 = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-aaa",
            actor_id="op-1",
            hint="first hint",
        ),
        request_id=new_uuid7(clock=clk2, rng=rng),
    )
    await handle_task_retry_requested(db_session, env_retry1)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.hint == "first hint"

    # Second retry without hint — should clear.
    clk3 = FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH)
    env_retry2 = EventEnvelope.create(
        event_id=new_event_id(clock=clk3, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk3.now(),
        emitted_at_monotonic_ns=clk3.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-bbb",
            actor_id="op-1",
            hint=None,
        ),
        request_id=new_uuid7(clock=clk3, rng=rng),
    )
    await handle_task_retry_requested(db_session, env_retry2)
    await db_session.refresh(task)
    assert task.hint is None
    assert task.status == "pending"
    assert task.blocker_reason is None


@pytest.mark.asyncio
async def test_task_retry_requested_transitions_to_pending(
    db_session: AsyncSession,
) -> None:
    """Retry transitions task from blocked to pending (Story 7.6 AC-1)."""
    rng = Random(402)
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=402)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # Manually set status to blocked (simulating prior blocker_raised event).
    task = await db_session.get(Task, task_id)
    assert task is not None
    task.status = "blocked"
    await db_session.flush()

    clk2 = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    env_retry = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-ddd",
            actor_id="op-1",
            hint="unblock it",
        ),
        request_id=new_uuid7(clock=clk2, rng=rng),
    )
    await handle_task_retry_requested(db_session, env_retry)
    await db_session.refresh(task)
    assert task.status == "pending"
    assert task.hint == "unblock it"


@pytest.mark.asyncio
async def test_task_retry_from_failed_transitions_to_pending(
    db_session: AsyncSession,
) -> None:
    """Retry transitions task from failed to pending (lifecycle allows retry from failed)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=403)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # Manually set status to failed.
    task = await db_session.get(Task, task_id)
    assert task is not None
    task.status = "failed"
    await db_session.flush()

    clk2 = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    env_retry = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=Random(403)),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-eee",
            actor_id="op-1",
            hint="try again",
        ),
        request_id=new_uuid7(clock=clk2, rng=Random(403)),
    )
    await handle_task_retry_requested(db_session, env_retry)
    await db_session.refresh(task)
    assert task.status == "pending"
    assert task.hint == "try again"
    assert task.blocker_reason is None


@pytest.mark.asyncio
async def test_story65_handlers_raise_materializer_error_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """All 4 Story 6.5 handlers raise MaterializerError when task is missing."""
    rng = Random(9999)
    clk = FrozenClock(mono_ns=20_000_000, now=FROZEN_EPOCH)
    missing_id = new_task_id(clock=clk, rng=rng)

    env_grant = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.granted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalGrantedPayload(
            task_id=missing_id,
            decision_id="d-x1",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_approval_granted(db_session, env_grant)

    env_reject = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.rejected",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalRejectedPayload(
            task_id=missing_id,
            decision_id="d-x2",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_approval_rejected(db_session, env_reject)

    env_stop = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(
            task_id=missing_id,
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_stop_requested(db_session, env_stop)

    env_retry = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=missing_id,
            decision_id="d-x3",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_retry_requested(db_session, env_retry)


@pytest.mark.asyncio
async def test_story65_audit_fields_in_envelope(db_session: AsyncSession) -> None:
    """AC-5: Envelope carries NFR-S3 audit fields for all 4 decision event types."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=105)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id
    rng = Random(305)
    clk = FrozenClock(mono_ns=14_000_000, now=FROZEN_EPOCH)

    # approval.granted — has decision_id
    env_grant = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.granted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalGrantedPayload(
            task_id=task_id,
            decision_id="d-a1",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert env_grant.actor.kind == "system"
    assert env_grant.emitted_at is not None
    assert env_grant.request_id is not None
    assert isinstance(env_grant.payload, ApprovalGrantedPayload)
    assert env_grant.payload.task_id == task_id
    assert env_grant.payload.decision_id == "d-a1"

    # approval.rejected — has decision_id + reason
    env_reject = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="approval.rejected",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalRejectedPayload(
            task_id=task_id,
            decision_id="d-a2",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env_reject.payload, ApprovalRejectedPayload)
    assert env_reject.payload.task_id == task_id
    assert env_reject.payload.decision_id == "d-a2"

    # task.stop_requested — has actor_id (no decision_id)
    env_stop = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(
            task_id=task_id,
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env_stop.payload, TaskStopRequestedPayload)
    assert env_stop.payload.task_id == task_id
    assert env_stop.payload.actor_id == "op-1"

    # task.retry_requested — has decision_id + hint
    env_retry = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.retry_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id="d-a3",
            actor_id="op-1",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env_retry.payload, TaskRetryRequestedPayload)
    assert env_retry.payload.task_id == task_id
    assert env_retry.payload.decision_id == "d-a3"


@pytest.mark.asyncio
async def test_task_stop_requested_is_idempotent(db_session: AsyncSession) -> None:
    """handle_task_stop_requested is idempotent on double-call."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=106)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng1 = Random(401)
    clk1 = FrozenClock(mono_ns=15_000_000, now=FROZEN_EPOCH)
    env_stop1 = EventEnvelope.create(
        event_id=new_event_id(clock=clk1, rng=rng1),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk1.now(),
        emitted_at_monotonic_ns=clk1.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(task_id=task_id, actor_id="op-1"),
        request_id=new_uuid7(clock=clk1, rng=rng1),
    )
    await handle_task_stop_requested(db_session, env_stop1)

    rng2 = Random(402)
    clk2 = FrozenClock(mono_ns=16_000_000, now=FROZEN_EPOCH)
    env_stop2 = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng2),
        schema_version="1.0.0",
        type="task.stop_requested",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStopRequestedPayload(task_id=task_id, actor_id="op-1"),
        request_id=new_uuid7(clock=clk2, rng=rng2),
    )
    await handle_task_stop_requested(db_session, env_stop2)

    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "stopped"
    assert task.last_event_id == env_stop2.event_id


# ===========================================================================
# Story 6.6 — Tier-3 audit event handler tests (AC-1 through AC-6)
# ===========================================================================


@pytest.mark.asyncio
async def test_tier3_action_attempted_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_action_attempted updates last_event_id + updated_at; status unchanged (AC-1)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=201)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(501)
    clk = FrozenClock(mono_ns=30_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_attempted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionAttemptedPayload(
            action="git_push",
            task_id=task_id,
            accepted=True,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_tier3_action_attempted(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.updated_at == env.emitted_at
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_tier3_action_performed_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_action_performed updates last_event_id + updated_at; status unchanged (AC-2)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=202)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(502)
    clk = FrozenClock(mono_ns=31_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_performed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionPerformedPayload(
            task_id=task_id,
            action="git_push",
            accepted=True,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_tier3_action_performed(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.updated_at == env.emitted_at
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_tier3_license_override_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_license_override updates last_event_id + updated_at; status unchanged (AC-3)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=203)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(503)
    clk = FrozenClock(mono_ns=32_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.license_override",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=LicenseOverridePayload(
            task_id=task_id,
            decision_id="d-lic",
            actor_id="op-1",
            reason="operator_license_override",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_tier3_license_override(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.updated_at == env.emitted_at
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_story66_handlers_raise_materializer_error_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """All 3 Story 6.6 handlers raise MaterializerError when task is missing."""
    rng = Random(9998)
    clk = FrozenClock(mono_ns=40_000_000, now=FROZEN_EPOCH)
    missing_id = new_task_id(clock=clk, rng=rng)

    env_attempt = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_attempted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionAttemptedPayload(
            action="git_push",
            task_id=missing_id,
            accepted=False,
            reason="denied",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_tier3_action_attempted(db_session, env_attempt)

    env_performed = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_performed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionPerformedPayload(
            task_id=missing_id,
            action="git_push",
            accepted=True,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_tier3_action_performed(db_session, env_performed)

    env_override = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.license_override",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=LicenseOverridePayload(
            task_id=missing_id,
            decision_id="d-x",
            actor_id="op-1",
            reason="test",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_tier3_license_override(db_session, env_override)


@pytest.mark.asyncio
async def test_story66_audit_fields_in_envelope(db_session: AsyncSession) -> None:
    """AC-6: Envelopes carry NFR-S3 audit fields for all 3 tier-3 event types."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=204)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id
    rng = Random(504)
    clk = FrozenClock(mono_ns=33_000_000, now=FROZEN_EPOCH)

    # tier3.action_attempted
    env_a = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_attempted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionAttemptedPayload(
            action="git_push",
            task_id=task_id,
            accepted=True,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert env_a.actor.kind == "system"
    assert env_a.actor.id is not None
    assert env_a.emitted_at is not None
    assert env_a.request_id is not None
    assert isinstance(env_a.payload, Tier3ActionAttemptedPayload)
    assert env_a.payload.task_id == task_id

    # tier3.action_performed
    env_p = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_performed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionPerformedPayload(
            task_id=task_id,
            action="git_push",
            accepted=True,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env_p.payload, Tier3ActionPerformedPayload)
    assert env_p.payload.task_id == task_id

    # tier3.license_override
    env_l = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.license_override",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=LicenseOverridePayload(
            task_id=task_id,
            decision_id="d-a",
            actor_id="op-1",
            reason="override",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env_l.payload, LicenseOverridePayload)
    assert env_l.payload.task_id == task_id


@pytest.mark.asyncio
async def test_tier3_action_performed_with_optional_fields(
    db_session: AsyncSession,
) -> None:
    """Tier3ActionPerformedPayload round-trips with approval_event_id and reason."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=205)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(505)
    clk = FrozenClock(mono_ns=34_000_000, now=FROZEN_EPOCH)
    approval_id = new_event_id(clock=clk, rng=rng)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_performed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionPerformedPayload(
            task_id=task_id,
            action="git_push",
            accepted=True,
            approval_event_id=approval_id,
            reason="manual operator approval",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    assert isinstance(env.payload, Tier3ActionPerformedPayload)
    assert env.payload.approval_event_id == approval_id
    assert env.payload.reason == "manual operator approval"
    await handle_tier3_action_performed(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_tier3_action_attempted_accepted_false_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_action_attempted with accepted=False still updates last_event_id."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=206)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(506)
    clk = FrozenClock(mono_ns=35_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.action_attempted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=Tier3ActionAttemptedPayload(
            action="git_push",
            task_id=task_id,
            accepted=False,
            reason="tier-3 capability denied",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_tier3_action_attempted(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.updated_at == env.emitted_at
    assert task.status == "pending"


# ---------------------------------------------------------------------------
# Story 6.11 — budget-exceeded enforcement handler tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exceeded_sets_blocked_status(
    db_session: AsyncSession,
) -> None:
    """handle_task_budget_exceeded transitions task to blocked."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=301)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(601)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.budget_exceeded",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBudgetExceededPayload(
            task_id=task_id,
            token_limit=50_000,
            tokens_used=52_000,
            step=3,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_budget_exceeded(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "blocked"
    assert task.blocker_reason == "budget_exceeded"
    assert task.last_event_id == env.event_id


@pytest.mark.asyncio
async def test_budget_exceeded_raises_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """handle_task_budget_exceeded raises MaterializerError for missing task."""
    rng = Random(602)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.budget_exceeded",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBudgetExceededPayload(
            task_id="t-00000000-0000-7000-8000-000000009999",
            token_limit=50_000,
            tokens_used=52_000,
            step=1,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_budget_exceeded(db_session, env)


@pytest.mark.asyncio
async def test_budget_override_resumes_to_executing(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_budget_override transitions blocked task back to executing."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=303)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # First block it
    rng_block = Random(603)
    clk_block = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env_block = EventEnvelope.create(
        event_id=new_event_id(clock=clk_block, rng=rng_block),
        schema_version="1.0.0",
        type="task.budget_exceeded",
        emitted_at=clk_block.now(),
        emitted_at_monotonic_ns=clk_block.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBudgetExceededPayload(
            task_id=task_id,
            token_limit=50_000,
            tokens_used=52_000,
            step=3,
        ),
        request_id=new_uuid7(clock=clk_block, rng=rng_block),
    )
    await handle_task_budget_exceeded(db_session, env_block)

    # Now override
    rng_ov = Random(604)
    clk_ov = FrozenClock(mono_ns=20_000_000, now=FROZEN_EPOCH)
    env_ov = EventEnvelope.create(
        event_id=new_event_id(clock=clk_ov, rng=rng_ov),
        schema_version="1.0.0",
        type="tier3.budget_override",
        emitted_at=clk_ov.now(),
        emitted_at_monotonic_ns=clk_ov.monotonic_ns(),
        actor=_ACTOR,
        payload=BudgetOverridePayload(
            task_id=task_id,
            decision_id="d-budget-override-0000000000",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=100_000,
        ),
        request_id=new_uuid7(clock=clk_ov, rng=rng_ov),
    )
    await handle_tier3_budget_override(db_session, env_ov)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "executing"
    assert task.blocker_reason is None
    assert task.last_event_id == env_ov.event_id


@pytest.mark.asyncio
async def test_budget_override_raises_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """handle_tier3_budget_override raises MaterializerError for missing task."""
    rng = Random(605)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="tier3.budget_override",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=BudgetOverridePayload(
            task_id="t-00000000-0000-7000-8000-000000009999",
            decision_id="d-budget-override-0000000000",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=100_000,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_tier3_budget_override(db_session, env)


# ---------------------------------------------------------------------------
# Story 6.10 — license-flagged handler test (code-review gap fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_license_flagged_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_license_flagged updates last_event_id without changing status."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=401)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(701)
    clk = FrozenClock(mono_ns=40_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.license_flagged",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskLicenseFlaggedPayload(
            task_id=task_id,
            reason_code="copyleft-incompatible",
            file_list=["src/gpl_code.py"],
            detected_licenses=["gpl-2.0"],
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_license_flagged(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env.event_id
    assert task.status == "pending"


# ---------------------------------------------------------------------------
# Story 7.1 — Reconstituted-state handler tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_completed_updates_current_step(
    db_session: AsyncSession,
) -> None:
    """handle_task_step_completed sets current_step on the task row."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=801)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(801)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.step.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStepCompletedPayload(
            task_id=task_id,
            step=3,
            description="Refactor module",
            output_summary="Renamed helper functions",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_step_completed(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.current_step == 3
    assert task.last_event_id == env.event_id


@pytest.mark.asyncio
async def test_step_completed_raises_on_missing_task(
    db_session: AsyncSession,
) -> None:
    """handle_task_step_completed raises MaterializerError for missing task."""
    rng = Random(802)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.step.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskStepCompletedPayload(
            task_id="t-00000000-0000-7000-8000-000000009999",
            step=1,
            description="x",
            output_summary="",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    with pytest.raises(MaterializerError):
        await handle_task_step_completed(db_session, env)


@pytest.mark.asyncio
async def test_file_edited_updates_last_agent_action(
    db_session: AsyncSession,
) -> None:
    """handle_file_edited sets last_agent_action via session lookup."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=811)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # Insert a session row so _task_id_for_session can resolve it.
    rng_sid = Random(811)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    session_id = new_session_id(clock=clk_sid, rng=rng_sid)
    session_row = SessionRow(
        id=session_id,
        task_id=task_id,
        worker_kind="claude-code",
        status="active",
        started_at=FROZEN_EPOCH,
    )
    db_session.add(session_row)
    await db_session.flush()

    rng = Random(812)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="file.edited",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=FileEditedPayload(
            session_id=session_id,
            file_path="src/server/middleware.py",
            tool_name="Edit",
            lines_added=5,
            lines_removed=2,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_file_edited(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_agent_action == "Edit src/server/middleware.py"
    assert task.last_event_id == env.event_id


@pytest.mark.asyncio
async def test_file_edited_noop_on_missing_session(
    db_session: AsyncSession,
) -> None:
    """handle_file_edited silently skips when session row is missing."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=821)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng_sid = Random(821)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    phantom_session_id = new_session_id(clock=clk_sid, rng=rng_sid)

    rng = Random(822)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="file.edited",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=FileEditedPayload(
            session_id=phantom_session_id,
            file_path="src/main.py",
            tool_name="Write",
            lines_added=10,
            lines_removed=0,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    # Should not raise — no-op.
    await handle_file_edited(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_agent_action is None


@pytest.mark.asyncio
async def test_reasoning_breadcrumb_updates_last_agent_action(
    db_session: AsyncSession,
) -> None:
    """handle_agent_reasoning_breadcrumb sets last_agent_action for non-suppressed breadcrumbs."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=831)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng_sid = Random(831)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    session_id = new_session_id(clock=clk_sid, rng=rng_sid)
    session_row = SessionRow(
        id=session_id,
        task_id=task_id,
        worker_kind="claude-code",
        status="active",
        started_at=FROZEN_EPOCH,
    )
    db_session.add(session_row)
    await db_session.flush()

    rng = Random(832)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="agent.reasoning.step_summary",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=AgentReasoningBreadcrumbPayload(
            session_id=session_id,
            subtype="step_summary",
            text="Step 2 complete: refactored auth module",
            suppressed=False,
            raw_length=38,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_agent_reasoning_breadcrumb(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_agent_action == "Step 2 complete: refactored auth module"


@pytest.mark.asyncio
async def test_reasoning_breadcrumb_suppressed_is_noop(
    db_session: AsyncSession,
) -> None:
    """handle_agent_reasoning_breadcrumb skips suppressed breadcrumbs."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=841)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng_sid = Random(841)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    session_id = new_session_id(clock=clk_sid, rng=rng_sid)
    session_row = SessionRow(
        id=session_id,
        task_id=task_id,
        worker_kind="claude-code",
        status="active",
        started_at=FROZEN_EPOCH,
    )
    db_session.add(session_row)
    await db_session.flush()

    rng = Random(842)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="agent.reasoning.plan_drafted",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=AgentReasoningBreadcrumbPayload(
            session_id=session_id,
            subtype="plan_drafted",
            text="",
            suppressed=True,
            raw_length=100,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_agent_reasoning_breadcrumb(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_agent_action is None


@pytest.mark.asyncio
async def test_reasoning_breadcrumb_noop_on_missing_session(
    db_session: AsyncSession,
) -> None:
    """handle_agent_reasoning_breadcrumb silently skips when session row is missing."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=851)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng_sid = Random(851)
    clk_sid = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    phantom_session_id = new_session_id(clock=clk_sid, rng=rng_sid)

    rng = Random(852)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="agent.reasoning.tool_call_rationale",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=AgentReasoningBreadcrumbPayload(
            session_id=phantom_session_id,
            subtype="tool_call_rationale",
            text="Running tests to verify changes",
            suppressed=False,
            raw_length=30,
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_agent_reasoning_breadcrumb(db_session, env)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_agent_action is None


# ---------------------------------------------------------------------------
# Story 7.5.2 — Bulk session close + compound index tests
# ---------------------------------------------------------------------------


async def _seed_task_with_sessions(
    db_session: AsyncSession,
    *,
    task_seed: int,
    num_sessions: int,
    status: str = "active",
    worktree_path: str | None = "/tmp/worktree-abc",
) -> tuple[str, list[SessionRow]]:
    """Create a task + N session rows with the given status. Returns (task_id, sessions)."""
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=task_seed)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    sessions: list[SessionRow] = []
    for i in range(num_sessions):
        rng_sid = Random(task_seed * 100 + i)
        clk_sid = FrozenClock(mono_ns=2_000_000 + i * 1_000_000, now=FROZEN_EPOCH)
        row = SessionRow(
            id=new_session_id(clock=clk_sid, rng=rng_sid),
            task_id=task_id,
            worker_kind="claude-code",
            status=status,
            started_at=FROZEN_EPOCH,
            worktree_path=worktree_path,
        )
        db_session.add(row)
        sessions.append(row)
    await db_session.flush()
    return task_id, sessions


@pytest.mark.asyncio
async def test_close_active_session_for_task_closes_all_sessions(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2 AC-1: bulk UPDATE closes ALL active sessions for a task."""
    task_id, sessions = await _seed_task_with_sessions(
        db_session,
        task_seed=901,
        num_sessions=3,
        status="active",
    )

    await _close_active_session_for_task(db_session, task_id, FROZEN_EPOCH)

    for row in sessions:
        await db_session.refresh(row)
        assert row.status == "closed"
        assert row.worktree_path is None
        assert row.ended_at == FROZEN_EPOCH


@pytest.mark.asyncio
async def test_close_active_session_closes_idle_sessions(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2 AC-1: bulk UPDATE closes idle sessions with full field assertions."""
    task_id, sessions = await _seed_task_with_sessions(
        db_session,
        task_seed=902,
        num_sessions=2,
        status="idle",
    )

    await _close_active_session_for_task(db_session, task_id, FROZEN_EPOCH)

    for row in sessions:
        await db_session.refresh(row)
        assert row.status == "closed"
        assert row.worktree_path is None
        assert row.ended_at == FROZEN_EPOCH


@pytest.mark.asyncio
async def test_close_active_session_is_noop_when_none_exist(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2: closing a task with no active sessions is a no-op (replay safety)."""
    # Create task but no sessions.
    env_created = _make_created_envelope(mono_ns=1_000_000, seed=903)
    await handle_task_created(db_session, env_created)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    # Should not raise.
    await _close_active_session_for_task(db_session, task_id, FROZEN_EPOCH)

    # Task unchanged.
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_close_does_not_affect_already_closed_sessions(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2: already-closed sessions are not touched by the bulk close."""
    task_id, sessions = await _seed_task_with_sessions(
        db_session,
        task_seed=904,
        num_sessions=2,
        status="active",
    )
    # Manually close the first session with a distinct ended_at.
    _pre_closed_at = FROZEN_EPOCH.replace(year=2024)
    sessions[0].status = "closed"
    sessions[0].ended_at = _pre_closed_at
    await db_session.flush()

    await _close_active_session_for_task(db_session, task_id, FROZEN_EPOCH)

    await db_session.refresh(sessions[0])
    await db_session.refresh(sessions[1])
    # First was already closed — ended_at and worktree_path stay as original.
    assert sessions[0].status == "closed"
    assert sessions[0].ended_at == _pre_closed_at
    assert sessions[0].worktree_path == "/tmp/worktree-abc"
    # Second was active — now closed by the bulk UPDATE.
    assert sessions[1].status == "closed"
    assert sessions[1].worktree_path is None
    assert sessions[1].ended_at == FROZEN_EPOCH


@pytest.mark.asyncio
async def test_close_does_not_affect_other_tasks(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2: bulk close for one task leaves another task's sessions untouched."""
    task_a, sessions_a = await _seed_task_with_sessions(
        db_session,
        task_seed=905,
        num_sessions=2,
        status="active",
    )
    task_b, sessions_b = await _seed_task_with_sessions(
        db_session,
        task_seed=906,
        num_sessions=2,
        status="active",
    )

    await _close_active_session_for_task(db_session, task_a, FROZEN_EPOCH)

    # Task A sessions closed.
    for row in sessions_a:
        await db_session.refresh(row)
        assert row.status == "closed"
    # Task B sessions untouched.
    for row in sessions_b:
        await db_session.refresh(row)
        assert row.status == "active"
        assert row.worktree_path == "/tmp/worktree-abc"
        assert row.ended_at is None


@pytest.mark.asyncio
async def test_close_mixed_active_and_idle_sessions(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2: bulk close handles a mix of active AND idle sessions on one task."""
    task_id, active_sessions = await _seed_task_with_sessions(
        db_session,
        task_seed=907,
        num_sessions=2,
        status="active",
    )
    # Seed idle sessions on the same task — use separate rng seeds to avoid ID collision.
    idle_sessions: list[SessionRow] = []
    for i in range(2):
        rng_idle = Random(90750 + i)
        clk = FrozenClock(mono_ns=5_000_000 + i * 1_000_000, now=FROZEN_EPOCH)
        row = SessionRow(
            id=new_session_id(clock=clk, rng=rng_idle),
            task_id=task_id,
            worker_kind="claude-code",
            status="idle",
            started_at=FROZEN_EPOCH,
            worktree_path="/tmp/worktree-idle",
        )
        db_session.add(row)
        idle_sessions.append(row)
    await db_session.flush()

    await _close_active_session_for_task(db_session, task_id, FROZEN_EPOCH)

    for row in active_sessions + idle_sessions:
        await db_session.refresh(row)
        assert row.status == "closed"
        assert row.worktree_path is None
        assert row.ended_at == FROZEN_EPOCH


@pytest.mark.asyncio
async def test_close_is_noop_for_nonexistent_task(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2: bulk close with a non-existent task_id is a no-op (no raise)."""
    # No task or session created — pure non-existent task_id.
    phantom_id = new_task_id(
        clock=FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH),
        rng=Random(999),
    )
    await _close_active_session_for_task(db_session, phantom_id, FROZEN_EPOCH)
    # No assertion needed — the contract is "does not raise".


@pytest.mark.asyncio
async def test_compound_index_exists(
    db_session: AsyncSession,
) -> None:
    """Story 7.5.2 AC-2: compound index ix_sessions_task_id_status exists in metadata."""
    index_names = {idx.name for idx in SessionRow.__table__.indexes}  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
    assert "ix_sessions_task_id_status" in index_names
    assert "ix_sessions_task_id" not in index_names
