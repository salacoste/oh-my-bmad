"""Atomic file-edit primitive (Story 2.12 — FR30 / NFR-R2).

POSIX-only stdlib implementation of write-tmpfile + fsync + ``os.replace``
atomicity.  No third-party deps.

Algorithm
---------
1. Choose tmpfile path: ``<target>.tmp.<pid>.<8-hex-random>`` (sibling of
   *target* — guaranteed same filesystem).
2. ``os.open`` the tmpfile with ``O_WRONLY | O_CREAT | O_EXCL`` so collisions
   between concurrent writers fail loudly rather than silently truncate.
3. ``_chunked_write(fd, data)`` — module-level helper so the write-interrupt
   harness (``tests/crash-injection/_atomic_edit_runner.py``) can monkey-patch
   it to ``os._exit(137)`` after exactly N bytes.
4. ``os.fsync(fd)`` (gated on ``fsync_data``) → ``os.close(fd)``.
5. ``os.replace(tmp, target)`` — POSIX guarantees same-fs rename is atomic
   at the syscall level; observers see either the pre-edit inode or the
   post-edit inode but never a partial state.
6. ``os.fsync(parent_dir_fd)`` (gated on ``fsync_dir``) — POSIX rename
   atomicity does NOT imply durability of the directory entry.  A crash
   between the rename and the next page-cache flush can lose the rename.
   Default ON; opt-out for tests writing thousands of small files.
7. Cleanup tmpfile on ANY exception via ``try/except/finally``
   (``unlink(missing_ok=True)``; OSError on cleanup is logged + swallowed).

Cross-filesystem handling
-------------------------
``os.replace`` raises ``OSError(EXDEV)`` if the rename would cross
filesystems.  Because tmpfile and target are siblings this is impossible
under ordinary mounts; the explicit catch + clearer re-raise is
defense-in-depth for unusual configurations (bind-mounts, FUSE).

Stale-tmpfile recovery
----------------------
This primitive does NOT scavenge orphan ``*.tmp.*.*`` siblings from
prior crashed runs — that is a separate sweeper concern (out of scope
for Story 2.12).  Per-call cleanup is best-effort; orphans only persist
when the process is hard-killed mid-write.

Concurrent writers
------------------
Two ``atomic_write_bytes(same_target)`` calls race at the rename step;
each call individually remains atomic (the loser's tmpfile is shadowed
by the winner's rename).  Worktree-level mutual exclusion lives higher
in the stack (Story 5.3 worktree-lock).

Platform support
----------------
POSIX-only (macOS + Linux).  Windows support is out of scope per FR48 /
Architecture line 200 base-image decision.  ``os.replace``,
``os.fsync``, and ``O_EXCL`` all have POSIX-equivalent semantics on
both supported platforms.
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

# Chunk size for the write loop.  Small enough that the harness can
# interrupt at fine-grained byte offsets; large enough that 10 MB
# payloads don't spend disproportionate time in syscall overhead.
_DEFAULT_CHUNK_SIZE = 64 * 1024


def _chunked_write(fd: int, data: bytes) -> None:
    """Write *data* to *fd* in chunks, looping past short writes.

    POSIX ``write(2)`` is permitted to write fewer bytes than requested
    (rare on regular files, common on pipes/sockets).  Looping until
    ``pos == len(data)`` is the standard idiom.

    The write-interrupt harness monkey-patches THIS function (not
    ``os.write`` directly) so the patch scope is surgical: only
    atomic_edit's writes are interrupted, not Python's I/O layer or
    third-party deps that may also call ``os.write``.
    """
    pos = 0
    n = len(data)
    while pos < n:
        chunk_end = min(pos + _DEFAULT_CHUNK_SIZE, n)
        written = os.write(fd, data[pos:chunk_end])
        if written <= 0:  # pragma: no cover — POSIX guarantees > 0 or raises
            raise OSError(f"os.write returned {written} on fd={fd}")
        pos += written


def atomic_write_bytes(
    target: Path,
    data: bytes,
    *,
    fsync_data: bool = True,
    fsync_dir: bool = True,
) -> None:
    """Atomically replace *target* with *data*.

    Writes *data* to a sibling tmpfile (``<target.name>.tmp.<pid>.<rand>``),
    optionally fsyncs the file then renames atomically into *target* via
    :func:`os.replace` (POSIX guarantees same-fs rename is atomic).
    Optionally fsyncs the parent directory after the rename so the
    directory entry survives a host crash.

    Stale-tmpfile recovery is OUT OF SCOPE — orphan siblings from
    crashed prior runs are not scavenged here (separate sweeper).

    Args:
        target: Destination path.  Must have a parent directory (i.e.,
            cannot be ``/`` or any path whose parent is itself).
        data: Bytes to write.
        fsync_data: When True (default), ``fsync`` the tmpfile before
            close.  Disable for performance-critical batch writes that
            can tolerate post-crash data loss.
        fsync_dir: When True (default), ``fsync`` the parent directory
            after the rename so the directory entry survives a host
            crash.  Disable in tests writing thousands of small files.

    Raises:
        ValueError: if *target* has no parent directory.
        OSError: on tmpfile creation, write, fsync, or rename failure.
            Cross-filesystem rename re-raised with a clear message
            including both paths.
    """
    parent = target.parent
    if parent == target:
        raise ValueError(f"target has no parent directory: {target!r}")

    tmp_name = f"{target.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
    tmp = parent / tmp_name

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    closed = False
    try:
        try:
            _chunked_write(fd, data)
            if fsync_data:
                os.fsync(fd)
        finally:
            if not closed:
                os.close(fd)
                closed = True

        try:
            os.replace(tmp, target)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise OSError(
                    f"cross-filesystem atomic_write_bytes is unsafe — "
                    f"tmpfile path={tmp}, target={target}"
                ) from exc
            raise

        if fsync_dir:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        # Best-effort cleanup on ANY failure path (write, fsync, replace,
        # dir-fsync).  unlink may fail if os.replace already consumed
        # the tmpfile (success path doesn't reach here) or if the tmpfile
        # was never created — both are benign.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning(
                "atomic_write_bytes: tmpfile cleanup failed: %s (tmp=%s)",
                cleanup_exc,
                tmp,
            )
        raise


def atomic_write_text(
    target: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync_data: bool = True,
    fsync_dir: bool = True,
) -> None:
    """Encode *text* and call :func:`atomic_write_bytes`.

    ``fsync_data`` / ``fsync_dir`` are forwarded to
    :func:`atomic_write_bytes` so callers can opt out of either fsync
    from the text wrapper.
    """
    atomic_write_bytes(
        target,
        text.encode(encoding),
        fsync_data=fsync_data,
        fsync_dir=fsync_dir,
    )


__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
]
