"""Async lifespan entrypoint for the β metrics-subscriber service.

Story 10.2 replaces the Story-10.1 scaffold print with the actual tail
loop:

  1. Construct :class:`MetricsSubscriberSettings` from env (AC6).
  2. Construct :class:`EventLogReader` against ``settings.event_log_dir``.
  3. :meth:`CursorPersistence.restore_into` — seek into the saved offset
     OR start fresh if the cursor doesn't exist / is stale (day-rollover
     during downtime; VH-1 drains yesterday's tail first).
  4. Async-iterate :meth:`EventLogReader.tail` — for each envelope,
     note it on the cursor; every ``persist_every_n_events`` envelopes,
     atomic-write ``cursor.json``.
  5. SIGTERM / SIGINT triggers :meth:`CursorPersistence.persist_now`
     (drain) before the loop exits.  Story 10.4 will inject metric
     updates between step 4's ``async for`` and the cursor bookkeeping.
  6. AC9 lag observability log emitted on each persist
     (``bytes_behind``, ``wall_clock_lag_s``,
     ``last_envelope_emitted_at_monotonic_ns``).

VH-2 lag-semantics note: ``wall_clock_lag_s`` is computed from
``envelope.emitted_at`` (UTC datetime, set by the writer process) versus
``datetime.now(UTC)`` (the subscriber's wall clock).  This is correct
across process boundaries — the previous implementation subtracted
``time.monotonic_ns()`` values across processes, which is undefined
(``monotonic_ns`` is per-process).  Trade-off: the result is sensitive
to clock skew between writer and subscriber hosts; we document an
NTP-sync assumption (deployments should run chrony/ntpd; observed
skews >2s should trigger an operator alert).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

import structlog
from events import EventEnvelope
from events.log_reader import EventLogReader

from metrics_subscriber import __version__
from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.cursor import CursorPersistence

log = structlog.get_logger(__name__)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Best-effort SIGTERM/SIGINT → ``stop_event.set()`` registration.

    Tries :meth:`loop.add_signal_handler`; on platforms where it raises
    :class:`NotImplementedError` (Windows) we fall back to default
    Python ``KeyboardInterrupt`` behaviour.

    VM-3: ``RuntimeError`` from :meth:`loop.add_signal_handler` is a
    programmer error (wrong loop state, signal already installed by
    another handler).  We do NOT swallow it — propagate so the
    operator sees the misconfiguration rather than a silent SIGTERM
    fail.  Only :class:`NotImplementedError` (Windows) is caught.
    """
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            log.warning(
                "metrics_subscriber_signal_handler_unavailable",
                signal=sig_name,
                fallback="default_python_keyboard_interrupt",
            )
            continue


def _emit_lag_log(
    offset: int,
    path: Path,
    last_envelope: EventEnvelope | None,
) -> None:
    """AC9 — emit structured lag log on each persist.

    VH-2 fix: ``wall_clock_lag_s`` is computed from
    ``envelope.emitted_at`` (UTC datetime) vs ``datetime.now(UTC)``.
    The previous implementation subtracted ``time.monotonic_ns()``
    across processes — which is undefined (``monotonic_ns`` is
    per-process) and produced meaningless numbers.

    AC9 also requires field ``last_envelope_emitted_at_monotonic_ns``
    (a writer-side observation, useful as a tracing handle even though
    we no longer subtract it cross-process).

    NTP-sync assumption: ``wall_clock_lag_s`` is only meaningful when
    writer and subscriber hosts have synchronised wall clocks (chrony/
    ntpd).  An unsynchronised deployment can produce negative values
    or wildly inflated lags; operators should configure clock-sync
    monitoring (Story 10.5 cardinality discipline tests will check
    that the gauge stays within reasonable bounds).
    """
    try:
        file_size = path.stat().st_size if path.exists() else offset
    except OSError:
        file_size = offset
    bytes_behind = max(0, file_size - offset)
    if last_envelope is not None:
        # VH-2: wall_clock_lag_s = now - envelope.emitted_at (both UTC).
        now_utc = datetime.now(UTC)
        wall_clock_lag_s = (now_utc - last_envelope.emitted_at).total_seconds()
        last_event_id: str | None = last_envelope.event_id
        last_envelope_emitted_at_monotonic_ns: int | None = last_envelope.emitted_at_monotonic_ns
    else:
        wall_clock_lag_s = 0.0
        last_event_id = None
        last_envelope_emitted_at_monotonic_ns = None
    log.info(
        "metrics_subscriber_lag",
        offset=offset,
        file_size=file_size,
        bytes_behind=bytes_behind,
        last_event_id=last_event_id,
        last_envelope_emitted_at_monotonic_ns=last_envelope_emitted_at_monotonic_ns,
        wall_clock_lag_s=wall_clock_lag_s,
    )


