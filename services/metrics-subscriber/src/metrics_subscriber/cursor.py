"""Atomic cursor persistence for metrics-subscriber tail-loop (Story 10.2).

ACs implemented:

* **AC3** — ``cursor.json`` schema:

  .. code-block:: json

     {
       "schema_version": "1",
       "path": "/var/lib/.../events/2026-05-19.jsonl",
       "offset": 12345,
       "persisted_at": "2026-05-19T04:00:00Z",
       "events_processed_since_last_persist": 1000
     }

  Persisted via :func:`tempfile.NamedTemporaryFile` + :func:`os.replace`
  so a power loss mid-write leaves either the previous file (intact) or
  the new file (intact) — never a half-written cursor.

* **AC4** — :meth:`CursorPersistence.maybe_persist` increments an internal
  counter; once the counter ≥ ``persist_every`` an atomic write happens.

* **AC3 day-rollover restore** — :meth:`CursorPersistence.restore_into`
  reads the on-disk cursor (if present); if ``path`` matches
  :func:`current_day_path`, the reader seeks to ``offset``; if ``path``
  is a previous day's file, the reader opens **today's** file at offset 0
  and a ``tail.restart_after_day_rollover`` WARNING is emitted so
  operators can grep for missed-day scenarios.

* **AC4 drain on shutdown** — :meth:`persist_now` forces a write
  regardless of the counter (called by the SIGTERM handler in
  ``__main__``).

Schema-version migration policy: bumping ``schema_version`` past ``"1"`` is
a breaking change for any subscriber that reads cursors written by older
versions.  The current implementation **rejects** non-``"1"`` schemas with
a structured warning and behaves as if the cursor were absent (resumes
from offset 0 of today's file) so an accidental newer write does not
corrupt downstream invariants.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC
from pathlib import Path

from events.clock import Clock, SystemClock
from events.log_reader import EventLogReader, current_day_path

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = "1"


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

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_into(self, reader: EventLogReader, *, base_dir: Path) -> None:
        """Restore the reader's cursor from ``cursor.json`` (AC3).

        Three cases:

        1. No cursor file → reader opens today's file at offset 0.
        2. Cursor's ``path`` matches today's :func:`current_day_path` →
           reader seeks to ``offset``.
        3. Cursor's ``path`` is from a previous day (rollover during
           downtime) → reader opens today's file at offset 0 AND we emit
           ``tail.restart_after_day_rollover`` WARNING.  Operators can
           then decide whether to replay the missed day manually.

        Corrupt-cursor handling: a malformed JSON file, an unknown
        ``schema_version``, or missing keys all log a WARNING and fall
        through to case (1).

        Args:
            reader: The reader whose cursor will be set.
            base_dir: The JSONL event-log directory (used to compute
                today's path).
        """
        today_path = current_day_path(base_dir, self._clock.now())

        if not self._path.exists():
            reader.open(initial_offset=0)
            _log.info(
                "metrics_subscriber_cursor_restore_fresh path=%s",
                today_path,
            )
            return

        try:
            raw = self._path.read_text(encoding="utf-8")
            payload: object = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "metrics_subscriber_cursor_corrupt path=%s error_type=%s detail=%s "
                "— starting fresh at today's offset 0",
                self._path,
                type(exc).__name__,
                exc,
            )
            reader.open(initial_offset=0)
            return

        if not isinstance(payload, dict):
            _log.warning(
                "metrics_subscriber_cursor_invalid_shape path=%s "
                "— starting fresh at today's offset 0",
                self._path,
            )
            reader.open(initial_offset=0)
            return

        schema_version = payload.get("schema_version")
        if schema_version != _SCHEMA_VERSION:
            _log.warning(
                "metrics_subscriber_cursor_unknown_schema path=%s got=%r expected=%r "
                "— starting fresh at today's offset 0",
                self._path,
                schema_version,
                _SCHEMA_VERSION,
            )
            reader.open(initial_offset=0)
            return

        persisted_path_str = payload.get("path")
        persisted_offset = payload.get("offset")
        if not isinstance(persisted_path_str, str) or not isinstance(persisted_offset, int):
            _log.warning(
                "metrics_subscriber_cursor_missing_fields path=%s "
                "— starting fresh at today's offset 0",
                self._path,
            )
            reader.open(initial_offset=0)
            return

        persisted_path = Path(persisted_path_str)
        if persisted_path == today_path:
            reader.seek(path=persisted_path, offset=persisted_offset)
            _log.info(
                "metrics_subscriber_cursor_restored path=%s offset=%d",
                persisted_path,
                persisted_offset,
            )
        else:
            _log.warning(
                "tail.restart_after_day_rollover "
                "cursor_path=%s today_path=%s persisted_offset=%d "
                "— previous day's events between [%d, EOF) NOT replayed; "
                "operator runbook required for backfill",
                persisted_path,
                today_path,
                persisted_offset,
                persisted_offset,
            )
            reader.open(initial_offset=0)

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def note_event_processed(self, n: int = 1) -> None:
        """Increment the internal counter by *n* events (AC4)."""
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
        """``tempfile`` + :func:`os.replace` — power-loss-safe (AC3)."""
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
            "events_processed_since_last_persist": self._events_since_last_persist,
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self._path.parent),
            delete=False,
            prefix="cursor.",
            suffix=".json.tmp",
        ) as tmp:
            json.dump(body, tmp, sort_keys=True)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.replace(tmp_name, self._path)
        self._events_since_last_persist = 0


__all__ = ["CursorPersistence"]
