"""Async lifespan entrypoint for the β metrics-subscriber service.

Exit code matrix (Q6 — pass-2):

* **0** — graceful shutdown (SIGTERM, SIGINT, or stop_event set).
* **1** — concurrent-start refused (VH-10 ``BlockingIOError``) OR
  filesystem-unsupported (P2-H6 — ``fcntl.flock`` raised non-EWOULDBLOCK
  ``OSError`` on NFS/FUSE/overlay).  Distinct via the structured log
  ``reason`` field (``"concurrent_start"`` vs ``"filesystem_unsupported"``).
* **2** — cursor schema_version refused (VH-9 + P2-H2 —
  :class:`CursorSchemaVersionError`).  Operator must inspect
  ``cursor.json`` manually.
* **3** — corrupt-region detected (P2-H3 — :class:`ParseSkipThresholdExceeded`).
  The JSONL log has a contiguous run of un-parseable lines exceeding
  the threshold; operator must inspect and advance the cursor past
  the corrupted region via an offline tool (Story 10.4+ scope).

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
from events.errors import CursorSchemaVersionError, ParseSkipThresholdExceeded
from events.log_reader import EventLogReader

from metrics_subscriber import __version__
from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.cursor import (
    CursorLockUnsupportedFilesystemError,
    CursorPersistence,
)

# P3-H3 (test-only): when set, after a successful ``cursor.lock()`` we
# touch the path in this env var so subprocess tests can wait on a
# sentinel that EXISTS-IFF-FLOCK-HELD.  ``<cursor>.lock`` itself is
# created by ``os.open(O_CREAT)`` BEFORE ``fcntl.flock`` is called, so
# polling for the lockfile races on the proc1-crashed-between-open-and-
# flock window.  This sentinel is the only test-observable evidence
# that the lock is actually held.  Production deployments do NOT set
# this variable; the code path is a no-op otherwise.
_LOCK_ACQUIRED_SENTINEL_ENV = "OMB_METRICS_LOCK_ACQUIRED_SENTINEL"

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
    # VH-10 + P2-H6 — acquire fcntl lock; refuse to run on contention.
    # Both ``concurrent_start`` and ``filesystem_unsupported`` exit
    # with code 1 but emit distinct structured logs so dashboards can
    # alert separately.
    try:
        cursor.lock()
    except BlockingIOError as exc:
        # P3-H1 (Q10): discriminate the two exit-code-1 sub-classes via
        # ``isinstance`` rather than substring-matching ``str(exc)``.
        # The typed exception
        # :class:`CursorLockUnsupportedFilesystemError` (a subclass of
        # ``BlockingIOError``) is raised on the unsupported-FS path
        # inside ``cursor.lock()``; plain ``BlockingIOError`` indicates
        # concurrent-start.  Substring matching was a landmine — one
        # message-edit (locale, refactor, error-string review) would
        # silently invert the dashboard alerting split.
        if isinstance(exc, CursorLockUnsupportedFilesystemError):
            reason = "filesystem_unsupported"
        else:
            reason = "concurrent_start"
        log.error(
            "metrics_subscriber_concurrent_start_refused",
            cursor_path=str(settings.cursor_path),
            reason=reason,
            note=(
                "another subscriber process already holds the cursor lock; "
                "refusing to start to avoid corrupting cursor.json"
                if reason == "concurrent_start"
                else "fcntl.flock unsupported on cursor filesystem"
            ),
        )
        return 1

    # P3-H3 (test-only): write the lock-acquired sentinel marker so
    # subprocess tests can poll for it.  Polling for ``<cursor>.lock``
    # itself races on the open-but-not-flocked window.  Production
    # deployments do not set ``OMB_METRICS_LOCK_ACQUIRED_SENTINEL``; the
    # write is a no-op when the env var is unset.
    sentinel_path_str = os.environ.get(_LOCK_ACQUIRED_SENTINEL_ENV)
    if sentinel_path_str:
        try:
            Path(sentinel_path_str).touch()
        except OSError as sentinel_exc:
            log.warning(
                "metrics_subscriber_lock_sentinel_write_failed",
                sentinel_path=sentinel_path_str,
                detail=str(sentinel_exc),
            )

    try:
        # VM-6 / P2-M2: ``async with`` per AC5 spec sketch.  Pass-2:
        # ``__aexit__`` now sets ``_closed=True`` so post-exit calls
        # raise ``RuntimeError`` rather than silently no-oping.
        async with EventLogReader(settings.event_log_dir) as reader:
            # P2-H2: wrap restore_into so VH-9's CursorSchemaVersionError
            # surfaces as a structured log + exit code 2 instead of an
            # uncaught traceback through asyncio.run().
            try:
                cursor.restore_into(reader, base_dir=settings.event_log_dir)
            except CursorSchemaVersionError as exc:
                log.error(
                    "metrics_subscriber_cursor_schema_version_refused",
                    cursor_path=str(settings.cursor_path),
                    found_schema_version=repr(exc.schema_version),
                    expected=exc.expected,
                    note=(
                        "cursor.json declares an unknown schema_version; "
                        "refusing to start to avoid replaying an entire "
                        "day's events.  Operator must inspect the cursor "
                        "file manually."
                    ),
                )
                return 2

            # P2-M5: ``reader.current_path`` returns ``Path | None``;
            # annotate accordingly and handle the None branch.  In
            # practice ``restore_into`` always seats the cursor before
            # we read this, but mypy --strict requires the annotation
            # to match the property's signature.
            previous_path: Path | None = reader.current_path
            last_envelope: EventEnvelope | None = None
            try:
                # P2-H3: catch the typed corruption exception so the
                # subscriber exits with code 3 instead of crashing
                # through asyncio.run() in an infinite restart loop.
                try:
                    async for envelope in reader.tail(
                        poll_interval_s=settings.poll_interval_s, stop_event=stop
                    ):
                        # VM-1 — day-rollover mid-loop: log the
                        # transition and clear ``last_envelope`` so the
                        # lag log does not attribute yesterday's
                        # envelope to today's path.  P2-M5: drop the
                        # dead ``last_envelope = None`` (the
                        # unconditional assignment below already does
                        # the right thing for a non-None last_envelope
                        # at the new path).
                        if reader.current_path != previous_path:
                            log.info(
                                "metrics_subscriber_day_rollover_observed",
                                from_path=str(previous_path),
                                to_path=str(reader.current_path),
                            )
                            previous_path = reader.current_path
                        # Story 10.4 will inject counter/gauge updates here.
                        last_envelope = envelope
                        cursor.note_event_processed()
                        if cursor.maybe_persist(reader.cursor_offset, reader.current_path):
                            _emit_lag_log(reader.cursor_offset, reader.current_path, last_envelope)
                except ParseSkipThresholdExceeded as exc:
                    log.critical(
                        "metrics_subscriber_corrupt_region_detected",
                        cursor_path=str(settings.cursor_path),
                        log_path=exc.path,
                        corrupt_offset=exc.offset,
                        threshold=exc.threshold,
                        last_envelope_id=(last_envelope.event_id if last_envelope else None),
                        note=(
                            "contiguous parse-skip threshold exceeded; "
                            "refusing to advance cursor past corrupted region. "
                            "Operator inspection required."
                        ),
                    )
                    # P3-M2: persist ``exc.offset`` (the start of the
                    # corrupt region — anchored across polls per
                    # P3-H2) so restart picks up AT the corrupt
                    # region.  Pre-pass-3 we persisted
                    # ``reader.cursor_offset`` (last successful yield),
                    # which meant restart re-parsed the good prefix
                    # between cursor_offset and exc.offset every time
                    # before tripping the threshold again.  Cosmetic
                    # but wasteful — exit-3 restart loops are now
                    # O(corrupt-region-only) instead of O(file-size).
                    #
                    # Reseat the reader's cursor BEFORE the persist
                    # so the outer ``finally`` block (which always
                    # re-drains at ``reader.cursor_offset``) does not
                    # silently overwrite the corrupt-region anchor.
                    reader.seek(path=reader.current_path, offset=exc.offset)
                    try:
                        cursor.persist_now(exc.offset, reader.current_path)
                    except OSError as drain_exc:
                        # P3-M6: remove ``pragma: no cover`` and exercise
                        # this branch via
                        # ``test_main_exit_3_persist_failure_still_returns_3_with_warning``.
                        # Persist-failure on the corrupt-exit path is a
                        # distinct failure shape from the corrupt-loop
                        # P2-H3 escaped — operators need a structured
                        # log warning to recognise it.
                        log.warning(
                            "metrics_subscriber_persist_on_corrupt_region_failed",
                            error_type=type(drain_exc).__name__,
                            detail=str(drain_exc),
                        )
                    return 3
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
