"""Day-rollover integration test for metrics-subscriber (Story 10.2 AC8).

Scenario:

1. Write 100 envelopes to ``2026-05-19.jsonl``.
2. Tail-process them with a deterministic clock at 23:59 UTC.
3. Advance the clock to 2026-05-20 00:01 UTC, write 100 envelopes to
   ``2026-05-20.jsonl``.
4. Confirm the tail loop transitions cleanly via VH-6 file-existence
   + quiescence detection (no dropped envelopes).
5. **VM-7 fix**: compare by ``event_id`` lists rather than full
   envelope equality (which depended on payload-type registration
   ordering and was fragile under suite-wide reordering).  Also set
   ``clock.current = day1`` BEFORE writing day1 envelopes so the
   rollover-detection-quiescence window observes the writer-quiescent
   state before transitioning.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    new_event_id,
    new_uuid7,
    to_canonical_json,
)
from events.clock import Clock
from events.schema_registry import register
from pydantic import BaseModel

from metrics_subscriber.app.config import MetricsSubscriberSettings


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None, None, None]:
    """Idempotently register a test-only event type for the envelopes.

    We deliberately AVOID ``unregister_all()`` (Epic 9 retro D5 — schema-
    registry is global session-scoped). Wiping the registry would strip
    production-side registrations made at module-load in registry-state
    (``event_types.ensure_registered()``) and break downstream tests
    that share the pytest session.
    """
    register("test.metrics_subscriber.envelope", "1.0.0", _SimplePayload)
    yield


_ACTOR = Actor(kind="system", id="test")
_DEFAULT_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


class _StepClock(Clock):
    """Mutable clock — :attr:`current` may be reassigned from the test.

    P2-M3 (E7): the monotonic counter is incremented under an explicit
    ``threading.Lock`` so the test helper does not silently break
    under PEP 703 (no-GIL Python 3.14+).  Production code uses the
    ``time`` module's monotonic clock which is GIL-independent.
    """

    def __init__(self, *, current: datetime) -> None:
        self.current = current
        self._mono = 0
        self._mono_lock = threading.Lock()

    def now(self) -> datetime:
        return self.current

    def monotonic_ns(self) -> int:
        with self._mono_lock:
            self._mono += 1
            return self._mono


def _make_envelope(value: str, mono_seed: int) -> EventEnvelope:
    rng = Random(mono_seed)
    from events.clock import FrozenClock

    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    return EventEnvelope(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="test.metrics_subscriber.envelope",  # noqa: EVT001 test-only fixture envelope
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=mono_seed,
        actor=_ACTOR,
        payload={"value": value},
        trace_id=_DEFAULT_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _write_envelopes(path: Path, envs: list[EventEnvelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for env in envs:
            f.write(to_canonical_json(env) + b"\n")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_day_rollover_transitions_to_new_file(tmp_path: Path) -> None:
    """AC8 + VH-6: tail-loop seamlessly switches to next day's file."""
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)

    day0 = datetime(2026, 5, 19, 23, 59, 0, tzinfo=UTC)
    day1 = day0 + timedelta(minutes=2)  # → 2026-05-20 00:01:00 UTC
    day0_path = events_dir / "2026-05-19.jsonl"
    day1_path = events_dir / "2026-05-20.jsonl"

    day0_envs = [_make_envelope(f"d0-{i}", mono_seed=i) for i in range(100)]
    _write_envelopes(day0_path, day0_envs)

    clock = _StepClock(current=day0)

    from events.log_reader import EventLogReader

    from metrics_subscriber.cursor import CursorPersistence

    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    # VH-6: zero quiescence window so the test does not have to wait
    # 60s for rollover detection.  Production default is 60s.
    reader = EventLogReader(settings.event_log_dir, clock=clock, rollover_quiescence_s=0.0)
    cursor = CursorPersistence(
        settings.cursor_path,
        persist_every=settings.persist_every_n_events,
        clock=clock,
    )
    cursor.restore_into(reader, base_dir=settings.event_log_dir)

    received: list[EventEnvelope] = []
    stop = asyncio.Event()

    async def _drive() -> None:
        async for envelope in reader.tail(
            poll_interval_s=settings.poll_interval_s, stop_event=stop
        ):
            received.append(envelope)
            cursor.note_event_processed()
            cursor.maybe_persist(reader.cursor_offset, reader.current_path)

    task = asyncio.create_task(_drive())

    # Wait for the first 100 envelopes (day 0) to be consumed.
    for _ in range(200):
        if len(received) >= 100:
            break
        await asyncio.sleep(0.02)
    assert len(received) == 100

    # VM-7 fix: advance clock BEFORE writing day1 envelopes so the
    # quiescence window observes a stable yesterday-size while today's
    # path exists.
    clock.current = day1
    day1_envs = [_make_envelope(f"d1-{i}", mono_seed=1000 + i) for i in range(100)]
    _write_envelopes(day1_path, day1_envs)

    # Wait for the 200 total.
    for _ in range(300):
        if len(received) >= 200:
            break
        await asyncio.sleep(0.02)

    stop.set()
    await asyncio.wait_for(task, timeout=5.0)

    assert len(received) == 200
    # VM-7: compare by event_id lists (not full envelope equality) to
    # avoid coupling to payload-type registration ordering.
    received_ids = [e.event_id for e in received]
    expected_ids = [e.event_id for e in day0_envs] + [e.event_id for e in day1_envs]
    assert received_ids == expected_ids

    # Persist final state + verify cursor points at day 1's file.
    cursor.persist_now(reader.cursor_offset, reader.current_path)
    body = json.loads(cursor_path.read_text())
    assert body["path"] == str(day1_path)
    assert body["offset"] == day1_path.stat().st_size


