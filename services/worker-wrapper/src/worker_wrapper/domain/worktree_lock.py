"""Exclusive worktree lock acquisition + release (Story 5.3 — FR27, FR32, NFR-SC3).

Provides POSIX-safe mutual exclusion so two workers never mutate the same
worktree concurrently. The lock is a JSON file (``.oh-my-bmad.lock``) created
via ``O_CREAT | O_EXCL`` so the kernel enforces atomic create-or-fail —
no TOCTOU window between the check and the write.

Lock acquisition is **not** best-effort: if the worktree is locked by another
session, :class:`WorktreeLockHeld` is raised and the worker MUST NOT start.
This is the whole point of FR27.

Lock release is idempotent and safe to call during shutdown cleanup.

Stale lock recovery is a manual procedure — the new worker does NOT silently
steal the lock. The operator deletes the lock file after confirming the old
session is failed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from events.errors import WorktreeLockHeld

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
    """Return ``True`` if a valid (parseable) lock file exists."""
    return read_lock(worktree_path) is not None


def acquire_lock(
    worktree_path: Path,
    session_id: str,
    worker_id: str,
) -> None:
    """Acquire an exclusive lock on *worktree_path*.

    Raises :class:`WorktreeLockHeld` if the worktree is already locked by a
    **different** session. Idempotent if the lock is already held by this
    session (same *session_id*).

    Uses ``O_CREAT | O_EXCL`` for atomic create-or-fail at the kernel level,
    closing the TOCTOU window between the existence check and the write.

    Args:
        worktree_path: Absolute path to the git worktree root.
        session_id: The current worker's session ID (``s-...``).
        worker_id: The current worker's worker ID (``w-...``).
    """
    lock_file = _lock_path(worktree_path)
    payload = json.dumps(
        {
            "session_id": session_id,
            "worker_id": worker_id,
            "acquired_at": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
    ).encode("utf-8")

    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as err:
        # Lock file exists — check if we already hold it (idempotent) or
        # if another session holds it (contention).
        existing = read_lock(worktree_path)
        if existing is not None and existing.get("session_id") == session_id:
            return  # already held by us — idempotent
        held_by = existing.get("session_id", "<unknown>") if existing else "<corrupt>"
        raise WorktreeLockHeld(
            session_id=held_by,
            worktree_path=str(worktree_path),
        ) from err

    # We won the atomic create — write the payload.
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
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

    existing = read_lock(worktree_path)
    if existing is None:
        return  # no lock file — idempotent no-op

    if existing.get("session_id") != session_id:
        logger.warning(
            "worktree_lock_release_mismatch worktree=%s expected=%s found=%s",
            worktree_path,
            session_id,
            existing.get("session_id"),
        )
        return  # do not remove a lock we don't own

    try:  # noqa: SIM105 — explicit handler documents the TOCTOU acceptance
        lock_file.unlink()
    except FileNotFoundError:
        # Another process removed the lock between read_lock and unlink.
        # Safe — the lock is gone, which is the desired end state.
        logger.info(
            "worktree_lock_already_released worktree=%s session=%s",
            worktree_path,
            session_id,
        )
        return
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
