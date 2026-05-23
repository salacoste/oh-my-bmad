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

import copy
import logging
from collections.abc import Callable, Iterator
from random import Random
from typing import Any

import pytest
import structlog
from events import FROZEN_EPOCH, FrozenClock, new_uuid7

from tests._log_capture import CapturedLogList

# NOTE (Story 8.7.6 PP1 — relocation): the aiosqlite daemon-thread drain
# previously lived here as a session-scoped autouse fixture. It has been
# relocated to repo-root ``conftest.py`` as a ``pytest_sessionfinish`` hook so
# it applies to ALL ``testpaths`` (services/, packages/, mcp-servers/, tests/)
# — not just collections under tests/. See repo-root conftest.py for details.


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

    def _capture(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event_dict))
        raise structlog.DropEvent

    return _capture


def _capture_structlog_generator() -> Iterator[CapturedLogList]:
    """Generator backing ``capture_structlog`` — also drivable directly.

    Exposed (single-underscore module-private) so tests can drive the
    teardown branch programmatically with ``next(gen)`` / ``next(gen)``
    rather than relying on pytest's fixture machinery — that's the only
    way to assert the round-trip restoration contract from inside a single
    test scope (see AC-9).
    """
    # Lazy import: a contributor running ``tests/unit/...`` without the
    # secret-hygiene venv shouldn't be blocked at collection time.
    from secret_hygiene.sanitizer import redact_secrets

    captured = CapturedLogList()
    # Snapshot the prior config IF structlog was already configured. Deep-copy
    # the processors list so a later structlog mutation cannot retroactively
    # alter our snapshot (the dict ``get_config()`` returns shares list
    # identity with structlog internals).
    was_configured = structlog.is_configured()
    snapshot: dict[str, Any] | None = (
        copy.deepcopy(dict(structlog.get_config())) if was_configured else None
    )

    try:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                redact_secrets,  # MUST run before capture
                _list_capture_processor(captured),  # terminal: append + DropEvent
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
            cache_logger_on_first_use=False,
        )
        yield captured
    finally:
        # ``reset_defaults`` first so we never leave the test-installed chain
        # active if a later ``configure(**snapshot)`` raises. Wrap the restore
        # itself so a snapshot-replay failure can't mask the original test
        # exception (M1 / L24).
        structlog.reset_defaults()
        if was_configured and snapshot is not None:
            try:
                structlog.configure(**snapshot)
            except Exception:  # noqa: BLE001 — defensive; preserve original test error
                structlog.reset_defaults()


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

    Concurrency
    -----------
    ``structlog.configure()`` mutates process-global state. This fixture is
    safe under pytest-xdist (workers are separate processes) but is NOT safe
    under in-worker parallelism (pytest-parallel, xdist ``--dist loadgroup``
    co-residency). Tests using this fixture MUST run serially within a worker.

    Caveats
    -------
    The fixture chain does NOT include ``structlog.processors.format_exc_info``
    — calls to ``log.exception(...)`` inside captured tests will produce only
    the basic fields (``event``, ``level``, ``timestamp``) plus whatever the
    caller passes as kwargs. Wire ``format_exc_info`` explicitly in the test
    if you need exception rendering.
    """
    yield from _capture_structlog_generator()
