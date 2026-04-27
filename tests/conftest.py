"""Top-level pytest fixtures — cross-cutting (clock, UUIDv7) delivered by Story 2.2.

Usage example::

    def test_deterministic_event(fixed_clock, seeded_uuid7):
        eid = seeded_uuid7()  # deterministic UUIDv7 str
        envelope = EventEnvelope(
            event_id=f"e-{eid}",
            emitted_at=fixed_clock.now(),
            emitted_at_monotonic_ns=fixed_clock.monotonic_ns(),
            ...
        )

Story 2.17 adds the ``capture_structlog`` fixture for FR43 / NFR-S1
integration tests — installs ``secret_hygiene.sanitizer.redact_secrets`` ahead
of a list-capture terminal processor and yields the captured records.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from random import Random
from typing import Any

import pytest
import structlog
from events import FROZEN_EPOCH, FrozenClock, new_uuid7
from secret_hygiene.sanitizer import redact_secrets

from tests._log_capture import CapturedLogList


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=0."""
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    """Factory producing a deterministic, time-ordered UUIDv7 sequence.

    Uses ``TickingClock`` + ``Random(42)`` so each call advances the
    timestamp by 1 ms — consecutive UUIDs are k-sortable. Across pytest
    runs the first N calls always produce the same N UUIDs (both clock
    and rng are freshly constructed per test via the fixture scope).
    """
    from events import TickingClock

    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# Story 2.17 — log-capture harness (FR43 / NFR-S1)
# ---------------------------------------------------------------------------


def _list_capture_processor(
    captured: CapturedLogList,
) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    """Build a terminal structlog processor that snapshots & drops the event.

    Appends a *copy* of ``event_dict`` to *captured* (so later mutation by
    structlog doesn't bleed into the captured snapshot) and raises
    ``structlog.DropEvent`` so no downstream renderer ever runs — keeps test
    stderr clean.
    """

    def _proc(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event_dict))
        raise structlog.DropEvent

    return _proc


@pytest.fixture
def capture_structlog() -> Iterator[CapturedLogList]:
    """Install ``redact_secrets`` ahead of a list-capture terminal processor.

    Yields the list of captured event_dict records. On teardown, restores the
    prior structlog configuration via the recorded snapshot (or
    ``structlog.reset_defaults()`` if structlog was previously unconfigured).

    Function-scoped (default) — each test gets a fresh, isolated processor
    chain. ``cache_logger_on_first_use=False`` is REQUIRED so loggers retrieved
    inside the test pick up the new chain (otherwise the structlog cache pins
    the previous configuration).
    """
    captured = CapturedLogList()
    # ``get_config`` returns a dict suitable for re-passing to ``configure``.
    snapshot: dict[str, Any] | None = (
        dict(structlog.get_config()) if structlog.is_configured() else None
    )

    try:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                redact_secrets,  # MUST run before capture
                _list_capture_processor(captured),  # terminal: append + DropEvent
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
            cache_logger_on_first_use=False,
        )
        yield captured
    finally:
        structlog.reset_defaults()
        if snapshot is not None:
            structlog.configure(**snapshot)
