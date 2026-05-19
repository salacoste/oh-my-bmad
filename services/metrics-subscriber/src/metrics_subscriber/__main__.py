"""Async lifespan entrypoint for the β metrics-subscriber service.

Story 10.2 replaces the Story-10.1 scaffold print with the actual tail
loop:

  1. Construct :class:`MetricsSubscriberSettings` from env (AC6).
  2. Construct :class:`EventLogReader` against ``settings.event_log_dir``.
  3. :meth:`CursorPersistence.restore_into` — seek into the saved offset
     OR start fresh if the cursor doesn't exist / is stale (day-rollover
     during downtime).
  4. Async-iterate :meth:`EventLogReader.tail` — for each envelope,
     note it on the cursor; every ``persist_every_n_events`` envelopes,
     atomic-write ``cursor.json``.
  5. SIGTERM / SIGINT triggers :meth:`CursorPersistence.persist_now`
     (drain) before the loop exits.  Story 10.4 will inject metric
     updates between step 4's ``async for`` and the cursor bookkeeping.
  6. AC9 lag observability log emitted on each persist
     (``bytes_behind``, ``wall_clock_lag_s``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from events import EventEnvelope
from events.clock import SystemClock
from events.log_reader import EventLogReader

from metrics_subscriber import __version__
from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.cursor import CursorPersistence

log = logging.getLogger(__name__)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Best-effort SIGTERM/SIGINT → ``stop_event.set()`` registration.

    Same pattern as registry-state's subscriber: tries
    :meth:`loop.add_signal_handler`; on platforms where it raises
    (Windows) we fall back to default Python ``KeyboardInterrupt``
    behaviour.
    """
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue


def _emit_lag_log(
    offset: int,
    path: Path,
    last_envelope: EventEnvelope | None,
) -> None:
    """AC9 — emit structured lag log on each persist.

    Story 10.3 will lift these into Prometheus gauges; for now they are
    grep-able lines so operators can correlate persistence with
    writer-side liveness.
    """
    try:
        file_size = path.stat().st_size if path.exists() else offset
    except OSError:
        file_size = offset
    bytes_behind = max(0, file_size - offset)
    # wall_clock_lag_s: only meaningful if the envelope has
    # ``emitted_at_monotonic_ns``; we use it as a relative measure within
    # one writer process (NFR observability — not a clock-sync
    # measurement across hosts).
    if last_envelope is not None:
        wall_clock_lag_s = (
            SystemClock().monotonic_ns() - last_envelope.emitted_at_monotonic_ns
        ) / 1_000_000_000
        last_event_id: str | None = last_envelope.event_id
    else:
        wall_clock_lag_s = 0.0
        last_event_id = None
    log.info(
        "metrics_subscriber_lag offset=%d file_size=%d bytes_behind=%d "
        "last_event_id=%s wall_clock_lag_s=%.6f",
        offset,
        file_size,
        bytes_behind,
        last_event_id,
        wall_clock_lag_s,
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
        "metrics_subscriber_starting version=%s event_log_dir=%s cursor_path=%s "
        "poll_interval_s=%.3f persist_every_n_events=%d",
        __version__,
        settings.event_log_dir,
        settings.cursor_path,
        settings.poll_interval_s,
        settings.persist_every_n_events,
    )

    reader = EventLogReader(settings.event_log_dir)
    cursor = CursorPersistence(
        settings.cursor_path,
        persist_every=settings.persist_every_n_events,
    )
    cursor.restore_into(reader, base_dir=settings.event_log_dir)

    last_envelope: EventEnvelope | None = None
    try:
        async for envelope in reader.tail(
            poll_interval_s=settings.poll_interval_s, stop_event=stop
        ):
            # Story 10.4 will inject counter/gauge updates here.
            last_envelope = envelope
            cursor.note_event_processed()
            if cursor.maybe_persist(reader.cursor_offset, reader.current_path):
                _emit_lag_log(reader.cursor_offset, reader.current_path, last_envelope)
    finally:
        # AC4 SIGTERM drain: force-persist on shutdown regardless of
        # the per-1000 counter — preserves the resumability invariant.
        # Wrap in try/except so a failure to persist (disk full, etc.)
        # is logged but does not crash the shutdown path.
        try:
            cursor.persist_now(reader.cursor_offset, reader.current_path)
            _emit_lag_log(reader.cursor_offset, reader.current_path, last_envelope)
        except OSError as exc:  # pragma: no cover — surfaced via log
            log.warning(
                "metrics_subscriber_persist_on_shutdown_failed error_type=%s detail=%s",
                type(exc).__name__,
                exc,
            )

    log.info("metrics_subscriber_stopped offset=%d", reader.cursor_offset)
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
