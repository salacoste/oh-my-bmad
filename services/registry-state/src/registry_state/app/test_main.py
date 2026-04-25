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


# ===========================================================================
# Story 2.6 integration tests — snapshot capture + replay on startup
# ===========================================================================


def _build_snapshot_journey_envelopes(
    n_tasks: int, *, start_mono_ns: int = 1_000_000, tick_ns: int = 1_000_000
) -> list[EventEnvelope]:
    """Build *n_tasks* full 4-event journeys (created→planning→ready→executing).

    Returns a flat chronologically-ordered list of ``4 * n_tasks`` envelopes
    with strictly-increasing ``emitted_at_monotonic_ns``. Used by Story 2.6
    integration tests that need plenty of envelopes (50, 1000) to exercise
    snapshot capture + replay.
    """
    clock = TickingClock(start_now=FROZEN_EPOCH, start_ns=start_mono_ns, tick_ns=tick_ns)
    rng = Random(2026)
    envelopes: list[EventEnvelope] = []
    for _ in range(n_tasks):
        task_id = new_task_id(clock=clock, rng=rng)
        session_id = new_session_id(clock=clock, rng=rng)

        def _env(type_: str, payload: dict[str, object]) -> EventEnvelope:
            return EventEnvelope(
                event_id=new_event_id(clock=clock, rng=rng),
                schema_version="1.0.0",
                type=type_,  # noqa: EVT001 — variable type passed from caller
                emitted_at=clock.now(),
                emitted_at_monotonic_ns=clock.monotonic_ns(),
                actor=_ACTOR,
                payload=payload,
                request_id=new_uuid7(clock=clock, rng=rng),
            )

        envelopes.append(_env("task.created", {"task_id": task_id, "title": "snap-task"}))
        envelopes.append(_env("task.planning.started", {"task_id": task_id}))
        envelopes.append(_env("task.plan.ready", {"task_id": task_id, "plan_summary": "plan"}))
        envelopes.append(
            _env(
                "task.execution.started",
                {"task_id": task_id, "session_id": session_id},
            )
        )
    return envelopes


