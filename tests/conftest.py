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
from datetime import UTC, datetime
from random import Random

import pytest
from events import FROZEN_EPOCH, FrozenClock, new_uuid7


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=0."""
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    """Factory producing a deterministic UUIDv7 sequence (Random seed 42)."""
    rng = Random(42)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


# Kept for any test that imports this directly.
_ = datetime(2026, 1, 1, tzinfo=UTC)  # sanity — matches FROZEN_EPOCH
