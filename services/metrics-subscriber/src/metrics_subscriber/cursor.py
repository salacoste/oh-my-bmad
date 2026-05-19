"""Atomic cursor persistence for metrics-subscriber tail-loop (Story 10.2).

ACs implemented:

* **AC3** — ``cursor.json`` schema:

  .. code-block:: json

     {
       "schema_version": "1",
       "path": "/var/lib/.../events/2026-05-19.jsonl",
       "offset": 12345,
       "persisted_at": "2026-05-19T04:00:00Z",
       "events_in_this_persist_window": 1000
     }

  Persisted via :func:`tempfile.NamedTemporaryFile` + :func:`os.replace`
  so a power loss mid-write leaves either the previous file (intact) or
  the new file (intact) — never a half-written cursor.  The parent
  directory is fsynced after the rename so the directory-entry change
  is durable across power loss (VH-12).

* **AC4** — :meth:`CursorPersistence.maybe_persist` increments an internal
  counter; once the counter ≥ ``persist_every`` an atomic write happens.

* **AC3 day-rollover restore (VH-1 — yesterday-tail backfill)** —
  :meth:`CursorPersistence.restore_into` reads the on-disk cursor (if
  present).  Three cases:

    1. No cursor → reader opens today's file at offset 0.
    2. Cursor path == today's path → reader seeks to ``offset``.
    3. Cursor path is a previous day (rollover during downtime) →
       reader is seated on **yesterday's path at persisted_offset** so
       the tail loop drains the remaining bytes BEFORE transitioning to
       today's file (preserves the AC7 exactly-once invariant; previous
       implementation silently abandoned those events).

* **AC4 drain on shutdown** — :meth:`persist_now` forces a write
  regardless of the counter (called by the SIGTERM handler in
  ``__main__``).

Schema-version migration policy (VH-9): an unknown ``schema_version``
raises :class:`CursorSchemaVersionError`.  The subscriber exits non-zero
rather than silently resetting to offset 0 — that would replay an
entire day of events on a forward-rollback.  Operators must inspect
the cursor file manually.

Concurrent-subscriber guard (VH-10): :meth:`CursorPersistence.lock`
acquires an exclusive ``fcntl.flock`` on ``<cursor_path>.lock``.  A
second subscriber on the same cursor path raises immediately on
startup.

Bounds validation (VH-11): on restore, ``persisted_offset`` is
validated against the on-disk file size.  Negative offsets raise
``ValueError``; offsets beyond EOF are treated as a skip-ahead with
``cursor_offset_beyond_eof`` CRITICAL log + metric placeholder.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from events.clock import Clock, SystemClock
from events.errors import CursorSchemaVersionError
from events.log_reader import EventLogReader, current_day_path

if TYPE_CHECKING:
    from types import TracebackType

_log = structlog.get_logger(__name__)

# P2-L2: bump from "1" → "1.1" for the ``events_in_this_persist_window``
# field rename (semver-minor: backwards-compatible).  Both schema_versions
# are accepted on restore; dual field-name write keeps log-aggregation
# queries grepping the old name working for one release cycle, and the
# old name will be dropped in Story 10.4.
_SCHEMA_VERSION = "1.1"
_ACCEPTED_SCHEMA_VERSIONS = frozenset({"1", "1.1"})


class CursorPersistence:
    """Persist the metrics-subscriber's read cursor atomically (AC3/AC4).

    Args:
        path: Where to write ``cursor.json``.  Parent directory is
            created lazily on first persist.
        persist_every: Number of events between persists
            (default 1000 per AC4).  Tests override to exercise the
            persist boundary without 1000-event setup.
        clock: Injected clock so ``persisted_at`` timestamps are
            deterministic under test.
    """

    def __init__(
        self,
        path: Path,
        *,
        persist_every: int = 1000,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        self._persist_every = persist_every
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._events_since_last_persist: int = 0
        # VH-10: fcntl lock state.  Populated by :meth:`lock` / cleared
        # by :meth:`unlock` (or ``__exit__``).
        self._lock_fd: int | None = None
        self._lock_path: Path = path.parent / (path.name + ".lock")

    # ------------------------------------------------------------------
    # VH-10 — concurrent-subscriber guard via fcntl.flock
    # ------------------------------------------------------------------

    def lock(self) -> None:
        """Acquire an exclusive fcntl.flock on ``<cursor_path>.lock`` (VH-10).

        Raises :class:`BlockingIOError` if another process already holds
        the lock — the caller (``__main__``) logs and exits non-zero so
        a second subscriber does not race with the first on cursor
        writes (which would silently corrupt the cursor file).

        P2-H6 (E4): the ``fcntl.flock`` call also raises
        :class:`OSError` (``EINVAL`` / ``ENOLCK`` / ``EOPNOTSUPP``) on
        non-local filesystems (NFS without lockd, some FUSE mounts,
        overlayfs in containers).  Pre-pass-2 these crashed the
        subscriber at startup with an uncaught exception.  Now we
        distinguish ``EWOULDBLOCK`` (concurrent-start, legitimate;
        re-raise as :class:`BlockingIOError`) from other ``OSError``
        codes (unsupported filesystem; log CRITICAL and re-raise as
        :class:`BlockingIOError` so :func:`run_subscriber` exits 1
        with a distinct ``reason="filesystem_unsupported"`` field).

        Deployment requirement: the cursor path must live on a local
        filesystem that supports ``fcntl.flock``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # O_CREAT to create the lockfile if missing; mode 0o640 to match
        # the event log mode.  The fd is kept open for the lifetime of
        # the subscriber so flock remains held.
        fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise
        except OSError as exc:
            os.close(fd)
            # P2-H6: distinguish EWOULDBLOCK (concurrent-start) from
            # other OSError codes (unsupported filesystem).  On Linux
            # ``BlockingIOError`` is a subclass of ``OSError`` so this
            # path is reached only for non-EWOULDBLOCK codes.
            if exc.errno == errno.EWOULDBLOCK:
                raise BlockingIOError(exc.errno, exc.strerror) from exc
            _log.critical(
                "metrics_subscriber_flock_unsupported_filesystem",
                cursor_path=str(self._path),
                lock_path=str(self._lock_path),
                errno=exc.errno,
                errno_name=errno.errorcode.get(exc.errno or 0, "?"),
                detail=str(exc),
                note=(
                    "fcntl.flock returned a non-EWOULDBLOCK OSError; the "
                    "cursor path likely lives on a filesystem that does not "
                    "support advisory locking (NFS without lockd, some FUSE "
                    "mounts, overlayfs).  Local filesystem required."
                ),
            )
            raise BlockingIOError(exc.errno, "fcntl.flock unsupported on filesystem") from exc
        self._lock_fd = fd

    def unlock(self) -> None:
        """Release the fcntl lock (idempotent)."""
        if self._lock_fd is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(self._lock_fd)
        self._lock_fd = None

    def __enter__(self) -> CursorPersistence:
        self.lock()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.unlock()

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_into(self, reader: EventLogReader, *, base_dir: Path) -> None:
        """Restore the reader's cursor from ``cursor.json`` (AC3).

        Three cases:

        1. No cursor file → reader opens today's file at offset 0.
        2. Cursor's ``path`` matches today's :func:`current_day_path` →
           reader seeks to ``offset`` (after bounds-validation per
           VH-11).
        3. Cursor's ``path`` is from a previous day (rollover during
           downtime) → **VH-1 yesterday-tail backfill**: reader is
           seated on yesterday's path at ``persisted_offset`` and a
           ``tail.draining_yesterday_before_rollover`` WARNING is
           emitted.  The tail loop drains the remaining bytes from
           yesterday's file BEFORE the day-rollover transition fires,
           preserving AC7 exactly-once across midnight downtime.

        Corrupt-cursor handling: a malformed JSON file, missing keys,
        or invalid shape all log a WARNING and fall through to case
        (1).  Unknown ``schema_version`` raises
        :class:`CursorSchemaVersionError` (VH-9) — the subscriber
        refuses to start.

        Args:
            reader: The reader whose cursor will be set.
            base_dir: The JSONL event-log directory (used to compute
                today's path).

        Raises:
            CursorSchemaVersionError: When ``cursor.json`` declares an
                unknown schema_version (VH-9).
            ValueError: When the persisted offset is negative (VH-11).
        """
        today_path = current_day_path(base_dir, self._clock.now())

        if not self._path.exists():
            reader.open(initial_offset=0)
            _log.info(
                "metrics_subscriber_cursor_restore_fresh",
                today_path=str(today_path),
            )
            return

        try:
            raw = self._path.read_text(encoding="utf-8")
            payload: object = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "metrics_subscriber_cursor_corrupt",
                cursor_path=str(self._path),
                error_type=type(exc).__name__,
                detail=str(exc),
            )
            reader.open(initial_offset=0)
            return

        if not isinstance(payload, dict):
            _log.warning(
                "metrics_subscriber_cursor_invalid_shape",
                cursor_path=str(self._path),
            )
            reader.open(initial_offset=0)
            return

        schema_version = payload.get("schema_version")
        if schema_version not in _ACCEPTED_SCHEMA_VERSIONS:
            # VH-9 + P2-L2: refuse to start on unknown schema_version.
            # Both "1" (pre-pass-2) and "1.1" (current) are accepted so
            # an in-place upgrade does NOT replay yesterday's events.
            raise CursorSchemaVersionError(
                cursor_path=str(self._path),
                schema_version=schema_version,
                expected=_SCHEMA_VERSION,
            )

        persisted_path_str = payload.get("path")
        persisted_offset = payload.get("offset")
        if not isinstance(persisted_path_str, str) or not isinstance(persisted_offset, int):
            _log.warning(
                "metrics_subscriber_cursor_missing_fields",
                cursor_path=str(self._path),
            )
            reader.open(initial_offset=0)
            return

        persisted_path = Path(persisted_path_str)

        # P2-H7: yesterday-tail backfill is only meaningful when
        # yesterday's file is still on disk.  After logrotate or
        # accidental deletion the file is gone — seating the reader on
        # a missing path would stall forever (the reader's own
        # rollover detection compares to ``current_path`` so it never
        # transitions away from the missing file).  Fall through to
        # today-fresh instead.
        if persisted_path != today_path and not persisted_path.exists():
            _log.critical(
                "metrics_subscriber_persisted_path_missing_falling_through_to_today",
                cursor_path=str(self._path),
                missing_path=str(persisted_path),
                today_path=str(today_path),
                persisted_offset=persisted_offset,
                note=(
                    "yesterday's JSONL file is missing on disk (logrotate, "
                    "accidental delete, etc.); abandoning yesterday-tail "
                    "backfill and starting fresh on today's path at offset 0. "
                    "Events between [persisted_offset, yesterday_EOF) are "
                    "permanently lost — operator runbook required."
                ),
            )
            reader.open(initial_offset=0)
            return

        # VH-11 — bounds validation against on-disk file size.
        validated_offset = self._validate_offset(persisted_path, persisted_offset)

        if persisted_path == today_path:
            reader.seek(path=persisted_path, offset=validated_offset)
            _log.info(
                "metrics_subscriber_cursor_restored",
                path=str(persisted_path),
                offset=validated_offset,
            )
        else:
            # VH-1 — yesterday-tail backfill.  Seat the reader on
            # yesterday's persisted offset so the tail loop drains
            # remaining bytes BEFORE the day-rollover transition fires.
            # The reader's own day-rollover detection in ``tail()``
            # will catch the transition once yesterday's file is
            # drained, at which point it switches to today's file at
            # offset 0 and the next iteration begins.  This is the
            # actual fix for the AC7 exactly-once violation:
            # previously, the warning was emitted but events between
            # [persisted_offset, yesterday_EOF) were silently dropped.
            _log.warning(
                "tail.draining_yesterday_before_rollover",
                cursor_path=str(persisted_path),
                today_path=str(today_path),
                persisted_offset=validated_offset,
                note=(
                    "previous-day downtime detected; draining yesterday's tail "
                    "before transitioning to today (was: events between "
                    "[persisted_offset, yesterday_EOF) silently abandoned)"
                ),
            )
            reader.seek(path=persisted_path, offset=validated_offset)

    def _validate_offset(self, path: Path, offset: int) -> int:
        """VH-11 — validate ``offset`` against ``path``'s on-disk size.

        - Negative offsets are programmer / corruption errors.  Raise
          :class:`ValueError`.
        - Offsets beyond EOF (file truncated externally) log CRITICAL
          ``cursor_offset_beyond_eof`` and reset to ``file_size`` so
          the reader does not stall forever waiting for bytes that
          will never arrive.  Story 10.3 will wire a Prometheus
          counter; for now we emit a structured log placeholder.

        Args:
            path: The path the offset points into.
            offset: The persisted byte offset.

        Returns:
            The (possibly clamped) offset to seat the reader on.

        Raises:
            ValueError: If ``offset`` < 0.
        """
        if offset < 0:
            _log.critical(
                "metrics_subscriber_cursor_offset_invalid",
                cursor_path=str(self._path),
                target_path=str(path),
                offset=offset,
                reason="negative_offset",
            )
            raise ValueError(f"cursor offset must be non-negative; got {offset!r} for {path!r}")
        try:
            file_size = path.stat().st_size if path.exists() else None
        except OSError:
            file_size = None
        if file_size is not None and offset > file_size:
            _log.critical(
                "cursor_offset_beyond_eof",
                cursor_path=str(self._path),
                target_path=str(path),
                offset=offset,
                file_size=file_size,
                action="reset_to_file_size",
                metric="metrics_subscriber_cursor_offset_beyond_eof_total{reason=clamp}",
            )
            return file_size
        return offset

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def note_event_processed(self, n: int = 1) -> None:
        """Increment the internal counter by *n* events (AC4).

        Story 10.4 idempotency requirement (P2-H5): the AC7 invariant
        is "at-most-one-duplicate at restart boundary" — the cursor
        advances AFTER ``yield envelope`` returns control in
        :meth:`EventLogReader.tail`, so a consumer-side raise leaves
        the cursor on the previous successful line.  This means the
        ONE envelope yielded immediately before a SIGTERM-set break
        may be re-yielded after restart.  Story 10.4 counter / gauge
        update side-effects MUST therefore be idempotent on envelope
        ``event_id`` — incrementing a counter by ``event_id`` is safe
        only if the consumer keeps a small dedupe window OR if the
        counter naturally tolerates a single duplicate (e.g.,
        ``set.add`` semantics).
        """
        self._events_since_last_persist += n

    def maybe_persist(self, offset: int, path: Path) -> bool:
        """Atomic-write the cursor if the counter has hit ``persist_every``.

        Returns ``True`` if a persist happened, ``False`` otherwise.
        Resets the counter to 0 on persist.
        """
        if self._events_since_last_persist < self._persist_every:
            return False
        self._write_atomic(offset=offset, path=path)
        return True

    def persist_now(self, offset: int, path: Path) -> None:
        """Force-persist regardless of counter (AC4 SIGTERM drain)."""
        self._write_atomic(offset=offset, path=path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write_atomic(self, *, offset: int, path: Path) -> None:
        """``tempfile`` + :func:`os.replace` — power-loss-safe (AC3, VH-12).

        Hardening per VH-12:

        * On any exception during the JSON write / fsync, unlink the
          tempfile before re-raising so the directory does not
          accumulate ``cursor.*.json.tmp`` orphans.
        * After ``os.replace``, fsync the parent directory so the
          directory-entry change is durable across power loss (rename
          is otherwise only durable for the file's *data*, not the
          containing directory's inode).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # AC3 schema: explicit "Z" suffix for UTC; we format directly
        # rather than relying on isoformat() so the format is stable
        # across Python versions (some emit ``+00:00`` instead of ``Z``).
        now = self._clock.now().astimezone(UTC)
        persisted_at = now.strftime("%Y-%m-%dT%H:%M:%S") + (
            f".{now.microsecond:06d}Z" if now.microsecond else "Z"
        )
        body = {
            "schema_version": _SCHEMA_VERSION,
            "path": str(path),
            "offset": offset,
            "persisted_at": persisted_at,
            # VL-2: rename to make the semantics explicit — this is
            # "events seen in this persist window", not "since the
            # prior persist call ran".
            "events_in_this_persist_window": self._events_since_last_persist,
            # P2-L2: dual-field write for one release cycle so operator
            # tooling / log-aggregation queries grepping the old name
            # keep working.  Drop in Story 10.4.
            "events_processed_since_last_persist": self._events_since_last_persist,
        }
        tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._path.parent),
                delete=False,
                prefix="cursor.",
                suffix=".json.tmp",
            ) as tmp:
                tmp_name = tmp.name
                json.dump(body, tmp, sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self._path)
            tmp_name = None  # ownership transferred to self._path
            # VH-12: fsync the parent directory so the rename's
            # directory-entry change is durable.
            # P2-M6: wrap the parent-dir open+fsync+close in
            # try/except OSError.  If ``os.replace`` succeeded but
            # ``os.open(parent)`` raises (EMFILE / parent dir vanished),
            # the rename is already committed and partial durability
            # is still better than aborting and re-raising — the
            # cursor body is on disk; only the parent-dir-entry sync
            # is missing.  Log a warning and continue.
            try:
                dir_fd = os.open(str(self._path.parent), os.O_RDONLY)
            except OSError as exc:
                _log.warning(
                    "metrics_subscriber_parent_fsync_failed",
                    cursor_path=str(self._path),
                    parent=str(self._path.parent),
                    errno=exc.errno,
                    errno_name=errno.errorcode.get(exc.errno or 0, "?"),
                    detail=str(exc),
                    note=(
                        "os.replace succeeded but parent-dir fsync could not "
                        "be performed (rename data is on disk; directory-entry "
                        "durability across power-loss is not guaranteed). "
                        "Continuing — partial durability > aborting."
                    ),
                )
            else:
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except BaseException:
            # VH-12: cleanup the tempfile on any failure so we don't
            # leak ``cursor.*.json.tmp`` orphans.
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
            raise
        self._events_since_last_persist = 0


__all__ = ["CursorPersistence"]
