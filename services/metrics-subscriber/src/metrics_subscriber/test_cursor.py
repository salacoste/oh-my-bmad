"""Unit tests for :class:`CursorPersistence` (Story 10.2 AC3/AC4).

Coverage:

* first-time write produces a valid schema-version="1" payload
* corrupt-file restore → starts fresh + WARNING
* schema_version mismatch → raises ``CursorSchemaVersionError`` (VH-9)
* persist-counter logic: counter < persist_every → no write
* persist-counter logic: counter ≥ persist_every → atomic write + reset
* restore-into matching path seeks to offset
* restore-into mismatching path (day rollover during downtime) → VH-1
  drains yesterday's tail before transitioning
* persist_now forces write regardless of counter (SIGTERM drain)
* VH-10 — concurrent subscriber refuses to start
* VH-11 — cursor offset bounds validation (negative + beyond-EOF)
* VH-12 — atomic write cleans tempfile on exception
* VL-2 — ``events_in_this_persist_window`` field rename
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from events.clock import FrozenClock
from events.errors import CursorSchemaVersionError
from events.log_reader import EventLogReader, current_day_path

from metrics_subscriber.cursor import CursorPersistence


@pytest.fixture
def captured_log_events() -> Iterator[list[MutableMapping[str, Any]]]:
    """Capture structlog events emitted during the test.

    Uses :func:`structlog.testing.capture_logs` which is robust to
    process-wide structlog configuration changes from other test
    conftests (Story 9.x ``capture_structlog`` fixture, etc.).
    """
    with structlog.testing.capture_logs() as caps:
        yield caps


_TODAY = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _make_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=_TODAY)


def test_first_time_persist_writes_schema_v1(tmp_path: Path) -> None:
    """Pass-2 P2-L2: bumped to schema_version="1.1" with dual-field write."""
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.persist_now(offset=42, path=log_path)
    body = json.loads(cursor_path.read_text())
    assert body["schema_version"] == "1.1"
    assert body["path"] == str(log_path)
    assert body["offset"] == 42
    assert body["persisted_at"].endswith("Z")
    # VL-2: field renamed to ``events_in_this_persist_window``.
    assert body["events_in_this_persist_window"] == 0
    # P2-L2: legacy field also written for one release cycle.
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


def test_restore_into_matching_path_seeks(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    today_path = current_day_path(tmp_path, _TODAY)
    # Create the file so bounds validation has something to stat.
    today_path.write_bytes(b"x" * 1024)
    cp.persist_now(offset=512, path=today_path)

    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.current_path == today_path
    assert reader.cursor_offset == 512


def test_restore_into_day_rollover_drains_yesterday_first(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """VH-1 — restore_into seats reader on yesterday's path at persisted offset.

    The tail loop then drains the remaining bytes BEFORE the day-rollover
    transition fires, preserving the AC7 exactly-once invariant.
    """
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    yesterday_path = tmp_path / "2026-05-18.jsonl"
    # Yesterday's file has 16384 bytes; cursor was at 8192 → 8192 unread.
    yesterday_path.write_bytes(b"x" * 16384)
    cp.persist_now(offset=8192, path=yesterday_path)

    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    # VH-1: reader points at YESTERDAY's file (not today's) so the tail
    # loop drains [persisted_offset, yesterday_EOF) first.
    assert reader.current_path == yesterday_path
    assert reader.cursor_offset == 8192
    # WARNING message changed from "abandons events" to the new
    # backfill-aware marker.
    assert any(
        entry.get("event") == "tail.draining_yesterday_before_rollover"
        for entry in captured_log_events
    )


def test_restore_into_missing_cursor_starts_fresh(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert reader.current_path == current_day_path(tmp_path, _TODAY)


def test_restore_into_corrupt_file_starts_fresh_with_warning(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_text("{not valid json")
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert any(
        entry.get("event") == "metrics_subscriber_cursor_corrupt" for entry in captured_log_events
    )


def test_restore_into_unknown_schema_version_raises(tmp_path: Path) -> None:
    """VH-9 — unknown schema_version REFUSES TO START (was: silent reset)."""
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
    with pytest.raises(CursorSchemaVersionError):
        cp.restore_into(reader, base_dir=tmp_path)


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
    # Note: VH-10's lockfile is a separate concern — it's only created
    # by ``lock()``, not by persist_now.
    assert files == ["cursor.json"]
    body = json.loads(cursor_path.read_text())
    assert body["offset"] == 2


# ---------------------------------------------------------------------------
# VH-10 — concurrent subscriber refuses to start
# ---------------------------------------------------------------------------


def test_concurrent_subscriber_refuses_to_start(tmp_path: Path) -> None:
    """VH-10 — second :meth:`lock` call (in same process) raises BlockingIOError.

    fcntl.flock semantics (corrected in P2-L1): on Linux fcntl.flock
    is per-OFD — contends across distinct ``os.open`` calls in the
    same process AND across processes.  This in-process test
    validates OFD contention only.  See
    ``test_restart_recovery_subprocess.test_subprocess_second_instance_refuses_with_concurrent_start_event``
    for cross-process coverage (P2-L1, ``@pytest.mark.slow``).
    """
    cursor_path = tmp_path / "cursor.json"
    cp1 = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    cp1.lock()
    try:
        cp2 = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
        with pytest.raises(BlockingIOError):
            cp2.lock()
    finally:
        cp1.unlock()


def test_lock_released_by_unlock(tmp_path: Path) -> None:
    """After ``unlock`` a fresh :class:`CursorPersistence` can acquire."""
    cursor_path = tmp_path / "cursor.json"
    cp1 = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    cp1.lock()
    cp1.unlock()
    cp2 = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    cp2.lock()
    cp2.unlock()


# ---------------------------------------------------------------------------
# VH-11 — cursor offset bounds validation
# ---------------------------------------------------------------------------


def test_restore_into_negative_offset_resets_to_zero_and_logs_warning(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P3-H4 / Q12 — negative offset emits WARNING and resets to 0.

    Pre-pass-3 a negative offset raised ``ValueError`` — uncaught
    through ``asyncio.run()``, undefined exit code, broke the Q6
    0/1/2/3 matrix.  Aligned with the existing
    ``cursor_invalid_shape`` / ``cursor_missing_fields`` handling
    which already warn+reset.
    """
    cursor_path = tmp_path / "cursor.json"
    today_path = current_day_path(tmp_path, _TODAY)
    today_path.write_bytes(b"x" * 100)
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "path": str(today_path),
                "offset": -1,
            }
        )
    )
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    # Must NOT raise; must reset cursor to 0 and emit the new warning.
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 0
    assert any(
        entry.get("event") == "metrics_subscriber_cursor_offset_negative_resetting_to_zero"
        for entry in captured_log_events
    )


