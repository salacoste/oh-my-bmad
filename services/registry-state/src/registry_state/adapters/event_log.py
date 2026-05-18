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

**Short-write loop + poison-pill** — ``os.write`` may return a byte count
smaller than the requested length (short write) under resource pressure or
signals.  We loop until all bytes are written.  If any failure occurs during
the write sequence (short-write-zero, ENOSPC, EIO, KeyboardInterrupt), the
file may be left with a partial line on disk.  We mark the writer *poisoned*
so the next ``append()`` raises immediately instead of silently corrupting
the log.  Recovery requires calling ``recover()`` (which trims partial tails
and clears the poison) and reopening the writer.

**asyncio.to_thread layering** — ``append()`` is ``async def`` and offloads the
blocking ``os.write`` + ``os.fdatasync`` syscalls to the default
``ThreadPoolExecutor`` via ``await asyncio.to_thread(...)``.  This keeps the
asyncio event loop unblocked.  The sync impl (``_sync_append_impl``) is the
only place that touches file descriptors, which keeps the threading model clean.

**UTC-midnight rollover** — per-day file selection is driven by ``clock.now()``
at each ``append()`` call, using the UTC date only.  No background task; the
overhead is one ``datetime.date`` comparison per append.  Rollover opens the
new fd *before* closing the old one — if ``os.open`` fails, the writer keeps
working on the previous day's file.

**asyncio.Lock for intra-process serialization** — even though FR26 guarantees a
single writer process, an asyncio.Lock guards against the edge case where
multiple coroutines in the same process race (e.g., during a hot-reload).

**File mode 0o640** — audit logs contain task contents + approval trails.
Files are created group-readable (not world-readable) to limit accidental
exposure on shared systems.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from events import EventEnvelope, from_canonical_json, to_canonical_json
from events.clock import Clock

_log = logging.getLogger(__name__)

# macOS compatibility: os.fdatasync is available on Linux but may be absent on
# some macOS environments.  Fall back to os.fsync (which also forces metadata,
# but is still durable).  Production target is Linux — this fallback is a
# dev-convenience measure only.
_fdatasync = getattr(os, "fdatasync", os.fsync)

