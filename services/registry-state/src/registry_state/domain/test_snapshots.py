"""Tests for registry_state.domain.snapshots — Story 2.6 AC-16 (9 tests).

Each test opens a fresh in-memory SQLite DB via StaticPool, creates the
schema, optionally seeds tasks/sessions/events, and exercises the
``SnapshotPolicy`` API directly. Local fixtures ``fixed_clock`` +
``seeded_uuid7`` are inlined per the Story 2.4/2.5 convention (no new
conftest.py).
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
    new_session_id,
    new_task_id,
    new_uuid7,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.snapshots import SnapshotPolicy
from registry_state.schema import Base, Event, Task
from registry_state.schema import Session as SessionRow

# ---------------------------------------------------------------------------
# Local fixtures (no conftest per Story 2.4/2.5 convention)
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-snapshots")


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
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
async def session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return get_session(engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(*, mono_ns: int, seed: int = 1) -> EventEnvelope:
    """Build a minimal task.created envelope used as a snapshot cursor anchor."""
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    return EventEnvelope(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.created",  # noqa: EVT001 — registry only populated at runtime; AST scanner can't see that
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=mono_ns,
        actor=_ACTOR,
        payload={"task_id": tid, "title": f"snap-helper-{seed}"},
        request_id=new_uuid7(clock=clk, rng=rng),
    )


async def _seed_task(
    sm: async_sessionmaker[AsyncSession],
    *,
    task_id: str,
    title: str,
    last_event_id: str | None = None,
) -> None:
    async with sm() as session, session.begin():
        session.add(
            Task(
                id=task_id,
                status="pending",
                created_at=FROZEN_EPOCH,
                updated_at=FROZEN_EPOCH,
                actor_kind="system",
                actor_id="test",
                title=title,
                last_event_id=last_event_id,
            )
        )


async def _seed_session_row(
    sm: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    task_id: str,
) -> None:
    async with sm() as session, session.begin():
        session.add(
            SessionRow(
                id=session_id,
                task_id=task_id,
                worker_kind="claude-code",
                worktree_path="/tmp/wt",
                status="active",
                started_at=FROZEN_EPOCH,
            )
        )


async def _seed_event(
    sm: async_sessionmaker[AsyncSession],
    *,
    event_id: str,
    mono_ns: int,
    task_id: str | None = None,
) -> None:
    async with sm() as session, session.begin():
        session.add(
            Event(
                id=event_id,
                type="task.created",
                schema_version="1.0.0",
                emitted_at=FROZEN_EPOCH,
                emitted_at_monotonic_ns=mono_ns,
                actor_kind="system",
                actor_id="test",
                task_id=task_id,
                session_id=None,
                parent_event_id=None,
                request_id=new_uuid7(clock=FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)),
                payload_json="{}",
            )
        )


async def _count_snapshots(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM snapshots"))
        return int(result.scalar_one())


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_snapshot_policy_below_threshold_no_capture(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """interval=10, applied=5 → no snapshot row written, return value None."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=10)
    env = _make_envelope(mono_ns=1_000_000)
    result = await policy.maybe_capture(env, applied_count=5)
    assert result is None
    assert await _count_snapshots(session_maker) == 0


