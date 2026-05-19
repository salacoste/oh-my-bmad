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

    We DO NOT call ``unregister_all()`` because module-load side-effect
    registrations in registry-state (``event_types.ensure_registered()``)
    register the production ``TaskCreatedPayload`` against the same key
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
    """VH-13 — parse-skip threshold raises RuntimeError on corruption.

    A run of N+1 garbage lines after the threshold triggers a refusal
    to advance.
    """
    from events.log_reader import iter_new_envelopes_since

    path = tmp_path / "2026-05-19.jsonl"
    # Write 5 garbage lines then 1 valid envelope.
    path.write_bytes(b"{not json\n" * 5 + to_canonical_json(_make_envelope()) + b"\n")
    # Threshold=3 → after 4 contiguous skips raise.
    with pytest.raises(RuntimeError, match="parse_skip_threshold"):
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
