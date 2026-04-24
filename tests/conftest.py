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
"""

from __future__ import annotations

from collections.abc import Callable
from random import Random

import pytest
from events import FROZEN_EPOCH, FrozenClock, new_uuid7


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
