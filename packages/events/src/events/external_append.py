"""Shared FR26-respecting safe-append for EXTERNAL event emitters.

registry-state is the canonical single-writer of the event log (FR26). A handful
of EXTERNAL emitters — operator-run scripts / cron (e.g.
``scripts/emit_signature_rejected.py``, ``scripts/check_replication_lag.py``) —
legitimately need to append ONE event to the per-day JSONL when registry-state is
not the one producing it. Story 13.4a / Epic-13 retro AI-13.2: those emitters kept
re-implementing the append independently, and the original copy
(``emit_signature_rejected.py``, Story 8.6) baked in the **pre-11.3.11 ``0o640``
file mode** — which crash-loops registry-state's cross-uid recovery. This helper
is the SINGLE correct implementation they all share so the bug cannot recur.

The discipline (all three are load-bearing):

* **mode 0o660** (rw-rw----) created via ``os.open(..., O_CREAT, 0o660)`` AND an
  explicit ``os.fchmod(fd, 0o660)`` to DEFEAT the process umask (022 strips
  group-write back to 0o640). If an external emitter creates the day's file at
  0o640, registry-state (a different uid in the shared ``omb`` group) cannot
  re-open it ``r+b`` for crash recovery and crash-loops (Stories 11.3.11/11.3.12).
  The others-triad stays 0 — the audit log is NEVER world-readable.
* **fcntl.flock(LOCK_EX | LOCK_NB)** — non-blocking. Raises ``BlockingIOError`` if
  registry-state (the live single-writer) holds the lock; callers map that to a
  clean "try later" exit (exit 3 by convention) rather than blocking.
* **fsync under the lock** — the bytes are on stable storage before any other
  writer can race for the lock (durable atomic append).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

#: File mode for event-log JSONL: owner+group read-write, never world-readable.
EVENT_LOG_FILE_MODE: int = 0o660

__all__ = ["EVENT_LOG_FILE_MODE", "append_event_line"]


def append_event_line(path: Path, line: bytes) -> None:
    """Append *line* (a single canonical-JSON event + ``b"\\n"``) to *path*.

    FR26-respecting external-emitter append — see the module docstring. Creates
    *path* (and parents) at mode ``0o660`` if absent, locks non-blocking, writes,
    fsyncs under the lock, unlocks.

    Raises:
        BlockingIOError: the file lock is held (registry-state is the live
            writer). Callers should treat this as "not my turn" and exit cleanly
            (convention: exit code 3), NOT retry-block.
        OSError: other I/O failures (disk full, permission, etc.).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, EVENT_LOG_FILE_MODE)
    with os.fdopen(fd, "ab", closefd=True) as f:
        # Defeat umask 022 (which would leave 0o640, no group-write → registry-state
        # cross-uid recovery fails). Best-effort: only the file owner may fchmod, so
        # a file owned by another same-group uid is left as-is (it was created 0o660
        # by whoever owns it, or this raises harmlessly under suppress).
        with contextlib.suppress(OSError):
            os.fchmod(f.fileno(), EVENT_LOG_FILE_MODE)
        # Non-blocking exclusive lock: BlockingIOError propagates to the caller
        # (FR26 contention with the live writer → clean exit, not a block).
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