@pytest.mark.asyncio
async def test_snapshot_policy_at_threshold_captures(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """interval=10, applied=10 → one snapshot row, tally resets."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=10)
    env = _make_envelope(mono_ns=2_000_000)
    snapshot_id = await policy.maybe_capture(env, applied_count=10)
    assert snapshot_id is not None
    assert await _count_snapshots(session_maker) == 1
    # Tally should now be 0 — another batch of 5 should not trigger capture.
    result = await policy.maybe_capture(_make_envelope(mono_ns=3_000_000, seed=2), 5)
    assert result is None
    assert await _count_snapshots(session_maker) == 1


@pytest.mark.asyncio
async def test_snapshot_policy_above_threshold_captures_once_resets(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """interval=10, applied=12 → ONE snapshot at 12; tally resets to 0."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=10)
    env = _make_envelope(mono_ns=4_000_000)
    snapshot_id = await policy.maybe_capture(env, applied_count=12)
    assert snapshot_id is not None
    assert await _count_snapshots(session_maker) == 1
    # Next 2 events should NOT trigger another capture (tally back at 2).
    env2 = _make_envelope(mono_ns=5_000_000, seed=3)
    second = await policy.maybe_capture(env2, applied_count=2)
    assert second is None
    assert await _count_snapshots(session_maker) == 1


@pytest.mark.asyncio
async def test_snapshot_policy_force_capture(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """capture() ignores the tally and always writes a snapshot."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=10_000)
    env = _make_envelope(mono_ns=6_000_000)
    snapshot_id = await policy.capture(env)
    assert snapshot_id is not None
    assert await _count_snapshots(session_maker) == 1


@pytest.mark.asyncio
async def test_snapshot_payload_contains_tasks_and_sessions(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Seed 2 tasks + 1 session; capture; payload JSON round-trips them."""
    rng = Random(123)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    t1 = new_task_id(clock=clk, rng=rng)
    t2 = new_task_id(clock=clk, rng=rng)
    s1 = new_session_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=t1, title="task-one")
    await _seed_task(session_maker, task_id=t2, title="task-two")
    await _seed_session_row(session_maker, session_id=s1, task_id=t1)

    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env = _make_envelope(mono_ns=7_000_000)
    snapshot_id = await policy.capture(env)

    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT payload_json FROM snapshots WHERE id = :sid"),
                {"sid": snapshot_id},
            )
        ).one()
    payload = json.loads(row[0])
    assert payload["version"] == 1
    task_ids = sorted(t["id"] for t in payload["tasks"])
    assert task_ids == sorted([t1, t2])
    titles = {t["id"]: t["title"] for t in payload["tasks"]}
    assert titles[t1] == "task-one"
    assert titles[t2] == "task-two"
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["id"] == s1
    assert payload["sessions"][0]["task_id"] == t1


@pytest.mark.asyncio
async def test_snapshot_payload_includes_cursor_monotonic_ns(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Payload's cursor_emitted_at_monotonic_ns matches the last_envelope's value."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    cursor_ns = 12_345_678_901_234
    env = _make_envelope(mono_ns=cursor_ns)
    snapshot_id = await policy.capture(env)
    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT payload_json FROM snapshots WHERE id = :sid"),
                {"sid": snapshot_id},
            )
        ).one()
    payload = json.loads(row[0])
    assert payload["cursor_emitted_at_monotonic_ns"] == cursor_ns


@pytest.mark.asyncio
async def test_snapshot_byte_size_matches_payload_length(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """``byte_size == len(payload_json.encode("utf-8"))`` for every captured snapshot."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env = _make_envelope(mono_ns=9_999_999)
    snapshot_id = await policy.capture(env)
    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT byte_size, payload_json FROM snapshots WHERE id = :sid"),
                {"sid": snapshot_id},
            )
        ).one()
    byte_size, payload_json = int(row[0]), str(row[1])
    assert byte_size == len(payload_json.encode("utf-8"))


@pytest.mark.asyncio
async def test_snapshot_event_count_matches_events_table(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Pre-insert 5 events; capture; event_count == 5."""
    rng = Random(321)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    # Need a task row so events.task_id FK is satisfied.
    tid = new_task_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=tid, title="event-count-task")
    for i in range(5):
        eid = new_event_id(clock=clk, rng=rng)
        await _seed_event(session_maker, event_id=eid, mono_ns=1_000_000 * (i + 1), task_id=tid)
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env = _make_envelope(mono_ns=10_000_000)
    snapshot_id = await policy.capture(env)
    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT event_count FROM snapshots WHERE id = :sid"),
                {"sid": snapshot_id},
            )
        ).one()
    assert int(row[0]) == 5


@pytest.mark.asyncio
async def test_snapshot_policy_rejects_interval_zero_or_negative(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """ValueError for interval <= 0."""
    with pytest.raises(ValueError, match="interval must be positive"):
        SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=0)
    with pytest.raises(ValueError, match="interval must be positive"):
        SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=-5)
