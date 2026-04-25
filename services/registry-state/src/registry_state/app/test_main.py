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
        type="task.created",  # noqa: EVT001 — registry only populated after registry_state.domain.event_types is imported at runtime; the AST scanner can't see that
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": tid, "title": "Live tail test"},
        request_id=new_uuid7(clock=clk, rng=rng),
    )
    writer = EventLogWriter(base_dir=log_dir, clock=sub_clock)
    await writer.recover()
    # Capture t0 *after* the durable append completes — the SLA budget covers
    # subscriber-side propagation latency only.  Earlier versions started the
    # timer before the fdatasync returned, conflating writer + reader time.
    # We deliberately leave the writer open: closing it forces a flush we do
    # not need (append already fdatasync'd) and would race with the
    # subscriber's tail-read on poll boundaries.
    await writer.append(env)
    t0 = time.monotonic()

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
    await writer.close()
    await eng.dispose()

    assert found, "task row never appeared within 1s"
    assert latency_ms < 200, f"SLA breach: materialized in {latency_ms:.1f}ms (budget 200ms)"


async def _capture_db_state(db_url: str) -> dict[str, list[tuple[object, ...]]]:
    """Snapshot the rows that the materializer should converge on.

    Returns lists of plain tuples — they sort and compare predictably and
    avoid pulling SQLAlchemy session-bound state into the assertion.
    """
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        async with eng.connect() as conn:
            event_rows = (
                await conn.execute(
                    text(
                        "SELECT id, type, emitted_at_monotonic_ns "
                        "FROM events ORDER BY emitted_at_monotonic_ns, id"
                    )
                )
            ).all()
            task_rows = (
                await conn.execute(text("SELECT id, status, last_event_id FROM tasks ORDER BY id"))
            ).all()
            session_rows = (
                await conn.execute(
                    text("SELECT id, task_id, status, worker_kind FROM sessions ORDER BY id")
                )
            ).all()
    finally:
        await eng.dispose()
    return {
        "events": [tuple(r) for r in event_rows],
        "tasks": [tuple(r) for r in task_rows],
        "sessions": [tuple(r) for r in session_rows],
    }


async def _wait_for_status(db_url: str, *, task_id: str, expected: str, timeout_s: float) -> bool:
    """Poll *db_url* every 50ms until ``tasks.status == expected`` or *timeout_s*."""
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        sm = get_session(eng)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            async with sm() as session:
                result = await session.execute(
                    text("SELECT status FROM tasks WHERE id = :tid"), {"tid": task_id}
                )
                row = result.one_or_none()
            if row is not None and row[0] == expected:
                return True
        return False
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_run_subscriber_is_idempotent_across_3x_replay(tmp_path: Path) -> None:
    """Run subscriber 3× against the SAME DB; full DB snapshot identical each time.

    This is the strict reading of "the materializer is idempotent": replaying
    the same event log 3× into the same SQLite database must converge to the
    exact same rows in tasks/sessions/events.  Earlier versions of this test
    used a fresh DB per run, which could only catch a per-run divergence
    (i.e., non-determinism in the handlers themselves) — it could NOT catch
    a duplicate-row bug, because the second run started from an empty DB.
    """
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # Schema once, shared across all 3 runs.
    await _make_db(db_url)

    envelopes, task_id, _session_id = _build_journey_envelopes()
    writer_clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=log_dir, clock=writer_clock)
    await writer.recover()
    for env in envelopes:
        await writer.append(env)
    await writer.close()

    snapshots: list[dict[str, list[tuple[object, ...]]]] = []

    for _run in range(3):
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

        reached = await _wait_for_status(
            db_url, task_id=task_id, expected="executing", timeout_s=2.0
        )

        stop.set()
        await asyncio.wait_for(sub, timeout=2.0)

        assert reached, "subscriber never reached 'executing' state within 2s"
        snapshots.append(await _capture_db_state(db_url))

    assert snapshots[0] == snapshots[1] == snapshots[2], (
        f"3× replay produced different states; snapshots: {snapshots}"
    )
    # Sanity: verify last_event_id points at the execution.started event.
    last_event_id_by_task = dict((row[0], row[2]) for row in snapshots[0]["tasks"])
    assert last_event_id_by_task[task_id] == envelopes[-1].event_id


