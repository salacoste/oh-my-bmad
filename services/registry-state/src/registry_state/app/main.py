"""Subscriber loop entrypoint for registry-state (Stories 2.5, 2.6).

``run_subscriber`` is the long-lived async loop that:
  1. Runs ``recover_all_logs(base_dir)`` to trim trailing partial lines.
  2. (Story 2.6) Restores tasks + sessions from the latest snapshot via
     ``restore_state_from_latest_snapshot``; logs how many events the
     snapshot allows us to skip.
  3. Computes a startup cursor as ``max(snapshot_cursor,
     MAX(events.emitted_at_monotonic_ns))`` via ``compute_replay_cursor`` —
     the higher of the snapshot's anchor and any events already persisted
     past the snapshot.
  4. Replays all ``*.jsonl`` files in *base_dir* sorted chronologically,
     filtering events already in the DB (``emitted_at_monotonic_ns <= cursor``).
  5. (Story 2.6) After every successful ``apply_many`` calls
     ``SnapshotPolicy.maybe_capture(last_env, applied_count)``; once the
     tally hits the configured ``snapshot_interval`` a fresh snapshot row
     is written.
  6. Tails ALL ``*.jsonl`` files (not just today's) in a 100ms poll loop,
     reading only the bytes appended since the last poll, until ``stop_event``
     fires.  Tailing every file in date order means events appended to
     yesterday's file in the last 100ms before UTC midnight are not lost
     across a rollover boundary.

``main()`` is the sync wrapper for ``python -m registry_state``:
  - reads env vars (``REGISTRY_STATE_DB_URL``, ``REGISTRY_STATE_LOG_DIR``),
  - installs SIGTERM/SIGINT → ``stop_event.set()`` (best-effort: on Windows
    ``loop.add_signal_handler`` raises ``NotImplementedError`` for both
    signals, so we fall back to default Python handling — only ``SIGINT``
    is honoured there, via the ``KeyboardInterrupt`` raised in ``main()``).
  - calls ``asyncio.run(run_subscriber(...))``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Callable
from pathlib import Path

from events import EventEnvelope
from events.clock import Clock, SystemClock
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_state.adapters.event_log import (
    read_new_envelopes_since,
    recover_all_logs,
)
from registry_state.adapters.sqlite_store import create_engine, get_session
from registry_state.domain.event_types import (  # noqa: F401 — side-effect: register() calls
    ServiceCrashedPayload,
    SessionHeartbeatTimeoutPayload,
    SinkDeliveryFailedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskStopRequestedPayload,
)
from registry_state.domain.handlers import register_default_handlers
from registry_state.domain.materializer import Materializer
from registry_state.domain.recovery import (
    compute_events_max_cursor,
    restore_state_from_latest_snapshot,
)
from registry_state.domain.snapshots import SnapshotPolicy
from registry_state.schema import Base

log = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_DB_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"


async def _scan_new_envelopes(base_dir: Path, offsets: dict[str, int]) -> list[EventEnvelope]:
    """Scan every ``*.jsonl`` in *base_dir* for newly-appended envelopes.

    Iterates files in lexicographic (= chronological) order so the returned
    list is globally ordered.  For each file we read only the bytes after
    ``offsets[path.name]`` and update the offset to the new EOF position;
    new files start at offset 0.  Reads are offloaded to the default thread
    executor via ``asyncio.to_thread`` to keep the asyncio loop responsive.

    Args:
        base_dir: Root directory containing ``YYYY-MM-DD.jsonl`` event logs.
        offsets:  Per-file byte-offset checkpoint, mutated in-place to track
                  the EOF position seen by the caller.  Keys are
                  ``path.name`` (e.g. ``"2026-04-24.jsonl"``).

    Returns:
        Concatenated list of newly-parsed envelopes across all files in
        date order.
    """
    collected: list[EventEnvelope] = []
    for path in sorted(base_dir.glob("*.jsonl")):
        prior = offsets.get(path.name, 0)
        new_offset, envelopes = await asyncio.to_thread(read_new_envelopes_since, path, prior)
        offsets[path.name] = new_offset
        collected.extend(envelopes)
    return collected


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Best-effort SIGTERM/SIGINT → ``stop_event.set()`` registration.

    On POSIX both signals trigger a clean shutdown via the asyncio loop.
    On Windows ``loop.add_signal_handler`` raises ``NotImplementedError``
    (the Proactor and Selector loops do not implement it), so we fall back
    to default Python behaviour — ``SIGINT`` becomes ``KeyboardInterrupt``
    inside ``asyncio.run`` and is caught by the outer ``contextlib.suppress``.
    ``SIGTERM`` cannot be intercepted on Windows.
    """
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows: add_signal_handler is unsupported for SIGTERM/SIGINT.
            # KeyboardInterrupt-via-main() handles SIGINT; SIGTERM is lost.
            continue


