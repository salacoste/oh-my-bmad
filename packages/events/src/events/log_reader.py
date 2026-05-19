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
* :func:`iter_new_envelopes_since` — per-line generator returning
  ``(offset_after_line, envelope)`` pairs.  The **single source of
  truth** for incremental parsing — :func:`read_new_envelopes_since`
  is now a thin batch-form wrapper that drains this generator
  internally (Q3 unification).
* :func:`read_new_envelopes_since` — incremental tail (used by
  subscribers when the streaming form is inconvenient).  Public
  re-name of the previously underscore-prefixed
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
* **Offset-beyond-EOF** — left unchanged at the reader level; the
  cursor's :meth:`CursorPersistence._validate_offset` does clamp
  (VH-11) before seating the reader.
* **Pre-1.1.0 back-fill** — schema bump made ``trace_id`` REQUIRED; any
  pre-bump record without trace_id would raise pydantic.ValidationError
  inside ``from_canonical_json``. Inject migrator-style back-fill
  (trace_id ← request_id) before parsing. If both are absent/invalid,
  skip the line with a structured WARNING rather than crash the
  consumer.

Read-only contract for ``services/`` consumers (P2-L4): single-writer
gate (Story 1.6) enforces that production writes to the JSONL log
happen ONLY in the orchestrator/registry-state write path.  Callers
in ``services/`` (worker-wrapper approval_waiter, metrics-subscriber)
MUST treat this module as read-only.  The single-writer-gate
allowlist is path-based (not module-based); new read-then-write
helpers added in ``packages/events/`` will require an explicit
allowlist entry — see ``tests/separability/conftest.py``.

Story 10.2 review pass-1 hardening:

* **VH-3** — :func:`read_new_envelopes_since` and
  :meth:`EventLogReader.read_batch` accept ``max_events`` and STOP
  reading lines once the cap is reached, advancing the offset to the
  last yielded line's end (not EOF).  The previous post-hoc slice
  silently dropped envelopes past the cap because the cursor was
  already past them.
* **VH-4** — :func:`iter_new_envelopes_since` accepts
  ``max_lines_per_poll`` (default 10_000) to bound per-poll work and
  prevent multi-GB RAM blowup after multi-hour-outage restart.
* **VH-6** — :class:`EventLogReader.tail` detects day-rollover by
  ``today_path.exists()`` + quiescence-window over yesterday's file
  size, NOT pure clock comparison.  Eliminates the writer-still-
  appending-past-reader-clock-rollover race.
* **VH-8** — :func:`iter_new_envelopes_since` advances the offset
  AFTER yielding, so a consumer-side exception leaves the cursor on
  the previous successful line.  Required for AC7 exactly-once with
  Story 10.4's counter-update side-effects.
* **VH-13** — :func:`parse_with_pre110_backfill` skips emit a
  structured ``metrics_subscriber_parse_skip_total`` log event
  (Prometheus counter wired in Story 10.3); the per-poll loop refuses
  to advance past ``max_contiguous_parse_skips`` consecutive bad
  lines (raises :class:`RuntimeError`) so a corrupted file does not
  silently drop a million events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from events.canonical import from_canonical_json
from events.clock import Clock, SystemClock
from events.envelope import EventEnvelope
from events.errors import ParseSkipThresholdExceeded

if TYPE_CHECKING:
    from types import TracebackType

_log = structlog.get_logger(__name__)

# VH-4: bound per-poll work to prevent multi-GB RAM blowup on
# multi-hour-outage restart.  Configurable via :class:`EventLogReader`'s
# ``max_lines_per_poll`` constructor argument.
_DEFAULT_MAX_LINES_PER_POLL = 10_000

# VH-6: quiescence window for day-rollover detection.  We require
# yesterday's file size to be stable for at least this many seconds
# BEFORE switching to today's file.  Eliminates the
# "writer-still-appending-past-reader-clock-midnight" race.
#
# P2-H1 (Q4): lowered from 60.0s → 5.0s.  The 60s value introduced a
# restart-after-midnight cold-start regression (every restart added
# 60s of latency before the reader switched to today's file).  5s
# bounds startup latency while keeping enough headroom for short
# writer-clock drift.  The fast-path in :meth:`_is_rollover_ready`
# eliminates the cost entirely when there is nothing left to drain on
# yesterday's file.
_DEFAULT_ROLLOVER_QUIESCENCE_S = 5.0

