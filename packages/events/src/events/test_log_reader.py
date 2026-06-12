"""Unit tests for the extracted :mod:`events.log_reader` (Story 10.2 AC1/AC2).

The deep-coverage tests for the underlying free functions live in
``services/registry-state/src/registry_state/test_event_log.py`` — those
must continue to pass via the re-export shim (verified by the registry-
state suite). The tests here focus on:

* :class:`EventLogReader` public API (open / seek / read_batch /
  cursor_offset / current_path)
* :func:`read_log_lines` basic + CRLF
* :func:`read_new_envelopes_since` cursor advance + partial-line stop
* :func:`parse_with_pre110_backfill` skip + back-fill paths
* :func:`current_day_path` validation
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from random import Random

import pytest
from pydantic import BaseModel

from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    EventLogReader,
    FrozenClock,
    current_day_path,
    new_event_id,
    new_uuid7,
    read_log_lines,
    read_new_envelopes_since,
    to_canonical_json,
)
from events.log_reader import parse_with_pre110_backfill
from events.schema_registry import register


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None, None, None]:
    """Register the test payload model idempotently.

    We DO NOT call ``unregister_all()`` because canonical production registrations
    (``event_types.ensure_registered()`` via the shared events module or the
    registry-state compatibility shim) can register ``TaskCreatedPayload`` against the same key
    used here; wiping them would break downstream cross-service tests
    that share the pytest session (Epic 9 retro D5 — schema-registry
    is global session-scoped). We rely on ``register()`` being
    idempotent for same-model re-registrations and use a fresh
    test-only payload model for our envelopes.
    """
    # Use a non-production event_type so we never collide with
    # registry-state's TaskCreatedPayload registration.
    register("test.log_reader.envelope", "1.0.0", _SimplePayload)
    yield


_ACTOR = Actor(kind="system", id="test")
_DEFAULT_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


def _make_envelope(value: str = "hello", mono_seed: int = 0) -> EventEnvelope:
    rng = Random(mono_seed)
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope(
        event_id=eid,
        schema_version="1.0.0",
        type="test.log_reader.envelope",  # noqa: EVT001 test-only fixture envelope
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"value": value},
        trace_id=_DEFAULT_TRACE_ID,
        request_id=rid,
    )


def _write_envelopes(path: Path, envelopes: list[EventEnvelope]) -> None:
    with open(path, "wb") as f:
        for env in envelopes:
            f.write(to_canonical_json(env) + b"\n")


def test_current_day_path_utc_only() -> None:
    base = Path("/tmp/x")
    now = datetime(2026, 5, 19, 23, 59, 0, tzinfo=UTC)
    assert current_day_path(base, now) == base / "2026-05-19.jsonl"
    naive = datetime(2026, 5, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="UTC-aware"):
        current_day_path(base, naive)
    non_utc = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    with pytest.raises(ValueError, match="UTC-aware"):
        current_day_path(base, non_utc)


def test_read_log_lines_basic(tmp_path: Path) -> None:
    path = tmp_path / "2026-05-19.jsonl"
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(3)]
    _write_envelopes(path, envs)
    recovered = list(read_log_lines(path))
    assert recovered == envs


def test_read_log_lines_missing_raises_eagerly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_log_lines(tmp_path / "nonexistent.jsonl")


def test_read_log_lines_skips_trailing_partial(tmp_path: Path) -> None:
    path = tmp_path / "2026-05-19.jsonl"
    env = _make_envelope()
    canonical = to_canonical_json(env)
    # Complete line + partial line with no terminating \n.
    path.write_bytes(canonical + b"\n" + canonical[:50])
    recovered = list(read_log_lines(path))
    assert recovered == [env]


def test_read_new_envelopes_since_cursor_advance(tmp_path: Path) -> None:
    path = tmp_path / "2026-05-19.jsonl"
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(5)]
    _write_envelopes(path, envs)

    new_offset, batch = read_new_envelopes_since(path, 0)
    assert batch == envs
    assert new_offset == path.stat().st_size

    # Subsequent call at EOF returns nothing.
    new_offset2, batch2 = read_new_envelopes_since(path, new_offset)
    assert batch2 == []
    assert new_offset2 == new_offset


def test_read_new_envelopes_since_partial_line_held_back(tmp_path: Path) -> None:
    path = tmp_path / "2026-05-19.jsonl"
    env1, env2 = _make_envelope(mono_seed=0), _make_envelope(mono_seed=1)
    can1 = to_canonical_json(env1)
    can2 = to_canonical_json(env2)
    path.write_bytes(can1 + b"\n" + can2[:20])  # partial 2nd line
    new_offset, batch = read_new_envelopes_since(path, 0)
    assert batch == [env1]
    # Cursor stops at the partial line's start (= just past env1's newline).
    assert new_offset == len(can1) + 1

    # Now append the rest of env2 + newline; next call picks it up.
    with open(path, "wb") as f:
        f.write(can1 + b"\n" + can2 + b"\n")
    new_offset2, batch2 = read_new_envelopes_since(path, new_offset)
    assert batch2 == [env2]


def test_read_new_envelopes_since_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no.jsonl"
    new_offset, batch = read_new_envelopes_since(missing, 42)
    assert batch == []
    assert new_offset == 42


def test_parse_with_pre110_backfill_skips_invalid_json(tmp_path: Path) -> None:
    result = parse_with_pre110_backfill(b"{garbage", tmp_path / "x.jsonl")
    assert result is None


def test_parse_with_pre110_backfill_skips_non_dict(tmp_path: Path) -> None:
    result = parse_with_pre110_backfill(b'"a string"', tmp_path / "x.jsonl")
    assert result is None


def test_event_log_reader_open_and_read_batch(tmp_path: Path) -> None:
    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    path = current_day_path(tmp_path, clock.now())
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(4)]
    _write_envelopes(path, envs)

    reader = EventLogReader(tmp_path, clock=clock)
    reader.open(initial_offset=0)
    assert reader.current_path == path
    batch = reader.read_batch()
    assert batch == envs
    assert reader.cursor_offset == path.stat().st_size

    # Read at EOF returns empty.
    assert reader.read_batch() == []


def test_event_log_reader_read_batch_before_open_raises(tmp_path: Path) -> None:
    reader = EventLogReader(tmp_path)
    with pytest.raises(RuntimeError, match="before open"):
        reader.read_batch()


def test_event_log_reader_current_path_before_open_raises(tmp_path: Path) -> None:
    reader = EventLogReader(tmp_path)
    with pytest.raises(RuntimeError, match="before open"):
        _ = reader.current_path


def test_event_log_reader_seek_overrides_path(tmp_path: Path) -> None:
    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    reader = EventLogReader(tmp_path, clock=clock)
    arbitrary = tmp_path / "2026-05-17.jsonl"
    reader.seek(path=arbitrary, offset=1234)
    assert reader.current_path == arbitrary
    assert reader.cursor_offset == 1234


def test_event_log_reader_max_events_soft_cap(tmp_path: Path) -> None:
    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    path = current_day_path(tmp_path, clock.now())
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(10)]
    _write_envelopes(path, envs)
    reader = EventLogReader(tmp_path, clock=clock)
    reader.open(initial_offset=0)
    batch = reader.read_batch(max_events=3)
    assert len(batch) == 3


def test_read_batch_max_events_preserves_unread_tail(tmp_path: Path) -> None:
    """VH-3 — ``max_events`` cap must not silently drop the unread tail.

    Write 10 envelopes; call ``read_batch(max_events=3)`` four times;
    assert the union covers all 10 exactly once.  Previously the
    implementation advanced the cursor to EOF then sliced the list,
    silently dropping envelopes 3..9 on the first call.
    """
    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    path = current_day_path(tmp_path, clock.now())
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(10)]
    _write_envelopes(path, envs)
    reader = EventLogReader(tmp_path, clock=clock)
    reader.open(initial_offset=0)

    collected: list[EventEnvelope] = []
    for _ in range(4):
        collected.extend(reader.read_batch(max_events=3))
    # All 10 envelopes accounted for exactly once, in order.
    assert collected == envs
    # And the cursor lands at EOF after the final drain.
    assert reader.cursor_offset == path.stat().st_size


def test_iter_new_envelopes_since_chunk_cap(tmp_path: Path) -> None:
    """VH-4 — ``iter_new_envelopes_since`` stops at ``max_lines_per_poll``.

    Write 200 envelopes; iterate with ``max_lines_per_poll=50`` and
    assert exactly 50 are returned.  The cursor advances to the 50th
    line's end (not EOF).
    """
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(200)]
    _write_envelopes(path, envs)

    items = list(iter_new_envelopes_since(path, 0, max_lines_per_poll=50))
    assert len(items) == 50
    last_offset = items[-1][0]
    # The offset is past the 50th line's newline, so a follow-up call
    # picks up envelope 50 next.
    more = list(iter_new_envelopes_since(path, last_offset, max_lines_per_poll=50))
    assert len(more) == 50
    assert [env.event_id for _, env in items] == [env.event_id for env in envs[:50]]
    assert [env.event_id for _, env in more] == [env.event_id for env in envs[50:100]]


def test_iter_new_envelopes_since_max_contiguous_parse_skips(tmp_path: Path) -> None:
    """VH-13 + P2-H3/P2-H4 — parse-skip threshold raises typed exception.

    Pass-2 changes:
    * Comparison is now ``>=`` (P2-H4 off-by-one fix); threshold=3
      trips on the 3rd contiguous skip, not the 4th.
    * Exception type is :class:`ParseSkipThresholdExceeded` (was
      generic ``RuntimeError``) per P2-H3 so the subscriber catches
      a typed exception and exits with code 3.
    """
    from events.errors import ParseSkipThresholdExceeded
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    # Write 5 garbage lines then 1 valid envelope.
    path.write_bytes(b"{not json\n" * 5 + to_canonical_json(_make_envelope()) + b"\n")
    # Threshold=3 → trips on 3rd contiguous skip (>= comparison).
    with pytest.raises(ParseSkipThresholdExceeded):
        list(iter_new_envelopes_since(path, 0, max_contiguous_parse_skips=3))


def test_iter_new_envelopes_since_cursor_advances_after_yield(tmp_path: Path) -> None:
    """VH-8 — generator yields envelope BEFORE caller has cursor advancement.

    Verified by consuming one item, raising, and confirming we can
    re-iterate from the same offset (the offset was the post-yield
    offset, captured for the NEXT iteration, not this one).
    """
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(3)]
    _write_envelopes(path, envs)

    gen = iter_new_envelopes_since(path, 0)
    offset1, env1 = next(gen)
    assert env1.event_id == envs[0].event_id
    # If a consumer-side exception occurs before they store offset1,
    # they can restart from offset=0 and re-see env1 (exactly-once
    # guarantee for the consumer side).
    gen2 = iter_new_envelopes_since(path, 0)
    offset_re, env_re = next(gen2)
    assert env_re.event_id == envs[0].event_id
    assert offset_re == offset1


def test_iter_new_envelopes_since_parse_skip_state_persists_across_polls(
    tmp_path: Path,
) -> None:
    """P2-H4 — contiguous parse-skip counter accumulates across polls.

    Pre-pass-2 the counter was a per-call local; each poll opened the
    file fresh and reset to 0.  A corruption pattern of N bad lines
    per poll across K polls (K*N total) never tripped the threshold.

    With the new ``parse_skip_state`` mutable cell, callers can hand
    in their own counter that persists across polls.  Test: 5 bad
    lines per poll, 3 polls = 15 contiguous skips → trips threshold=10.
    """
    from events.errors import ParseSkipThresholdExceeded
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    path.write_bytes(b"{garbage\n" * 15)
    state = [0]
    # Poll 1: 4 lines — below threshold (4 < 10).
    list(
        iter_new_envelopes_since(
            path,
            0,
            max_lines_per_poll=4,
            max_contiguous_parse_skips=10,
            parse_skip_state=state,
        )
    )
    assert state[0] == 4
    # Poll 2: 4 more lines — still below threshold (8 < 10).
    list(
        iter_new_envelopes_since(
            path,
            4 * len(b"{garbage\n"),
            max_lines_per_poll=4,
            max_contiguous_parse_skips=10,
            parse_skip_state=state,
        )
    )
    assert state[0] == 8
    # Poll 3: trips on the 2nd bad line of this poll (8+2 = 10 = threshold).
    with pytest.raises(ParseSkipThresholdExceeded):
        list(
            iter_new_envelopes_since(
                path,
                8 * len(b"{garbage\n"),
                max_lines_per_poll=4,
                max_contiguous_parse_skips=10,
                parse_skip_state=state,
            )
        )


def test_iter_new_envelopes_since_corruption_offset_anchored_at_run_start_across_polls(
    tmp_path: Path,
) -> None:
    """P3-H2 / Q11 — ``ParseSkipThresholdExceeded.offset`` reports TRUE run-start.

    Pre-pass-3 ``corruption_run_start`` was a per-call local — when
    the threshold tripped in a later poll than where corruption
    started, ``exc.offset`` reported a stale anchor (often the
    current poll's entry offset).  This defeated P2-H3's headline
    guarantee that operators can manually advance the cursor past
    the corrupt region using ``exc.offset``.

    Scenario: 5 valid envelopes + 30 garbage + 30 more garbage,
    across two polls, threshold=50.  The exception must report
    ``offset == 5 * envelope_size`` (start of the corrupt run, not
    current poll's entry offset).
    """
    from events.errors import ParseSkipThresholdExceeded
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    valid_lines = b"".join(to_canonical_json(_make_envelope(mono_seed=i)) + b"\n" for i in range(5))
    valid_size = len(valid_lines)
    garbage_line = b"{garbage\n"
    path.write_bytes(valid_lines + garbage_line * 60)

    state: list[int] = [0, 0]
    # Poll 1: drain 5 valid envelopes + 30 garbage lines.  Below
    # threshold (30 < 50).
    items_p1 = list(
        iter_new_envelopes_since(
            path,
            0,
            max_lines_per_poll=35,
            max_contiguous_parse_skips=50,
            parse_skip_state=state,
        )
    )
    assert len(items_p1) == 5
    assert state[0] == 30
    # Cross-poll: the run-start anchor MUST be the byte after the
    # last valid envelope (where the first garbage line begins).
    assert state[1] == valid_size, (
        f"run-start anchor lost across poll boundary: state={state}, expected[1]={valid_size}"
    )
    # Poll 2: continue from where poll 1 stopped (5 valid + 30
    # garbage lines = valid_size + 30 * len(garbage_line)).  This
    # poll reads 30 more garbage lines, trips threshold on the 20th
    # additional (30 + 20 = 50 >= threshold).
    next_offset = valid_size + 30 * len(garbage_line)
    with pytest.raises(ParseSkipThresholdExceeded) as exc_info:
        list(
            iter_new_envelopes_since(
                path,
                next_offset,
                max_lines_per_poll=30,
                max_contiguous_parse_skips=50,
                parse_skip_state=state,
            )
        )
    # The exception offset must point at the START of the corrupt
    # run (immediately after the 5 valid envelopes), NOT at
    # ``next_offset`` (poll 2's entry).
    assert exc_info.value.offset == valid_size, (
        f"exc.offset={exc_info.value.offset} should equal valid_size={valid_size} "
        f"(the byte position where the corrupt run BEGAN, across poll boundary)"
    )


def test_iter_new_envelopes_since_parse_skip_state_resets_on_valid(tmp_path: Path) -> None:
    """P2-H4 — successful parse resets the cross-poll counter to 0."""
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    valid_line = to_canonical_json(_make_envelope()) + b"\n"
    # 3 bad lines, then 1 valid, then 3 bad lines.
    path.write_bytes(b"{bad\n" * 3 + valid_line + b"{bad\n" * 3)
    state = [0]
    items = list(
        iter_new_envelopes_since(
            path, 0, max_lines_per_poll=100, max_contiguous_parse_skips=5, parse_skip_state=state
        )
    )
    # 1 valid envelope yielded, counter at 3 (the trailing bad run).
    assert len(items) == 1
    assert state[0] == 3


@pytest.mark.asyncio
async def test_tail_cursor_unchanged_on_consumer_raise(tmp_path: Path) -> None:
    """P2-H5 — VH-8 invariant: consumer-side raise leaves cursor on prior line.

    The cursor advance follows ``yield envelope`` in
    :meth:`EventLogReader.tail`.  When the consumer raises on
    envelope N, the cursor must stay on the offset PRIOR to N (so a
    restart re-yields N).
    """
    import asyncio

    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    path = current_day_path(tmp_path, clock.now())
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(5)]
    _write_envelopes(path, envs)
    reader = EventLogReader(tmp_path, clock=clock)
    reader.open(initial_offset=0)

    # Capture the byte-offset PRIOR to envelope 2 (zero-indexed).
    can_lines = [to_canonical_json(e) + b"\n" for e in envs]
    offset_prior_to_env2 = sum(len(line) for line in can_lines[:2])

    received: list[EventEnvelope] = []
    stop = asyncio.Event()

    class _ConsumerError(RuntimeError):
        pass

    async def _drive() -> None:
        async for env in reader.tail(poll_interval_s=0.01, stop_event=stop):
            received.append(env)
            if len(received) == 2:
                # Consumer raises BEFORE the cursor-advance line in
                # tail() runs (the advance is post-yield in the loop
                # body — raising here aborts before that statement).
                raise _ConsumerError

    with pytest.raises(_ConsumerError):
        await asyncio.wait_for(_drive(), timeout=5.0)

    # VH-8 invariant: cursor stays on the offset PRIOR to envelope 2.
    # The two yielded envelopes were envs[0] and envs[1]; the cursor
    # advanced past envs[0] (post-yield of envs[0]) but NOT past
    # envs[1] (the raise pre-empted the post-yield assignment).
    expected_offset = sum(len(line) for line in can_lines[:1])
    assert reader.cursor_offset == expected_offset
    # Sanity: the offset is strictly less than "past env 2".
    assert reader.cursor_offset < offset_prior_to_env2


@pytest.mark.asyncio
async def test_tail_clamps_offset_after_external_rotation(tmp_path: Path) -> None:
    """P2-H12 (Q7) — offset > file_size mid-stream is clamped + logged.

    Restart-time clamping in cursor.restore_into seats the reader at
    old EOF.  If the file is then externally rotated to a smaller
    size (logrotate, replay-from-archive, accidental truncation), the
    next tail() poll would seek past the (new) EOF and stall silently
    forever.  Re-validate per-poll and clamp to current size.

    Test sequence:
    1. Write 10 envelopes (large file).
    2. Truncate to half — file now ends on a newline boundary at byte K.
    3. Seat reader at the ORIGINAL EOF (file_size_before > K).
    4. Start tailing → clamp logs at the start of the first poll +
       cursor seats at K.
    5. Append 2 fresh envelopes (at bytes K..K+envelope*2).
    6. Reader picks up the 2 new envelopes (proof: it didn't stall).
    """
    import asyncio

    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    path = current_day_path(tmp_path, clock.now())
    envs = [_make_envelope(value=f"e{i}", mono_seed=i) for i in range(10)]
    _write_envelopes(path, envs)
    file_size_before = path.stat().st_size

    # External rotation: truncate to half its size, ending on a
    # newline boundary so envelopes parse cleanly.
    body = path.read_bytes()
    half = file_size_before // 2
    last_nl = body.rfind(b"\n", 0, half)
    assert last_nl > 0
    path.write_bytes(body[: last_nl + 1])
    truncated_size = path.stat().st_size
    assert truncated_size < file_size_before

    reader = EventLogReader(tmp_path, clock=clock)
    # Seat the reader past the (current/truncated) EOF, simulating
    # restore-time clamping to old EOF.
    reader.seek(path=path, offset=file_size_before)
    received: list[EventEnvelope] = []
    stop = asyncio.Event()

    async def _drive() -> None:
        async for env in reader.tail(poll_interval_s=0.05, stop_event=stop):
            received.append(env)
            if len(received) >= 2:
                stop.set()
                return

    drive_task = asyncio.create_task(_drive())
    # Give the tail loop a moment to clamp + observe the EOF.
    await asyncio.sleep(0.2)
    # Append 2 fresh envelopes AFTER the clamp has occurred.
    new_envs = [_make_envelope(value=f"f{i}", mono_seed=100 + i) for i in range(2)]
    with open(path, "ab") as f:
        for e in new_envs:
            f.write(to_canonical_json(e) + b"\n")

    import contextlib

    try:
        await asyncio.wait_for(drive_task, timeout=5.0)
    finally:
        stop.set()
        if not drive_task.done():
            drive_task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await drive_task
    # The reader recovered (did not stall): we saw both new envelopes.
    assert len(received) == 2
    assert [e.event_id for e in received] == [e.event_id for e in new_envs]


def test_open_after_aexit_raises() -> None:
    """P2-M2 (VM-6) — async-CM exit makes the reader unusable."""
    import asyncio

    async def _run() -> None:
        async with EventLogReader(Path("/tmp")) as reader:
            pass
        with pytest.raises(RuntimeError, match="used after close"):
            reader.open()

    asyncio.run(_run())


def test_rollover_skips_if_today_path_is_stale_mtime(tmp_path: Path) -> None:
    """P2-M4 + P3-M1 — stale today_path (older than yesterday) does NOT trigger rollover.

    P3-M1 reframes the stale-mtime guard as RELATIVE ordering
    (today_mtime >= yesterday_mtime) so it is immune to absolute
    clock jumps in both directions.  Test uses ``os.utime`` to set
    BOTH mtimes explicitly.
    """
    import os
    import time

    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    yesterday_path = tmp_path / "2026-05-18.jsonl"
    today_path = tmp_path / "2026-05-19.jsonl"
    yesterday_path.write_bytes(b"x" * 100)
    today_path.write_bytes(b"y" * 100)
    # Stale scenario: today's mtime is older than yesterday's mtime
    # (e.g., today_path is a leftover from a prior day with a stale
    # mtime; yesterday_path was the actually-written file).
    yesterday_mtime = time.time()
    stale_today_mtime = yesterday_mtime - 3600  # 1h older than yesterday
    os.utime(str(yesterday_path), (yesterday_mtime, yesterday_mtime))
    os.utime(str(today_path), (stale_today_mtime, stale_today_mtime))

    reader = EventLogReader(tmp_path, clock=clock, rollover_quiescence_s=0.0)
    reader.seek(path=yesterday_path, offset=100)  # already drained
    # Use a fake event-loop-like object that supports .time()
    fake_loop_time = [0.0]

    class _FakeLoop:
        def time(self) -> float:
            return fake_loop_time[0]

    ready = reader._is_rollover_ready(today_path, _FakeLoop())  # type: ignore[arg-type]
    # Stale today_path (older than yesterday) → rollover refused.
    assert ready is False
    # Touch today's mtime to NEWER than yesterday → fast-path engages.
    fresh_today_mtime = yesterday_mtime + 3600
    os.utime(str(today_path), (fresh_today_mtime, fresh_today_mtime))
    reader2 = EventLogReader(tmp_path, clock=clock, rollover_quiescence_s=0.0)
    reader2.seek(path=yesterday_path, offset=100)
    ready2 = reader2._is_rollover_ready(today_path, _FakeLoop())  # type: ignore[arg-type]
    assert ready2 is True


def test_rollover_immune_to_forward_clock_skew(tmp_path: Path) -> None:
    """P3-M5 — forward clock skew >25h does NOT falsely refuse rollover.

    Pre-pass-3 a system-clock forward jump (NTP correction, VM
    snapshot resume to future time) made
    ``today_mtime < wall_now - 25h`` even for a freshly written
    today file → rollover refused indefinitely.  P3-M1's relative
    ordering (today_mtime >= yesterday_mtime) is immune.

    Scenario: yesterday_mtime and today_mtime both set to "real
    wall-clock now" but the reader's clock returns a far-future
    timestamp (30h ahead).  Pre-fix this would trip the 25h window;
    post-fix the relative ordering passes.
    """
    import os
    import time

    # FrozenClock returns May 2026 timestamps; real file mtimes will
    # be the actual current wall clock — which on a fresh CI runner
    # is also May 2026 BUT we additionally simulate a forward jump
    # by setting BOTH files' mtimes to (clock.now - 30h) i.e., the
    # file was written 30h before "now" per the reader's clock.
    clock = FrozenClock(mono_ns=0, now=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC))
    yesterday_path = tmp_path / "2026-05-18.jsonl"
    today_path = tmp_path / "2026-05-19.jsonl"
    yesterday_path.write_bytes(b"x" * 100)
    today_path.write_bytes(b"y" * 100)
    base_mtime = time.time() - 30 * 3600
    today_mtime = base_mtime + 3600  # today written 1h after yesterday
    os.utime(str(yesterday_path), (base_mtime, base_mtime))
    os.utime(str(today_path), (today_mtime, today_mtime))

    reader = EventLogReader(tmp_path, clock=clock, rollover_quiescence_s=0.0)
    reader.seek(path=yesterday_path, offset=100)  # already drained

    class _FakeLoop:
        def time(self) -> float:
            return 0.0

    ready = reader._is_rollover_ready(today_path, _FakeLoop())  # type: ignore[arg-type]
    # today_mtime > yesterday_mtime → rollover fires, regardless of
    # absolute clock skew.
    assert ready is True
