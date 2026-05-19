"""Day-rollover integration test for metrics-subscriber (Story 10.2 AC8).

Scenario:

1. Write 100 envelopes to ``events-2026-05-19.jsonl``.
2. Tail-process them via :func:`run_subscriber` with a deterministic
   clock at 23:59:00 UTC, ``poll_interval_s`` very short so the loop
   spins.
3. Advance the clock to 2026-05-20 00:00:01 UTC.
4. Write 100 envelopes to ``events-2026-05-20.jsonl``.
5. Confirm the tail loop transitions cleanly (no dropped envelopes;
   final cursor points at the new path).
"""

from __future__ import annotations

import asyncio
import json
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
    """Mutable clock — :attr:`current` may be reassigned from the test."""

    def __init__(self, *, current: datetime) -> None:
        self.current = current
        self._mono = 0

    def now(self) -> datetime:
        return self.current

    def monotonic_ns(self) -> int:
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
    """AC8: tail-loop seamlessly switches to next day's file at midnight."""
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

    # Patch the EventLogReader's clock + the cursor's clock via run_subscriber.
    # We construct a custom run by inlining the relevant logic to avoid
    # globally patching SystemClock — keeping the test deterministic.
    from events.log_reader import EventLogReader

    from metrics_subscriber.cursor import CursorPersistence

    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    reader = EventLogReader(settings.event_log_dir, clock=clock)
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

    # Cross midnight: advance the clock + write day1 envelopes.
    clock.current = day1
    day1_envs = [_make_envelope(f"d1-{i}", mono_seed=1000 + i) for i in range(100)]
    _write_envelopes(day1_path, day1_envs)

    # Wait for the 200 total.
    for _ in range(300):
        if len(received) >= 200:
            break
        await asyncio.sleep(0.02)

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 200
    assert received[:100] == day0_envs
    assert received[100:] == day1_envs

    # Persist final state + verify cursor points at day 1's file.
    cursor.persist_now(reader.cursor_offset, reader.current_path)
    body = json.loads(cursor_path.read_text())
    assert body["path"] == str(day1_path)
    assert body["offset"] == day1_path.stat().st_size
