"""Unit tests for events.external_append.append_event_line (Epic-13 retro AI-13.2).

The load-bearing property: the file is created GROUP-WRITABLE (0o660) even under a
umask of 022 — a 0o640 file crash-loops registry-state's cross-uid recovery
(Stories 11.3.11/11.3.12). Plus: append semantics, never-world-readable, and the
FR26 non-blocking lock-contention behaviour.
"""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

import pytest

from events import append_event_line
from events.external_append import EVENT_LOG_FILE_MODE


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_creates_file_group_writable_0o660_under_umask_022(tmp_path: Path) -> None:
    """The whole point of AI-13.2: even with umask 022 (which would strip
    group-write to 0o640), the created file is 0o660 — registry-state (same omb
    group, different uid) must be able to re-open it r+b for recovery."""
    old = os.umask(0o022)
    try:
        p = tmp_path / "2026-06-02.jsonl"
        append_event_line(p, b'{"event":"one"}\n')
        assert _mode(p) == EVENT_LOG_FILE_MODE == 0o660
        # group-write bit present; others-triad zero (never world-readable).
        assert _mode(p) & stat.S_IWGRP
        assert _mode(p) & (stat.S_IRWXO) == 0
    finally:
        os.umask(old)


def test_appends_without_truncating(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    append_event_line(p, b'{"n":1}\n')
    append_event_line(p, b'{"n":2}\n')
    assert p.read_bytes() == b'{"n":1}\n{"n":2}\n'


def test_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "registry" / "events" / "day.jsonl"
    append_event_line(p, b"x\n")
    assert p.exists()
    assert _mode(p) == 0o660


def test_lock_contention_raises_blockingioerror(tmp_path: Path) -> None:
    """FR26: if another holder has LOCK_EX, append_event_line raises
    BlockingIOError (callers map to a clean exit-3, not a block)."""
    p = tmp_path / "contended.jsonl"
    append_event_line(p, b"seed\n")  # create it first
    holder = os.open(str(p), os.O_WRONLY | os.O_APPEND)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            append_event_line(p, b"blocked\n")
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)
    # After the holder releases, append succeeds again.
    append_event_line(p, b"after\n")
    assert b"after\n" in p.read_bytes()
    assert b"blocked\n" not in p.read_bytes()
