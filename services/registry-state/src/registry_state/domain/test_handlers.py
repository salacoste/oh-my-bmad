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
from registry_state.domain.handlers import (
    handle_approval_granted,
    handle_approval_rejected,
    handle_task_approval_requested,
    handle_task_blocker_raised,
    handle_task_completed,
    handle_task_created,
    handle_task_execution_started,
    handle_task_plan_ready,
    handle_task_planning_started,
    handle_task_retry_requested,
    handle_task_stop_requested,
    handle_task_summary_emitted,
    handle_tier3_action_attempted,
    handle_tier3_action_performed,
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
    """handle_task_blocker_raised updates last_event_id + updated_at; status unchanged."""
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
    # Status must NOT change — lifecycle for blockers lands in Stories 5.x/6.x
    assert task.status == "pending"


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
            task_id=task_id, decision_id="d-aaa", actor_id="op-1",
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
            task_id=task_id, decision_id="d-bbb", actor_id="op-1", reason="bad plan",
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
            task_id=task_id, actor_id="op-1",
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
async def test_task_retry_requested_updates_last_event_id(
    db_session: AsyncSession,
) -> None:
    """handle_task_retry_requested updates last_event_id + updated_at; status unchanged (AC-4)."""
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
            task_id=task_id, decision_id="d-ccc", actor_id="op-1", hint="focus on X",
        ),
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    await handle_task_retry_requested(db_session, env_retry)
    task = await db_session.get(Task, task_id)
    assert task is not None
    assert task.last_event_id == env_retry.event_id
    assert task.updated_at == env_retry.emitted_at
    assert task.status == "pending"


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
            task_id=missing_id, decision_id="d-x1", actor_id="op-1",
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
            task_id=missing_id, decision_id="d-x2", actor_id="op-1",
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
            task_id=missing_id, actor_id="op-1",
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
            task_id=missing_id, decision_id="d-x3", actor_id="op-1",
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
            task_id=task_id, decision_id="d-a1", actor_id="op-1",
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
            task_id=task_id, decision_id="d-a2", actor_id="op-1",
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
            task_id=task_id, actor_id="op-1",
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
            task_id=task_id, decision_id="d-a3", actor_id="op-1",
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
            action="git_push", task_id=task_id, accepted=True,
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
            task_id=task_id, action="git_push", accepted=True,
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
            task_id=task_id, decision_id="d-lic", actor_id="op-1",
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
            action="git_push", task_id=missing_id, accepted=False,
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
            task_id=missing_id, action="git_push", accepted=True,
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
            task_id=missing_id, decision_id="d-x", actor_id="op-1",
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
            action="git_push", task_id=task_id, accepted=True,
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
            task_id=task_id, action="git_push", accepted=True,
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
            task_id=task_id, decision_id="d-a", actor_id="op-1",
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
            action="git_push", task_id=task_id, accepted=False,
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
