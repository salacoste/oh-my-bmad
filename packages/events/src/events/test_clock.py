"""Unit tests for events.clock — Clock protocol + SystemClock / FrozenClock / TickingClock.

AC-6 / Story 2.2: ~10 tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from events.clock import (
    FROZEN_EPOCH,
    Clock,
    FrozenClock,
    SystemClock,
    TickingClock,
)


class TestFrozenEpoch:
    def test_frozen_epoch_value(self) -> None:
        assert datetime(2026, 1, 1, tzinfo=UTC) == FROZEN_EPOCH


class TestProtocolRuntimeCheckable:
    def test_system_clock_is_clock(self) -> None:
        assert isinstance(SystemClock(), Clock)

    def test_frozen_clock_is_clock(self) -> None:
        assert isinstance(FrozenClock(), Clock)

    def test_ticking_clock_is_clock(self) -> None:
        assert isinstance(TickingClock(), Clock)


class TestSystemClock:
    def test_now_returns_utc_aware(self) -> None:
        dt = SystemClock().now()
        assert dt.tzinfo is UTC

    def test_monotonic_ns_returns_int(self) -> None:
        assert isinstance(SystemClock().monotonic_ns(), int)

    def test_monotonic_ns_non_decreasing(self) -> None:
        sc = SystemClock()
        a = sc.monotonic_ns()
        b = sc.monotonic_ns()
        assert b >= a


class TestFrozenClock:
    def test_defaults_now_is_frozen_epoch(self) -> None:
        fc = FrozenClock()
        assert fc.now() == FROZEN_EPOCH

    def test_defaults_monotonic_ns_is_zero(self) -> None:
        fc = FrozenClock()
        assert fc.monotonic_ns() == 0

    def test_custom_mono_ns(self) -> None:
        fc = FrozenClock(42)
        assert fc.monotonic_ns() == 42

    def test_stationary_now_never_advances(self) -> None:
        fc = FrozenClock()
        values = [fc.now() for _ in range(3)]
        assert values[0] == values[1] == values[2]

    def test_stationary_monotonic_ns_never_advances(self) -> None:
        fc = FrozenClock(99)
        values = [fc.monotonic_ns() for _ in range(3)]
        assert values[0] == values[1] == values[2] == 99

    def test_custom_now_datetime(self) -> None:
        custom = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        fc = FrozenClock(now=custom)
        assert fc.now() == custom


class TestTickingClock:
    def test_default_monotonic_starts_at_zero(self) -> None:
        tc = TickingClock()
        assert tc.monotonic_ns() == 0

    def test_default_tick_is_1ms(self) -> None:
        tc = TickingClock()
        assert tc.monotonic_ns() == 0
        assert tc.monotonic_ns() == 1_000_000
        assert tc.monotonic_ns() == 2_000_000

    def test_custom_tick_ns(self) -> None:
        tc = TickingClock(tick_ns=500)
        assert tc.monotonic_ns() == 0
        assert tc.monotonic_ns() == 500

    def test_now_strictly_increasing(self) -> None:
        tc = TickingClock()
        times = [tc.now() for _ in range(5)]
        for i in range(1, len(times)):
            assert times[i] > times[i - 1]
