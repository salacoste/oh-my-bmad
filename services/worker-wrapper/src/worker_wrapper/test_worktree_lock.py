"""Tests for worktree lock acquisition + release (Story 5.3)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from events.errors import WorktreeLockHeld
from events.ids import new_session_id, new_worker_id

from worker_wrapper.domain.worktree_lock import (
    acquire_lock,
    is_lock_held,
    read_lock,
    release_lock,
)


def _sid() -> str:
    return new_session_id()


def _wid() -> str:
    return new_worker_id()


# ---------------------------------------------------------------------------
# read_lock / is_lock_held
# ---------------------------------------------------------------------------


class TestReadLock:
    def test_returns_none_when_no_lock(self, tmp_path: Path) -> None:
        assert read_lock(tmp_path) is None

    def test_parses_valid_lock(self, tmp_path: Path) -> None:
        lock_data = {
            "session_id": "s-abc",
            "worker_id": "w-xyz",
            "acquired_at": "2026-01-01T00:00:00+00:00",
        }
        (tmp_path / ".oh-my-bmad.lock").write_text(json.dumps(lock_data))
        result = read_lock(tmp_path)
        assert result is not None
        assert result["session_id"] == "s-abc"

    def test_returns_none_on_corrupt_lock(self, tmp_path: Path) -> None:
        (tmp_path / ".oh-my-bmad.lock").write_text("not json{{{")
        assert read_lock(tmp_path) is None


class TestIsLockHeld:
    def test_false_when_no_lock(self, tmp_path: Path) -> None:
        assert is_lock_held(tmp_path) is False

    def test_true_when_lock_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".oh-my-bmad.lock").write_text("{}")
        assert is_lock_held(tmp_path) is True


# ---------------------------------------------------------------------------
# acquire_lock
# ---------------------------------------------------------------------------


class TestAcquireLock:
    def test_creates_lock_file(self, tmp_path: Path) -> None:
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)
        lock = read_lock(tmp_path)
        assert lock is not None
        assert lock["session_id"] == sid
        assert lock["worker_id"] == wid
        assert "acquired_at" in lock

    def test_lock_file_is_valid_json(self, tmp_path: Path) -> None:
        acquire_lock(tmp_path, _sid(), _wid())
        text = (tmp_path / ".oh-my-bmad.lock").read_text()
        parsed = json.loads(text)
        assert all(k in parsed for k in ("session_id", "worker_id", "acquired_at"))

    def test_idempotent_same_session(self, tmp_path: Path) -> None:
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)
        acquire_lock(tmp_path, sid, wid)  # no raise
        lock = read_lock(tmp_path)
        assert lock is not None
        assert lock["session_id"] == sid

    def test_raises_on_contention(self, tmp_path: Path) -> None:
        sid1, wid1 = _sid(), _wid()
        sid2, wid2 = _sid(), _wid()
        acquire_lock(tmp_path, sid1, wid1)
        with pytest.raises(WorktreeLockHeld) as exc_info:
            acquire_lock(tmp_path, sid2, wid2)
        assert exc_info.value.session_id == sid1
        assert str(tmp_path) in exc_info.value.worktree_path

    def test_retains_original_lock_on_contention(self, tmp_path: Path) -> None:
        sid1, wid1 = _sid(), _wid()
        acquire_lock(tmp_path, sid1, wid1)
        with contextlib.suppress(WorktreeLockHeld):
            acquire_lock(tmp_path, _sid(), _wid())
        lock = read_lock(tmp_path)
        assert lock is not None
        assert lock["session_id"] == sid1  # original holder unchanged


# ---------------------------------------------------------------------------
# release_lock
# ---------------------------------------------------------------------------


class TestReleaseLock:
    def test_removes_lock_file(self, tmp_path: Path) -> None:
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)
        release_lock(tmp_path, sid)
        assert is_lock_held(tmp_path) is False

    def test_noop_when_no_lock(self, tmp_path: Path) -> None:
        release_lock(tmp_path, _sid())  # no raise

    def test_noop_when_session_mismatch(self, tmp_path: Path) -> None:
        sid1, wid1 = _sid(), _wid()
        acquire_lock(tmp_path, sid1, wid1)
        release_lock(tmp_path, _sid())  # different session
        assert is_lock_held(tmp_path) is True  # lock NOT removed

    def test_idempotent_release(self, tmp_path: Path) -> None:
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)
        release_lock(tmp_path, sid)
        release_lock(tmp_path, sid)  # second call — no raise


# ---------------------------------------------------------------------------
# Integration: acquire + release cycle
# ---------------------------------------------------------------------------


class TestLockCycle:
    def test_acquire_release_reacquire(self, tmp_path: Path) -> None:
        sid1, wid1 = _sid(), _wid()
        acquire_lock(tmp_path, sid1, wid1)
        release_lock(tmp_path, sid1)
        sid2, wid2 = _sid(), _wid()
        acquire_lock(tmp_path, sid2, wid2)  # new session can acquire
        lock = read_lock(tmp_path)
        assert lock is not None
        assert lock["session_id"] == sid2

    def test_stale_lock_blocks_new_worker(self, tmp_path: Path) -> None:
        """AC-3: stale lock is not silently stolen."""
        sid1, wid1 = _sid(), _wid()
        acquire_lock(tmp_path, sid1, wid1)
        # Simulate ungraceful exit — lock file remains
        sid2, wid2 = _sid(), _wid()
        with pytest.raises(WorktreeLockHeld):
            acquire_lock(tmp_path, sid2, wid2)


# ---------------------------------------------------------------------------
# Story 7.5.5: TOCTOU regression tests for release_lock
# ---------------------------------------------------------------------------


class TestReleaseLockTOCTOU:
    """AC-2: release_lock safe when lock file vanishes between read and unlink."""

    def test_release_no_raise_when_file_deleted_by_another_process(self, tmp_path: Path) -> None:
        """File vanishes before read_lock: another process deletes it."""
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)
        (tmp_path / ".oh-my-bmad.lock").unlink()

        release_lock(tmp_path, sid)  # no raise
        assert is_lock_held(tmp_path) is False

    def test_release_handles_fnfe_on_unlink(self, tmp_path: Path) -> None:
        """File vanishes between read_lock and unlink: FNFE during unlink."""
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)

        lock_file = tmp_path / ".oh-my-bmad.lock"

        def _unlink_only_lock(self_path: Path, *args: Any, **kwargs: Any) -> None:
            if self_path == lock_file:
                raise FileNotFoundError("simulated TOCTOU race")
            return original_unlink(self_path, *args, **kwargs)

        original_unlink = Path.unlink
        with patch.object(Path, "unlink", _unlink_only_lock):
            release_lock(tmp_path, sid)  # no raise

    def test_concurrent_release_both_succeed(self, tmp_path: Path) -> None:
        """Same session releases lock twice: second call is idempotent no-op."""
        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)

        release_lock(tmp_path, sid)
        release_lock(tmp_path, sid)

        assert is_lock_held(tmp_path) is False

    def test_release_no_error_log_on_vanished_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """FNFE on unlink produces no ERROR-level logs."""
        import logging

        sid, wid = _sid(), _wid()
        acquire_lock(tmp_path, sid, wid)

        lock_file = tmp_path / ".oh-my-bmad.lock"

        def _unlink_only_lock(self_path: Path, *args: Any, **kwargs: Any) -> None:
            if self_path == lock_file:
                raise FileNotFoundError("simulated TOCTOU race")
            return original_unlink(self_path, *args, **kwargs)

        original_unlink = Path.unlink
        with (
            patch.object(Path, "unlink", _unlink_only_lock),
            caplog.at_level(logging.DEBUG, logger="worker_wrapper.domain.worktree_lock"),
        ):
            release_lock(tmp_path, sid)

        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_logs) == 0
