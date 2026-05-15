"""Atomic file-edit primitive (Story 2.12 — FR30 / NFR-R2).

POSIX-only stdlib implementation of write-tmpfile + fsync + ``os.replace``
atomicity.  No third-party deps.

Algorithm
---------
1. Choose tmpfile path: ``<target>.tmp.<pid>.<16-hex-random>`` (sibling of
   *target* — guaranteed same filesystem). ``token_hex(8)`` → 64 random
   bits, comfortably collision-free in practice (vs. 32 bits from
   ``token_hex(4)`` which had a birthday collision at ~65k concurrent
   writers).
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

   **Failure handling for dir-fsync:** if the dir-fsync raises after the
   rename has succeeded, the data is *already on disk* — only its
   directory-entry durability is weakened.  Raising at this point would
   tell the caller "the write didn't happen", which would prompt a retry
   that double-writes.  Instead, the dir-fsync error is logged at WARNING
   and SUPPRESSED; the next syscall flushing this directory's inode
   (any subsequent metadata change, periodic background flush) repairs
   the durability gap.
7. Cleanup tmpfile on ANY pre-rename exception via ``try/except/finally``
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

Mode preservation
-----------------
When *target* exists pre-write, its file-mode bits are captured via
``os.stat`` and re-applied via ``os.chmod`` after ``os.replace`` so that
e.g. a 0o644 target isn't silently downgraded to 0o600 (the tmpfile's
``O_EXCL`` creation mode).  The chmod is best-effort: a failure is
logged at WARNING but does not raise.

Platform support
----------------
POSIX-only (macOS + Linux).  Windows support is out of scope per FR48 /
Architecture line 200 base-image decision.  ``os.replace``,
``os.fsync``, and ``O_EXCL`` all have POSIX-equivalent semantics on
both supported platforms.

Platform durability notes
-------------------------
Linux ext4/xfs honor ``fsync(dir_fd)`` strictly: after it returns,
the directory entry IS on stable storage.  macOS APFS's
``fsync(dir_fd)`` is best-effort (Apple's documented design — a
companion ``F_FULLFSYNC`` ioctl is required for full barrier
semantics, but it costs an order of magnitude more wall time and
isn't called from this primitive).  Production deploys target Linux
containers per FR48 / Architecture line 200; macOS is supported for
local development where the slightly weaker durability is acceptable.
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from secret_hygiene.scanner import scan_text

logger = logging.getLogger(__name__)

# Chunk size for the write loop.  Small enough that the harness can
# interrupt at fine-grained byte offsets; large enough that 10 MB
# payloads don't spend disproportionate time in syscall overhead.
_DEFAULT_CHUNK_SIZE = 64 * 1024
_MAX_EDIT_SIZE: int = 1_000_000


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
        if written <= 0:
            # POSIX guarantees write() returns > 0 or raises; this guard
            # exists so a misbehaving libc / FUSE filesystem cannot loop
            # forever silently consuming CPU.
            raise OSError(f"os.write returned {written} on fd={fd}")
        pos += written


def atomic_write_bytes(
    target: Path | str,
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

    Validation runs BEFORE any I/O: empty paths and parent-less targets
    raise ``ValueError`` with no side-effect on the filesystem.

    File mode of an existing *target* is preserved across the replace
    (best-effort ``os.chmod`` from a captured ``os.stat`` snapshot).

    Args:
        target: Destination path (``Path`` or ``str``).  Must have a
            parent directory and a non-empty basename (i.e., cannot be
            ``/`` / ``""`` / any path whose parent is itself).
        data: Bytes to write.
        fsync_data: When True (default), ``fsync`` the tmpfile before
            close.  Disable for performance-critical batch writes that
            can tolerate post-crash data loss.
        fsync_dir: When True (default), ``fsync`` the parent directory
            after the rename so the directory entry survives a host
            crash.  Disable in tests writing thousands of small files.
            **A failure of the dir-fsync after a successful rename is
            logged at WARNING and SUPPRESSED** — the data is already on
            disk; raising would mislead the caller into double-writing.

    Raises:
        ValueError: if *target* is empty or has no parent directory.
            Raised before any I/O.
        OSError: on tmpfile creation, write, fsync(data), or rename
            failure.  Cross-filesystem rename re-raised with a clear
            message including both paths.  ``FileNotFoundError`` from
            a missing parent directory is re-raised with a clearer
            message.  Dir-fsync errors are logged-not-raised (see above).
    """
    target = Path(target)
    if not target.name:
        raise ValueError(f"target has empty basename: {target!r}")
    parent = target.parent
    if parent == target:
        raise ValueError(f"target has no parent directory: {target!r}")

    # token_hex(8) → 16 hex chars, 64 bits of randomness.
    tmp_name = f"{target.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    tmp = parent / tmp_name

    # Capture existing target's mode (best-effort) so the replace doesn't
    # silently downgrade e.g. 0o644 → 0o600.  None if target doesn't exist.
    preserved_mode: int | None = None
    try:
        preserved_mode = os.stat(target).st_mode & 0o7777
    except FileNotFoundError:
        preserved_mode = None
    except OSError:
        # Permission / loop / etc. — fall back to default tmp mode.
        preserved_mode = None

    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileNotFoundError as exc:
        # Parent directory missing — re-raise with a clearer message.
        raise FileNotFoundError(f"target's parent directory does not exist: {parent}") from exc

    try:
        try:
            _chunked_write(fd, data)
            if fsync_data:
                os.fsync(fd)
        finally:
            # finally guarantees close runs even if write/fsync raised.
            os.close(fd)

        try:
            os.replace(tmp, target)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise OSError(
                    f"cross-filesystem atomic_write_bytes is unsafe — "
                    f"tmpfile path={tmp}, target={target}"
                ) from exc
            raise

        # Restore mode of the previous target (best-effort).
        if preserved_mode is not None:
            try:
                os.chmod(target, preserved_mode)
            except OSError as chmod_exc:
                logger.warning(
                    "atomic_write_bytes: chmod-restore failed: %s (target=%s, mode=%o)",
                    chmod_exc,
                    target,
                    preserved_mode,
                )

        if fsync_dir:
            # Best-effort directory-entry durability.  Failure here means
            # data is already on disk but the directory inode hasn't been
            # flushed; the next metadata change or periodic background
            # flush will repair it.  Raising would mislead the caller
            # into thinking the write didn't happen → double-write.
            try:
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as dir_exc:
                code = errno.errorcode.get(dir_exc.errno if dir_exc.errno is not None else -1, "?")
                logger.warning(
                    "atomic_write_bytes: dir-fsync failed (data already on "
                    "disk; suppressing): %s [%s] (parent=%s)",
                    dir_exc,
                    code,
                    parent,
                )
    except BaseException:
        # Best-effort cleanup on ANY failure path BEFORE the rename
        # succeeded.  unlink may fail if os.replace already consumed
        # the tmpfile (success path doesn't reach here) or if the tmpfile
        # was never created — both are benign.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            code = errno.errorcode.get(
                cleanup_exc.errno if cleanup_exc.errno is not None else -1, "?"
            )
            logger.warning(
                "atomic_write_bytes: tmpfile cleanup failed: %s [%s] (tmp=%s)",
                cleanup_exc,
                code,
                tmp,
            )
        raise


