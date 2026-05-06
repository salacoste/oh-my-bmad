"""Exclusive worktree lock acquisition + release (Story 5.3 — FR27, FR32, NFR-SC3).

Provides POSIX-safe mutual exclusion so two workers never mutate the same
worktree concurrently. The lock is a JSON file (``.oh-my-bmad.lock``) written
via :func:`atomic_write_text` so it is either fully present or absent — no
partial writes.

Lock acquisition is **not** best-effort: if the worktree is locked by another
session, :class:`WorktreeLockHeld` is raised and the worker MUST NOT start.
This is the whole point of FR27.

Lock release is idempotent and safe to call during shutdown cleanup.

Stale lock recovery is a manual procedure — the new worker does NOT silently
steal the lock. The operator deletes the lock file after confirming the old
session is failed.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from events.errors import WorktreeLockHeld

from worker_wrapper.domain.atomic_edit import atomic_write_text

logger = logging.getLogger(__name__)

_LOCK_FILE_NAME = ".oh-my-bmad.lock"


def _lock_path(worktree_path: Path) -> Path:
    return worktree_path / _LOCK_FILE_NAME


def read_lock(worktree_path: Path) -> dict[str, Any] | None:
    """Parse the lock file contents, or return ``None`` if absent or corrupt."""
    lock_file = _lock_path(worktree_path)
    try:
        text = lock_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        logger.warning("worktree_lock_corrupt path=%s", lock_file)
        return None


def is_lock_held(worktree_path: Path) -> bool:
    """Return ``True`` if a lock file exists in the worktree."""
    return _lock_path(worktree_path).exists()


def acquire_lock(
    worktree_path: Path,
    session_id: str,
    worker_id: str,
) -> None:
    """Acquire an exclusive lock on *worktree_path*.

    Raises :class:`WorktreeLockHeld` if the worktree is already locked by a
    **different** session. Idempotent if the lock is already held by this
    session (same *session_id*).

    Args:
        worktree_path: Absolute path to the git worktree root.
        session_id: The current worker's session ID (``s-...``).
        worker_id: The current worker's worker ID (``w-...``).
    """
    existing = read_lock(worktree_path)
    if existing is not None:
        held_by = existing.get("session_id", "")
        if held_by != session_id:
            raise WorktreeLockHeld(
                session_id=held_by,
                worktree_path=str(worktree_path),
            )
        return  # already held by us — idempotent

    payload = json.dumps(
        {
            "session_id": session_id,
            "worker_id": worker_id,
            "acquired_at": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
    )
    atomic_write_text(
        _lock_path(worktree_path),
        payload,
        fsync_data=False,
        fsync_dir=False,
    )
    logger.info(
        "worktree_lock_acquired worktree=%s session=%s",
        worktree_path,
        session_id,
    )


def release_lock(worktree_path: Path, session_id: str) -> None:
    """Release the lock on *worktree_path*.

    Idempotent: no-op if the lock file does not exist. Logs a warning if the
    lock is held by a different session (should not happen in normal flow —
    indicates a logic error in the caller).

    Args:
        worktree_path: Absolute path to the git worktree root.
        session_id: The current worker's session ID (``s-...``).
    """
    lock_file = _lock_path(worktree_path)
    if not lock_file.exists():
        return

    existing = read_lock(worktree_path)
    if existing is not None and existing.get("session_id") != session_id:
        logger.warning(
            "worktree_lock_release_mismatch worktree=%s expected=%s found=%s",
            worktree_path,
            session_id,
            existing.get("session_id"),
        )
        return  # do not remove a lock we don't own

    with contextlib.suppress(FileNotFoundError):
        lock_file.unlink()
    logger.info(
        "worktree_lock_released worktree=%s session=%s",
        worktree_path,
        session_id,
    )


__all__ = [
    "WorktreeLockHeld",
    "acquire_lock",
    "is_lock_held",
    "read_lock",
    "release_lock",
]
