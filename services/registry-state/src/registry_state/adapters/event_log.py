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

**File mode 0o660** (Story 11.3.11) — audit logs contain task contents +
approval trails, so files are created group-read/WRITE but NEVER
world-readable.  Group-write (the upgrade from the original 0o640) lets
any service in the shared ``omb`` group append to / recover the day's file
regardless of which uid created it first — the file-level sibling of Story
11.3.8's 0o2775 DIRECTORY fix.  Because ``os.open``'s mode is masked by the
process umask (022 strips group-write back to 0o640), the writer follows
its ``os.open`` with an explicit ``os.fchmod(fd, 0o660)``.  The others-triad
stays ``0`` — the non-world-readable audit invariant is preserved.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

# Story 11.2.3: fcntl is POSIX-only. Linux + macOS support it; Windows
# does not. The codebase targets Linux (CI) + macOS (dev); Windows is
# explicitly not a supported runtime. If fcntl is unavailable, the
# writer still works in single-process mode — but cross-process file
# locking is silently disabled. We log a WARNING at module load so
# operators on unsupported platforms see the degradation immediately.
try:
    import fcntl as _fcntl

    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover — Windows-only branch
    _fcntl = None  # type: ignore[assignment]
    _FCNTL_AVAILABLE = False

from events import EventEnvelope, ensure_shared_dir, to_canonical_json

