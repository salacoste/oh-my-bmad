"""Unit tests for replay.snapshots (Phase 12 / Story 62-2).

Tests snapshot creation, listing, loading, and find-nearest logic using
temp directories and in-memory JSONL event logs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
from events import FROZEN_EPOCH, FrozenClock, TaskCreatedPayload
from events.canonical import to_canonical_json
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id, new_task_id, new_uuid7
from events.schema_registry import register as _reg
from replay.snapshots import (
    create_snapshot,
    find_nearest_snapshot,
    list_snapshots,
    load_snapshot,
)

_FROZEN_MONO_NS = 1_000_000
_RNG = Random(42)
_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
_TASK_ID = new_task_id(clock=_CLOCK, rng=_RNG)
_EVENT_ID = new_event_id(clock=_CLOCK, rng=_RNG)


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register task.created before each test."""
    _reg("task.created", "1.0.0", TaskCreatedPayload)


def _make_task_created_envelope(
    *,
    task_id: str = _TASK_ID,
    event_id: str = _EVENT_ID,
    title: str = "test task",
    mono_ns: int = _FROZEN_MONO_NS,
    emitted_at: datetime = FROZEN_EPOCH,
) -> EventEnvelope:
    """Create a task.created envelope for test fixtures."""
    clock = FrozenClock(mono_ns=mono_ns, now=emitted_at)
    return EventEnvelope.create(
        event_id=event_id,
        type="task.created",
        schema_version="1.0.0",
        emitted_at=emitted_at,
        emitted_at_monotonic_ns=mono_ns,
        actor=Actor(kind="operator", id="test-op"),
        payload=TaskCreatedPayload(task_id=task_id, title=title),
        request_id=new_request_id(clock=clock, rng=Random(99)),
        trace_id=new_uuid7(clock=clock),
        parent_event_id=None,
    )


def _write_jsonl(
    events_dir: Path,
    date: str,
    envelopes: list[EventEnvelope],
) -> None:
    """Write envelopes as JSONL lines to ``events_dir/{date}.jsonl``."""
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / f"{date}.jsonl"
    with open(path, "wb") as f:
        for env in envelopes:
            f.write(to_canonical_json(env) + b"\n")


class TestCreateSnapshot:
    """Tests for create_snapshot."""

    @pytest.mark.asyncio
    async def test_creates_file_on_disk(self, tmp_path: Path) -> None:
        """create_snapshot produces a JSON file in snapshot_dir."""
        events_dir = tmp_path / "events"
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=_TASK_ID,
                    mono_ns=5000,
                    emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
                ),
            ],
        )
        snap_dir = tmp_path / "snapshots"

        info = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        # Verify the file exists
        snap_file = snap_dir / f"{info.snapshot_id}.json"
        assert snap_file.is_file()

        # Verify it is valid JSON with expected keys
        data = json.loads(snap_file.read_text())
        assert data["snapshot_id"] == info.snapshot_id
        assert data["sequence_number"] == 5000
        assert "state" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_snapshot_json_is_pretty_printed(self, tmp_path: Path) -> None:
        """Snapshot JSON is indented and sorted for human readability."""
        events_dir = tmp_path / "events"
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=_TASK_ID,
                    mono_ns=5000,
                    emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
                ),
            ],
        )
        snap_dir = tmp_path / "snapshots"

        info = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        raw = (snap_dir / f"{info.snapshot_id}.json").read_text()
        # Pretty-printed means indented
        assert "\n " in raw or "\n  " in raw
        # Sorted keys means "sequence_number" before "snapshot_id" before "state"
        seq_pos = raw.index('"sequence_number"')
        snap_pos = raw.index('"snapshot_id"')
        state_pos = raw.index('"state"')
        assert seq_pos < snap_pos < state_pos

    @pytest.mark.asyncio
    async def test_snapshot_size_bytes_matches_file(self, tmp_path: Path) -> None:
        """size_bytes matches the actual file size on disk."""
        events_dir = tmp_path / "events"
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=_TASK_ID,
                    mono_ns=5000,
                    emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
                ),
            ],
        )
        snap_dir = tmp_path / "snapshots"

        info = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        actual_size = (snap_dir / f"{info.snapshot_id}.json").stat().st_size
        assert info.size_bytes == actual_size

    @pytest.mark.asyncio
    async def test_empty_log_snapshot(self, tmp_path: Path) -> None:
        """Snapshot of an empty event log has sequence_number 0."""
        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        snap_dir = tmp_path / "snapshots"

        info = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        assert info.sequence_number == 0
        assert info.state == {"tasks": [], "sessions": []}


