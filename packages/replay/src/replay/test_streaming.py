"""Tests for package-only replay_events_stream (Phase 13)."""

from __future__ import annotations

from pathlib import Path
from random import Random

import pytest
from events import FROZEN_EPOCH, FrozenClock, new_session_id, new_task_id

from replay import ReplayProgress, ReplayResult, replay_events, replay_events_stream
from replay.test_engine import _build_full_lifecycle, _write_jsonl


@pytest.mark.asyncio
async def test_replay_events_stream_emits_progress_per_batch_and_terminal_result(
    tmp_path: Path,
) -> None:
    """Streaming emits ceil(N/B) progress items then one ReplayResult."""
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(42))
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(7))
    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000, step_count=2)
    _write_jsonl(tmp_path / "2026-06-09.jsonl", envs)

    items = [
        item
        async for item in replay_events_stream(
            up_to=9_999_999,
            event_log_dir=tmp_path,
            batch_size=2,
        )
    ]

    progress = [item for item in items if isinstance(item, ReplayProgress)]
    terminal = items[-1]
    assert len(progress) == 4
    assert all(isinstance(p.total_events, int) for p in progress)
    assert [p.processed_events for p in progress] == [2, 4, 6, len(envs)]
    assert all(p.total_events == len(envs) for p in progress)
    assert isinstance(terminal, ReplayResult)

    single = await replay_events(up_to=9_999_999, event_log_dir=tmp_path)
    assert terminal.state == single.state
    assert terminal.metadata.event_count == single.metadata.event_count
    assert terminal.metadata.sequence_start == single.metadata.sequence_start
    assert terminal.metadata.sequence_end == single.metadata.sequence_end


@pytest.mark.asyncio
async def test_replay_events_stream_zero_events_emits_only_terminal_result(tmp_path: Path) -> None:
    """Zero selected events produce no progress item and exactly one terminal result."""
    items = [item async for item in replay_events_stream(up_to=1, event_log_dir=tmp_path)]

    assert len(items) == 1
    assert isinstance(items[0], ReplayResult)
    assert items[0].metadata.event_count == 0