async def _count_table(db_url: str, table: str) -> int:
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        async with eng.connect() as conn:
            result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608 — table is a literal in our control
            return int(result.scalar_one())
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_run_subscriber_captures_snapshots_during_replay(tmp_path: Path) -> None:
    """25 envelopes (~6 tasks) with snapshot_interval=10 → 2 snapshots captured.

    Walks 6 task journeys (24 envelopes) plus one extra task.created to land
    on exactly 25 events. Snapshots fire after the 10th and 20th applied
    event; tally lands at 5 → no third snapshot.
    """
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    envelopes = _build_snapshot_journey_envelopes(6)  # 24 envs
    # Add one extra task.created to reach 25 total.
    extra_clock = TickingClock(start_now=FROZEN_EPOCH, start_ns=999_000_000, tick_ns=1_000_000)
    extra_rng = Random(99)
    extra_task = new_task_id(clock=extra_clock, rng=extra_rng)
    envelopes.append(
        EventEnvelope(
            event_id=new_event_id(clock=extra_clock, rng=extra_rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 — registry only populated at runtime
            emitted_at=extra_clock.now(),
            emitted_at_monotonic_ns=extra_clock.monotonic_ns(),
            actor=_ACTOR,
            payload={"task_id": extra_task, "title": "extra"},
            request_id=new_uuid7(clock=extra_clock, rng=extra_rng),
        )
    )
    assert len(envelopes) == 25

    writer_clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=log_dir, clock=writer_clock)
    await writer.recover()
    for env in envelopes:
        await writer.append(env)
    await writer.close()

    stop = asyncio.Event()
    # Use a TickingClock so each snapshot id (UUIDv7) is unique.
    sub_clock = TickingClock(start_now=FROZEN_EPOCH, start_ns=0, tick_ns=1_000_000)
    sub_task = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=sub_clock,
            poll_interval_s=0.05,
            stop_event=stop,
            snapshot_interval=10,
        )
    )

    # Wait until at least 25 events are materialized.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if await _count_table(db_url, "events") >= 25:
            break
        await asyncio.sleep(0.05)

    stop.set()
    await asyncio.wait_for(sub_task, timeout=2.0)

    snap_count = await _count_table(db_url, "snapshots")
    assert snap_count == 2, f"expected 2 snapshots, got {snap_count}"

    # Latest snapshot's event_count >= 20 (snapshots track running count).
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        async with eng.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT event_count FROM snapshots ORDER BY created_at DESC LIMIT 1")
                )
            ).one()
        assert int(row[0]) >= 20, f"latest snapshot event_count {row[0]} < 20"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_run_subscriber_resumes_from_snapshot_skipping_events(tmp_path: Path) -> None:
    """Pre-populate DB with 10 events + a snapshot; subscriber's apply_many gets only 11-20."""
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    # 5 task journeys = 20 envelopes; we'll persist the first 10 + a snapshot.
    envelopes = _build_snapshot_journey_envelopes(5)
    assert len(envelopes) == 20

    # Write all 20 to the JSONL log so the subscriber sees them on disk.
    writer = EventLogWriter(base_dir=log_dir, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    await writer.recover()
    for env in envelopes:
        await writer.append(env)
    await writer.close()

    # Pre-populate DB: apply the first 10 envelopes via a fresh Materializer,
    # then capture a snapshot at envelope #10.
    from registry_state.adapters.sqlite_store import create_engine as _ce
    from registry_state.domain.handlers import register_default_handlers
    from registry_state.domain.materializer import Materializer
    from registry_state.domain.snapshots import SnapshotPolicy

    pre_engine = _ce(db_url)
    try:
        pre_sm = get_session(pre_engine)
        pre_mat = Materializer(session_maker=pre_sm)
        register_default_handlers(pre_mat)
        await pre_mat.apply_many(envelopes[:10])
        pre_policy = SnapshotPolicy(
            session_maker=pre_sm,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            interval=1,
        )
        await pre_policy.capture(envelopes[9])  # snapshot at envelope #10
    finally:
        await pre_engine.dispose()

    # Sanity: 10 events + 1 snapshot.
    assert await _count_table(db_url, "events") == 10
    assert await _count_table(db_url, "snapshots") == 1

    # Now boot the subscriber. We monkey-patch Materializer.apply_many on the
    # specific instance the subscriber creates by intercepting it via a
    # subclass — but the simpler route is to wrap the module-level handler
    # registration. Instead, we use a tiny shim Materializer in app.main by
    # monkey-patching the class globally for this test.
    import registry_state.app.main as main_mod
    from registry_state.domain.materializer import Materializer as RealMaterializer

    received: list[list[EventEnvelope]] = []

    class CountingMaterializer(RealMaterializer):
        async def apply_many(self, envelopes_arg):  # type: ignore[no-untyped-def]
            collected = list(envelopes_arg)
            received.append(collected)
            return await super().apply_many(collected)

    monkey_target = main_mod.Materializer  # type: ignore[attr-defined]
    main_mod.Materializer = CountingMaterializer  # type: ignore[attr-defined]
    try:
        stop = asyncio.Event()
        sub_task = asyncio.create_task(
            run_subscriber(
                base_dir=log_dir,
                db_url=db_url,
                clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
                poll_interval_s=0.05,
                stop_event=stop,
                snapshot_interval=1000,  # avoid extra snapshots during this test
            )
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if await _count_table(db_url, "events") >= 20:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(sub_task, timeout=2.0)
    finally:
        main_mod.Materializer = monkey_target  # type: ignore[attr-defined]

    # Assert apply_many was invoked with envelopes[10:20] only — no envs from
    # 0..9 (those have monotonic_ns <= snapshot's cursor and are skipped).
    flat_received: list[EventEnvelope] = [e for batch in received for e in batch]
    received_ids = {env.event_id for env in flat_received}
    expected_ids = {env.event_id for env in envelopes[10:20]}
    skipped_ids = {env.event_id for env in envelopes[:10]}
    assert received_ids >= expected_ids, "subscriber did not apply envelopes 11-20"
    assert received_ids.isdisjoint(skipped_ids), (
        f"subscriber re-applied snapshot-covered envelopes: {received_ids & skipped_ids}"
    )
    # Every event ends up in DB.
    assert await _count_table(db_url, "events") == 20


@pytest.mark.asyncio
async def test_full_replay_vs_snapshot_replay_byte_identical(tmp_path: Path) -> None:
    """Epic-2.6 BDD AC: full replay == snapshot replay, byte-for-byte across all 3 tables."""
    log_dir_a = tmp_path / "events_a"
    log_dir_a.mkdir()
    db_path_a = tmp_path / "state_a.sqlite3"
    db_url_a = f"sqlite+aiosqlite:///{db_path_a}"
    await _make_db(db_url_a)

    log_dir_b = tmp_path / "events_b"
    log_dir_b.mkdir()
    db_path_b = tmp_path / "state_b.sqlite3"
    db_url_b = f"sqlite+aiosqlite:///{db_path_b}"
    await _make_db(db_url_b)

    # Same 50-envelope sequence written into BOTH log dirs.
    envelopes = _build_snapshot_journey_envelopes(12)  # 48 envs
    extra_clock = TickingClock(start_now=FROZEN_EPOCH, start_ns=900_000_000, tick_ns=1_000_000)
    extra_rng = Random(7)
    for _ in range(2):  # +2 task.created → 50 total
        tid = new_task_id(clock=extra_clock, rng=extra_rng)
        envelopes.append(
            EventEnvelope(
                event_id=new_event_id(clock=extra_clock, rng=extra_rng),
                schema_version="1.0.0",
                type="task.created",  # noqa: EVT001 — registry only populated at runtime
                emitted_at=extra_clock.now(),
                emitted_at_monotonic_ns=extra_clock.monotonic_ns(),
                actor=_ACTOR,
                payload={"task_id": tid, "title": "extra"},
                request_id=new_uuid7(clock=extra_clock, rng=extra_rng),
            )
        )
    assert len(envelopes) == 50

    for log_dir in (log_dir_a, log_dir_b):
        writer = EventLogWriter(base_dir=log_dir, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
        await writer.recover()
        for env in envelopes:
            await writer.append(env)
        await writer.close()

    # Run A: full replay (no snapshots ever taken — interval is huge).
    stop_a = asyncio.Event()
    task_a = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir_a,
            db_url=db_url_a,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            poll_interval_s=0.05,
            stop_event=stop_a,
            snapshot_interval=10_000,
        )
    )
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if await _count_table(db_url_a, "events") >= 50:
            break
        await asyncio.sleep(0.05)
    stop_a.set()
    await asyncio.wait_for(task_a, timeout=2.0)

    # Run B: snapshot-driven (interval=10 → 4-5 snapshots).
    stop_b = asyncio.Event()
    sub_clock_b = TickingClock(start_now=FROZEN_EPOCH, start_ns=0, tick_ns=1_000_000)
    task_b = asyncio.create_task(
        run_subscriber(
            base_dir=log_dir_b,
            db_url=db_url_b,
            clock=sub_clock_b,
            poll_interval_s=0.05,
            stop_event=stop_b,
            snapshot_interval=10,
        )
    )
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if await _count_table(db_url_b, "events") >= 50:
            break
        await asyncio.sleep(0.05)
    stop_b.set()
    await asyncio.wait_for(task_b, timeout=2.0)

    # Snapshots were captured.
    assert await _count_table(db_url_b, "snapshots") >= 4

    # The byte-for-byte equivalence: tasks + sessions + events identical.
    state_a = await _capture_db_state(db_url_a)
    state_b = await _capture_db_state(db_url_b)
    assert state_a == state_b, (
        f"BDD AC failure: full-replay state != snapshot-replay state\nA: {state_a}\nB: {state_b}"
    )


@pytest.mark.asyncio
async def test_synthetic_1k_replay_under_500ms(tmp_path: Path) -> None:
    """Pre-populate 1K events + a snapshot near the end; restore + cursor < 500ms."""
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    await _make_db(db_url)

    # 250 task journeys = 1000 envelopes.
    envelopes = _build_snapshot_journey_envelopes(250)
    assert len(envelopes) == 1000

    # Apply all 1000 + capture a snapshot at event 900.
    from registry_state.adapters.sqlite_store import create_engine as _ce
    from registry_state.domain.handlers import register_default_handlers
    from registry_state.domain.materializer import Materializer
    from registry_state.domain.snapshots import SnapshotPolicy

    pre_engine = _ce(db_url)
    try:
        pre_sm = get_session(pre_engine)
        pre_mat = Materializer(session_maker=pre_sm)
        register_default_handlers(pre_mat)
        # Apply first 900 events to land on a safe handler boundary.
        await pre_mat.apply_many(envelopes[:900])
        pre_policy = SnapshotPolicy(
            session_maker=pre_sm,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            interval=1,
        )
        await pre_policy.capture(envelopes[899])
        # Apply the remaining 100 events.
        await pre_mat.apply_many(envelopes[900:])
    finally:
        await pre_engine.dispose()

    # Now measure compute_replay_cursor + restore_state_from_latest_snapshot.
    from registry_state.domain.recovery import (
        compute_replay_cursor,
        restore_state_from_latest_snapshot,
    )

    measure_engine = _ce(db_url)
    try:
        sm = get_session(measure_engine)
        t0 = time.monotonic()
        await restore_state_from_latest_snapshot(sm)
        cursor = await compute_replay_cursor(sm)
        elapsed_ms = (time.monotonic() - t0) * 1000
    finally:
        await measure_engine.dispose()

    assert cursor > 0
    assert elapsed_ms < 500, f"NFR-P3 (1K) breach: {elapsed_ms:.1f}ms (budget 500ms)"
