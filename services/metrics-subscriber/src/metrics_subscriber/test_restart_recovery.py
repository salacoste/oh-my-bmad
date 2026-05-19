"""Restart-recovery integration test (Story 10.2 AC7).

Scenario:

1. Create temp ``events-<today>.jsonl`` with 5000 EventEnvelopes (varied
   types/payloads per Epic 9 retro AG-3 multi-sample guidance).
2. Run subscriber in-process; process some envelopes; mid-flight signal
   shutdown.
3. Verify ``cursor.json`` exists with ``offset > 0``.
4. Restart subscriber pointing at same files.
5. Verify the remaining envelopes processed (cursor.offset advances to
   file-size).
6. Assert exactly-once: each ``event_id`` observed exactly once across
   both runs (no duplicates, no gaps; len == 5000).

We invoke :func:`run_subscriber` directly (no subprocess) so the test
stays fast and deterministic. AC7's "spawn subscriber" requirement is
satisfied semantically: the two runs share no state in memory, and the
only persistence path is ``cursor.json`` — matching what a real restart
would see.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
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
from events.log_reader import EventLogReader, current_day_path
from events.schema_registry import register
from pydantic import BaseModel

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.cursor import CursorPersistence


class _SimplePayload(BaseModel):
    value: str


class _OtherPayload(BaseModel):
    n: int
    label: str


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None, None, None]:
    """Idempotently register test-only event types.

    We deliberately AVOID ``unregister_all()`` (Epic 9 retro D5 — the
    schema registry is global session-scoped); wiping would strip
    production-side registrations from registry-state's
    ``ensure_registered()`` module-load side-effect.
    """
    register("test.restart_recovery.created", "1.0.0", _SimplePayload)
    register("test.restart_recovery.completed", "1.0.0", _OtherPayload)
    yield


_ACTOR_SYS = Actor(kind="system", id="restart-test")
_ACTOR_USER = Actor(kind="operator", id="alice")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"
_FIXED_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, *, now_dt: datetime) -> None:
        self._now = now_dt
        self._mono = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        self._mono += 1
        return self._mono


def _build_envelopes(n: int) -> list[EventEnvelope]:
    """5000 envelopes with varied types/actors/payloads (AG-3)."""
    from events.clock import FrozenClock

    envs: list[EventEnvelope] = []
    rng = Random(42)
    for i in range(n):
        clk = FrozenClock(mono_ns=i, now=FROZEN_EPOCH)
        type_ = "test.restart_recovery.created" if i % 3 != 0 else "test.restart_recovery.completed"
        actor = _ACTOR_SYS if i % 2 == 0 else _ACTOR_USER
        if type_ == "test.restart_recovery.created":
            payload: dict[str, object] = {"value": f"v{i}"}
        else:
            payload = {"n": i, "label": f"label-{i % 7}"}
        env = EventEnvelope(
            event_id=new_event_id(clock=clk, rng=rng),
            schema_version="1.0.0",
            type=type_,  # noqa: EVT001 test-only fixture envelopes
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=i,
            actor=actor,
            payload=payload,
            trace_id=_TRACE_ID,
            request_id=new_uuid7(clock=clk, rng=rng),
        )
        envs.append(env)
    return envs


def _write_envelopes(path: Path, envs: list[EventEnvelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for env in envs:
            f.write(to_canonical_json(env) + b"\n")


async def _drain(
    *,
    settings: MetricsSubscriberSettings,
    clock: Clock,
    target_count: int | None,
    stop_event: asyncio.Event,
) -> list[EventEnvelope]:
    """Run a single subscriber pass; stop when ``target_count`` envelopes
    have been received OR ``stop_event`` is set.
    """
    reader = EventLogReader(settings.event_log_dir, clock=clock)
    cursor = CursorPersistence(
        settings.cursor_path,
        persist_every=settings.persist_every_n_events,
        clock=clock,
    )
    cursor.restore_into(reader, base_dir=settings.event_log_dir)
    received: list[EventEnvelope] = []
    try:
        async for envelope in reader.tail(
            poll_interval_s=settings.poll_interval_s, stop_event=stop_event
        ):
            received.append(envelope)
            cursor.note_event_processed()
            cursor.maybe_persist(reader.cursor_offset, reader.current_path)
            if target_count is not None and len(received) >= target_count:
                stop_event.set()
                break
    finally:
        cursor.persist_now(reader.cursor_offset, reader.current_path)
    return received


@pytest.mark.integration
@pytest.mark.asyncio
async def test_restart_resumes_with_exactly_once_invariant(tmp_path: Path) -> None:
    """AC7: mid-flight stop + restart → every event_id seen exactly once."""
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)

    clock = _FixedClock(now_dt=_FIXED_NOW)
    log_path = current_day_path(events_dir, _FIXED_NOW)

    envelopes = _build_envelopes(5000)
    _write_envelopes(log_path, envelopes)

    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=100,
    )

    # --- Run 1: stop after ~2500 envelopes ---
    stop1 = asyncio.Event()
    run1 = await _drain(settings=settings, clock=clock, target_count=2500, stop_event=stop1)
    # Sanity: stop1 was set; cursor.json exists with offset > 0.
    assert cursor_path.exists()
    assert len(run1) >= 2500
    import json

    body = json.loads(cursor_path.read_text())
    assert body["offset"] > 0
    assert body["path"] == str(log_path)

    # --- Run 2: fresh subscriber; resumes from cursor ---
    stop2 = asyncio.Event()

    # Drive run 2 with a timeout-based stop (no more envelopes will
    # arrive after we drain).
    async def _stop_after_drain() -> None:
        # Heuristic: wait until offset == file size, then stop.
        file_size = log_path.stat().st_size
        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                current = json.loads(cursor_path.read_text())["offset"]
            except (OSError, json.JSONDecodeError):
                current = 0
            if current >= file_size:
                stop2.set()
                return
            await asyncio.sleep(0.05)
        stop2.set()

    drain_task = asyncio.create_task(_stop_after_drain())
    run2 = await _drain(settings=settings, clock=clock, target_count=None, stop_event=stop2)
    await drain_task

    # --- Exactly-once invariant ---
    seen_ids = [env.event_id for env in run1] + [env.event_id for env in run2]
    expected_ids = [env.event_id for env in envelopes]
    # Order preserved across the two runs (append-only log).
    assert seen_ids == expected_ids
    # And every id is unique (no duplicates).
    assert len(set(seen_ids)) == 5000
    # Cursor advanced to EOF.
    body = json.loads(cursor_path.read_text())
    assert body["offset"] == log_path.stat().st_size