# VH-13: refuse to advance past this many consecutive parse-skips.
_DEFAULT_MAX_CONTIGUOUS_PARSE_SKIPS = 100

# P2-H11: once-per-process guard so the "max_events exceeds line-cap"
# WARNING does not flood the log if a caller hammers ``read_batch``
# in a tight loop.  Per-process is sufficient — the WARNING signals a
# caller configuration mistake, not a per-call event.
_MAX_EVENTS_EXCEEDS_LINE_CAP_WARNED: bool = False


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


def iter_new_envelopes_since(
    path: Path,
    offset: int,
    *,
    max_lines_per_poll: int = _DEFAULT_MAX_LINES_PER_POLL,
    max_contiguous_parse_skips: int = _DEFAULT_MAX_CONTIGUOUS_PARSE_SKIPS,
    parse_skip_state: list[int] | None = None,
) -> Iterator[tuple[int, EventEnvelope]]:
    """Yield ``(offset_after_line, envelope)`` pairs from *path* past *offset*.

    This is the **single source of truth** for incremental JSONL parsing
    (Q3 unification): :func:`read_new_envelopes_since` is now a thin
    batch-form wrapper that drains this generator into a list.

    Cursor-advance semantics (VH-8): the offset is yielded BEFORE the
    consumer takes control of the envelope.  The consumer can then run
    side-effects (Story 10.4 counter updates) before the cursor is
    persisted — if the side-effects raise, the cursor stays on the
    PREVIOUS successful line.  Required for AC7 exactly-once with
    fallible consumers.

    Per-poll cap (VH-4): the generator stops after
    ``max_lines_per_poll`` LINES READ (parsed or skipped).  This bounds
    per-poll work to ~O(10K syscalls + parses) regardless of file size,
    preventing multi-GB RAM blowup on multi-hour-outage restart.  The
    outer tail loop simply iterates again to drain the next chunk.

    Parse-skip threshold (VH-13 + pass-2 P2-H3/P2-H4): if more than
    ``max_contiguous_parse_skips`` consecutive lines fail to parse,
    raises :class:`ParseSkipThresholdExceeded` (was generic
    ``RuntimeError`` pre-pass-2) so the subscriber catches a typed
    exception, logs the corrupted-region offset, and exits with code
    3.  Successful parses reset the counter.

    Parse-skip persistence across polls (P2-H4): pre-pass-2 the
    counter was a per-call local — each poll opened the file fresh
    and reset to 0.  A real corruption pattern of "50 bad lines per
    poll across 10 polls = 500 silently dropped" never tripped the
    100-line threshold.  Fix: callers may pass a mutable
    ``parse_skip_state`` (a single-element list of int) that the
    generator updates across calls.  :class:`EventLogReader` owns
    this state at the instance level and passes it through each
    poll.  When omitted, the counter is per-call (preserving the
    behaviour expected by free-function callers / tests).

    Args:
        path: Path to the ``.jsonl`` file.
        offset: Byte offset to start reading from.
        max_lines_per_poll: Max lines to read in one generator-drain.
        max_contiguous_parse_skips: Refuse to advance past this many
            consecutive bad lines.
        parse_skip_state: Optional ``[int]`` mutable counter used to
            track contiguous parse-skips across calls.  When ``None``
            (default), an internal per-call counter is used.

    Yields:
        ``(offset_after_line, envelope)`` tuples in append order.

    Raises:
        ParseSkipThresholdExceeded: If the contiguous-skip count
            reaches or exceeds ``max_contiguous_parse_skips``.
    """
    if not path.exists():
        return
    lines_read = 0
    # P2-H4: counter lives in *parse_skip_state* (mutable list-as-cell)
    # when provided so it persists across polls; otherwise we keep a
    # per-call local for free-function callers.
    if parse_skip_state is None:
        parse_skip_state = [0]
    with open(path, "rb") as f:
        f.seek(offset)
        last_complete_end = offset
        # P2-H3: capture the byte offset at the START of the contiguous
        # corruption run so the exception reports the actionable
        # "where does the bad region begin" anchor.
        corruption_run_start = last_complete_end
        while True:
            if lines_read >= max_lines_per_poll:
                return
            raw = f.readline()
            if not raw:
                return
            if not raw.endswith(b"\n"):
                return  # partial line — leave for next poll
            lines_read += 1
            line_bytes = raw.rstrip(b"\r\n")
            envelope = parse_with_pre110_backfill(line_bytes, path)
            line_start = last_complete_end
            last_complete_end += len(raw)
            if envelope is not None:
                parse_skip_state[0] = 0
                corruption_run_start = last_complete_end
                yield last_complete_end, envelope
            else:
                if parse_skip_state[0] == 0:
                    corruption_run_start = line_start
                parse_skip_state[0] += 1
                # P2-H4: comparison flipped to >= so the Nth contiguous
                # skip trips immediately (was >, off-by-one).
                if parse_skip_state[0] >= max_contiguous_parse_skips:
                    raise ParseSkipThresholdExceeded(
                        path=str(path),
                        offset=corruption_run_start,
                        threshold=max_contiguous_parse_skips,
                    )