# macOS compatibility: os.O_DIRECTORY may not exist on all platforms.  When
# absent, fall back to plain O_RDONLY (directory-fsync is best-effort on such
# platforms; Linux production is always the target).
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


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

    CRLF tolerance: the writer emits LF only, so any CR byte in a file is the
    result of external tooling (editors, VCS translation).  We strip trailing
    ``\\r`` bytes pragmatically before JSON parsing.

    Args:
        path: Path to the ``.jsonl`` file.

    Yields:
        ``EventEnvelope`` objects, one per complete line.

    Raises:
        FileNotFoundError: If *path* does not exist.  Raised eagerly (before
            iteration begins) so callers don't receive a half-constructed
            generator that only fails on ``next()``.
    """
    if not path.exists():
        raise FileNotFoundError(f"event log file not found: {path}")
    return _read_log_lines_gen(path)


def _read_log_lines_gen(path: Path) -> Iterator[EventEnvelope]:
    """Inner generator for ``read_log_lines`` — deferred file I/O.

    Story 9.7 pass-2 TH-E1: ``approval_waiter`` (and other production
    consumers) iterate this helper directly, so the same pre-1.1.0
    back-fill applied to :func:`_read_new_envelopes_since` is applied
    here. Without it the first pre-1.1.0 record raises
    :class:`pydantic.ValidationError` and the consumer hangs/crashes.
    """
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                return  # trailing partial line — skip silently
            line_bytes = raw.rstrip(b"\r\n")
            envelope = _parse_with_pre110_backfill(line_bytes, path)
            if envelope is not None:
                yield envelope


def _read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]:
    """Read complete ``\\n``-terminated envelopes from *path* starting at *offset*.

    Used by the subscriber tail loop to consume only the bytes appended since
    the last poll — bounding the per-iteration cost regardless of how large
    the per-day log file grows.  Designed to be invoked via
    ``asyncio.to_thread`` so the blocking ``open``/``seek``/``read`` syscalls
    do not stall the event loop.

    Behaviour:
      - If *path* does not exist (e.g., the day's first event has not been
        appended yet) returns ``(offset, [])`` unchanged.
      - If *offset* is beyond EOF (file was rotated/truncated externally),
        the read returns no bytes and the offset is left unchanged.  We do
        NOT auto-reset to zero — re-reading from the start would replay
        every event we have already applied.
      - Trailing partial lines (no terminating ``\\n``) are NOT consumed:
        the returned offset stops at the last newline so the next call
        picks up the partial line once it is completed.
      - CRLF tolerance: any ``\\r`` bytes are stripped before JSON parsing,
        matching ``read_log_lines``'s permissive policy.

    Args:
        path: Path to the ``.jsonl`` file.
        offset: Byte offset to start reading from.

    Returns:
        ``(new_offset, envelopes)`` where ``new_offset`` is the byte
        position just past the last complete line, and ``envelopes`` is
        the parsed envelope list (possibly empty).
    """
    if not path.exists():
        return offset, []
    envelopes: list[EventEnvelope] = []
    new_offset = offset
    with open(path, "rb") as f:
        f.seek(offset)
        last_complete_end = offset
        while True:
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # Trailing partial line — leave it for the next poll to
                # finish.  The cursor stays at last_complete_end.
                break
            line_bytes = raw.rstrip(b"\r\n")
            # PH-E1 (Story 9.7 pass-1): subscriber startup replay must not
            # crash on pre-1.1.0 JSONL.  After the schema bump, trace_id is
            # REQUIRED by EventEnvelope; any pre-bump record without trace_id
            # would raise pydantic.ValidationError inside from_canonical_json.
            # Fix: inject migrator-style back-fill (trace_id = request_id)
            # before parsing.  If request_id is also absent/invalid, skip the
            # line with a structured warning rather than crashing the subscriber.
            envelope = _parse_with_pre110_backfill(line_bytes, path)
            if envelope is not None:
                envelopes.append(envelope)
            last_complete_end += len(raw)
        new_offset = last_complete_end
    return new_offset, envelopes


def _parse_with_pre110_backfill(
    line_bytes: bytes,
    source_path: Path,
) -> EventEnvelope | None:
    """Parse a JSONL line, back-filling trace_id for pre-1.1.0 envelopes.

    Story 9.7 pass-1 PH-E1 / pass-2 TH-B5: avoids deployment-breaking
    ValidationError when subscriber replays pre-1.1.0 JSONL during startup.
    Delegates to :func:`events.backfill.backfill_trace_id_from_request_id`
    so the migrator + subscriber paths share one back-fill rule.

    Returns None (with structured warning) when the line is unrecoverable so
    the subscriber skips it rather than crashing. Returns the parsed
    EventEnvelope on success.
    """
    from events.backfill import (  # noqa: PLC0415 — avoid circular at module level
        backfill_trace_id_from_request_id,
    )

    try:
        raw_dict: object = json.loads(line_bytes)
    except json.JSONDecodeError as exc:
        _log.warning(
            "event_log_parse_skip path=%s error_type=JSONDecodeError detail=%s",
            source_path,
            exc,
        )
        return None

    if not isinstance(raw_dict, dict):
        _log.warning(
            "event_log_parse_skip path=%s error_type=NotADict",
            source_path,
        )
        return None

    # TH-B5 shared helper: back-fill trace_id from request_id (with e-prefix
    # strip per Q6). Returns None when neither trace_id nor request_id
    # produces a valid bare UUIDv7. Story 9.8 D6: pass the subscriber-specific
    # provenance label so the materializer can route it to
    # events.trace_id_synthetic_source — distinguishing online replay
    # back-fills from the offline migrator's records.
    backfilled = backfill_trace_id_from_request_id(
        raw_dict,
        caller_label="subscriber-pre110-replay",
    )
    if backfilled is None:
        _log.warning(
            "event_log_parse_skip path=%s event_id=%s "
            "error_type=pre110_missing_trace_id request_id=%r "
            "— cannot back-fill; skipping envelope",
            source_path,
            raw_dict.get("event_id", "?"),
            raw_dict.get("request_id"),
        )
        return None
    if backfilled is not raw_dict:
        _log.debug(
            "event_log_pre110_backfill path=%s event_id=%s synthetic_trace_id=%s",
            source_path,
            backfilled.get("event_id", "?"),
            backfilled.get("trace_id"),
        )
    raw_dict = backfilled

    try:
        return from_canonical_json(json.dumps(raw_dict).encode())
    except Exception as exc:  # noqa: BLE001 — log and skip; never crash subscriber
        _log.warning(
            "event_log_parse_skip path=%s event_id=%s error_type=%s detail=%s",
            source_path,
            raw_dict.get("event_id", "?") if isinstance(raw_dict, dict) else "?",
            type(exc).__name__,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def recover_all_logs(base_dir: Path) -> int:
    """Trim trailing partial lines from every ``*.jsonl`` file in *base_dir*.

    Free-function counterpart to :meth:`EventLogWriter.recover` for services
    that consume the log without writing to it (e.g. the subscriber loop).
    Sorted iteration produces deterministic ordering for test assertions.
    TOCTOU: if a file disappears between ``glob()`` and ``open()`` we skip it
    silently — recovery is best-effort cleanup.

    File I/O is offloaded to the default thread executor via
    ``asyncio.to_thread`` so the asyncio event loop is not blocked.

    Args:
        base_dir: Root directory containing ``YYYY-MM-DD.jsonl`` event logs.

    Returns:
        Total bytes trimmed across all files.  Zero if *base_dir* does not
        exist or every file was already clean.
    """
    if not base_dir.exists():
        return 0
    total = 0
    for path in sorted(base_dir.glob("*.jsonl")):
        try:
            total += await asyncio.to_thread(_recover_file, path)
        except FileNotFoundError:
            # TOCTOU: file disappeared between glob and open.  Skip.
            continue
    return total


def _recover_file(path: Path) -> int:
    """Truncate any trailing partial line from *path*.

    Implements the backward-chunk scan described in AC-6:
    1. If the file is empty, return 0.
    2. Scan backward in 4 KiB chunks looking for the last ``\\n``.
    3. If found at position P, the complete region ends at P+1.
       If ``file_size == P+1``, no truncation needed — return 0.
       Otherwise ``ftruncate(fd, P+1)`` and return the truncated byte count.
    4. If no ``\\n`` is found anywhere, truncate to 0 and return file_size.

    After any truncation, the file *and* its parent directory are fsynced so
    the size-change metadata is durable across power loss.

    Returns:
        Number of bytes truncated (0 if file was already clean).
    """
    size = path.stat().st_size
    if size == 0:
        return 0

    trimmed = 0
    truncated = False
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
            trimmed = size
            truncated = True
        else:
            complete_end = last_nl + 1
            if complete_end == size:
                # File already ends cleanly on a newline — no truncation.
                trimmed = 0
                truncated = False
            else:
                f.truncate(complete_end)
                trimmed = size - complete_end
                truncated = True

        if truncated:
            f.flush()
            os.fsync(f.fileno())

    # Fsync the parent directory so the truncation's metadata change (new file
    # size in the inode / directory entry) is durable across power loss.
    if truncated:
        dir_fd = os.open(str(path.parent), os.O_RDONLY | _O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    return trimmed


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

    State model:

    - ``_poisoned`` — set to True if any write attempt raised.  The next
      ``append()`` will raise RuntimeError immediately.  ``recover()`` clears
      the poison after trimming partial tails.
    - ``_closed`` — set to True by ``close()``.  Further ``append()`` raises
      RuntimeError until a fresh writer is constructed.
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
        self._poisoned: bool = False
        self._closed: bool = False
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

        Raises:
            RuntimeError: If the writer is closed, or if a previous write
                failed (poisoned state).  In the poisoned case, call
                ``recover()`` and reconstruct the writer to recover.
        """
        data = to_canonical_json(envelope) + b"\n"
        async with self._lock:
            await asyncio.to_thread(self._sync_append_impl, data)

    async def close(self) -> None:
        """Close the current file handle and mark the writer terminal.

        Idempotent — safe to call on a writer that is already closed or that
        has never written any data.  After ``close()``, further ``append()``
        calls raise ``RuntimeError`` — construct a fresh writer to resume.
        """
        async with self._lock:
            if self._fd is not None:
                # Best-effort flush on close — data was already fdatasync'd
                # after each successful append.
                with contextlib.suppress(OSError):
                    _fdatasync(self._fd)
                fd = self._fd
                await asyncio.to_thread(os.close, fd)
                self._fd = None
                self._current_date = None
            self._closed = True

    async def recover(self) -> int:
        """Trim trailing partial lines from every ``*.jsonl`` file under *base_dir*.

        Must be called ONCE at service startup, BEFORE any ``append()`` call.
        Iterates every ``*.jsonl`` file in ``base_dir`` (not just today's) so
        partial-tail bytes left by a pre-midnight crash are cleaned up even if
        restart happens after UTC midnight.  Uses a backward-chunk scan +
        ``ftruncate`` to trim each file.

        Closes any currently-held fd and clears the rollover cache — the next
        ``append()`` opens fresh.  Also clears the poison flag: ``recover()``
        is the cure for a previously poisoned writer.

        Returns:
            Total number of bytes trimmed across all files (0 if every file
            was already clean).
        """
        async with self._lock:
            # Invalidate any held fd — we may truncate its underlying file.
            if self._fd is not None:
                with contextlib.suppress(OSError):
                    _fdatasync(self._fd)
                os.close(self._fd)
                self._fd = None
                self._current_date = None
            # ``recover()`` is the cure for a previously poisoned writer.
            self._poisoned = False
            return await recover_all_logs(self._base_dir)

    # ------------------------------------------------------------------
    # Internal sync helpers (called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _sync_append_impl(self, data: bytes) -> None:
        """Blocking write + fdatasync — called in a thread via asyncio.to_thread.

        Opens (or rolls to) the current-day file descriptor, writes *data*
        under ``O_APPEND`` with a short-write retry loop, and calls fdatasync.
        Any exception during the write sequence poisons the writer so a
        partial-line-on-disk state cannot be silently ignored.

        Raises:
            RuntimeError: If the writer is closed or poisoned.
            OSError: If ``os.write`` returns 0, or any syscall fails.
        """
        if self._poisoned:
            raise RuntimeError(
                "EventLogWriter poisoned — previous write failed; call recover() and reopen"
            )
        if self._closed:
            raise RuntimeError("EventLogWriter is closed")
        now = self._clock.now()
        self._ensure_current_day(now)
        assert self._fd is not None  # _ensure_current_day guarantees this
        try:
            remaining = data
            while remaining:
                n = os.write(self._fd, remaining)
                if n == 0:
                    raise OSError("os.write returned 0 — cannot proceed")
                remaining = remaining[n:]
            _fdatasync(self._fd)
        except BaseException:
            # Any failure (ENOSPC, EIO, KeyboardInterrupt, etc.) may have left
            # a partial line on disk.  Poison the writer so the next append()
            # raises immediately until recover() + fresh write cycle runs.
            self._poisoned = True
            raise

    def _ensure_current_day(self, now: datetime) -> None:
        """Open (or roll) the fd to the correct per-day file — atomically.

        If the current fd is already open for today's date, this is a no-op.
        Otherwise the new fd is opened *first*; on success the old fd is
        fsynced and closed.  If ``os.open`` for the new path fails, the old
        fd remains valid and the writer continues on the previous day's file
        — no partial-failure state where ``self._fd`` is None mid-rollover.

        Args:
            now: UTC-aware datetime from the clock.
        """
        today = now.date()
        if self._current_date == today and self._fd is not None:
            return
        path = current_day_path(self._base_dir, now)
        # Open the new fd FIRST.  If this raises, the old fd stays valid and
        # the writer keeps working on the previous day's file.  File mode is
        # 0o640 (group-readable) — audit logs should not be world-readable.
        new_fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        old_fd = self._fd
        self._fd = new_fd
        self._current_date = today
        if old_fd is not None:
            # Flush old fd before closing — belt-and-braces durability for
            # the final bytes written to the previous day's file.  Data was
            # already fdatasync'd after each successful append, so errors
            # here are non-fatal.
            with contextlib.suppress(OSError):
                _fdatasync(old_fd)
            os.close(old_fd)


__all__ = [
    "EventLogWriter",
    "current_day_path",
    "read_log_lines",
    "recover_all_logs",
]
