"""Event-log JSONL append writer for registry-state (Story 2.4).

Design decisions:

**Canonical-JSON-first serialization** — every line on disk is produced by
``to_canonical_json(envelope)``, the deterministic byte-stable serializer from
Story 2.1.  Using ``model_dump_json()`` or any other path would violate the
replay-determinism guarantee (FR20).  The format is:
``<canonical-json-bytes>\\n`` — exactly one 0x0A byte appended, no BOM, no CR,
no whitespace.

**fdatasync not fsync** — after every ``os.write(fd, data)`` we call
``os.fdatasync(fd)`` (not ``os.fsync(fd)``).  ``fdatasync`` flushes file data
to the storage device without forcing inode-metadata (mtime, size attribute)
to disk.  This is 10-30% faster on ext4/XFS while preserving the full
durability guarantee we care about: the line's bytes are durable.  We do not
care about mtime.  Python's ``os.fdatasync`` is available on Linux (and macOS
via ``fcntl(F_FULLFSYNC)`` since Python 3.3).  On macOS environments that lack
``os.fdatasync`` we fall back to ``os.fsync`` via ``getattr`` at module load;
this is a dev-convenience fallback and should not occur on production Linux.

**O_APPEND atomic write — no temp-file-and-rename** — a single ``write(fd,
bytes)`` call under ``O_APPEND`` on Linux ext4/XFS holds the inode lock for the
duration of the syscall, preventing interleaving with any concurrent writers on
the same inode.  Combined with FR26 (single writer enforced by
``scripts/check_single_writer.py``), one ``write()`` call per event is
sufficient — no temp-file-and-rename dance needed.  Note: POSIX guarantees
atomicity only for writes ≤ PIPE_BUF on pipes; we rely on the stronger Linux
ext4/XFS guarantee for regular files.  macOS is a development convenience only.

**asyncio.to_thread layering** — ``append()`` is ``async def`` and offloads the
blocking ``os.write`` + ``os.fdatasync`` syscalls to the default
``ThreadPoolExecutor`` via ``await asyncio.to_thread(...)``.  This keeps the
asyncio event loop unblocked.  The sync impl (``_sync_append_impl``) is the
only place that touches file descriptors, which keeps the threading model clean.

**UTC-midnight rollover** — per-day file selection is driven by ``clock.now()``
at each ``append()`` call, using the UTC date only.  No background task; the
overhead is one ``datetime.date`` comparison per append.

**asyncio.Lock for intra-process serialization** — even though FR26 guarantees a
single writer process, an asyncio.Lock guards against the edge case where
multiple coroutines in the same process race (e.g., during a hot-reload).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from events import EventEnvelope, from_canonical_json, to_canonical_json
from events.clock import Clock

# macOS compatibility: os.fdatasync is available on Linux but may be absent on
# some macOS environments.  Fall back to os.fsync (which also forces metadata,
# but is still durable).  Production target is Linux — this fallback is a
# dev-convenience measure only.
_fdatasync = getattr(os, "fdatasync", os.fsync)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def current_day_path(base_dir: Path, now: datetime) -> Path:
    """Return the JSONL file path for the UTC date of *now*.

    Args:
        base_dir: Root directory for the event log.
        now: UTC-aware datetime.  Raises ``ValueError`` for naïve datetimes
            or datetimes with a non-zero UTC offset.

    Returns:
        ``base_dir / "YYYY-MM-DD.jsonl"`` for the UTC date of *now*.

    Raises:
        ValueError: If *now* is naïve or not exactly UTC (i.e., offset ≠ 0).
    """
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(f"current_day_path requires UTC-aware datetime; got tzinfo={now.tzinfo!r}")
    return base_dir / f"{now.date().isoformat()}.jsonl"


def read_log_lines(path: Path) -> Iterator[EventEnvelope]:
    """Read a JSONL event-log file and yield ``EventEnvelope`` objects.

    Trailing partial lines (no terminating ``\\n``) are silently skipped —
    they indicate an interrupted write that ``recover()`` has not yet cleaned
    up, or that the writer was killed mid-line.  Callers should run
    ``recover()`` before reading in production.

    Args:
        path: Path to the ``.jsonl`` file.

    Yields:
        ``EventEnvelope`` objects, one per complete line.

    Raises:
        FileNotFoundError: If *path* does not exist (caller must check).
    """
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                return  # trailing partial line — skip silently
            yield from_canonical_json(raw.rstrip(b"\n"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _recover_file(path: Path) -> int:
    """Truncate any trailing partial line from *path*.

    Implements the backward-chunk scan described in AC-6:
    1. If the file is empty, return 0.
    2. Scan backward in 4 KiB chunks looking for the last ``\\n``.
    3. If found at position P, the complete region ends at P+1.
       If ``file_size == P+1``, no truncation needed — return 0.
       Otherwise ``ftruncate(fd, P+1)`` and return the truncated byte count.
    4. If no ``\\n`` is found anywhere, truncate to 0 and return file_size.

    Returns:
        Number of bytes truncated (0 if file was already clean).
    """
    size = path.stat().st_size
    if size == 0:
        return 0

    with open(path, "r+b") as f:
        chunk_size = 4096
        pos = size
        last_nl = -1

        while pos > 0:
            read_start = max(0, pos - chunk_size)
            f.seek(read_start)
            chunk = f.read(pos - read_start)
            idx = chunk.rfind(b"\n")
            if idx != -1:
                last_nl = read_start + idx
                break
            pos = read_start

        if last_nl == -1:
            # No newline anywhere — the entire file is a partial line.
            f.truncate(0)
            return size

        complete_end = last_nl + 1
        if complete_end == size:
            # File already ends cleanly on a newline.
            return 0

        f.truncate(complete_end)
        return size - complete_end


# ---------------------------------------------------------------------------
# EventLogWriter
# ---------------------------------------------------------------------------


class EventLogWriter:
    """Async append writer for per-day JSONL event-log files.

    Usage::

        writer = EventLogWriter(base_dir=Path("/var/lib/oh-my-bmad/registry/events"),
                                clock=SystemClock())
        await writer.recover()   # trim any partial tail from previous run
        await writer.append(envelope)
        await writer.close()

    The writer is NOT thread-safe across multiple ``EventLogWriter`` instances;
    use only one instance per process (FR26 enforces this at CI level).
    ``asyncio.Lock`` guards intra-process coroutine races within one instance.
    """

    def __init__(self, *, base_dir: Path, clock: Clock) -> None:
        """Initialise the writer.

        Creates *base_dir* (and any parent directories) if it does not exist.
        Pre-existing files are preserved — this is a pure-create-if-missing
        operation (``parents=True, exist_ok=True``).

        Args:
            base_dir: Root directory for the event log.
            clock: Injected clock (Story 2.2 discipline) that supplies UTC now.
        """
        self._base_dir = base_dir
        self._clock = clock
        self._fd: int | None = None
        self._current_date: date | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def append(self, envelope: EventEnvelope) -> None:
        """Append *envelope* as a single canonical-JSON line and fdatasync.

        Serializes via ``to_canonical_json(envelope)`` (deterministic, byte-
        stable), writes ``<json-bytes>\\n`` under an inode-level lock
        (``O_APPEND``), and calls ``os.fdatasync(fd)`` before returning.

        Every successful return is a durability guarantee: if power is cut
        immediately after, the line is on disk.

        Args:
            envelope: The event to persist.
        """
        data = to_canonical_json(envelope) + b"\n"
        async with self._lock:
            await asyncio.to_thread(self._sync_append_impl, data)

    async def close(self) -> None:
        """Close the current file handle.

        Idempotent — safe to call on a writer that is already closed or that
        has never written any data.
        """
        async with self._lock:
            if self._fd is not None:
                fd = self._fd
                self._fd = None
                self._current_date = None
                await asyncio.to_thread(os.close, fd)

    async def recover(self) -> int:
        """Trim any trailing partial line from the current-day file.

        Must be called ONCE at service startup, BEFORE any ``append()`` call.
        Scans the current-day file (as determined by ``clock.now()``) backward
        for the last ``\\n`` and truncates past it if needed.

        Returns:
            Number of bytes truncated (0 if file was already clean or absent).
        """
        async with self._lock:
            now = self._clock.now()
            path = current_day_path(self._base_dir, now)
            if not path.exists():
                return 0
            return await asyncio.to_thread(_recover_file, path)

    # ------------------------------------------------------------------
    # Internal sync helpers (called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _sync_append_impl(self, data: bytes) -> None:
        """Blocking write + fdatasync — called in a thread via asyncio.to_thread.

        Opens (or rolls to) the current-day file descriptor, writes *data*
        atomically under ``O_APPEND``, and calls fdatasync.
        """
        now = self._clock.now()
        self._ensure_current_day(now)
        assert self._fd is not None  # _ensure_current_day guarantees this
        os.write(self._fd, data)
        _fdatasync(self._fd)

    def _ensure_current_day(self, now: datetime) -> None:
        """Open (or roll) the fd to the correct per-day file.

        If the current fd is already open for today's date, this is a no-op.
        Otherwise the existing fd (if any) is closed and a new one is opened
        for today's JSONL file.

        Args:
            now: UTC-aware datetime from the clock.
        """
        today = now.date()
        if self._current_date == today and self._fd is not None:
            return
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        path = current_day_path(self._base_dir, now)
        self._fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        self._current_date = today


__all__ = [
    "EventLogWriter",
    "current_day_path",
    "read_log_lines",
]
