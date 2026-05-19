"""Unit tests for :class:`CursorPersistence` (Story 10.2 AC3/AC4).

Coverage:

* first-time write produces a valid schema-version="1" payload
* corrupt-file restore → starts fresh + WARNING
* schema_version mismatch → starts fresh + WARNING
* persist-counter logic: counter < persist_every → no write
* persist-counter logic: counter ≥ persist_every → atomic write + reset
* restore-into matching path seeks to offset
* restore-into mismatching path (day rollover during downtime) → opens
  today's at offset 0 + day-rollover WARNING
* persist_now forces write regardless of counter (SIGTERM drain)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from events.clock import FrozenClock
from events.log_reader import EventLogReader, current_day_path

from metrics_subscriber.cursor import CursorPersistence

_TODAY = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _make_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=_TODAY)


def test_first_time_persist_writes_schema_v1(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.persist_now(offset=42, path=log_path)
    body = json.loads(cursor_path.read_text())
    assert body["schema_version"] == "1"
    assert body["path"] == str(log_path)
    assert body["offset"] == 42
    assert body["persisted_at"].endswith("Z")
    assert body["events_processed_since_last_persist"] == 0


def test_maybe_persist_below_threshold_does_nothing(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=10, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.note_event_processed(5)
    persisted = cp.maybe_persist(offset=100, path=log_path)
    assert persisted is False
    assert not cursor_path.exists()


def test_maybe_persist_at_threshold_writes_and_resets(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=3, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.note_event_processed(3)
    persisted = cp.maybe_persist(offset=999, path=log_path)
    assert persisted is True
    body = json.loads(cursor_path.read_text())
    assert body["offset"] == 999
    # Counter resets to 0 after persist.
    assert cp.maybe_persist(offset=1000, path=log_path) is False


def test_restore_into_matching_path_seeks(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    today_path = current_day_path(tmp_path, _TODAY)
    cp.persist_now(offset=512, path=today_path)

    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.current_path == today_path
    assert reader.cursor_offset == 512


def test_restore_into_day_rollover_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    # Cursor written for yesterday — simulate downtime spanning UTC
    # midnight.
    yesterday_path = tmp_path / "2026-05-18.jsonl"
    cp.persist_now(offset=8192, path=yesterday_path)

    reader = EventLogReader(tmp_path, clock=_make_clock())
    with caplog.at_level("WARNING"):
        cp.restore_into(reader, base_dir=tmp_path)
    # Reader points at today's file, offset 0.
    assert reader.current_path == current_day_path(tmp_path, _TODAY)
    assert reader.cursor_offset == 0
    # WARNING explicitly mentions the rollover marker.
    assert any("tail.restart_after_day_rollover" in record.message for record in caplog.records)


def test_restore_into_missing_cursor_starts_fresh(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert reader.current_path == current_day_path(tmp_path, _TODAY)


def test_restore_into_corrupt_file_starts_fresh_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_text("{not valid json")
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    with caplog.at_level("WARNING"):
        cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert any("cursor_corrupt" in r.message for r in caplog.records)


def test_restore_into_unknown_schema_version_starts_fresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "path": str(tmp_path / "2026-05-19.jsonl"),
                "offset": 999,
            }
        )
    )
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    with caplog.at_level("WARNING"):
        cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert any("unknown_schema" in r.message for r in caplog.records)


def test_atomic_write_no_partial_visible(tmp_path: Path) -> None:
    """The cursor file is replaced atomically — no temp suffix lingers.

    Approximates the OS-level atomicity guarantee of os.replace by
    checking that after a successful persist (a) only one file exists
    in the directory and (b) it parses cleanly.
    """
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=1, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.persist_now(offset=1, path=log_path)
    cp.persist_now(offset=2, path=log_path)
    files = sorted(p.name for p in tmp_path.iterdir())
    # No leftover ``cursor.*.json.tmp`` files; only the final cursor.
    assert files == ["cursor.json"]
    body = json.loads(cursor_path.read_text())
    assert body["offset"] == 2