class TestListSnapshots:
    """Tests for list_snapshots."""

    @pytest.mark.asyncio
    async def test_returns_created_snapshots_sorted(self, tmp_path: Path) -> None:
        """list_snapshots returns snapshots sorted by sequence_number."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        # Create two event files with different sequence numbers
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=new_task_id(
                        clock=FrozenClock(mono_ns=1000, now=FROZEN_EPOCH),
                        rng=Random(1),
                    ),
                    mono_ns=1000,
                    title="first",
                ),
            ],
        )

        info1 = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        # Add another event
        _write_jsonl(
            events_dir,
            "2026-06-10",
            [
                _make_task_created_envelope(
                    task_id=new_task_id(
                        clock=FrozenClock(mono_ns=2000, now=FROZEN_EPOCH),
                        rng=Random(2),
                    ),
                    event_id=new_event_id(
                        clock=FrozenClock(mono_ns=2000, now=FROZEN_EPOCH),
                        rng=Random(2),
                    ),
                    mono_ns=2000,
                    title="second",
                ),
            ],
        )

        info2 = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        snapshots = list_snapshots(snapshot_dir=snap_dir)

        assert len(snapshots) == 2
        # Sorted by sequence_number ascending
        assert snapshots[0].snapshot_id == info1.snapshot_id
        assert snapshots[1].snapshot_id == info2.snapshot_id
        assert snapshots[0].sequence_number <= snapshots[1].sequence_number

    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        """list_snapshots on non-existent dir returns empty list."""
        snapshots = list_snapshots(snapshot_dir=tmp_path / "nope")
        assert snapshots == []


class TestLoadSnapshot:
    """Tests for load_snapshot."""

    @pytest.mark.asyncio
    async def test_load_existing_snapshot(self, tmp_path: Path) -> None:
        """load_snapshot returns the correct SnapshotInfo by ID."""
        events_dir = tmp_path / "events"
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=_TASK_ID,
                    mono_ns=5000,
                    emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
                ),
            ],
        )
        snap_dir = tmp_path / "snapshots"

        created = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        loaded = load_snapshot(snapshot_id=created.snapshot_id, snapshot_dir=snap_dir)

        assert loaded is not None
        assert loaded.snapshot_id == created.snapshot_id
        assert loaded.sequence_number == created.sequence_number

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """load_snapshot with unknown ID returns None."""
        result = load_snapshot(snapshot_id="nonexistent", snapshot_dir=tmp_path)
        assert result is None


class TestFindNearestSnapshot:
    """Tests for find_nearest_snapshot."""

    @pytest.mark.asyncio
    async def test_finds_nearest_before_target(self, tmp_path: Path) -> None:
        """find_nearest_snapshot returns the closest snapshot before the target."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        # Create snapshot at seq 1000
        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=new_task_id(
                        clock=FrozenClock(mono_ns=1000, now=FROZEN_EPOCH),
                        rng=Random(1),
                    ),
                    mono_ns=1000,
                    title="first",
                ),
            ],
        )
        create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        # Add event at 2000 and create another snapshot
        _write_jsonl(
            events_dir,
            "2026-06-10",
            [
                _make_task_created_envelope(
                    task_id=new_task_id(
                        clock=FrozenClock(mono_ns=2000, now=FROZEN_EPOCH),
                        rng=Random(2),
                    ),
                    event_id=new_event_id(
                        clock=FrozenClock(mono_ns=2000, now=FROZEN_EPOCH),
                        rng=Random(2),
                    ),
                    mono_ns=2000,
                    title="second",
                ),
            ],
        )
        snap2 = create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        # Ask for nearest before target 3000
        result = find_nearest_snapshot(target_sequence=3000, snapshot_dir=snap_dir)

        assert result is not None
        assert result.snapshot_id == snap2.snapshot_id
        assert result.sequence_number == 2000

    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot_before_target(self, tmp_path: Path) -> None:
        """find_nearest_snapshot returns None when all snapshots are >= target."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        _write_jsonl(
            events_dir,
            "2026-06-09",
            [
                _make_task_created_envelope(
                    task_id=_TASK_ID,
                    mono_ns=5000,
                    emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
                ),
            ],
        )
        create_snapshot(event_log_dir=events_dir, snapshot_dir=snap_dir)

        # Target is 100 — snapshot at 5000 is AFTER, so no match
        result = find_nearest_snapshot(target_sequence=100, snapshot_dir=snap_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_dir(self, tmp_path: Path) -> None:
        """find_nearest_snapshot returns None when snapshot dir is empty."""
        result = find_nearest_snapshot(target_sequence=9999, snapshot_dir=tmp_path / "nope")
        assert result is None