def atomic_write_text(
    target: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    fsync_data: bool = True,
    fsync_dir: bool = True,
) -> None:
    """Encode *text* and call :func:`atomic_write_bytes`.

    *encoding* and *errors* are forwarded to :meth:`str.encode`.
    ``fsync_data`` / ``fsync_dir`` are forwarded to
    :func:`atomic_write_bytes` so callers can opt out of either fsync
    from the text wrapper.
    """
    atomic_write_bytes(
        target,
        text.encode(encoding, errors),
        fsync_data=fsync_data,
        fsync_dir=fsync_dir,
    )


# ---------------------------------------------------------------------------
# Higher-level edit/write primitives (Story 5.6 — FR30 / NFR-R2)
# ---------------------------------------------------------------------------


@dataclass
class EditValidation:
    """Result of validating edit parameters against file content."""

    valid: bool
    match_count: int
    error: str | None = None


@dataclass
class FileEditResult:
    """Result of an atomic file edit or write operation.

    ``secret_matches`` tri-state semantics:
      ``None``  — scan not reached (earlier error before secret check)
      ``[]``    — scanned, clean
      ``[...]`` — secrets detected, write aborted
    """

    target_path: str
    success: bool
    session_id: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    secrets_detected: bool = False
    secret_matches: list[str] | None = None
    error: str | None = None