@pytest.mark.integration
@pytest.mark.asyncio
async def test_day_rollover_writer_appending_past_reader_clock_does_not_drop_events(
    tmp_path: Path,
) -> None:
    """VH-6 — writer keeps appending past reader's clock midnight; no events lost.

    Scenario:

    1. Reader's clock advances past UTC midnight.
    2. Writer is still appending to yesterday's file (NTP skew).
    3. Today's file does NOT exist yet.
    4. Reader must continue draining yesterday's file (NOT switch to
       a non-existent today-path at offset 0).
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    day0 = datetime(2026, 5, 19, 23, 59, 30, tzinfo=UTC)
    day1 = day0 + timedelta(minutes=2)
    yesterday_path = events_dir / "2026-05-19.jsonl"

    envs = [_make_envelope(f"e{i}", mono_seed=i) for i in range(10)]
    _write_envelopes(yesterday_path, envs[:5])  # 5 initial events

    clock = _StepClock(current=day0)

    from events.log_reader import EventLogReader

    reader = EventLogReader(events_dir, clock=clock, rollover_quiescence_s=0.0)
    reader.open(initial_offset=0)
    received: list[EventEnvelope] = []
    stop = asyncio.Event()

    async def _drive() -> None:
        async for env in reader.tail(poll_interval_s=0.01, stop_event=stop):
            received.append(env)

    task = asyncio.create_task(_drive())
    for _ in range(100):
        if len(received) >= 5:
            break
        await asyncio.sleep(0.01)

    # Reader's clock crosses midnight, but today's file does NOT
    # exist; writer continues appending to yesterday.
    clock.current = day1
    with open(yesterday_path, "ab") as f:
        for env in envs[5:]:
            f.write(to_canonical_json(env) + b"\n")

    for _ in range(200):
        if len(received) >= 10:
            break
        await asyncio.sleep(0.01)

    stop.set()
    await asyncio.wait_for(task, timeout=5.0)

    # All 10 events received from yesterday's file (no false rollover).
    assert [e.event_id for e in received] == [e.event_id for e in envs]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_restart_after_midnight_completes_within_5s(tmp_path: Path) -> None:
    """P2-H1 (Q4) — restart-after-midnight uses fast-path skip.

    Scenario: subscriber restarts AFTER midnight; cursor.json points
    at yesterday's file fully drained (offset == file_size); today's
    file already exists with non-zero size.  Pre-pass-2 the
    quiescence default was 60s — the reader would idle for 60s before
    transitioning to today.  Pass-2's fast-path skip should
    short-circuit (no quiescence wait) since there is nothing left to
    drain on yesterday.

    Uses **production defaults** for ``rollover_quiescence_s`` (5.0s
    after P2-H1) — the fast-path means the wall-clock should be well
    under 5s.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    yesterday = datetime(2026, 5, 18, 23, 59, 30, tzinfo=UTC)
    today = yesterday + timedelta(minutes=2)  # → 2026-05-19 00:01:30 UTC
    yesterday_path = events_dir / "2026-05-18.jsonl"
    today_path = events_dir / "2026-05-19.jsonl"

    # Yesterday fully drained; today already has a fresh event.
    yesterday_envs = [_make_envelope(f"y{i}", mono_seed=i) for i in range(3)]
    today_envs = [_make_envelope(f"t{i}", mono_seed=100 + i) for i in range(3)]
    _write_envelopes(yesterday_path, yesterday_envs)
    _write_envelopes(today_path, today_envs)

    clock = _StepClock(current=today)
    from events.log_reader import EventLogReader

    # **Production default** rollover_quiescence_s — exercising the
    # fast-path skip is the entire point of this test.
    reader = EventLogReader(events_dir, clock=clock)
    # Restart seats reader at yesterday EOF (fully drained).
    reader.seek(path=yesterday_path, offset=yesterday_path.stat().st_size)
    received: list[EventEnvelope] = []
    stop = asyncio.Event()

    async def _drive() -> None:
        async for env in reader.tail(poll_interval_s=0.01, stop_event=stop):
            received.append(env)
            if len(received) >= len(today_envs):
                stop.set()
                return

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    task = asyncio.create_task(_drive())
    await asyncio.wait_for(task, timeout=5.0)
    elapsed = loop.time() - t0
    # Fast-path means we should NOT have waited the full 5s quiescence
    # window.  Generous ceiling for CI variance.
    assert elapsed < 2.0, f"P2-H1 fast-path did not engage: elapsed={elapsed:.3f}s"
    assert [e.event_id for e in received] == [e.event_id for e in today_envs]