def _default_materializer_factory(
    session_maker: async_sessionmaker[AsyncSession],
) -> Materializer:
    """Production-default ``materializer_factory`` for :func:`run_subscriber`.

    Defined at module level (rather than as a default-arg lambda) so the
    function has a stable name + signature mypy can verify against the
    ``Callable`` type annotation. Tests inject their own factory to wrap
    the materializer (e.g. counting subclass) without monkey-patching.
    """
    return Materializer(session_maker=session_maker)


async def run_subscriber(
    *,
    base_dir: Path,
    db_url: str,
    clock: Clock,
    poll_interval_s: float = 0.1,
    stop_event: asyncio.Event | None = None,
    snapshot_interval: int = 1000,
    materializer_factory: Callable[
        [async_sessionmaker[AsyncSession]], Materializer
    ] = _default_materializer_factory,
) -> None:
    """Long-lived subscriber loop: tail the JSONL event log → materialize SQLite state.

    Args:
        base_dir:           Root directory containing ``YYYY-MM-DD.jsonl`` event-log files.
        db_url:             SQLAlchemy async URL for the registry-state SQLite store.
        clock:              Injected clock (Story 2.2 discipline) for UTC now +
                            monotonic_ns. Used as the snapshot policy's clock so
                            snapshot ids + ``created_at`` stamps are deterministic
                            in tests.
        poll_interval_s:    How long to sleep between tail-loop iterations (default 100ms).
        stop_event:         Optional asyncio.Event; set it to request a clean shutdown.
                            If ``None``, a local event is created (useful in tests).
        snapshot_interval:  (Story 2.6) Number of newly-applied events between
                            snapshot captures. ``SnapshotPolicy`` accumulates the
                            tally and writes a snapshot row once the tally hits
                            this threshold. The integration suite passes
                            ``snapshot_interval=2`` to exercise capture without
                            having to write thousands of envelopes.
        materializer_factory: Callable that builds a ``Materializer`` from a
                            session-maker. Defaults to the :class:`Materializer`
                            class itself; tests inject subclasses (e.g. a counter
                            wrapper) without monkey-patching the module.
    """
    stop = stop_event if stop_event is not None else asyncio.Event()
    engine = create_engine(db_url)
    try:
        # Story 2.11: optional schema bootstrap, gated behind an env var.
        # In production the Alembic migrations are the authoritative source
        # of schema (Story 2.14's migrator runs ``alembic upgrade head``).
        # The crash-injection harness does not run Alembic; setting
        # ``REGISTRY_STATE_AUTO_CREATE_SCHEMA=1`` (only in
        # ``tests/crash-injection/docker-compose.test.yml``) opts the test
        # container into a one-shot ``Base.metadata.create_all`` so the
        # events/tasks tables exist on first boot. Tests that inject an
        # in-memory engine already call create_all in their own fixtures
        # and do not need this path.
        if os.environ.get("REGISTRY_STATE_AUTO_CREATE_SCHEMA") == "1":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        # Startup contract: trim trailing partial lines across all *.jsonl.
        # Use the free function so we don't construct a full writer just to
        # reach its recovery routine.
        await recover_all_logs(base_dir)

        session_maker = get_session(engine)
        materializer = materializer_factory(session_maker)
        register_default_handlers(materializer)
        # Story 2.11: /tmp/ready touchpoint flips the docker-compose healthcheck
        # to "healthy" once the subscriber's startup wiring has completed
        # (engine open, session-maker ready, handlers registered). The
        # crash-injection harness (tests/crash-injection/) polls
        # ``docker inspect --format='{{.State.Health.Status}}'`` against this
        # signal to know when restart is complete. Best-effort: if /tmp is
        # read-only or otherwise unwritable, we log and continue — the
        # subscriber's correctness does not depend on this file.
        try:
            Path("/tmp/ready").touch()  # noqa: S108 — healthcheck signal, not data store
        except OSError as exc:
            log.warning("failed to touch /tmp/ready healthcheck signal: %s", exc)
        snapshot_policy = SnapshotPolicy(
            session_maker=session_maker,
            clock=clock,
            interval=snapshot_interval,
        )

        # Story 2.6 startup phase 1: restore tasks + sessions from the
        # latest snapshot (if any). UPSERTs are idempotent so this is safe
        # even when the events table already contains rows past the
        # snapshot's cursor (the AC-12 "stale snapshot + newer events"
        # case). Capture the cursor so phase 2 doesn't re-parse the
        # snapshot's payload JSON a second time (Story 2.6 F9).
        restored_cursor_ns = await restore_state_from_latest_snapshot(session_maker)

        # Story 2.6 startup phase 2: compute the replay cursor as the
        # higher of the snapshot's cursor (from phase 1) and the events
        # table's max — neither anchor regresses past the other.
        # Story 9.7 PH-B11/E3: revert pass-1 removal — cursor-filter is the
        # only mechanism preventing snapshot-covered re-application. See
        # deferred-work.md D5 for re-evaluation as a separate Story 2.6.X.
        events_max_ns = await compute_events_max_cursor(session_maker)
        cursor_ns = max(restored_cursor_ns, events_max_ns)

        # Startup replay: read every *.jsonl byte-by-byte (offloaded to thread)
        # and apply only events newer than the persisted cursor.  Populate the
        # per-file byte-offset checkpoint so the tail loop only has to read
        # NEW bytes from each file.
        offsets: dict[str, int] = {}
        startup_applied = 0
        startup_skipped = 0
        for path in sorted(base_dir.glob("*.jsonl")):
            new_offset, envelopes = await asyncio.to_thread(read_new_envelopes_since, path, 0)
            offsets[path.name] = new_offset
            new_envelopes = [env for env in envelopes if env.emitted_at_monotonic_ns > cursor_ns]
            startup_skipped += len(envelopes) - len(new_envelopes)
            if new_envelopes:
                applied = await materializer.apply_many(new_envelopes)
                startup_applied += applied
                if applied:
                    last_env = new_envelopes[-1]
                    await snapshot_policy.maybe_capture(last_env, applied)
        # Story 2.6 AC-9: instrumentation for "verified via instrumentation
        # counter". Always log once at startup-replay end so tests can
        # assert on the line. Pure facts — no causal "via snapshot" claim,
        # because the cursor may have come from the events table instead.
        log.info(
            "startup replay: cursor=%d, skipped=%d events, applied=%d new",
            cursor_ns,
            startup_skipped,
            startup_applied,
        )

        # Tail loop: scan ALL *.jsonl files for newly-appended bytes until
        # stop_event fires.  Scanning every file (not just today's) means
        # events appended to yesterday's file just before the UTC-midnight
        # rollover boundary are not lost.
        while not stop.is_set():
            envelopes = await _scan_new_envelopes(base_dir, offsets)
            if envelopes:
                # Tail loop cursor uses materializer.cursor (events-table-MAX,
                # not compute_replay_cursor) because:
                #   1. Events table grows monotonically post-startup
                #      (snapshots only added, never removed in current
                #      architecture).
                #   2. The snapshot cursor is always <= events-max once
                #      startup replay completes.
                # If event-table pruning is ever added (Phase 4 retention),
                # revisit this — pruning would invalidate (1).
                async with session_maker() as session:
                    cursor_ns = await materializer.cursor(session)
                to_apply = [env for env in envelopes if env.emitted_at_monotonic_ns > cursor_ns]
                if to_apply:
                    applied = await materializer.apply_many(to_apply)
                    if applied:
                        last_env = to_apply[-1]
                        await snapshot_policy.maybe_capture(last_env, applied)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
    finally:
        # Story 2.11: delete the /tmp/ready healthcheck signal on graceful
        # shutdown. Clarification on why this is needed:
        #   - ``docker compose stop`` + ``docker compose start`` REUSES the
        #     same container (does NOT recreate it), so /tmp persists across
        #     stop/start cycles (it is part of the writable container layer,
        #     not a tmpfs mount).
        #   - A SIGKILL-killed container that is then started via
        #     ``compose start`` also reuses the same layer, so a stale
        #     /tmp/ready from before the kill would make the healthcheck
        #     report "healthy" before the subscriber completes startup-replay.
        #   - Deleting /tmp/ready here on graceful shutdown ensures that any
        #     subsequent start (stop→start or kill→start) boots into
        #     health-status=starting until the subscriber re-establishes
        #     readiness.
        #   - Note: ``compose down`` + ``compose up`` RECREATES the container
        #     (fresh writable layer) so /tmp/ready is always absent on first
        #     boot. The delete matters only for the stop/start cycle.
        # Best-effort: log and continue if the delete fails.
        try:
            Path("/tmp/ready").unlink(missing_ok=True)  # noqa: S108 — healthcheck signal, not data store
        except OSError as exc:
            log.warning("failed to delete /tmp/ready on shutdown: %s", exc)
        await engine.dispose()


def main() -> None:
    """Sync entrypoint for ``python -m registry_state``.

    Reads configuration from environment variables:
      - ``REGISTRY_STATE_DB_URL``: SQLAlchemy async URL (default: local dev path).
      - ``REGISTRY_STATE_LOG_DIR``: Path to event-log directory (default: ``/var/lib/...``).

    Installs SIGTERM/SIGINT handlers that set the stop event for a clean shutdown.
    On Windows ``loop.add_signal_handler`` is unsupported for both signals and
    silently degrades — only ``SIGINT`` is honoured (via ``KeyboardInterrupt``
    raised inside ``asyncio.run`` and caught here).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db_url = os.environ.get("REGISTRY_STATE_DB_URL", _DEFAULT_DB_URL)
    log_dir = Path(os.environ.get("REGISTRY_STATE_LOG_DIR", _DEFAULT_LOG_DIR))

    stop_event = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
        await run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=SystemClock(),
            stop_event=stop_event,
        )

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


__all__ = ["main", "run_subscriber"]
