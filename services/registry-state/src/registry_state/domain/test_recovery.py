"""Tests for registry_state.domain.recovery — Story 2.6 AC-16 (7 tests).

Recovery is the read path: at subscriber startup we restore tasks +
sessions from the latest snapshot, then compute the replay cursor as
``max(snapshot_cursor, MAX(events.emitted_at_monotonic_ns))``.

This module is flagged HIGH-RISK in architecture.md (line 834) — the tests
here are the explicit-coverage half of the pair-review + test-coverage
mandate.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from random import Random

import pytest
import pytest_asyncio
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TickingClock,
    new_session_id,
    new_task_id,
    new_uuid7,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.recovery import (
    compute_events_max_cursor,
    compute_replay_cursor,
    restore_state_from_latest_snapshot,
)
from registry_state.domain.snapshots import SnapshotPolicy
from registry_state.schema import Base, Event, Snapshot, Task
from registry_state.schema import Session as SessionRow

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


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


def _make_cursor_envelope(*, mono_ns: int, seed: int = 1) -> EventEnvelope:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    return EventEnvelope(
        event_id=f"e-{new_uuid7(clock=clk, rng=rng)}",
        schema_version="1.0.0",
        type="task.created",  # noqa: EVT001 — registry only populated at runtime; AST scanner can't see that
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=mono_ns,
        actor=Actor(kind="system", id="test-recovery"),
        payload={"task_id": tid, "title": f"recovery-cursor-{seed}"},
        trace_id="01917e5c-a7d1-7000-8abc-000000000000",
        request_id=new_uuid7(clock=clk, rng=rng),
    )


async def _seed_task(
    sm: async_sessionmaker[AsyncSession],
    *,
    task_id: str,
    title: str,
    status: str = "pending",
    last_event_id: str | None = None,
) -> None:
    async with sm() as session, session.begin():
        session.add(
            Task(
                id=task_id,
                status=status,
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
    status: str = "active",
) -> None:
    async with sm() as session, session.begin():
        session.add(
            SessionRow(
                id=session_id,
                task_id=task_id,
                worker_kind="claude-code",
                worktree_path="/tmp/wt",
                status=status,
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


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_no_snapshot_returns_zero(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """No snapshot row → returns 0; tasks/sessions tables untouched."""
    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 0
    async with session_maker() as session:
        task_count = (await session.execute(text("SELECT COUNT(*) FROM tasks"))).scalar_one()
        session_count = (await session.execute(text("SELECT COUNT(*) FROM sessions"))).scalar_one()
    assert task_count == 0
    assert session_count == 0


@pytest.mark.asyncio
async def test_restore_one_snapshot_upserts_tasks_and_sessions(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Capture from a seeded DB, wipe tasks+sessions, restore → rows reappear."""
    rng = Random(1234)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    t1 = new_task_id(clock=clk, rng=rng)
    s1 = new_session_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=t1, title="restore-task", status="executing")
    await _seed_session_row(session_maker, session_id=s1, task_id=t1)

    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env = _make_cursor_envelope(mono_ns=99_000)
    await policy.capture(env)

    # Wipe the live tables (must DELETE from sessions before tasks: FK).
    async with session_maker() as session, session.begin():
        await session.execute(text("DELETE FROM sessions"))
        await session.execute(text("DELETE FROM tasks"))

    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 99_000

    async with session_maker() as session:
        task_row = (await session.execute(select(Task).where(Task.id == t1))).scalar_one()
        sess_row = (
            await session.execute(select(SessionRow).where(SessionRow.id == s1))
        ).scalar_one()
    assert task_row.title == "restore-task"
    assert task_row.status == "executing"
    assert sess_row.task_id == t1
    assert sess_row.status == "active"


