"""Injectable clock for time-controlled event emission (NFR-M6).

`Clock` Protocol + 3 concrete implementations:

- SystemClock   — real wall + monotonic via stdlib (production default).
- FrozenClock   — stationary; ``now()`` and ``monotonic_ns()`` return fixed values
                  for single-envelope construction in unit tests.
- TickingClock  — advances by ``tick_ns`` (default 1 ms) per call; use when a
                  test needs strict time-ordering across multiple envelopes
                  (e.g. UUIDv7 k-sortability verification).

``FROZEN_EPOCH`` is the canonical test-time baseline (2026-01-01T00:00:00Z).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

FROZEN_EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)


@runtime_checkable
class Clock(Protocol):
    """Dual-reading clock: wall-time for emitted_at, monotonic for ordering."""

    def now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...


class SystemClock:
    """Production default — delegates to stdlib ``datetime.now(UTC)`` + ``time.monotonic_ns()``."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class FrozenClock:
    """Test double: both `now()` and `monotonic_ns()` return fixed values, never advance.

    >>> fc = FrozenClock(42)
    >>> fc.monotonic_ns()
    42
    >>> fc.monotonic_ns()  # still 42 — frozen
    42
    """

    def __init__(self, mono_ns: int = 0, *, now: datetime | None = None) -> None:
        self._mono = mono_ns
        self._now = now if now is not None else FROZEN_EPOCH

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._mono


class TickingClock:
    """Test double: `now()` and `monotonic_ns()` advance by `tick_ns` per call.

    Default tick = 1 ms (1_000_000 ns) — guarantees strict time-ordering for
    UUIDv7 k-sortability tests. Both `now()` and `monotonic_ns()` return the
    CURRENT value and then advance, so the sequence they emit matches the
    call order.

    >>> tc = TickingClock()
    >>> tc.monotonic_ns()
    0
    >>> tc.monotonic_ns()
    1000000
    """

    def __init__(
        self,
        *,
        start_ns: int = 0,
        tick_ns: int = 1_000_000,
        start_now: datetime | None = None,
    ) -> None:
        self._mono = start_ns
        self._tick_ns = tick_ns
        self._now = start_now if start_now is not None else FROZEN_EPOCH
        # Microseconds per tick (1_000_000 ns = 1000 µs = 1 ms).
        self._tick_us = tick_ns // 1000

    def now(self) -> datetime:
        current = self._now
        self._now = current + timedelta(microseconds=self._tick_us)
        return current

    def monotonic_ns(self) -> int:
        current = self._mono
        self._mono += self._tick_ns
        return current


__all__ = [
    "FROZEN_EPOCH",
    "Clock",
    "FrozenClock",
    "SystemClock",
    "TickingClock",
]