# Story 10.2 AC1: read-side JSONL functions moved to packages/events/.
# Re-export here so existing call-sites (registry-state app/main.py,
# worker-wrapper.adapters.approval_waiter, registry-api tests, integration
# tests, idempotency tests, scripted_worker_stub fixture) keep working
# without code changes.  All callers migrated to public names during
# Story 10.2 extraction; no underscore-prefixed aliases re-exported
# (Story 10.2 review pass-1 VH-14: previous comment falsely claimed
# legacy ``_read_new_envelopes_since`` / ``_parse_with_pre110_backfill``
# aliases existed — they do not).
from events.clock import Clock
from events.log_reader import (
    EventLogReader,
    current_day_path,
    parse_with_pre110_backfill,
    read_log_lines,
    read_new_envelopes_since,
)

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
# Read-side functions (Story 10.2 AC1):
#
# ``current_day_path``, ``read_log_lines``, ``read_new_envelopes_since``,
# ``parse_with_pre110_backfill`` and ``EventLogReader`` are re-exported
# from ``events.log_reader`` at the top of this file — keeping this
# module's public surface backwards-compatible after the extraction.
# No underscore-prefixed aliases exist; all internal call-sites were
# migrated to the public names during the Story 10.2 extraction.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Internal helpers (write-side recovery)
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
        except PermissionError:
            # Story 11.3.11 (AC3) — a day-file owned by a DIFFERENT omb-uid
            # and still 0o640 (pre-11.3.11 / external tool) cannot be opened
            # r+b for recovery, and the AC2 self-heal chmod also failed
            # (we don't own it).  This used to crash-loop the whole subscriber
            # (PermissionError → lifespan dies → restart → repeat, 15→20+
            # restarts observed in Story 11.3.10 AC5).  Convert it into a
            # single clear, actionable log line and SKIP this file so the
            # rest of recovery (and the subscriber) proceeds.  The file's
            # producer will continue appending to it via its own writer; only
            # this recoverer's trailing-partial-line trim is skipped, which is
            # best-effort cleanup anyway (see this function's docstring).
            with contextlib.suppress(OSError):
                st = path.stat()
                _log.error(
                    "event_log_recovery_permission_denied",
                    extra={
                        "path": str(path),
                        "file_uid": st.st_uid,
                        "file_gid": st.st_gid,
                        "file_mode": oct(st.st_mode & 0o7777),
                        "remediation": (
                            "chmod 0o660 the file (or ensure its creator runs "
                            "post-Story-11.3.11 so it is created group-writable)"
                        ),
                    },
                )
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

    # Story 11.3.11 (AC2) — self-heal a stale-mode file before the r+b open.
    # A file created by a pre-11.3.11 writer (or any other tool) may still be
    # 0o640 (no group-write), which makes the read-WRITE ``r+b`` open below
    # fail with PermissionError for a cross-uid recoverer.  Best-effort chmod
    # to 0o660 fixes it IF we own the file; if it is owned by a different uid
    # the chmod fails (suppressed) and the open below raises PermissionError,
    # which ``recover_all_logs`` turns into a clear non-fatal diagnostic (AC3)
    # rather than a crash-loop.  others-triad stays 0 (non-world-readable).
    with contextlib.suppress(OSError):
        os.chmod(path, 0o660)

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

    def __init__(
        self,
        *,
        base_dir: Path,
        clock: Clock,
        lock_wait_observer: Callable[[float], None] | None = None,
    ) -> None:
        """Initialise the writer.

        Creates *base_dir* (and any parent directories) if it does not exist.
        Pre-existing files are preserved — this is a pure-create-if-missing
        operation (``parents=True, exist_ok=True``).

        Args:
            base_dir: Root directory for the event log.
            clock: Injected clock (Story 2.2 discipline) that supplies UTC now.
            lock_wait_observer: Story 11.2.3 — optional callback invoked
                with the time spent waiting for the inter-process
                ``fcntl.flock`` lock (milliseconds). When ``None``, no
                lock-wait observation occurs; production wires this to
                the ``omb_event_log_lock_wait_ms`` Histogram in
                ``metrics-subscriber``. The callback receives a single
                float (ms) and MUST NOT raise — exceptions propagate
                and poison the writer.
        """
        self._base_dir = base_dir
        self._clock = clock
        self._lock_wait_observer = lock_wait_observer
        self._fd: int | None = None
        self._current_date: date | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._poisoned: bool = False
        self._closed: bool = False
        # Story 11.3.8 / FR62a: use ``ensure_shared_dir`` instead of bare
        # ``mkdir`` so cross-uid services in the ``omb`` group can write to
        # the shared event-log directory regardless of which service booted
        # first. Pre-fix: bare mkdir used the process umask (typically 022 →
        # mode 0o755) and locked out every other service. Helper applies
        # explicit ``chmod 0o2775`` (setgid + group-write) after creation.
        # See ``packages/events/_filesystem.py`` for the full rationale +
        # POSIX setgid semantics docs.
        ensure_shared_dir(base_dir)
        if not _FCNTL_AVAILABLE:
            logging.getLogger(__name__).warning(
                "event_log_fcntl_unavailable",
                extra={
                    "reason": (
                        "Story 11.2.3 file-lock disabled — fcntl module not importable. "
                        "Multi-process writers are NOT serialized; FR26 single-writer "
                        "invariant becomes the sole correctness guarantee. Supported "
                        "runtimes: Linux + macOS."
                    ),
                },
            )

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

        # Story 11.2.3: acquire exclusive fcntl.flock for inter-process
        # serialization. Without this, concurrent clawhip-bridge subprocesses
        # writing to the same daily JSONL file rely only on Linux's O_APPEND
        # atomicity (sub-PIPE_BUF, ~4KB on Linux) — adequate for typical
        # envelopes but not architecturally clean per FR26.
        #
        # flock semantics:
        # - LOCK_EX: exclusive; blocks until acquired.
        # - Per-fd lock; kernel serializes across processes opening the
        #   same inode.
        # - Released by LOCK_UN OR fd close. We release explicitly in
        #   finally so the lock-hold window is exactly write+fdatasync.
        #
        # On non-fcntl platforms (Windows), this is a no-op — degrades
        # gracefully back to single-writer (a warning was logged at
        # writer construction time).
        #
        # Pass-1 review PP3: flock acquisition INSIDE the try block so the
        # outer finally always runs LOCK_UN — even if the observer
        # callback raises BaseException (KeyboardInterrupt, SystemExit,
        # asyncio.CancelledError on 3.11+). Capture the locked fd in a
        # local at acquisition time (PP3-Edge defensive nit) so day-roll
        # in a future refactor cannot point LOCK_UN at a different fd.
        # PP3-Edge: observe lock-wait AFTER LOCK_UN, not inside the
        # hold window — observer runtime would otherwise inflate
        # contention for other processes.
        # PP3-Blind: skip the timing+observer entirely on non-fcntl
        # platforms so Windows / WSL contributors don't pollute the
        # histogram with zero-wait samples.
        lock_start_ns = 0
        lock_acquired_ns: int = 0
        locked_fd: int | None = None
        try:
            if _FCNTL_AVAILABLE:
                lock_start_ns = time.monotonic_ns()
                _fcntl.flock(self._fd, _fcntl.LOCK_EX)
                lock_acquired_ns = time.monotonic_ns()
                locked_fd = self._fd

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
        finally:
            # Story 11.2.3: release the inter-process flock. Idempotent on
            # the never-acquired path (``locked_fd is None``). Even if the
            # caller observed BaseException during write, the LOCK_UN
            # still runs because this finally has no exception-class
            # filtering.
            if locked_fd is not None:
                with contextlib.suppress(OSError):
                    _fcntl.flock(locked_fd, _fcntl.LOCK_UN)
            # PP3-Edge: observe lock-acquisition-wait AFTER LOCK_UN so
            # observer runtime does not inflate the hold window for other
            # processes. The value is acquisition wait only (time from
            # LOCK_EX call to acquisition), NOT total hold duration.
            # Skipped entirely if flock wasn't available (avoids polluting
            # the histogram).
            if locked_fd is not None and self._lock_wait_observer is not None:
                lock_wait_ms = (lock_acquired_ns - lock_start_ns) / 1_000_000
                with contextlib.suppress(Exception):
                    # Observer must not break the write path; swallow defensively.
                    self._lock_wait_observer(lock_wait_ms)

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
        # the writer keeps working on the previous day's file.
        #
        # Story 11.3.11 (FR62a) — file mode 0o660 (rw-rw----): owner+group
        # read/write, NEVER world-readable (audit logs contain task contents +
        # approval trails).  Group-WRITE is required so a DIFFERENT omb-group
        # service uid (e.g. worker-wrapper 10005) that created the day's file
        # first does not lock out a later cross-uid append/recover by
        # registry-state (10002) — the file-level sibling of Story 11.3.8's
        # 0o2775 DIRECTORY fix.  os.open's mode is masked by the process umask
        # (typically 022 → strips group-write back to 0o640), so an explicit
        # fchmod on the just-opened fd is required to defeat umask — the
        # fd-native analog of ensure_shared_dir's chmod.  Best-effort: a
        # pre-existing file we don't own must not crash the writer (the
        # O_APPEND open already gave us a usable fd).
        new_fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o660)
        # AttributeError guard (code-review L1): os.fchmod is absent on Windows
        # (raises AttributeError, which suppress(OSError) would NOT catch).
        # Windows is an unsupported runtime (see module docstring) but the
        # writer must still degrade cleanly rather than poison itself on the
        # first rollover.  POSIX hosts (the deploy target) are unaffected.
        with contextlib.suppress(OSError, AttributeError):
            os.fchmod(new_fd, 0o660)
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


# ---------------------------------------------------------------------------
# Story 38.2 — In-memory event log writer for unit tests
# ---------------------------------------------------------------------------


class InMemoryEventLogWriter:
    """In-memory ``EventLogWriter`` stand-in for unit tests.

    Appends envelopes to an internal list instead of writing to disk.
    Implements the same ``append()`` coroutine interface as
    :class:`EventLogWriter` so test subjects can use either interchangeably.
    """

    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def append(self, envelope: EventEnvelope) -> None:
        """Record the envelope in memory."""
        self.envelopes.append(envelope)


__all__ = [
    "EventLogReader",  # Story 10.2 AC1 re-export
    "EventLogWriter",
    "InMemoryEventLogWriter",
    "current_day_path",
    "parse_with_pre110_backfill",
    "read_log_lines",
    "read_new_envelopes_since",
    "recover_all_logs",
]