def validate_edit(
    old_content: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> EditValidation:
    """Validate edit parameters without touching the filesystem.

    Checks that *old_string* is non-empty, present in *old_content*, and
    (when *replace_all* is ``False``) appears exactly once.  Also bounds-
    checks both *old_string* and *new_string* lengths to prevent unbounded
    operations, and rejects no-op edits where *old_string* == *new_string*.
    """
    if not old_string:
        return EditValidation(valid=False, match_count=0, error="old_string must be non-empty")
    if len(old_string) > _MAX_EDIT_SIZE:
        return EditValidation(
            valid=False,
            match_count=0,
            error=f"old_string exceeds {_MAX_EDIT_SIZE} chars",
        )
    if len(new_string) > _MAX_EDIT_SIZE:
        return EditValidation(
            valid=False,
            match_count=0,
            error=f"new_string exceeds {_MAX_EDIT_SIZE} chars",
        )
    if old_string == new_string:
        return EditValidation(
            valid=False,
            match_count=0,
            error="old_string identical to new_string — no-op edit",
        )
    count = old_content.count(old_string)
    if count == 0:
        return EditValidation(valid=False, match_count=0, error="old_string not found in content")
    if not replace_all and count > 1:
        return EditValidation(
            valid=False,
            match_count=count,
            error=f"old_string found {count} times; set replace_all=True",
        )
    return EditValidation(valid=True, match_count=count)


def apply_file_edit(
    target: Path | str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    session_id: str = "",
) -> FileEditResult:
    """Read *target*, apply ``old_string`` → ``new_string`` replacement, write atomically.

    Scans the result for secrets before writing; aborts (does not write) if
    any are detected.  Returns a :class:`FileEditResult` describing the
    outcome.

    .. note:: TOCTOU — callers must hold the worktree lock (Story 5.3)
        before calling.  The read-validate-write sequence is NOT atomic;
        a concurrent writer can modify the file between the read and the
        rename.  Path validation (worktree confinement) is the caller's
        responsibility.
    """
    target = Path(target)
    target_str = str(target)

    # Read existing content.
    try:
        old_content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"file not found: {target}",
        )
    except UnicodeDecodeError as exc:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"file is not valid UTF-8 text: {exc}",
        )
    except OSError as exc:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"cannot read file: {exc}",
        )

    # Validate edit parameters.
    validation = validate_edit(old_content, old_string, new_string, replace_all=replace_all)
    if not validation.valid:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=validation.error,
        )

    # Apply replacement.
    if replace_all:
        new_content = old_content.replace(old_string, new_string)
    else:
        new_content = old_content.replace(old_string, new_string, 1)

    # Secret scanning — abort if secrets detected.
    try:
        matches = scan_text(new_content)
    except Exception as exc:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"secret scan failed: {exc}",
        )
    if matches:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            secrets_detected=True,
            secret_matches=[m.excerpt for m in matches],
            error="secret detected in edited content — write aborted",
        )

    # Line counts for observability.
    old_lines = len(old_content.splitlines())
    new_lines = len(new_content.splitlines())

    # Atomic write.
    try:
        atomic_write_text(target, new_content)
    except (OSError, ValueError) as exc:
        logger.warning("apply_file_edit: write failed: %s (target=%s)", exc, target)
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"atomic write failed: {exc}",
        )

    return FileEditResult(
        target_path=target_str,
        success=True,
        session_id=session_id,
        lines_added=max(0, new_lines - old_lines),
        lines_removed=max(0, old_lines - new_lines),
        secret_matches=[],
    )


def apply_file_write(
    target: Path | str,
    content: str,
    *,
    session_id: str = "",
) -> FileEditResult:
    """Write *content* to *target* atomically, creating parent dirs if needed.

    Scans *content* for secrets before writing; aborts (does not write) if
    any are detected.  Returns a :class:`FileEditResult` describing the
    outcome.

    .. note:: Path validation (worktree confinement) is the caller's
        responsibility (enforced at the call site in Story 5.12).
    """
    target = Path(target)
    target_str = str(target)

    # Size guard — prevents unbounded memory/IO from pathological inputs.
    if len(content) > _MAX_EDIT_SIZE:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"content exceeds {_MAX_EDIT_SIZE} chars",
        )

    # Secret scanning — abort if secrets detected.
    try:
        matches = scan_text(content)
    except Exception as exc:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"secret scan failed: {exc}",
        )
    if matches:
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            secrets_detected=True,
            secret_matches=[m.excerpt for m in matches],
            error="secret detected in content — write aborted",
        )

    # Create parent directories if needed.
    parent = target.parent
    if parent and parent != target:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            return FileEditResult(
                target_path=target_str,
                success=False,
                session_id=session_id,
                error=f"cannot create parent directory: {exc}",
            )

    # Count lines for observability (compare with existing file if present).
    old_lines = 0
    try:
        old_content = target.read_text(encoding="utf-8")
        old_lines = len(old_content.splitlines())
    except UnicodeDecodeError:
        pass
    except (FileNotFoundError, OSError):
        pass
    new_lines = len(content.splitlines())

    # Atomic write.
    try:
        atomic_write_text(target, content)
    except (OSError, ValueError) as exc:
        logger.warning("apply_file_write: write failed: %s (target=%s)", exc, target)
        return FileEditResult(
            target_path=target_str,
            success=False,
            session_id=session_id,
            error=f"atomic write failed: {exc}",
        )

    return FileEditResult(
        target_path=target_str,
        success=True,
        session_id=session_id,
        lines_added=max(0, new_lines - old_lines),
        lines_removed=max(0, old_lines - new_lines),
        secret_matches=[],
    )


__all__ = [
    "EditValidation",
    "FileEditResult",
    "apply_file_edit",
    "apply_file_write",
    "atomic_write_bytes",
    "atomic_write_text",
    "validate_edit",
]
