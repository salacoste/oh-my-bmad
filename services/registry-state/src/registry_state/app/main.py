"""Subscriber loop entrypoint for registry-state (Story 2.5, AC-6/7/8).

``run_subscriber`` is the long-lived async loop that:
  1. Runs ``writer.recover()`` to trim trailing partial lines (Story 2.4).
  2. Computes a startup cursor from ``MAX(events.emitted_at_monotonic_ns)``.
  3. Replays all ``*.jsonl`` files in *base_dir* sorted chronologically,
     filtering events already in the DB (``emitted_at_monotonic_ns <= cursor``).
  4. Tails the current-day file in a 100ms poll loop until ``stop_event`` fires.

``main()`` is the sync wrapper for ``python -m registry_state``:
  - reads env vars (``REGISTRY_STATE_DB_URL``, ``REGISTRY_STATE_LOG_DIR``),
  - installs SIGTERM/SIGINT → ``stop_event.set()``,
  - calls ``asyncio.run(run_subscriber(...))``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from events.clock import Clock, SystemClock

from registry_state.adapters.event_log import EventLogWriter, current_day_path, read_log_lines
from registry_state.adapters.sqlite_store import create_engine, get_session
from registry_state.domain.event_types import (  # noqa: F401 — side-effect: register() calls
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
)
from registry_state.domain.handlers import register_default_handlers
from registry_state.domain.materializer import Materializer

log = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_DB_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"


async def _replay_all(base_dir: Path, materializer: Materializer, cursor_ns: int) -> int:
    """Replay all ``*.jsonl`` files in *base_dir* in chronological order.

    Files are sorted by filename (ISO-date ``YYYY-MM-DD.jsonl`` → lex-sort =
    chronological order).  Only events with
    ``emitted_at_monotonic_ns > cursor_ns`` are applied; earlier events would
    be no-ops via the PK conflict anyway, but we skip them eagerly for speed.

    Returns total new events applied across all files.
    """
    total = 0
    for path in sorted(base_dir.glob("*.jsonl")):
        if not path.exists():
            continue
        envelopes = [env for env in read_log_lines(path) if env.emitted_at_monotonic_ns > cursor_ns]
        if envelopes:
            total += await materializer.apply_many(envelopes)
    return total


async def run_subscriber(
    *,
    base_dir: Path,
    db_url: str,
    clock: Clock,
    poll_interval_s: float = 0.1,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-lived subscriber loop: tail the JSONL event log → materialize SQLite state.

    Args:
        base_dir:        Root directory containing ``YYYY-MM-DD.jsonl`` event-log files.
        db_url:          SQLAlchemy async URL for the registry-state SQLite store.
        clock:           Injected clock (Story 2.2 discipline) for UTC now + monotonic_ns.
        poll_interval_s: How long to sleep between tail-loop iterations (default 100ms).
        stop_event:      Optional asyncio.Event; set it to request a clean shutdown.
                         If ``None``, a local event is created (useful in tests).
    """
    stop = stop_event if stop_event is not None else asyncio.Event()
    engine = create_engine(db_url)
    writer = EventLogWriter(base_dir=base_dir, clock=clock)
    try:
        # Story 2.4 startup contract: trim trailing partial lines across all *.jsonl.
        await writer.recover()

        session_maker = get_session(engine)
        materializer = Materializer(session_maker=session_maker)
        register_default_handlers(materializer)

        # Compute startup cursor from events already in the DB.
        async with session_maker() as session:
            cursor_ns = await materializer.cursor(session)

        # Startup replay: process all historical *.jsonl files in date order.
        applied = await _replay_all(base_dir, materializer, cursor_ns)
        if applied:
            log.info("startup replay: applied %d new events", applied)

        # Tail loop: poll current-day file until stop_event fires.
        while not stop.is_set():
            today_path = current_day_path(base_dir, clock.now())
            if today_path.exists():
                async with session_maker() as session:
                    cursor_ns = await materializer.cursor(session)
                to_apply = [
                    env
                    for env in read_log_lines(today_path)
                    if env.emitted_at_monotonic_ns > cursor_ns
                ]
                if to_apply:
                    await materializer.apply_many(to_apply)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
    finally:
        await writer.close()
        await engine.dispose()


def main() -> None:
    """Sync entrypoint for ``python -m registry_state``.

    Reads configuration from environment variables:
      - ``REGISTRY_STATE_DB_URL``: SQLAlchemy async URL (default: local dev path).
      - ``REGISTRY_STATE_LOG_DIR``: Path to event-log directory (default: ``/var/lib/...``).

    Installs SIGTERM/SIGINT handlers that set the stop event for a clean shutdown.
    On Windows (no SIGTERM), only KeyboardInterrupt is caught.
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
        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is not None:
            loop.add_signal_handler(sigterm, stop_event.set)
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        await run_subscriber(
            base_dir=log_dir,
            db_url=db_url,
            clock=SystemClock(),
            stop_event=stop_event,
        )

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


__all__ = ["main", "run_subscriber"]