def read_new_envelopes_since(
    path: Path,
    offset: int,
    *,
    max_events: int | None = None,
    max_lines_per_poll: int = _DEFAULT_MAX_LINES_PER_POLL,
) -> tuple[int, list[EventEnvelope]]:
    """Batch-form: read complete ``\\n``-terminated envelopes past *offset*.

    Used by the subscriber tail loop and recovery code paths.  Q3
    unification: this is now a thin wrapper that drains
    :func:`iter_new_envelopes_since` into a list — eliminating the
    ~25 lines of duplicated parsing logic that previously existed.

    Behaviour:
      - If *path* does not exist returns ``(offset, [])`` unchanged.
      - If *offset* is beyond EOF returns ``(offset, [])`` unchanged.
      - Trailing partial lines (no terminating ``\\n``) are NOT consumed.
      - CRLF tolerance: any ``\\r`` bytes are stripped before parsing.
      - VH-3 hardening: if ``max_events`` is provided, reading stops
        after ``max_events`` parsed envelopes AND the returned offset
        advances ONLY to that line's end — NOT to EOF.  The previous
        implementation advanced the cursor past all parsed envelopes
        then sliced the list to ``max_events``, silently dropping the
        unsliced tail because the next call returned an empty batch
        with cursor already past those bytes.

    Args:
        path: Path to the ``.jsonl`` file.
        offset: Byte offset to start reading from.
        max_events: Optional hard cap on returned envelopes.  ``None``
            (default) means "drain whatever fits in
            ``max_lines_per_poll``".
        max_lines_per_poll: Per-poll line cap (inherited from
            :func:`iter_new_envelopes_since`).

    Returns:
        ``(new_offset, envelopes)`` where ``new_offset`` is the byte
        position just past the last yielded line and ``envelopes`` is
        the parsed envelope list (possibly empty).
    """
    # P2-H11: WARN once-per-process when ``max_events`` exceeds the
    # line-cap.  The interaction is subtle: the line-cap silently
    # truncates the batch to ``max_lines_per_poll`` regardless of the
    # caller-requested ``max_events`` (since the inner generator
    # stops at the line-cap).  A caller that asked for 10_000 events
    # would receive at most 10_000 (the default) and could infer
    # incorrectly that the log was drained.  Story 10.4 readers
    # SHOULD pass ``max_events <= max_lines_per_poll``.
    global _MAX_EVENTS_EXCEEDS_LINE_CAP_WARNED
    if (
        max_events is not None
        and max_events > max_lines_per_poll
        and not _MAX_EVENTS_EXCEEDS_LINE_CAP_WARNED
    ):
        _log.warning(
            "max_events_exceeds_line_cap",
            max_events=max_events,
            max_lines_per_poll=max_lines_per_poll,
            note=(
                "max_events exceeds max_lines_per_poll; the inner cap will "
                "silently truncate the batch.  Pass max_events <= "
                "max_lines_per_poll to avoid the surprise."
            ),
            metric="metrics_subscriber_max_events_exceeds_line_cap_total{reason=config}",
        )
        _MAX_EVENTS_EXCEEDS_LINE_CAP_WARNED = True
    if not path.exists():
        return offset, []
    envelopes: list[EventEnvelope] = []
    new_offset = offset
    for offset_after, envelope in iter_new_envelopes_since(
        path, offset, max_lines_per_poll=max_lines_per_poll
    ):
        envelopes.append(envelope)
        new_offset = offset_after
        if max_events is not None and len(envelopes) >= max_events:
            break
    return new_offset, envelopes


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

    VH-13: warning lines include the
    ``metrics_subscriber_parse_skip_total{reason=...}`` metric-name
    string so Story 10.3 can wire a Prometheus counter without touching
    this module.
    """
    from events.backfill import (  # noqa: PLC0415 — avoid circular at module level
        backfill_trace_id_from_request_id,
    )

    try:
        raw_dict: object = json.loads(line_bytes)
    except json.JSONDecodeError as exc:
        _log.warning(
            "event_log_parse_skip",
            path=str(source_path),
            error_type="JSONDecodeError",
            detail=str(exc),
            metric="metrics_subscriber_parse_skip_total{reason=json_decode}",
        )
        return None

    if not isinstance(raw_dict, dict):
        _log.warning(
            "event_log_parse_skip",
            path=str(source_path),
            error_type="NotADict",
            metric="metrics_subscriber_parse_skip_total{reason=not_a_dict}",
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
            "event_log_parse_skip",
            path=str(source_path),
            event_id=raw_dict.get("event_id", "?"),
            error_type="pre110_missing_trace_id",
            request_id=raw_dict.get("request_id"),
            metric="metrics_subscriber_parse_skip_total{reason=pre110_missing_trace_id}",
        )
        return None
    if backfilled is not raw_dict:
        _log.debug(
            "event_log_pre110_backfill",
            path=str(source_path),
            event_id=backfilled.get("event_id", "?"),
            synthetic_trace_id=backfilled.get("trace_id"),
        )
    raw_dict = backfilled

    try:
        return from_canonical_json(json.dumps(raw_dict).encode())
    except Exception as exc:  # noqa: BLE001 — log and skip; never crash subscriber
        _log.warning(
            "event_log_parse_skip",
            path=str(source_path),
            event_id=raw_dict.get("event_id", "?") if isinstance(raw_dict, dict) else "?",
            error_type=type(exc).__name__,
            detail=str(exc),
            metric="metrics_subscriber_parse_skip_total{reason=validation}",
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

        async with EventLogReader(base_dir) as reader:
            reader.open(initial_offset=cursor.offset)
            async for env in reader.tail(poll_interval_s=0.5):
                ...

    Day-rollover policy (AC8 + VH-6): the reader switches to today's
    file when (a) ``today_path`` EXISTS and (b) yesterday's file size
    has been stable for at least ``rollover_quiescence_s`` seconds.
    This file-existence + quiescence-based detection eliminates the
    race where the writer is still appending past the reader's clock
    midnight due to NTP skew or scheduling drift.

    Pre-1.1.0 back-fill is applied transparently (see
    :func:`parse_with_pre110_backfill`).

    VH-9 / VM-9: the reader's :meth:`open` does not eagerly compute
    today's path — :meth:`tail` is the single source of truth for
    "what day is it now", avoiding startup races where ``open()`` and
    the first :meth:`tail` iteration straddle UTC midnight.
    """

    def __init__(
        self,
        base_dir: Path,
        *,
        clock: Clock | None = None,
        max_lines_per_poll: int = _DEFAULT_MAX_LINES_PER_POLL,
        rollover_quiescence_s: float = _DEFAULT_ROLLOVER_QUIESCENCE_S,
    ) -> None:
        """Initialise the reader.

        Args:
            base_dir: Root directory containing ``YYYY-MM-DD.jsonl`` files.
            clock: Injected clock for UTC ``now()``.  Defaults to
                :class:`SystemClock`.  Tests inject ``FrozenClock`` /
                ``TickingClock`` to simulate day rollover deterministically.
            max_lines_per_poll: VH-4 chunk cap.  Default 10_000 lines
                per poll.
            rollover_quiescence_s: VH-6 minimum seconds yesterday's
                file size must be stable before rollover transition.
                Default 5.0s (P2-H1 Q4 — was 60s pre-pass-2).  The
                fast-path skip in :meth:`_is_rollover_ready` reduces
                this to ~0s when the reader has already drained
                yesterday's file to EOF.
        """
        self._base_dir = base_dir
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._cursor_offset: int = 0
        self._current_path: Path | None = None
        self._max_lines_per_poll = max_lines_per_poll
        self._rollover_quiescence_s = rollover_quiescence_s
        # VH-6 — rollover state.  Track yesterday's last observed size
        # and the wall-clock seconds since that observation so we can
        # require quiescence before switching to today.
        self._yesterday_last_size: int | None = None
        self._yesterday_last_size_at_monotonic_s: float | None = None
        # P2-H4: contiguous parse-skip counter hoisted from per-call
        # local to instance state so a long corruption run that spans
        # multiple polls eventually trips the threshold.  Wrapped in a
        # single-element list (mutable cell) so the free function
        # :func:`iter_new_envelopes_since` can update it without
        # round-tripping back through ``self``.
        self._parse_skip_state: list[int] = [0]
        # P2-M2 (VM-6): lifecycle flag enforced by ``__aenter__`` /
        # ``__aexit__``.  After ``__aexit__`` returns, calls to
        # :meth:`tail` / :meth:`open` / :meth:`seek` / :meth:`read_batch`
        # raise ``RuntimeError("EventLogReader used after close")``.
        # This converts the previously no-op async-CM into an enforced
        # contract for Story 10.4 contributors who may add fd/task
        # resources to the reader.
        self._closed: bool = False

    # ------------------------------------------------------------------
    # VM-6 — async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> EventLogReader:
        if self._closed:
            raise RuntimeError("EventLogReader used after close")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # P2-M2: enforce the lifecycle contract.  Future Story 10.4
        # contributors who add open fd or async tasks to the reader
        # MUST also wire teardown here; the ``_closed`` flag guards
        # against ``async with`` site reuse.
        self._closed = True
        return None

    def _check_not_closed(self) -> None:
        """P2-M2 — raise if the reader has been closed via ``__aexit__``."""
        if self._closed:
            raise RuntimeError("EventLogReader used after close")

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def open(self, initial_offset: int = 0) -> None:
        """Best-effort seat at today's path for fresh-start callers.

        For restart-with-cursor, callers should use
        :meth:`CursorPersistence.restore_into` which calls :meth:`seek`
        directly.  Midnight-straddle for fresh-start is handled by
        :meth:`tail`'s per-iteration today_path re-evaluation + the
        VH-6 rollover-quiescence window — :meth:`open` itself only
        snapshots today's path at-call (P2-M1 docstring clarification:
        the original VM-9 concern about restored offsets being
        silently discarded is mitigated via VH-6 quiescence, not via
        deferred path computation).

        Note: :meth:`CursorPersistence.restore_into` uses :meth:`seek`,
        not :meth:`open`.
        """
        self._check_not_closed()
        self._current_path = current_day_path(self._base_dir, self._clock.now())
        self._cursor_offset = initial_offset

    def seek(self, *, path: Path, offset: int) -> None:
        """Reseat the cursor to an explicit *path* + *offset*.

        Used by :class:`CursorPersistence.restore_into` when the on-disk
        cursor.json refers to a specific path (which may differ from
        ``current_day_path`` after a downtime that spans midnight).
        """
        self._check_not_closed()
        self._current_path = path
        self._cursor_offset = offset

    def read_batch(
        self,
        max_events: int = 1000,
    ) -> list[EventEnvelope]:
        """Read up to *max_events* new envelopes since the last call.

        VH-3 / P2-H11 (Q8): ``max_events`` is a HARD cap honoured at
        the line-read level.  The cursor advances to the last yielded
        line's end (NOT EOF), so a subsequent ``read_batch`` call
        returns the unread tail.  Previously the cursor was advanced
        past all parsed envelopes and the returned list was sliced —
        silently dropping the unsliced tail.

        Soft → hard cap CHANGELOG note (P2-H11, pass-2): the pre-pass-1
        docstring described ``max_events`` as a soft cap (cursor at
        EOF, slice post-hoc).  Pass-1 changed it to a hard cap as the
        VH-3 fix.  Callers that depended on soft-cap semantics would
        observe silent envelope loss; the audit at pass-2 found no
        such callers in ``services/registry-state`` or
        ``services/registry-api`` (the only known external callers).
        Story 10.4 readers SHOULD pass ``max_events <= max_lines_per_poll``
        to avoid the WARNING emitted by :func:`read_new_envelopes_since`.

        Returns an empty list if no new bytes are available.
        Idempotent for an EOF reader (returns ``[]``).

        Raises:
            RuntimeError: If called before :meth:`open` or :meth:`seek`.
        """
        self._check_not_closed()
        if self._current_path is None:
            raise RuntimeError(
                "EventLogReader.read_batch called before open(); "
                "call reader.open(initial_offset=...) first."
            )
        new_offset, envelopes = read_new_envelopes_since(
            self._current_path,
            self._cursor_offset,
            max_events=max_events,
            max_lines_per_poll=self._max_lines_per_poll,
        )
        self._cursor_offset = new_offset
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

        Polls the current file every *poll_interval_s* seconds.

        Day-rollover detection (VH-6): the loop switches to today's
        file when (a) ``today_path`` EXISTS and (b) yesterday's file
        size has been stable for at least ``rollover_quiescence_s``
        seconds.  This combination tolerates writer-clock skew up to
        the quiescence window without dropping events.

        Per-poll chunk cap (VH-4): each iteration drains at most
        ``max_lines_per_poll`` lines from yesterday's file via
        :func:`iter_new_envelopes_since`.  The outer loop spins until
        rollover is detected; a SIGTERM can interrupt cleanly between
        chunks.

        Per-line cursor advance (VH-8): the offset is updated AFTER
        the consumer takes the envelope, so a consumer-side raise
        leaves the cursor on the previous successful line.

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
        self._check_not_closed()
        if self._current_path is None:
            raise RuntimeError(
                "EventLogReader.tail called before open(); "
                "call reader.open(initial_offset=...) first."
            )
        # P2-H8 (Q9): use `get_running_loop()` — `get_event_loop()` is
        # deprecated inside coroutines since Python 3.10 and raises
        # `RuntimeError` in 3.14+.
        loop = asyncio.get_running_loop()
        # Per-iteration count for VL-1 rollover log.
        chunk_envelopes = 0
        while stop_event is None or not stop_event.is_set():
            today_path = current_day_path(self._base_dir, self._clock.now())
            # P2-H12 (Q7): re-validate the cursor against current file
            # size on every poll.  After an external rotation that
            # truncated the file (logrotate, replay-from-archive,
            # accidental ``> file``), the cursor may now point past
            # EOF.  Pre-pass-2 the reader would seek past EOF, read
            # zero bytes, and stall silently forever.  Now we log
            # CRITICAL and clamp to the current size.
            try:
                current_file_size = self._current_path.stat().st_size
            except OSError:
                current_file_size = None
            if current_file_size is not None and self._cursor_offset > current_file_size:
                _log.critical(
                    "metrics_subscriber_offset_clamp_after_rotation",
                    path=str(self._current_path),
                    old_offset=self._cursor_offset,
                    new_file_size=current_file_size,
                    metric=(
                        "metrics_subscriber_offset_clamp_after_rotation_total"
                        "{reason=external_rotation}"
                    ),
                )
                self._cursor_offset = current_file_size
            # Snapshot the path + offset BEFORE to_thread (VM-2): the
            # lambda captures locals (not ``self``) to avoid races on
            # the reader's internal state.
            path_snapshot = self._current_path
            offset_snapshot = self._cursor_offset
            max_lines = self._max_lines_per_poll
            # P2-H4: pass instance-state counter so contiguous parse-
            # skips accumulate across polls.
            parse_skip_state = self._parse_skip_state

            def _drain_chunk(
                path: Path = path_snapshot,
                start_offset: int = offset_snapshot,
                cap: int = max_lines,
                skip_state: list[int] = parse_skip_state,
            ) -> list[tuple[int, EventEnvelope]]:
                return list(
                    iter_new_envelopes_since(
                        path,
                        start_offset,
                        max_lines_per_poll=cap,
                        parse_skip_state=skip_state,
                    )
                )

            items = await asyncio.to_thread(_drain_chunk)
            for offset_after, envelope in items:
                yield envelope
                # VH-8: advance cursor AFTER the consumer regains control.
                # If the consumer raises, the cursor stays on the prior
                # line and the next restart re-yields this one.
                self._cursor_offset = offset_after
                chunk_envelopes += 1
                if stop_event is not None and stop_event.is_set():
                    return

            # VH-6: rollover detection by file existence + quiescence.
            # Pure clock comparison (today_path != current_path) loses
            # midnight events when the writer is still appending past
            # the reader's clock midnight (NTP skew).  We require:
            #   1. today_path EXISTS on disk (writer has produced at
            #      least one byte for today), AND
            #   2. yesterday's file size has been stable for at least
            #      rollover_quiescence_s seconds (writer is no longer
            #      appending to it).
            rollover_ready = self._is_rollover_ready(today_path, loop)
            if rollover_ready:
                _log.info(
                    "event_log_day_rollover",
                    from_path=str(self._current_path),
                    to_path=str(today_path),
                    final_offset_on_previous_day=self._cursor_offset,
                    # VL-1: include rolled-then-drained vs rolled-without-
                    # draining signal so operators can distinguish.
                    pre_rollover_envelope_count=chunk_envelopes,
                )
                self._current_path = today_path
                self._cursor_offset = 0
                self._yesterday_last_size = None
                self._yesterday_last_size_at_monotonic_s = None
                chunk_envelopes = 0
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

    def _is_rollover_ready(
        self,
        today_path: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """VH-6 — is the reader ready to transition to today's file?

        Returns ``True`` only when (a) ``today_path`` exists on disk
        AND (b) yesterday's file size has been stable for at least
        ``self._rollover_quiescence_s`` seconds.

        Subtle: ``today_path == self._current_path`` means we are
        already on today's file — return False so the tail loop just
        keeps polling.
        """
        assert self._current_path is not None
        if today_path == self._current_path:
            # Reset rollover tracking when we are not in a rollover window.
            self._yesterday_last_size = None
            self._yesterday_last_size_at_monotonic_s = None
            return False
        if not today_path.exists():
            return False
        # P2-M4: guard against stale ``today_path`` from a prior day
        # (retention failure / test detritus).  If the writer is dead
        # but a week-old ``YYYY-MM-DD.jsonl`` happens to be named after
        # today's date, ``today_path.exists()`` returns True and the
        # rollover would seat the reader at offset 0 of a stale file,
        # losing fresh writes in the (now-actual) yesterday file.
        # Require the today_path's mtime to be within the last ~25h.
        try:
            today_mtime = today_path.stat().st_mtime
        except OSError:
            return False
        # Use wall-clock seconds via clock.now() rather than loop.time()
        # since file mtime is wall-clock (not monotonic).
        wall_now_s = self._clock.now().timestamp()
        if today_mtime < wall_now_s - 25 * 3600:
            return False
        # Sample yesterday's size.  If it has grown since the last
        # observation, reset the quiescence timer.
        try:
            yesterday_size = self._current_path.stat().st_size
        except OSError:
            yesterday_size = 0
        # P2-H1 (Q4) fast-path: if the reader has already drained
        # yesterday to EOF (cursor == file_size) AND today_path exists
        # with non-zero size, skip the quiescence wait.  Restart-after-
        # midnight is the dominant call-path here; there is nothing
        # left to drain so blocking for 5s contributes pure latency.
        try:
            today_size = today_path.stat().st_size
        except OSError:
            today_size = 0
        if self._cursor_offset >= yesterday_size and today_size > 0:
            return True
        now_s = loop.time()
        if self._yesterday_last_size is None or yesterday_size != self._yesterday_last_size:
            self._yesterday_last_size = yesterday_size
            self._yesterday_last_size_at_monotonic_s = now_s
            return False
        # File size is stable; require quiescence window.
        assert self._yesterday_last_size_at_monotonic_s is not None
        elapsed = now_s - self._yesterday_last_size_at_monotonic_s
        return elapsed >= self._rollover_quiescence_s

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
