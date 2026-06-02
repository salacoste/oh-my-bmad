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
import uvicorn
from events import EventEnvelope, ensure_shared_dir
from events.errors import CursorSchemaVersionError, ParseSkipThresholdExceeded
from events.log_reader import EventLogReader

from metrics_subscriber import __version__
from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.metrics import MetricsState, update_for
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


# Story 10.3 pass-1 P1-M7: configure structlog at module-import time so
# the module-level ``log`` binding emits structured JSON for ALL events
# — including the invalid-``OMB_METRICS_RUN_MODE`` branch in
# :func:`main` that previously logged BEFORE ``logging.basicConfig``
# ran.  Idempotent guard mirrors ``registry_api.__main__`` Story 3.6
# AC-4 — repeated module imports (e.g. via pytest) do not double-wire
# the processor chain.
_STRUCTLOG_CONFIGURED: bool = False


def _configure_structlog_for_module() -> None:
    """Idempotent structlog config bound at module import.

    Production-only: tests bring their own structlog wiring via
    ``conftest.py`` BEFORE this module is imported, and the sentinel
    here short-circuits the call when those tests later trigger a
    re-import.  The check on the global flag is the load-bearing
    idempotency guard.
    """
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return
    logging.basicConfig(
        level=os.environ.get("OMB_METRICS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _STRUCTLOG_CONFIGURED = True


_configure_structlog_for_module()

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
    metrics_state: MetricsState | None = None,
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
    # Story 10.3 AC5: lift the structured-log values into the Prometheus
    # gauges held on ``MetricsState``.  Updating from inside the lag-log
    # emit guarantees both observation paths (logs + ``/metrics``) see
    # the SAME numbers at the SAME persist event — VH-8 silent-downgrade
    # lesson applied (Story 10.2 P2-H5).  ``metrics_state is None`` is
    # the Story 10.2 tail-only path that has no FastAPI surface; the
    # gauges simply do not exist in that deployment.
    if metrics_state is not None:
        metrics_state.record_lag(lag_seconds=wall_clock_lag_s, bytes_behind=bytes_behind)
        metrics_state.record_cursor(path=path, offset=offset)


async def run_subscriber(
    settings: MetricsSubscriberSettings,
    *,
    stop_event: asyncio.Event | None = None,
    metrics_state: MetricsState | None = None,
) -> int:
    """Async lifespan: open reader, restore cursor, tail JSONL.

    Args:
        settings: Validated config from env.
        stop_event: Optional shutdown signal.  ``None`` → tail forever.
        metrics_state: Optional Story 10.3 :class:`MetricsState` shared
            with the FastAPI ``/metrics`` route.  ``None`` (the Story
            10.2 standalone tail-loop path, also used by the
            ``test_restart_recovery_subprocess.py`` tests via
            ``OMB_METRICS_RUN_MODE=tail``) disables gauge/counter
            updates; the structured ``metrics_subscriber_lag`` log is
            still emitted on every persist so the no-FastAPI path
            preserves its observability surface.

    Returns:
        Process exit code (0 on graceful shutdown).
    """
    stop = stop_event if stop_event is not None else asyncio.Event()

    # Story 11.3.8 / FR62a: metrics-subscriber's event_log_dir.mkdir was the
    # FIRST-service-wins race trigger in Story 11.3.7's Task 7 repro — its
    # bare ``mkdir`` (umask 022 → mode 0o755) locked out registry-api and
    # other ``omb``-group services. Use ``ensure_shared_dir`` so the dir
    # gets explicit ``chmod 0o2775`` (setgid + group-write) after creation,
    # regardless of who reached the path first.
    #
    # cursor_path's parent (default ``/var/lib/oh-my-bmad/metrics-subscriber/``
    # per app/config.py:44) is ALSO under the shared ``/var/lib/oh-my-bmad/``
    # data root — defense-in-depth, also via the helper, so any future
    # cross-uid consumer (e.g. operator cleanup tooling, sibling tail-and-
    # replay services) in the ``omb`` group can write under it without
    # hitting the same uid-mismatch pattern. Convention-only "subscriber-
    # local" isolation isn't enforceable — the named volume is shared.
    ensure_shared_dir(settings.event_log_dir)
    ensure_shared_dir(settings.cursor_path.parent)

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
            # Story 10.3 AC6: install the parse-skip Prometheus counter
            # callback when a ``MetricsState`` was provided (the FastAPI
            # ``server`` run-mode).  Standalone ``tail`` mode (Story 10.2
            # subprocess tests) passes ``None`` and observes unchanged
            # behaviour — the parse-skip log line still emits.
            if metrics_state is not None:
                reader.set_on_skip(metrics_state.on_parse_skip)
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
                        # Story 10.4 AC7 — dispatch envelope to the
                        # FR62 metric updaters.  Wrapped in
                        # ``try/except Exception`` per Story 10.3
                        # P1-H5 lesson: a single bad envelope (e.g.
                        # payload-field drift, future schema_version
                        # the updater doesn't yet handle) MUST NOT
                        # kill the tail subscriber.  A failure here
                        # is observability degradation, not data
                        # loss — the cursor still advances and the
                        # event log is unaffected.
                        if metrics_state is not None:
                            try:
                                update_for(metrics_state, envelope)
                            except Exception as exc:  # noqa: BLE001 — defensive log-and-continue
                                log.warning(
                                    "metrics_subscriber_dispatch_updater_failed",
                                    envelope_type=envelope.type,
                                    event_id=str(envelope.event_id),
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                        last_envelope = envelope
                        cursor.note_event_processed()
                        if cursor.maybe_persist(reader.cursor_offset, reader.current_path):
                            _emit_lag_log(
                                reader.cursor_offset,
                                reader.current_path,
                                last_envelope,
                                metrics_state,
                            )
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
                #
                # Story 10.3 pass-1 P1-H2: guard ``reader.current_path``
                # access against the ``RuntimeError`` raised by the
                # property when ``_current_path`` is None.  ``restore_into``
                # can raise non-:class:`CursorSchemaVersionError`
                # exceptions (e.g. ``OSError`` / ``json.JSONDecodeError``
                # for a corrupt cursor file); pre-fix those propagated
                # into this finally block, ``reader.current_path``
                # raised a secondary ``RuntimeError``, and the original
                # exception was masked.  Probing ``_current_path``
                # directly (an attribute read, never raising) keeps the
                # finally drain side-effect-free on the no-path branch
                # while still attempting a best-effort persist when the
                # reader was actually seated.
                if reader._current_path is not None:
                    try:
                        cursor.persist_now(reader.cursor_offset, reader._current_path)
                        _emit_lag_log(
                            reader.cursor_offset,
                            reader._current_path,
                            last_envelope,
                            metrics_state,
                        )
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


def _run_tail_mode(settings: MetricsSubscriberSettings) -> int:
    """Story 10.2 standalone tail-loop entry path.

    Preserved for the ``test_restart_recovery_subprocess.py`` +
    ``test_exit_codes.py`` tests that exercise tail semantics directly
    (signal-handler installation, cursor-lock contention, SIGTERM
    drain).  Re-running those tests through the FastAPI lifespan would
    conflate Story 10.3 + 10.2 review semantics; defer that rewrite to
    a follow-up story per the trade-off note in the Story 10.3 spec.
    """
    stop_event = asyncio.Event()

    async def _run() -> int:
        # P2-H8 (Q9): get_running_loop, not the deprecated get_event_loop.
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
        return await run_subscriber(settings, stop_event=stop_event)

    rc = 0
    with contextlib.suppress(KeyboardInterrupt):
        rc = asyncio.run(_run())
    return rc


def _run_server_mode(settings: MetricsSubscriberSettings) -> int:
    """Story 10.3 default — uvicorn-hosted FastAPI factory.

    Mirrors ``registry_api.__main__.main`` programmatic-uvicorn pattern
    (Story 3.6 AC-4 lesson — ``log_config=None`` so structlog owns
    logging; ``access_log=False`` so /metrics scrapes do not spam the
    log).

    Exit-code matrix preservation: ``build_app``'s lifespan handler
    captures typed lifespan failures onto ``app.state.exit_code`` and
    flips ``uvicorn.Server.should_exit = True``.  Once ``serve()``
    returns we surface that value as the process exit code.
    """
    # Local import — the FastAPI factory module pulls in heavy
    # dependencies (fastapi, uvicorn, prometheus_client) that the
    # tail-mode entry path does NOT need; keeping the import inside
    # ``_run_server_mode`` lets the standalone tail subprocess tests
    # avoid the import cost.
    from metrics_subscriber.app.main import build_app  # noqa: PLC0415

    app = build_app(settings=settings)
    config = uvicorn.Config(
        app,
        host=settings.metrics_host,
        port=settings.metrics_port,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # Hand the server back to the app so the lifespan failure-handler
    # can request shutdown without circular imports.
    app.state.uvicorn_server = server

    async def _serve() -> None:
        await server.serve()
        # Story 10.3 pass-1 P1-M5: yield once after ``serve()`` returns
        # so any pending ``_on_tail_done`` done-callback that asyncio
        # scheduled on the final loop pass actually runs BEFORE we
        # unwind ``asyncio.run`` and read ``app.state.exit_code``.
        # Without this yield, CPython 3.12 may unwind the loop with
        # the callback still queued, leaving ``exit_code = 0`` even
        # when the tail crashed with a typed exception.
        await asyncio.sleep(0)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve())
    # ``app.state.exit_code`` carries the Story 10.2 1/2/3 matrix when
    # the lifespan tail task surfaces a typed exception.
    rc_obj = getattr(app.state, "exit_code", 0)
    rc: int = int(rc_obj) if isinstance(rc_obj, int) else 0
    return rc


def main() -> int:
    """Sync entrypoint for ``python -m metrics_subscriber``.

    AC8 dispatch: ``OMB_METRICS_RUN_MODE`` selects between the
    Story 10.2 standalone tail-loop (``tail``) and the Story 10.3
    FastAPI + uvicorn surface (``server``, default).  Tests that need
    to exercise tail semantics directly set
    ``OMB_METRICS_RUN_MODE=tail``; production deployments leave the
    env var unset and run the server path.
    """
    # Story 10.3 pass-1 P1-M7: structlog/stdlib logging is configured
    # at module-import time via :func:`_configure_structlog_for_module`
    # so the invalid-run-mode branch below emits structured output
    # rather than relying on basicConfig running first.  Idempotent —
    # safe to re-enter ``main()`` from tests.
    _configure_structlog_for_module()
    settings = MetricsSubscriberSettings()
    mode = os.environ.get("OMB_METRICS_RUN_MODE", "server").strip().lower()
    if mode == "tail":
        return _run_tail_mode(settings)
    if mode == "server":
        return _run_server_mode(settings)
    log.error(
        "metrics_subscriber_invalid_run_mode",
        run_mode=mode,
        valid_modes=["server", "tail"],
        note="unset OMB_METRICS_RUN_MODE for default server mode",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
