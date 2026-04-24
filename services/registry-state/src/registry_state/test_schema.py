"""Schema roundtrip tests for all 5 registry-state ORM models (Story 2.3 AC-10).

Each test:
  1. Builds an in-memory async engine with ``create_tables`` (via Base.metadata).
  2. Inserts one row with realistic values drawn from Story 2.2 generators.
  3. Commits + refreshes from DB.
  4. Asserts every column round-trips losslessly.

Special attention is paid to ``DateTime(timezone=True)`` columns — SQLite stores
datetimes as text, so the UTC-aware → text → UTC-aware roundtrip is a real
failure mode (AC-10 bullet 6).

Test data uses ``FrozenClock(mono_ns=0, now=FROZEN_EPOCH)`` + ``Random(42)``
for deterministic IDs matching the conftest pattern from Story 2.2.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any

import pytest
import pytest_asyncio
from events.clock import FROZEN_EPOCH, FrozenClock
from events.ids import (
    new_event_id,
    new_idempotency_key,
    new_request_id,
    new_session_id,
    new_task_id,
    new_uuid7,
)
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from registry_state.adapters.sqlite_store import get_session
from registry_state.schema import Base, Event, IdempotencyCache, Session, Snapshot, Task


def _rng() -> Random:
    """Fresh seeded RNG — deterministic per test."""
    return Random(42)


def _clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """AsyncSession backed by a StaticPool in-memory SQLite DB with all tables.

    StaticPool is required for in-memory SQLite + NullPool: without it, each
    ``engine.begin()`` / ``Session()`` call opens a fresh empty connection.
    StaticPool ensures create_all and INSERT/SELECT share the same underlying
    sqlite3 connection object.

    Pragmas are applied inline (same 4 pragmas as sqlite_store.create_engine).
    """
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @sa_event.listens_for(test_engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session(test_engine)
    async with session_factory() as sess:
        yield sess

    await test_engine.dispose()


# ---------------------------------------------------------------------------
# Task roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_roundtrip(session: AsyncSession) -> None:
    """Insert + refresh a Task row; assert all columns match."""
    c = _clock()
    r = _rng()
    task_id = new_task_id(clock=c, rng=r)
    event_id = new_event_id(clock=c, rng=r)
    now = FROZEN_EPOCH

    row = Task(
        id=task_id,
        status="pending",
        created_at=now,
        updated_at=now,
        actor_kind="human",
        actor_id="operator-1",
        title="Test task",
        last_event_id=event_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    assert row.id == task_id
    assert row.status == "pending"
    assert row.created_at == now
    assert row.updated_at == now
    assert row.actor_kind == "human"
    assert row.actor_id == "operator-1"
    assert row.title == "Test task"
    assert row.last_event_id == event_id


@pytest.mark.asyncio
async def test_task_nullable_columns_are_none(session: AsyncSession) -> None:
    """Task rows with no title/last_event_id store NULL."""
    c = _clock()
    r = _rng()
    row = Task(
        id=new_task_id(clock=c, rng=r),
        status="pending",
        created_at=FROZEN_EPOCH,
        updated_at=FROZEN_EPOCH,
        actor_kind="claude-code",
        actor_id="agent-1",
        title=None,
        last_event_id=None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    assert row.title is None
    assert row.last_event_id is None


# ---------------------------------------------------------------------------
# Session roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_roundtrip(session: AsyncSession) -> None:
    """Insert Task + Session; assert Session columns round-trip."""
    c = _clock()
    r = _rng()
    task_id = new_task_id(clock=c, rng=r)
    session_id = new_session_id(clock=c, rng=r)
    now = FROZEN_EPOCH
    ended = now + timedelta(minutes=5)

    task = Task(
        id=task_id,
        status="done",
        created_at=now,
        updated_at=now,
        actor_kind="human",
        actor_id="op-1",
    )
    session.add(task)
    await session.flush()  # ensure task row exists before session FK references it

    sess_row = Session(
        id=session_id,
        task_id=task_id,
        worker_kind="claude-code",
        worktree_path="/repo/worktrees/abc",
        status="completed",
        started_at=now,
        ended_at=ended,
        last_heartbeat_at=ended,
    )
    session.add(sess_row)
    await session.commit()
    await session.refresh(sess_row)

    assert sess_row.id == session_id
    assert sess_row.task_id == task_id
    assert sess_row.worker_kind == "claude-code"
    assert sess_row.worktree_path == "/repo/worktrees/abc"
    assert sess_row.status == "completed"
    assert sess_row.started_at == now
    assert sess_row.ended_at == ended
    assert sess_row.last_heartbeat_at == ended


@pytest.mark.asyncio
async def test_session_nullable_timestamps_are_none(session: AsyncSession) -> None:
    """Session rows in-progress have NULL ended_at / last_heartbeat_at."""
    c = _clock()
    r = _rng()
    task_id = new_task_id(clock=c, rng=r)
    task_row = Task(
        id=task_id,
        status="active",
        created_at=FROZEN_EPOCH,
        updated_at=FROZEN_EPOCH,
        actor_kind="human",
        actor_id="op-1",
    )
    session.add(task_row)
    await session.flush()  # ensure task row exists before session FK references it
    sess_row = Session(
        id=new_session_id(clock=c, rng=r),
        task_id=task_id,
        worker_kind="codex",
        worktree_path=None,
        status="running",
        started_at=FROZEN_EPOCH,
        ended_at=None,
        last_heartbeat_at=None,
    )
    session.add(sess_row)
    await session.commit()
    await session.refresh(sess_row)

    assert sess_row.ended_at is None
    assert sess_row.last_heartbeat_at is None
    assert sess_row.worktree_path is None


# ---------------------------------------------------------------------------
# Event roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_roundtrip(session: AsyncSession) -> None:
    """Insert Task + Session + Event; assert Event columns round-trip."""
    c = _clock()
    r = _rng()
    task_id = new_task_id(clock=c, rng=r)
    session_id = new_session_id(clock=c, rng=r)
    event_id = new_event_id(clock=c, rng=r)
    request_id = new_request_id(clock=c, rng=r)
    parent_event_id = new_event_id(clock=c, rng=r)
    now = FROZEN_EPOCH
    payload = '{"type":"task.created","data":{}}'

    task = Task(
        id=task_id,
        status="pending",
        created_at=now,
        updated_at=now,
        actor_kind="human",
        actor_id="op-1",
    )
    session.add(task)
    await session.flush()  # task must exist before session FK
    sess_row = Session(
        id=session_id,
        task_id=task_id,
        worker_kind="claude-code",
        status="running",
        started_at=now,
    )
    session.add(sess_row)
    await session.flush()  # session must exist before event FK
    event = Event(
        id=event_id,
        type="task.created",
        schema_version="1.0.0",
        emitted_at=now,
        emitted_at_monotonic_ns=0,
        actor_kind="human",
        actor_id="op-1",
        task_id=task_id,
        session_id=session_id,
        parent_event_id=parent_event_id,
        request_id=request_id,
        payload_json=payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    assert event.id == event_id
    assert event.type == "task.created"
    assert event.schema_version == "1.0.0"
    assert event.emitted_at == now
    assert event.emitted_at_monotonic_ns == 0
    assert event.actor_kind == "human"
    assert event.actor_id == "op-1"
    assert event.task_id == task_id
    assert event.session_id == session_id
    assert event.parent_event_id == parent_event_id
    assert event.request_id == request_id
    assert event.payload_json == payload


@pytest.mark.asyncio
async def test_event_nullable_fks_are_none(session: AsyncSession) -> None:
    """System-level events (no task/session binding) store NULL task_id/session_id."""
    c = _clock()
    r = _rng()
    event_id = new_event_id(clock=c, rng=r)
    request_id = new_request_id(clock=c, rng=r)

    event = Event(
        id=event_id,
        type="service.started",
        schema_version="1.0.0",
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=0,
        actor_kind="system",
        actor_id="registry-state",
        task_id=None,
        session_id=None,
        parent_event_id=None,
        request_id=request_id,
        payload_json="{}",
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    assert event.task_id is None
    assert event.session_id is None
    assert event.parent_event_id is None


# ---------------------------------------------------------------------------
# IdempotencyCache roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_cache_roundtrip(session: AsyncSession) -> None:
    """Insert + refresh an IdempotencyCache row; assert all columns match."""
    c = _clock()
    r = _rng()
    key = new_idempotency_key(clock=c, rng=r)
    result_event_id = new_event_id(clock=c, rng=r)
    request_id = new_request_id(clock=c, rng=r)
    now = FROZEN_EPOCH
    expires = now + timedelta(days=7)

    row = IdempotencyCache(
        idempotency_key=key,
        created_at=now,
        expires_at=expires,
        result_event_id=result_event_id,
        request_id_on_first_hit=request_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    assert row.idempotency_key == key
    assert row.created_at == now
    assert row.expires_at == expires
    assert row.result_event_id == result_event_id
    assert row.request_id_on_first_hit == request_id


# ---------------------------------------------------------------------------
# Snapshot roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_roundtrip(session: AsyncSession) -> None:
    """Insert + refresh a Snapshot row; assert all columns match."""
    c = _clock()
    r = _rng()
    snap_id = new_uuid7(clock=c, rng=r)  # bare UUIDv7 — no prefix for snapshots
    cursor_event_id = new_event_id(clock=c, rng=r)
    now = FROZEN_EPOCH
    payload = '{"tasks":{},"sessions":{}}'

    row = Snapshot(
        id=snap_id,
        created_at=now,
        cursor_event_id=cursor_event_id,
        event_count=42,
        byte_size=len(payload),
        payload_json=payload,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    assert row.id == snap_id
    assert row.created_at == now
    assert row.cursor_event_id == cursor_event_id
    assert row.event_count == 42
    assert row.byte_size == len(payload)
    assert row.payload_json == payload


# ---------------------------------------------------------------------------
# UTC-aware DateTime roundtrip (AC-10 explicit bullet)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_datetime_utc_aware_roundtrip(session: AsyncSession) -> None:
    """UTC-aware datetime survives the SQLite TEXT storage roundtrip losslessly.

    SQLite stores DateTime as text (ISO 8601). SQLAlchemy's aiosqlite dialect
    re-parses on read. This test asserts tzinfo is preserved (UTC, not naive)
    and the value is identical to what was written.
    """
    c = _clock()
    r = _rng()
    # Use a non-epoch timestamp to catch off-by-one or epoch-special-case bugs.
    ts = datetime(2026, 6, 15, 12, 30, 45, 123000, tzinfo=UTC)

    row = Task(
        id=new_task_id(clock=c, rng=r),
        status="pending",
        created_at=ts,
        updated_at=ts,
        actor_kind="human",
        actor_id="op-1",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    assert row.created_at == ts, f"created_at mismatch: {row.created_at!r} != {ts!r}"
    assert row.updated_at == ts, f"updated_at mismatch: {row.updated_at!r} != {ts!r}"
    # Verify timezone-awareness is preserved (not converted to naive datetime)
    assert row.created_at.tzinfo is not None, "created_at lost timezone info"
    assert row.updated_at.tzinfo is not None, "updated_at lost timezone info"
    assert row.created_at.utcoffset() == timedelta(0), "created_at not UTC"
