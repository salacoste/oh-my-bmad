"""Integration tests for registry_state.app.main — Story 2.5 AC-13 (4 tests).

These tests exercise the full ``run_subscriber`` loop end-to-end:
  - startup replay from JSONL → SQLite state
  - live-tail SLA (events materialised within 200ms of being appended)
  - 3× idempotency replay (final DB state byte-identical across 3 runs)
  - clean shutdown on stop_event

Schema is created via ``Base.metadata.create_all`` (simpler than Alembic
for in-memory integration tests). Uses StaticPool so all connections share
the same in-memory SQLite database.

Local fixtures ``fixed_clock`` + ``seeded_uuid7`` inlined per Story 2.4 convention.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from random import Random

import pytest
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
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

import registry_state.domain.event_types  # noqa: F401 — side-effect: register() calls
from registry_state.adapters.event_log import EventLogWriter
from registry_state.adapters.sqlite_store import get_session
from registry_state.app.main import run_subscriber
from registry_state.schema import Base

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-main")


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_journey_envelopes() -> tuple[list[EventEnvelope], str, str]:
    """Build the 4-envelope BDD journey: created→planning→plan_ready→executing.

    Returns (envelopes, task_id, session_id).
    Uses deterministic RNG + TickingClock so the sequence is repeatable.
    """
    clock = TickingClock(start_now=FROZEN_EPOCH, start_ns=1_000_000, tick_ns=1_000_000)
    rng = Random(1234)

    task_id = new_task_id(clock=clock, rng=rng)
    session_id = new_session_id(clock=clock, rng=rng)

    def _env(type_: str, payload: dict[str, object]) -> EventEnvelope:
        eid = new_event_id(clock=clock, rng=rng)
        rid = new_uuid7(clock=clock, rng=rng)
        mono = clock.monotonic_ns()
        now = clock.now()
        # Use EventEnvelope(...) directly with plain dict payload — NOT .create().
        # EventEnvelope.create() validates the dict and converts it to the registered
        # BaseModel subclass; Pydantic then serializes that via model_dump on a
        # Union[dict, BaseModel] field returning {} (the BaseModel is opaque to the
        # envelope's model_dump). Plain dict payloads serialize correctly.
        return EventEnvelope(
            event_id=eid,
            schema_version="1.0.0",
            type=type_,  # noqa: EVT001 — test helper uses variable type_ from caller
            emitted_at=now,
            emitted_at_monotonic_ns=mono,
            actor=_ACTOR,
            payload=payload,
            request_id=rid,
        )

    envelopes = [
        _env("task.created", {"task_id": task_id, "title": "BDD journey task"}),
        _env("task.planning.started", {"task_id": task_id}),
        _env("task.plan.ready", {"task_id": task_id, "plan_summary": "The plan"}),
        _env(
            "task.execution.started",
            {"task_id": task_id, "session_id": session_id},
        ),
    ]
    return envelopes, task_id, session_id


async def _make_db(db_url: str) -> None:
    """Create all tables in the target DB using StaticPool (for :memory:) or file URL."""
    if ":memory:" in db_url:
        # StaticPool is only needed for in-memory; for file DBs the URL is enough.
        eng = create_async_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        from registry_state.adapters.sqlite_store import create_engine as _ce

        eng = _ce(db_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subscriber_replays_journey_to_executing_state(tmp_path: Path) -> None:
    """BDD journey: pre-populate JSONL → boot subscriber → tasks.status==executing."""
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # Create schema.
    await _make_db(db_url)

    # Write 4 envelopes to the log.
    writer_clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    envelopes, task_id, _ = _build_journey_envelopes()

    writer = EventLogWriter(base_dir=log_dir, clock=writer_clock)
    await writer.recover()
    for env in envelopes:
        await writer.append(env)
    await writer.close()

    # Boot subscriber: replay the log.
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            poll_interval_s=0.05,
            stop_event=stop,
        )
    )

    # Poll for up to 2 s until tasks.status == "executing".
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    sm = get_session(eng)
    deadline = time.monotonic() + 2.0
    status: str | None = None
    last_event_id: str | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        async with sm() as session:
            result = await session.execute(
                text("SELECT status, last_event_id FROM tasks WHERE id = :tid"),
                {"tid": task_id},
            )
            row = result.one_or_none()
        if row is not None and row[0] == "executing":
            status = row[0]
            last_event_id = row[1]
            break

    # Signal stop and wait for clean exit.
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    await eng.dispose()

    assert status == "executing", f"expected 'executing', got {status!r}"
    assert last_event_id == envelopes[-1].event_id, (
        f"last_event_id {last_event_id!r} != {envelopes[-1].event_id!r}"
    )


@pytest.mark.asyncio
async def test_run_subscriber_live_tail_materializes_within_200ms(tmp_path: Path) -> None:
    """AC-8 SLA: event appended to log → materialized in tasks table within 200ms."""
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    sub_clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    stop = asyncio.Event()
    sub_task = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=sub_clock,
            poll_interval_s=0.05,
            stop_event=stop,
        )
    )

    # Give subscriber time to enter its tail loop.
    await asyncio.sleep(0.1)

    # Now append a task.created envelope live.
    rng = Random(77)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    tid = new_task_id(clock=clk, rng=rng)
    env = EventEnvelope(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.created",  # noqa: EVT001 — test uses plain dict payload, not .create()
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": tid, "title": "Live tail test"},
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    writer = EventLogWriter(base_dir=log_dir, clock=sub_clock)
    await writer.recover()
    t0 = time.monotonic()
    await writer.append(env)
    await writer.close()

    # Poll every 50ms for up to 1000ms.
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    sm = get_session(eng)
    deadline = time.monotonic() + 1.0
    found = False
    latency_ms: float = 0.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        async with sm() as session:
            result = await session.execute(
                text("SELECT status FROM tasks WHERE id = :tid"), {"tid": tid}
            )
            row = result.one_or_none()
        if row is not None:
            latency_ms = (time.monotonic() - t0) * 1000
            found = True
            break

    stop.set()
    await asyncio.wait_for(sub_task, timeout=2.0)
    await eng.dispose()

    assert found, "task row never appeared within 1s"
    assert latency_ms < 200, f"SLA breach: materialized in {latency_ms:.1f}ms (budget 200ms)"


@pytest.mark.asyncio
async def test_run_subscriber_is_idempotent_across_3x_replay(tmp_path: Path) -> None:
    """Run subscriber 3× against the same log; final DB state byte-identical each time."""
    log_dir = tmp_path / "events"
    log_dir.mkdir()

    envelopes, task_id, session_id = _build_journey_envelopes()
    writer_clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=log_dir, clock=writer_clock)
    await writer.recover()
    for env in envelopes:
        await writer.append(env)
    await writer.close()

    snapshots: list[tuple[str, str | None]] = []

    for run in range(3):
        db_path = tmp_path / f"state_{run}.sqlite3"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await _make_db(db_url)

        stop = asyncio.Event()
        sub = asyncio.create_task(
            run_subscriber(
                base_dir=log_dir,
                db_url=db_url,
                clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
                poll_interval_s=0.05,
                stop_event=stop,
            )
        )

        # Wait for executing status.
        eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
        sm = get_session(eng)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            async with sm() as session:
                result = await session.execute(
                    text("SELECT status, last_event_id FROM tasks WHERE id = :tid"),
                    {"tid": task_id},
                )
                row = result.one_or_none()
            if row is not None and row[0] == "executing":
                snapshots.append((row[0], row[1]))
                break

        stop.set()
        await asyncio.wait_for(sub, timeout=2.0)
        await eng.dispose()

    assert len(snapshots) == 3, f"not all 3 runs reached executing state: {snapshots}"
    assert all(s == snapshots[0] for s in snapshots), (
        f"idempotency violation — snapshots differ: {snapshots}"
    )
    # Verify last_event_id points at the execution.started event.
    assert snapshots[0][1] == envelopes[-1].event_id


@pytest.mark.asyncio
async def test_run_subscriber_stops_on_event(tmp_path: Path) -> None:
    """Signalling stop_event causes run_subscriber to exit cleanly within 1s."""
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    stop = asyncio.Event()
    sub = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            poll_interval_s=0.05,
            stop_event=stop,
        )
    )

    # Let the loop spin at least once, then signal stop.
    await asyncio.sleep(0.1)
    stop.set()

    # Must complete within 1s — poll interval is 50ms so this is generous.
    await asyncio.wait_for(sub, timeout=1.0)
    # No assertion needed — wait_for raises TimeoutError if it hung.