def test_restore_into_offset_beyond_eof_clamps_with_critical(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    cursor_path = tmp_path / "cursor.json"
    today_path = current_day_path(tmp_path, _TODAY)
    today_path.write_bytes(b"x" * 100)
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "path": str(today_path),
                "offset": 999_999,
            }
        )
    )
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    # Clamped to file_size (100).
    assert reader.cursor_offset == 100
    assert any(entry.get("event") == "cursor_offset_beyond_eof" for entry in captured_log_events)


# ---------------------------------------------------------------------------
# VH-12 — atomic write hardening
# ---------------------------------------------------------------------------


def test_atomic_write_cleans_tempfile_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VH-12 — a json.dump failure must NOT leave ``cursor.*.json.tmp`` orphans."""
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=1, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"

    original_dump = json.dump

    def _fail_dump(obj: object, fp: object, **kwargs: object) -> None:
        # Write some bytes first to ensure the tempfile is materialised
        # on disk before the OSError.
        original_dump({"sentinel": True}, fp, **kwargs)  # type: ignore[arg-type]
        raise OSError("simulated disk full")

    monkeypatch.setattr(json, "dump", _fail_dump)
    with pytest.raises(OSError):
        cp.persist_now(offset=1, path=log_path)

    # No ``cursor.*.json.tmp`` left behind.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith("cursor.")]
    assert leftovers == [], f"tempfile leak: {leftovers}"


def test_atomic_write_fsyncs_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """VH-12 — parent directory is fsynced after ``os.replace``.

    We patch ``os.fsync`` to record invocation paths and assert the
    parent-dir fd is among them.
    """
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=1, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"

    fsync_calls: list[int] = []
    original_fsync = os.fsync

    def _record_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", _record_fsync)
    cp.persist_now(offset=42, path=log_path)
    # At least 2 fsyncs: the tempfile + the parent directory.
    assert len(fsync_calls) >= 2


# ---------------------------------------------------------------------------
# P2-H7 — yesterday file missing → fall through to today-fresh
# ---------------------------------------------------------------------------


def test_restore_into_yesterday_missing_falls_through_to_today(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P2-H7 — persisted yesterday file deleted → start fresh on today.

    Logrotate or accidental deletion can remove yesterday's file
    between persist and restart.  Seating the reader on the missing
    path would stall forever (the rollover detection compares against
    ``self._current_path`` which IS the missing path).  Fix: fall
    through to today-fresh + log CRITICAL.
    """
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "path": str(tmp_path / "2026-05-18.jsonl"),  # does NOT exist
                "offset": 1000,
            }
        )
    )
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    # Fell through to today at offset 0.
    today_path = current_day_path(tmp_path, _TODAY)
    assert reader.current_path == today_path
    assert reader.cursor_offset == 0
    assert any(
        entry.get("event") == "metrics_subscriber_persisted_path_missing_falling_through_to_today"
        for entry in captured_log_events
    )