async def run_subscriber(
    settings: MetricsSubscriberSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Async lifespan: open reader, restore cursor, tail JSONL.

    Args:
        settings: Validated config from env.
        stop_event: Optional shutdown signal.  ``None`` → tail forever.

    Returns:
        Process exit code (0 on graceful shutdown).
    """
    stop = stop_event if stop_event is not None else asyncio.Event()

    settings.event_log_dir.mkdir(parents=True, exist_ok=True)
    settings.cursor_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        "metrics_subscriber_starting",
        version=__version__,
        event_log_dir=str(settings.event_log_dir),
        cursor_path=str(settings.cursor_path),
        poll_interval_s=settings.poll_interval_s,
        persist_every_n_events=settings.persist_every_n_events,
    )

    cursor = CursorPersistence(
        settings.cursor_path,
        persist_every=settings.persist_every_n_events,
    )
    # VH-10 — acquire fcntl lock; refuse to run on contention.
    try:
        cursor.lock()
    except BlockingIOError:
        log.error(
            "metrics_subscriber_concurrent_start_refused",
            cursor_path=str(settings.cursor_path),
            note=(
                "another subscriber process already holds the cursor lock; "
                "refusing to start to avoid corrupting cursor.json"
            ),
        )
        return 1

    try:
        # VM-6: ``async with`` per AC5 spec sketch.
        async with EventLogReader(settings.event_log_dir) as reader:
            cursor.restore_into(reader, base_dir=settings.event_log_dir)

            # Track the path the reader is currently on so we can clear
            # ``last_envelope`` when the day rolls (VM-1).
            previous_path: Path = reader.current_path
            last_envelope: EventEnvelope | None = None
            try:
                async for envelope in reader.tail(
                    poll_interval_s=settings.poll_interval_s, stop_event=stop
                ):
                    # VM-1 — day-rollover mid-loop: clear last_envelope
                    # so lag log does not attribute yesterday's envelope
                    # to today's path.
                    if reader.current_path != previous_path:
                        last_envelope = None
                        previous_path = reader.current_path
                    # Story 10.4 will inject counter/gauge updates here.
                    last_envelope = envelope
                    cursor.note_event_processed()
                    if cursor.maybe_persist(reader.cursor_offset, reader.current_path):
                        _emit_lag_log(reader.cursor_offset, reader.current_path, last_envelope)
            finally:
                # AC4 SIGTERM drain: force-persist on shutdown regardless of
                # the per-1000 counter — preserves the resumability invariant.
                # Wrap in try/except so a failure to persist (disk full,
                # etc.) is logged but does not crash the shutdown path.
                try:
                    cursor.persist_now(reader.cursor_offset, reader.current_path)
                    _emit_lag_log(reader.cursor_offset, reader.current_path, last_envelope)
                except OSError as exc:  # pragma: no cover — surfaced via log
                    log.warning(
                        "metrics_subscriber_persist_on_shutdown_failed",
                        error_type=type(exc).__name__,
                        detail=str(exc),
                    )

            log.info("metrics_subscriber_stopped", offset=reader.cursor_offset)
    finally:
        cursor.unlock()

    return 0


def main() -> int:
    """Sync entrypoint for ``python -m metrics_subscriber``."""
    logging.basicConfig(
        level=os.environ.get("OMB_METRICS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = MetricsSubscriberSettings()
    stop_event = asyncio.Event()

    async def _run() -> int:
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
        return await run_subscriber(settings, stop_event=stop_event)

    rc = 0
    with contextlib.suppress(KeyboardInterrupt):
        rc = asyncio.run(_run())
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
