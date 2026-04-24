"""Tests for registry_state.domain.materializer — Story 2.5 AC-13 (12 tests).

Uses an in-memory SQLite DB (``sqlite+aiosqlite:///:memory:``) for isolation.
Tables are created via ``Base.metadata.create_all`` at the start of each test
function (simpler than invoking Alembic for unit tests at this level).

Local fixtures ``fixed_clock`` + ``seeded_uuid7`` are inlined per the Story 2.4
convention (no new conftest.py added).
"""

from __future__ import annotations

import json
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
    new_task_id,
    new_uuid7,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (
    TaskCreatedPayload,
    TaskPlanningStartedPayload,
)
from registry_state.domain.handlers import register_default_handlers
from registry_state.domain.materializer import Materializer
from registry_state.schema import Base, Event, Task

# ---------------------------------------------------------------------------
# Local fixtures (mirror tests/conftest.py — no new conftest per AC-13)
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-materializer")


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register the 4 task event types before each test.

    ``test_event_log.py`` has an autouse ``_clean_registry`` fixture that calls
    ``unregister_all()`` at teardown. Re-registering here (idempotent per
    Story 2.1's register() contract) ensures a clean known state regardless of
    suite execution order.
    """
    from events.schema_registry import register as _reg

    from registry_state.domain.event_types import (
        TaskCreatedPayload,
        TaskExecutionStartedPayload,
        TaskPlanningStartedPayload,
        TaskPlanReadyPayload,
    )

    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
    _reg("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
    _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=0."""
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    """Deterministic UUIDv7 factory."""
    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# DB + materializer fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[object, None]:
    """In-memory async SQLite engine with tables created.

    Uses StaticPool so all connect() calls share the same underlying
    sqlite3 connection — required for in-memory SQLite where NullPool
    would give each caller an independent empty DB.
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
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def materializer(engine: object) -> Materializer:
    """Materializer with all 4 default handlers registered."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(engine, AsyncEngine)
    sm = get_session(engine)
    m = Materializer(session_maker=sm)
    register_default_handlers(m)
    return m


@pytest_asyncio.fixture
async def session_maker(engine: object) -> object:
    """Bare session_maker for direct DB assertions."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(engine, AsyncEngine)
    return get_session(engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_created_envelope(
    clock: FrozenClock | None = None,
    mono_ns: int = 1_000_000,
    task_id: str | None = None,
    title: str | None = "Test task",
) -> EventEnvelope:
    """Build a task.created envelope with a real TaskCreatedPayload."""
    rng = Random(42)
    clk = clock or FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    tid = task_id or new_task_id(clock=clk, rng=rng)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=tid, title=title),
        request_id=rid,
    )


def _planning_started_envelope(
    task_id: str,
    mono_ns: int = 2_000_000,
) -> EventEnvelope:
    """Build a task.planning.started envelope."""
    rng = Random(99)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.planning.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanningStartedPayload(task_id=task_id),
        request_id=rid,
    )


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_inserts_event_row(materializer: Materializer, session_maker: object) -> None:
    """One envelope → one row in the events table."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    env = _task_created_envelope()
    await materializer.apply(env)
    async with session_maker() as session:
        result = await session.execute(select(Event).where(Event.id == env.event_id))
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.type == "task.created"
    assert row.emitted_at_monotonic_ns == env.emitted_at_monotonic_ns


@pytest.mark.asyncio
async def test_apply_is_idempotent_by_event_id(
    materializer: Materializer, session_maker: object
) -> None:
    """Same envelope applied twice → one row; handler invoked ONCE (task row stays 1)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    env = _task_created_envelope()
    await materializer.apply(env)
    await materializer.apply(env)  # second apply is a no-op
    async with session_maker() as session:
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE id = :eid"), {"eid": env.event_id}
        )
        event_count = count_result.scalar()
        task_count_result = await session.execute(text("SELECT COUNT(*) FROM tasks"))
        task_count = task_count_result.scalar()
    assert event_count == 1
    assert task_count == 1  # handler fired exactly once


@pytest.mark.asyncio
async def test_apply_many_counts_new_only(materializer: Materializer) -> None:
    """Mixed new + duplicate envelopes → rowcount matches new-only count."""
    env1 = _task_created_envelope(mono_ns=1_000_000)
    rng2 = Random(7)
    clk2 = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    tid2 = new_task_id(clock=clk2, rng=rng2)
    env2 = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng2),
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=tid2, title="Task 2"),
        request_id=new_uuid7(clock=clk2, rng=rng2),
    )
    # Apply env1 first so it's a duplicate in the batch.
    await materializer.apply(env1)
    count = await materializer.apply_many([env1, env2])
    assert count == 1  # only env2 is new


@pytest.mark.asyncio
async def test_cursor_returns_zero_on_empty_table(
    materializer: Materializer, session_maker: object
) -> None:
    """cursor() returns 0 when the events table is empty."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    async with session_maker() as session:
        cursor = await materializer.cursor(session)
    assert cursor == 0


@pytest.mark.asyncio
async def test_cursor_returns_max_monotonic_ns_after_inserts(
    materializer: Materializer, session_maker: object
) -> None:
    """cursor() returns MAX(emitted_at_monotonic_ns) after applying events."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    env1 = _task_created_envelope(mono_ns=1_000_000)
    rng2 = Random(7)
    clk2 = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    tid2 = new_task_id(clock=clk2, rng=rng2)
    env2 = EventEnvelope.create(
        event_id=new_event_id(clock=clk2, rng=rng2),
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk2.now(),
        emitted_at_monotonic_ns=clk2.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=tid2, title=None),
        request_id=new_uuid7(clock=clk2, rng=rng2),
    )
    await materializer.apply(env1)
    await materializer.apply(env2)
    async with session_maker() as session:
        cursor = await materializer.cursor(session)
    assert cursor == 5_000_000


@pytest.mark.asyncio
async def test_register_handler_dispatches_on_type(
    engine: object,
) -> None:
    """register_handler wires a custom handler that is called on matching type."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(engine, AsyncEngine)
    sm = get_session(engine)
    m = Materializer(session_maker=sm)

    dispatched: list[str] = []

    async def _spy(session: AsyncSession, env: EventEnvelope) -> None:
        dispatched.append(env.event_id)

    m.register_handler("task.created", _spy)
    env = _task_created_envelope()
    await m.apply(env)
    assert dispatched == [env.event_id]


@pytest.mark.asyncio
async def test_unregistered_event_type_inserts_event_row_without_handler(
    engine: object, session_maker: object
) -> None:
    """Unknown event type still writes an events row; no error raised."""
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

    assert isinstance(engine, AsyncEngine)
    assert isinstance(session_maker, async_sessionmaker)

    # Register an ad-hoc payload model for this test's unknown type.
    from events.schema_registry import register as _reg
    from events.schema_registry import unregister_all
    from pydantic import BaseModel

    class _UnknownPayload(BaseModel):
        task_id: str

    _reg("service.started", "1.0.0", _UnknownPayload)
    try:
        from sqlalchemy.ext.asyncio import AsyncEngine

        assert isinstance(engine, AsyncEngine)
        sm = get_session(engine)
        m = Materializer(session_maker=sm)
        # No handler registered for service.started
        rng = Random(55)
        clk = FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH)
        env = EventEnvelope.create(
            event_id=new_event_id(clock=clk, rng=rng),
            schema_version="1.0.0",
            type="service.started",
            emitted_at=clk.now(),
            emitted_at_monotonic_ns=clk.monotonic_ns(),
            actor=_ACTOR,
            payload=_UnknownPayload(task_id="t-00000000-0000-7000-8000-000000000000"),
            request_id=new_uuid7(clock=clk, rng=rng),
        )
        await m.apply(env)  # must not raise
        async with session_maker() as session:
            row = await session.get(Event, env.event_id)
        assert row is not None
        assert row.type == "service.started"
    finally:
        unregister_all()
        # Re-register the 4 production types so other tests in the suite work.
        from registry_state.domain.event_types import (
            TaskCreatedPayload,
            TaskExecutionStartedPayload,
            TaskPlanningStartedPayload,
            TaskPlanReadyPayload,
        )

        _reg("task.created", "1.0.0", TaskCreatedPayload)
        _reg("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
        _reg("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
        _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)


@pytest.mark.asyncio
async def test_out_of_order_update_raises_materializer_error(
    materializer: Materializer,
) -> None:
    """task.planning.started without prior task.created raises MaterializerError."""
    rng = Random(42)
    clk = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    env = _planning_started_envelope(task_id=tid)
    with pytest.raises(MaterializerError) as exc_info:
        await materializer.apply(env)
    assert exc_info.value.event_type == "task.planning.started"
    assert tid in exc_info.value.reason


@pytest.mark.asyncio
async def test_events_payload_json_contains_payload_only(
    materializer: Materializer, session_maker: object
) -> None:
    """events.payload_json stores just the payload dict, not the full envelope."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    env = _task_created_envelope(title="Payload-only test")
    await materializer.apply(env)
    async with session_maker() as session:
        row = await session.get(Event, env.event_id)
    assert row is not None
    parsed = json.loads(row.payload_json)
    # payload_json must NOT contain envelope-level fields
    assert "event_id" not in parsed
    assert "actor" not in parsed
    assert "emitted_at" not in parsed
    # payload_json MUST contain the payload fields
    assert "task_id" in parsed
    assert "title" in parsed
    assert parsed["title"] == "Payload-only test"


@pytest.mark.asyncio
async def test_apply_planning_started_updates_task_status(
    materializer: Materializer, session_maker: object
) -> None:
    """task.planning.started handler transitions status pending → planning."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    env_created = _task_created_envelope(mono_ns=1_000_000)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id
    env_planning = _planning_started_envelope(task_id=task_id, mono_ns=2_000_000)

    await materializer.apply(env_created)
    await materializer.apply(env_planning)

    async with session_maker() as session:
        task = await session.get(Task, task_id)
    assert task is not None
    assert task.status == "planning"
    assert task.last_event_id == env_planning.event_id


@pytest.mark.asyncio
async def test_apply_plan_ready_updates_task_status(
    materializer: Materializer, session_maker: object
) -> None:
    """task.plan.ready handler transitions status planning → plan_ready."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    from registry_state.domain.event_types import TaskPlanReadyPayload

    env_created = _task_created_envelope(mono_ns=1_000_000)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    rng = Random(33)
    clk = FrozenClock(mono_ns=3_000_000, now=FROZEN_EPOCH)
    env_plan_ready = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanReadyPayload(task_id=task_id, plan_summary="Do the thing"),
        request_id=new_uuid7(clock=clk, rng=rng),
    )

    await materializer.apply(env_created)
    await materializer.apply(_planning_started_envelope(task_id=task_id, mono_ns=2_000_000))
    await materializer.apply(env_plan_ready)

    async with session_maker() as session:
        task = await session.get(Task, task_id)
    assert task is not None
    assert task.status == "plan_ready"


@pytest.mark.asyncio
async def test_apply_execution_started_updates_status_and_inserts_session(
    materializer: Materializer, session_maker: object
) -> None:
    """task.execution.started transitions status → executing and inserts session row."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(session_maker, async_sessionmaker)
    from registry_state.domain.event_types import (
        TaskExecutionStartedPayload,
        TaskPlanReadyPayload,
    )
    from registry_state.schema import Session as _SessionRow

    env_created = _task_created_envelope(mono_ns=1_000_000)
    assert isinstance(env_created.payload, TaskCreatedPayload)
    task_id = env_created.payload.task_id

    from events import new_session_id

    rng3 = Random(3)
    clk3 = FrozenClock(mono_ns=3_000_000, now=FROZEN_EPOCH)
    sid = new_session_id(clock=clk3, rng=rng3)

    rng4 = Random(4)
    clk4 = FrozenClock(mono_ns=4_000_000, now=FROZEN_EPOCH)
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

    await materializer.apply(env_created)
    await materializer.apply(_planning_started_envelope(task_id=task_id, mono_ns=2_000_000))

    rng_pr = Random(33)
    clk_pr = FrozenClock(mono_ns=3_000_000, now=FROZEN_EPOCH)
    await materializer.apply(
        EventEnvelope.create(
            event_id=new_event_id(clock=clk_pr, rng=rng_pr),
            schema_version="1.0.0",
            type="task.plan.ready",
            emitted_at=clk_pr.now(),
            emitted_at_monotonic_ns=clk_pr.monotonic_ns(),
            actor=_ACTOR,
            payload=TaskPlanReadyPayload(task_id=task_id, plan_summary="plan"),
            request_id=new_uuid7(clock=clk_pr, rng=rng_pr),
        )
    )
    await materializer.apply(env_exec)

    async with session_maker() as session:
        task = await session.get(Task, task_id)
        sess_row = await session.get(_SessionRow, sid)
    assert task is not None
    assert task.status == "executing"
    assert sess_row is not None
    assert sess_row.task_id == task_id
    assert sess_row.worker_kind == "unknown"
    assert sess_row.status == "active"