# ---------------------------------------------------------------------------
# P2-L2 — schema_version "1" still accepted for one release cycle
# ---------------------------------------------------------------------------


def test_restore_into_schema_v1_still_accepted(tmp_path: Path) -> None:
    """P2-L2 — schema_version "1" is accepted alongside "1.1" (transition)."""
    cursor_path = tmp_path / "cursor.json"
    today_path = current_day_path(tmp_path, _TODAY)
    today_path.write_bytes(b"x" * 1024)
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "1",  # pre-pass-2 cursor
                "path": str(today_path),
                "offset": 512,
            }
        )
    )
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    reader = EventLogReader(tmp_path, clock=_make_clock())
    cp.restore_into(reader, base_dir=tmp_path)
    assert reader.cursor_offset == 512


def test_persist_writes_dual_field_for_transition(tmp_path: Path) -> None:
    """P2-L2 — both legacy and renamed fields written for one release."""
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=5, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"
    cp.note_event_processed(3)
    cp.persist_now(offset=42, path=log_path)
    body = json.loads(cursor_path.read_text())
    # Both fields present; both equal.
    assert body["events_in_this_persist_window"] == 3
    assert body["events_processed_since_last_persist"] == 3
    assert body["schema_version"] == "1.1"


# ---------------------------------------------------------------------------
# P2-M6 — parent-dir fsync failure does not abort persist
# ---------------------------------------------------------------------------


def test_parent_dir_fsync_failure_logged_persist_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_log_events: list[MutableMapping[str, Any]],
) -> None:
    """P2-M6 — ``os.open(parent)`` failure is non-fatal; persist completes."""
    cursor_path = tmp_path / "cursor.json"
    cp = CursorPersistence(cursor_path, persist_every=1, clock=_make_clock())
    log_path = tmp_path / "2026-05-19.jsonl"

    original_open = os.open
    parent_str = str(cursor_path.parent)

    def _flaky_open(path: str | bytes, flags: int, mode: int = 0o777) -> int:
        if str(path) == parent_str and flags == os.O_RDONLY:
            raise OSError(24, "EMFILE simulation")
        return original_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _flaky_open)
    # Should NOT raise; the rename already succeeded.
    cp.persist_now(offset=99, path=log_path)
    # Cursor body is on disk.
    body = json.loads(cursor_path.read_text())
    assert body["offset"] == 99
    # Warning was emitted.
    assert any(
        entry.get("event") == "metrics_subscriber_parent_fsync_failed"
        for entry in captured_log_events
    )