@pytest.mark.asyncio
async def test_run_subscriber_tails_across_utc_midnight_boundary(tmp_path: Path) -> None:
    """F1 probe: events appended to yesterday.jsonl must materialize even after rollover.

    Direct repro of the bug fixed by F1.  Writes a ``task.created`` envelope
    directly into a YYYY-MM-DD.jsonl file dated ``yesterday``, then to a
    second file dated ``today``.  The subscriber must tail BOTH files (not
    just the file matching today's date) and materialize both events within
    the SLA budget.
    """
    from datetime import UTC, datetime, timedelta

    from events import to_canonical_json

    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    # Anchor the test on a specific UTC instant so "yesterday" / "today"
    # filenames are deterministic.
    today_dt = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
    yesterday_dt = today_dt - timedelta(days=1)

    def _write_envelope(when: datetime, mono_ns: int, seed: int) -> str:
        rng = Random(seed)
        clk = FrozenClock(mono_ns=mono_ns, now=when)
        tid = new_task_id(clock=clk, rng=rng)
        env = EventEnvelope(
            event_id=new_event_id(clock=clk, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 — registry only populated after registry_state.domain.event_types is imported at runtime; the AST scanner can't see that
            emitted_at=when,
            emitted_at_monotonic_ns=mono_ns,
            actor=_ACTOR,
            payload={"task_id": tid, "title": f"midnight-{seed}"},
            request_id=new_uuid7(clock=clk, rng=rng),
        )
        path = log_dir / f"{when.date().isoformat()}.jsonl"
        path.write_bytes(to_canonical_json(env) + b"\n")
        return tid

    # Step 1: log starts EMPTY when the subscriber boots — both tail-loop
    # paths (yesterday.jsonl and today.jsonl) must come into existence and
    # be picked up by the rolling rescan.  The pre-F1 implementation only
    # tailed today.jsonl, so the late-yesterday append below would never
    # be materialized until process restart.
    stop = asyncio.Event()
    sub_task = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=FrozenClock(mono_ns=0, now=today_dt),
            poll_interval_s=0.05,
            stop_event=stop,
        )
    )

    # Wait for the subscriber to settle into its tail loop on an empty log.
    await asyncio.sleep(0.15)

    # Step 2: simulate the real-world midnight race in monotonic order —
    # the LATE-pre-midnight event lands in yesterday.jsonl with the lower
    # mono_ns, and the post-rollover event lands in today.jsonl with the
    # higher mono_ns.  Both must materialise.
    yesterday_tid = _write_envelope(yesterday_dt, mono_ns=1_000_000, seed=11)
    today_tid = _write_envelope(today_dt, mono_ns=2_000_000, seed=22)

    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    sm = get_session(eng)
    deadline = time.monotonic() + 2.0
    found_yesterday = False
    found_today = False
    while time.monotonic() < deadline and not (found_yesterday and found_today):
        await asyncio.sleep(0.05)
        async with sm() as session:
            for tid, label in (
                (yesterday_tid, "yesterday"),
                (today_tid, "today"),
            ):
                row = (
                    await session.execute(text("SELECT 1 FROM tasks WHERE id = :tid"), {"tid": tid})
                ).one_or_none()
                if row is not None:
                    if label == "yesterday":
                        found_yesterday = True
                    else:
                        found_today = True

    stop.set()
    await asyncio.wait_for(sub_task, timeout=2.0)
    await eng.dispose()

    assert found_today, "today.jsonl event was not materialized"
    assert found_yesterday, (
        "yesterday.jsonl event was not materialized — "
        "tail loop is missing the rollover-safety scan (F1 regression)"
    )


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
