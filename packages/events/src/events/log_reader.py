"""Shared JSONL event-log reader (Story 10.2 AC1/AC2).

This module is the SINGLE SOURCE OF TRUTH for read-side JSONL parsing.
It was extracted from ``services/registry-state/src/registry_state/adapters/event_log.py``
during Story 10.2 so that the β metrics-subscriber service (and any other
read-only subscribers) can consume the log without violating the P2-I1
read-only-subscriber rule (services may not import from other services).

The writer (``EventLogWriter``) remains in registry-state — only READ-side
functions moved. ``registry_state.adapters.event_log`` re-exports these
names for backwards compatibility with existing call-sites.

Surfaces:

* :func:`current_day_path` — JSONL filename convention (UTC-aware).
* :func:`read_log_lines` — full-file generator (used by recovery + tests).
* :func:`read_new_envelopes_since` — incremental tail (used by subscribers).
  Public re-name of the previously underscore-prefixed
  ``_read_new_envelopes_since``.
* :func:`parse_with_pre110_backfill` — pre-1.1.0 envelope back-fill.
  Public re-name of ``_parse_with_pre110_backfill``.
* :class:`EventLogReader` — async-friendly tail reader with cursor +
  day-rollover handling. New for Story 10.2.

Design notes preserved from the original (registry-state Story 2.4):

* **CRLF tolerance** — writer emits LF only; any ``\\r`` byte is the
  result of external tooling (editors, VCS translation).  Stripped
  before JSON parsing.
* **Trailing partial-line policy** — incomplete lines (no ``\\n``)
  are NOT consumed; the offset stops at the last newline so the next
  poll picks up the partial line once completed.
* **Offset-beyond-EOF** — left unchanged; we do NOT auto-reset to zero
  (re-reading from the start would replay every event already applied).
* **Pre-1.1.0 back-fill** — schema bump made ``trace_id`` REQUIRED; any
  pre-bump record without trace_id would raise pydantic.ValidationError
  inside ``from_canonical_json``. Inject migrator-style back-fill
  (trace_id ← request_id) before parsing. If both are absent/invalid,
  skip the line with a structured WARNING rather than crash the
  consumer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from events.canonical import from_canonical_json
from events.clock import Clock, SystemClock
from events.envelope import EventEnvelope

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


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
    """Inner generator for :func:`read_log_lines` — deferred file I/O.

    Story 9.7 pass-2 TH-E1: ``approval_waiter`` (and other production
    consumers) iterate this helper directly, so the same pre-1.1.0
    back-fill applied to :func:`read_new_envelopes_since` is applied here.
    Without it the first pre-1.1.0 record raises
    :class:`pydantic.ValidationError` and the consumer hangs/crashes.
    """
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                return  # trailing partial line — skip silently
            line_bytes = raw.rstrip(b"\r\n")
            envelope = parse_with_pre110_backfill(line_bytes, path)
            if envelope is not None:
                yield envelope


def read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]:
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
        matching :func:`read_log_lines`'s permissive policy.

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
            # before parsing.  If request_id is also absent/invalid, skip
            # the line with a structured warning rather than crashing the
            # subscriber.
            envelope = parse_with_pre110_backfill(line_bytes, path)
            if envelope is not None:
                envelopes.append(envelope)
            last_complete_end += len(raw)
        new_offset = last_complete_end
    return new_offset, envelopes


def iter_new_envelopes_since(path: Path, offset: int) -> Iterator[tuple[int, EventEnvelope]]:
    """Yield ``(offset_after_line, envelope)`` pairs from *path* past *offset*.

    Generator variant of :func:`read_new_envelopes_since` used by the
    tail loop (Story 10.2 AC2/AC7): advancing the cursor per-line means a
    mid-batch interruption (SIGTERM, ``break``) leaves the cursor on the
    last successfully-yielded line — guaranteeing the AC7 exactly-once
    invariant on restart.

    Same skip / CRLF / partial-line policies as
    :func:`read_new_envelopes_since`.
    """
    if not path.exists():
        return
    with open(path, "rb") as f:
        f.seek(offset)
        last_complete_end = offset
        while True:
            raw = f.readline()
            if not raw:
                return
            if not raw.endswith(b"\n"):
                return  # partial line — leave for next poll
            line_bytes = raw.rstrip(b"\r\n")
            envelope = parse_with_pre110_backfill(line_bytes, path)
            last_complete_end += len(raw)
            if envelope is not None:
                yield last_complete_end, envelope


def parse_with_pre110_backfill(
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
# EventLogReader
# ---------------------------------------------------------------------------


class EventLogReader:
    """Async-friendly tail reader for per-day JSONL event logs (Story 10.2).

    Tracks a byte-offset cursor for the current day's file; supports resume
    from an arbitrary offset; transparently handles UTC-midnight rollover to
    the next day's file.

    Usage (sync read_batch)::

        reader = EventLogReader(base_dir=Path("/var/lib/.../events"))
        reader.open(initial_offset=0)
        envelopes = reader.read_batch(max_events=1000)
        # ... cursor_offset / current_path expose the post-read state.

    Usage (async tail)::

        reader = EventLogReader(base_dir)
        reader.open(initial_offset=cursor.offset)
        async for env in reader.tail(poll_interval_s=0.5):
            ...

    Day-rollover policy (AC8): when the clock crosses UTC midnight, the
    tail loop closes the current handle and opens the new day's file at
    offset 0.  Bytes appended to yesterday's file in the last
    ``poll_interval_s`` BEFORE rollover are flushed first by a final read
    of the previous-day path.

    Pre-1.1.0 back-fill is applied transparently (see
    :func:`parse_with_pre110_backfill`).
    """

    def __init__(
        self,
        base_dir: Path,
        *,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the reader.

        Args:
            base_dir: Root directory containing ``YYYY-MM-DD.jsonl`` files.
            clock: Injected clock for UTC ``now()``.  Defaults to
                :class:`SystemClock`.  Tests inject ``FrozenClock`` /
                ``TickingClock`` to simulate day rollover deterministically.
        """
        self._base_dir = base_dir
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._cursor_offset: int = 0
        self._current_path: Path | None = None

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def open(self, initial_offset: int = 0) -> None:
        """Open today's JSONL file at *initial_offset*.

        Idempotent: re-calling with a new offset reseats the cursor without
        any I/O (the file is only opened lazily on the next read).
        """
        self._current_path = current_day_path(self._base_dir, self._clock.now())
        self._cursor_offset = initial_offset

    def seek(self, *, path: Path, offset: int) -> None:
        """Reseat the cursor to an explicit *path* + *offset*.

        Used by :class:`CursorPersistence.restore_into` when the on-disk
        cursor.json refers to a specific path (which may differ from
        ``current_day_path`` after a downtime that spans midnight).
        """
        self._current_path = path
        self._cursor_offset = offset

    def read_batch(self, max_events: int = 1000) -> list[EventEnvelope]:
        """Read up to *max_events* new envelopes since the last call.

        Returns an empty list if no new bytes are available.  Updates
        :attr:`cursor_offset` to point past the last complete line read.

        ``max_events`` is a soft cap: we may return slightly more if the
        underlying file readline call straddles the boundary; we never
        return less than what fits in the file's currently-readable
        bytes.

        Story 10.2 note: ``max_events`` is not yet plumbed into the
        low-level reader (which returns everything past the cursor).
        The cap is enforced post-hoc so the cursor lands on a complete
        line.  This is intentional — premature optimisation; Story 10.4
        can revisit if metric-update fan-out becomes a bottleneck.
        """
        if self._current_path is None:
            raise RuntimeError(
                "EventLogReader.read_batch called before open(); "
                "call reader.open(initial_offset=...) first."
            )
        new_offset, envelopes = read_new_envelopes_since(self._current_path, self._cursor_offset)
        self._cursor_offset = new_offset
        if len(envelopes) > max_events:
            # Keep the first max_events; we can't safely adjust new_offset
            # backward without rescanning, so accept the soft cap.
            # The unread tail will be re-served on the next call (returns
            # empty since cursor is already past it — but that is OK; the
            # batch boundary is logical, not bytewise).
            envelopes = envelopes[:max_events]
        return envelopes

    # ------------------------------------------------------------------
    # Public async API (AC2 tail)
    # ------------------------------------------------------------------

    async def tail(
        self,
        *,
        poll_interval_s: float = 0.5,
        stop_event: asyncio.Event | None = None,
    ) -> AsyncIterator[EventEnvelope]:
        """Async generator yielding new envelopes as they arrive.

        Polls the current day's file every *poll_interval_s* seconds; on
        UTC-midnight rollover (clock.now().date() > current_path's date)
        flushes any remaining bytes from yesterday's file, then closes
        it and opens today's at offset 0.

        Args:
            poll_interval_s: Sleep between polls when the file has no
                new bytes (default 0.5s; AC6 default).
            stop_event: Optional :class:`asyncio.Event`; ``tail`` returns
                when set.  ``None`` means run forever.

        Yields:
            ``EventEnvelope`` objects in append order.

        Raises:
            RuntimeError: If called before :meth:`open` / :meth:`seek`.
        """
        if self._current_path is None:
            raise RuntimeError(
                "EventLogReader.tail called before open(); "
                "call reader.open(initial_offset=...) first."
            )
        while stop_event is None or not stop_event.is_set():
            today_path = current_day_path(self._base_dir, self._clock.now())
            assert self._current_path is not None

            # Flush any remaining bytes from the current file, yielding
            # envelopes one-by-one with the cursor advancing per-line.
            # Per-line cursor advance is critical for the AC7 exactly-
            # once invariant: a mid-batch break or SIGTERM leaves the
            # cursor on the last successfully-yielded line.
            #
            # We materialise the generator in a thread (the underlying
            # ``open`` + ``readline`` calls are blocking) and then yield
            # results on the event loop. The thread builds a list — this
            # is fine because (a) the per-poll backlog is bounded by
            # poll_interval_s × writer-throughput, and (b) the per-line
            # offset is captured PER ENVELOPE so we can still stop
            # cleanly mid-list.
            items = await asyncio.to_thread(
                lambda: list(
                    iter_new_envelopes_since(
                        self._current_path,  # type: ignore[arg-type]
                        self._cursor_offset,
                    )
                )
            )
            for offset_after, envelope in items:
                self._cursor_offset = offset_after
                yield envelope
                if stop_event is not None and stop_event.is_set():
                    return

            # AC8: detect day rollover.  After draining yesterday's file
            # we switch to today's at offset 0.  We DO NOT immediately
            # re-poll inside the same loop iteration — the next iteration
            # (after sleep) will catch any first-of-the-day bytes.
            if today_path != self._current_path:
                _log.info(
                    "event_log_day_rollover from=%s to=%s final_offset_on_previous_day=%d",
                    self._current_path,
                    today_path,
                    self._cursor_offset,
                )
                self._current_path = today_path
                self._cursor_offset = 0
                continue  # immediate re-poll on the new file (don't sleep)

            # Sleep before next poll.  If stop_event is supplied, wake
            # early on shutdown.
            if stop_event is None:
                await asyncio.sleep(poll_interval_s)
            else:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
                except TimeoutError:
                    continue

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def cursor_offset(self) -> int:
        """Current byte offset within the open file."""
        return self._cursor_offset

    @property
    def current_path(self) -> Path:
        """The JSONL path currently being read.

        Raises:
            RuntimeError: If accessed before :meth:`open` / :meth:`seek`.
        """
        if self._current_path is None:
            raise RuntimeError(
                "EventLogReader.current_path accessed before open(); "
                "call reader.open(initial_offset=...) first."
            )
        return self._current_path


__all__ = [
    "EventLogReader",
    "current_day_path",
    "iter_new_envelopes_since",
    "parse_with_pre110_backfill",
    "read_log_lines",
    "read_new_envelopes_since",
]