@pytest.mark.asyncio
async def test_restore_picks_latest_snapshot_when_multiple_exist(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Two snapshots, second has the higher created_at → restore reads the second."""
    rng = Random(77)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=tid, title="initial", status="pending")

    # First snapshot — created earlier, with task title "initial".
    policy_a = SnapshotPolicy(
        session_maker=session_maker,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
        interval=1,
    )
    env_a = _make_cursor_envelope(mono_ns=100, seed=1)
    await policy_a.capture(env_a)

    # Mutate the task and capture again with a later created_at (use a
    # FrozenClock 1 hour into the future for the snapshot's own
    # ``created_at`` field — that's what ORDER BY created_at DESC reads).
    async with session_maker() as session, session.begin():
        await session.execute(
            text("UPDATE tasks SET title = :t WHERE id = :tid"),
            {"t": "updated", "tid": tid},
        )
    later = FROZEN_EPOCH + timedelta(hours=1)
    policy_b = SnapshotPolicy(
        session_maker=session_maker,
        clock=FrozenClock(mono_ns=0, now=later),
        interval=1,
    )
    env_b = _make_cursor_envelope(mono_ns=999, seed=2)
    await policy_b.capture(env_b)

    # Wipe live state, restore — the LATEST (env_b's) snapshot wins.
    async with session_maker() as session, session.begin():
        await session.execute(text("DELETE FROM tasks"))

    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 999

    async with session_maker() as session:
        row = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
    assert row.title == "updated"


@pytest.mark.asyncio
async def test_restore_returns_cursor_monotonic_ns(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Restore's return value is the snapshot's cursor_emitted_at_monotonic_ns."""
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env = _make_cursor_envelope(mono_ns=42_000_000_000)
    await policy.capture(env)
    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 42_000_000_000


@pytest.mark.asyncio
async def test_compute_replay_cursor_returns_max_of_snapshot_and_events(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Cursor honors max(snapshot, events) regardless of which is higher."""
    rng = Random(11)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=tid, title="cursor-test")

    # Case A: snapshot at 1000, events max at 500 → expect 1000.
    policy = SnapshotPolicy(session_maker=session_maker, clock=fixed_clock, interval=1)
    env_low = _make_cursor_envelope(mono_ns=1000)
    await policy.capture(env_low)
    eid1 = f"e-{new_uuid7(clock=clk, rng=rng)}"
    await _seed_event(session_maker, event_id=eid1, mono_ns=500, task_id=tid)
    assert await compute_replay_cursor(session_maker) == 1000

    # Case B: now insert an event at 5000 — max(1000, 5000) = 5000.
    eid2 = f"e-{new_uuid7(clock=clk, rng=rng)}"
    await _seed_event(session_maker, event_id=eid2, mono_ns=5000, task_id=tid)
    assert await compute_replay_cursor(session_maker) == 5000


@pytest.mark.asyncio
async def test_compute_replay_cursor_returns_zero_when_both_empty(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """No snapshot AND no events → cursor is 0."""
    cursor = await compute_replay_cursor(session_maker)
    assert cursor == 0


@pytest.mark.asyncio
async def test_restore_payload_v1_format(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """Direct-INSERT a hand-crafted v1 payload; restore consumes it correctly.

    Also probes the version-dispatch branch by hand-crafting a v99 payload
    and asserting ValueError.
    """
    # ------ v1 happy path ------
    payload = {
        "version": 1,
        "tasks": [
            {
                "id": "t-019b76da-a800-7d79-b000-000000000001",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00.000Z",
                "updated_at": "2026-01-01T00:00:00.000Z",
                "actor_kind": "system",
                "actor_id": "test",
                "title": "v1-task",
                "last_event_id": None,
            }
        ],
        "sessions": [],
        "cursor_emitted_at_monotonic_ns": 12_345,
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    snapshot_id = new_uuid7(clock=fixed_clock)
    async with session_maker() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO snapshots (id, created_at, cursor_event_id, "
                "event_count, byte_size, payload_json) VALUES "
                "(:id, :ca, :cid, :ec, :bs, :pj)"
            ),
            {
                "id": snapshot_id,
                "ca": FROZEN_EPOCH.replace(tzinfo=None).isoformat(),
                "cid": "e-019b76da-a800-7d79-b000-000000000099",
                "ec": 0,
                "bs": len(payload_json.encode("utf-8")),
                "pj": payload_json,
            },
        )
    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 12_345
    async with session_maker() as session:
        row = (
            await session.execute(
                select(Task).where(Task.id == "t-019b76da-a800-7d79-b000-000000000001")
            )
        ).scalar_one()
    assert row.title == "v1-task"

    # ------ v99 unsupported-version branch ------
    bad_payload = json.dumps({"version": 99, "tasks": [], "sessions": []}, sort_keys=True)
    bad_id = new_uuid7(clock=fixed_clock)
    async with session_maker() as session, session.begin():
        # Wipe the v1 snapshot so the v99 row is the LATEST.
        await session.execute(text("DELETE FROM snapshots"))
        await session.execute(
            text(
                "INSERT INTO snapshots (id, created_at, cursor_event_id, "
                "event_count, byte_size, payload_json) VALUES "
                "(:id, :ca, :cid, :ec, :bs, :pj)"
            ),
            {
                "id": bad_id,
                "ca": FROZEN_EPOCH.replace(tzinfo=None).isoformat(),
                "cid": "e-019b76da-a800-7d79-b000-000000000fff",
                "ec": 0,
                "bs": len(bad_payload.encode("utf-8")),
                "pj": bad_payload,
            },
        )
    with pytest.raises(ValueError, match="unsupported snapshot payload version"):
        await restore_state_from_latest_snapshot(session_maker)


# ===========================================================================
# Story 2.6 code-review fixes — regression tests
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_does_not_overwrite_existing_created_at(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """F1 regression: UPSERT preserves the live row's ``created_at``.

    Setup:
      1. Live DB has a Task row with created_at = T_live (2027-01-01).
      2. Snapshot payload claims the same task id with created_at = T_snap
         (2026-01-01) — i.e. SNAPSHOT IS OLDER.
      3. After restore, the live row's created_at must still be T_live —
         the snapshot's older value must NOT regress the timestamp.

    This is the F1 bug: the original UPSERT included created_at in the
    DO UPDATE SET clause, so a stale snapshot would silently overwrite the
    insertion timestamp.
    """
    t_live = datetime(2027, 1, 1, tzinfo=UTC)
    # Snapshot's older created_at is hard-coded into the payload below as
    # the canonical "2026-01-01T00:00:00.000Z" form; we only need t_live
    # here for the post-restore equality check.
    tid = "t-019b76da-a800-7d79-b000-0000000000f1"

    # Live row with the LATER created_at.
    async with session_maker() as session, session.begin():
        session.add(
            Task(
                id=tid,
                status="pending",
                created_at=t_live,
                updated_at=t_live,
                actor_kind="system",
                actor_id="test",
                title="live-row",
            )
        )

    # Snapshot row whose payload's task carries the EARLIER created_at.
    payload = {
        "version": 1,
        "tasks": [
            {
                "id": tid,
                "status": "pending",
                "created_at": "2026-01-01T00:00:00.000Z",
                "updated_at": "2026-01-01T00:00:00.000Z",
                "actor_kind": "system",
                "actor_id": "test",
                "title": "snap-row",
                "last_event_id": None,
            }
        ],
        "sessions": [],
        "cursor_emitted_at_monotonic_ns": 1,
    }
    snap_id = new_uuid7(clock=fixed_clock)
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    async with session_maker() as session, session.begin():
        session.add(
            Snapshot(
                id=snap_id,
                created_at=FROZEN_EPOCH,
                cursor_event_id="e-019b76da-a800-7d79-b000-0000000000f1",
                event_count=0,
                byte_size=len(payload_json.encode("utf-8")),
                payload_json=payload_json,
            )
        )

    await restore_state_from_latest_snapshot(session_maker)

    async with session_maker() as session:
        row = (await session.execute(select(Task).where(Task.id == tid))).scalar_one()
    # created_at MUST still be the live (later) timestamp — NOT regressed
    # to the snapshot's older value.
    assert row.created_at.replace(tzinfo=UTC) == t_live, (
        f"F1 regression: created_at regressed from {t_live!r} to {row.created_at!r}"
    )
    # But other columns ARE updated by the snapshot (e.g. title was "live-row",
    # snapshot has "snap-row"). This confirms the UPSERT still ran and only
    # created_at was excluded.
    assert row.title == "snap-row"


@pytest.mark.asyncio
async def test_restore_orphan_session_in_snapshot_raises_value_error(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """F3 regression: snapshot with session referencing missing task → ValueError.

    Hand-crafts a v1 payload whose ``sessions[0].task_id`` is NOT present
    in ``tasks``. Restore must reject the payload with a descriptive
    ValueError naming the snapshot id and the missing task id, BEFORE any
    UPSERT touches the live tables.
    """
    payload = {
        "version": 1,
        "tasks": [],
        "sessions": [
            {
                "id": "s-X",
                "task_id": "t-MISSING",
                "worker_kind": "claude-code",
                "worktree_path": "/tmp/wt",
                "status": "active",
                "started_at": "2026-01-01T00:00:00.000Z",
                "ended_at": None,
                "last_heartbeat_at": None,
            }
        ],
        "cursor_emitted_at_monotonic_ns": 1,
    }
    snap_id = new_uuid7(clock=fixed_clock)
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    async with session_maker() as session, session.begin():
        session.add(
            Snapshot(
                id=snap_id,
                created_at=FROZEN_EPOCH,
                cursor_event_id="e-019b76da-a800-7d79-b000-0000000000f3",
                event_count=0,
                byte_size=len(payload_json.encode("utf-8")),
                payload_json=payload_json,
            )
        )

    with pytest.raises(ValueError, match="references missing task t-MISSING"):
        await restore_state_from_latest_snapshot(session_maker)


@pytest.mark.asyncio
async def test_restore_picks_higher_id_when_created_at_ties(
    session_maker: async_sessionmaker[AsyncSession], fixed_clock: FrozenClock
) -> None:
    """F4 regression: ``id DESC`` tiebreak when two snapshots share ``created_at``.

    Inserts two snapshots with the SAME ``created_at`` but different UUIDv7
    ids. The restore must pick the higher-id snapshot (which is also the
    one inserted later, since UUIDv7 is monotonic).
    """
    same_ca = FROZEN_EPOCH
    payload_a = {
        "version": 1,
        "tasks": [],
        "sessions": [],
        "cursor_emitted_at_monotonic_ns": 100,
    }
    payload_b = {
        "version": 1,
        "tasks": [],
        "sessions": [],
        "cursor_emitted_at_monotonic_ns": 200,
    }
    pa = json.dumps(payload_a, sort_keys=True, separators=(",", ":"))
    pb = json.dumps(payload_b, sort_keys=True, separators=(",", ":"))
    # Force ids in a known order: id_b is HIGHER (lexicographic) than id_a.
    id_a = "00000000-0000-7000-8000-000000000001"
    id_b = "00000000-0000-7000-8000-000000000002"
    async with session_maker() as session, session.begin():
        session.add(
            Snapshot(
                id=id_a,
                created_at=same_ca,
                cursor_event_id="e-a",
                event_count=0,
                byte_size=len(pa.encode("utf-8")),
                payload_json=pa,
            )
        )
        session.add(
            Snapshot(
                id=id_b,
                created_at=same_ca,
                cursor_event_id="e-b",
                event_count=0,
                byte_size=len(pb.encode("utf-8")),
                payload_json=pb,
            )
        )

    cursor = await restore_state_from_latest_snapshot(session_maker)
    assert cursor == 200, "id DESC tiebreak failed: should pick the higher-id snapshot"

    # compute_replay_cursor must agree with restore on the tiebreak.
    cursor2 = await compute_replay_cursor(session_maker)
    assert cursor2 == 200


@pytest.mark.asyncio
async def test_compute_events_max_cursor_returns_zero_when_empty(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """F9 split: events-only cursor helper returns 0 on empty events table."""
    cursor = await compute_events_max_cursor(session_maker)
    assert cursor == 0


@pytest.mark.asyncio
async def test_compute_events_max_cursor_returns_max(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """F9 split: events-only cursor helper returns ``MAX(events.emitted_at_monotonic_ns)``.

    Crucially does NOT consider snapshots — its caller composes the max
    with the cursor returned from ``restore_state_from_latest_snapshot``.
    """
    rng = Random(11)
    clk = TickingClock(start_now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    await _seed_task(session_maker, task_id=tid, title="events-max")
    eid1 = f"e-{new_uuid7(clock=clk, rng=rng)}"
    eid2 = f"e-{new_uuid7(clock=clk, rng=rng)}"
    await _seed_event(session_maker, event_id=eid1, mono_ns=500, task_id=tid)
    await _seed_event(session_maker, event_id=eid2, mono_ns=5000, task_id=tid)
    cursor = await compute_events_max_cursor(session_maker)
    assert cursor == 5000
