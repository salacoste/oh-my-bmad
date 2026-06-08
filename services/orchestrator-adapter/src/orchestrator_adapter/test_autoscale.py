"""Tests for AutoscaleController core logic (FC-P6-1 / Story P8-FC2)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from events.clock import TickingClock
from events.event_log_writer import InMemoryEventLogWriter
from events.payloads import PoolScaledPayload

from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.domain.autoscale import AutoscaleController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    enabled: bool = True,
    min_count: int = 1,
    max_count: int = 5,
    up_threshold: int = 3,
    down_threshold: int = 2,
    poll_interval_s: float = 30.0,
) -> OrchestratorSettings:
    """Build an ``OrchestratorSettings`` with autoscale fields."""
    return OrchestratorSettings(
        autoscale_enabled=enabled,
        autoscale_min=min_count,
        autoscale_max=max_count,
        autoscale_up_threshold=up_threshold,
        autoscale_down_threshold=down_threshold,
        autoscale_poll_interval_s=poll_interval_s,
    )


def _make_controller(
    settings: OrchestratorSettings | None = None,
    *,
    clock: Any = None,
    event_writer: Any = None,
    pending: int = 0,
    idle: int = 0,
) -> AutoscaleController:
    """Build a controller with stubbed query methods."""
    if settings is None:
        settings = _settings()

    log = structlog.get_logger("test_autoscale")
    ctrl = AutoscaleController(
        settings,
        log=log,
        event_writer=event_writer,
        clock=clock,
    )
    # Override query methods to return fixed counts.
    ctrl._query_pending_count = AsyncMock(return_value=pending)  # type: ignore[attr-defined]
    ctrl._query_idle_count = AsyncMock(return_value=idle)  # type: ignore[attr-defined]
    return ctrl


@dataclass
class _FakeMcpClientGroup:
    """Minimal stand-in satisfying McpClientGroupProto."""

    task_registry: object | None = None


# ---------------------------------------------------------------------------
# TestAutoscaleDisabled
# ---------------------------------------------------------------------------


class TestAutoscaleDisabled:
    """poll() is a no-op when autoscale_enabled=False."""

    @pytest.mark.asyncio
    async def test_poll_returns_immediately_when_disabled(self) -> None:
        ctrl = _make_controller(_settings(enabled=False), pending=10, idle=5)
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        assert ctrl.current_count == 1  # stays at min

    @pytest.mark.asyncio
    async def test_query_methods_not_called_when_disabled(self) -> None:
        ctrl = _make_controller(_settings(enabled=False), pending=10)
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        ctrl._query_pending_count.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# TestScaleUp
# ---------------------------------------------------------------------------


class TestScaleUp:
    """Scale up when pending_count > up_threshold."""

    @pytest.mark.asyncio
    async def test_scales_up_on_queue_depth(self) -> None:
        ctrl = _make_controller(_settings(up_threshold=3), pending=5, idle=0)
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2  # 1 -> 2

    @pytest.mark.asyncio
    async def test_does_not_exceed_max(self) -> None:
        ctrl = _make_controller(
            _settings(max_count=2, up_threshold=1),
            pending=10,
            idle=0,
        )
        mcp = _FakeMcpClientGroup()
        # First poll: 1 -> 2
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2
        # Second poll: already at max, no change.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

    @pytest.mark.asyncio
    async def test_emits_pool_scaled_event(self) -> None:
        writer = InMemoryEventLogWriter()
        clock = TickingClock()
        ctrl = _make_controller(
            _settings(up_threshold=2),
            pending=5,
            idle=0,
            event_writer=writer,
            clock=clock,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)

        assert len(writer.envelopes) == 1
        env = writer.envelopes[0]
        assert env.type == "pool.scaled"
        payload = PoolScaledPayload.model_validate(env.payload)
        assert payload.old_count == 1
        assert payload.new_count == 2
        assert payload.trigger_reason == "queue_depth_exceeded"

    @pytest.mark.asyncio
    async def test_respects_cooldown(self) -> None:
        """No double-scale within the same poll interval."""
        ctrl = _make_controller(
            _settings(up_threshold=1, poll_interval_s=100.0),
            pending=10,
            idle=0,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2
        # Second poll within cooldown window — should NOT scale.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

    @pytest.mark.asyncio
    async def test_scales_up_multiple_steps_over_multiple_polls(self) -> None:
        """Multiple polls can step up one at a time if cooldown elapses."""
        # Use a very short poll interval so cooldown passes between polls.
        ctrl = _make_controller(
            _settings(up_threshold=1, poll_interval_s=0.001),
            pending=10,
            idle=0,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2
        # Manually reset last_scale_time to simulate elapsed cooldown.
        ctrl._last_scale_time = 0.0
        await ctrl.poll(mcp)
        assert ctrl.current_count == 3

    @pytest.mark.asyncio
    async def test_no_scale_up_when_at_threshold(self) -> None:
        """pending_count == up_threshold should NOT trigger scale up."""
        ctrl = _make_controller(_settings(up_threshold=3), pending=3, idle=0)
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        assert ctrl.current_count == 1  # unchanged


# ---------------------------------------------------------------------------
# TestScaleDown
# ---------------------------------------------------------------------------


class TestScaleDown:
    """Scale down after 2 consecutive polls with idle_excess > down_threshold."""

    @pytest.mark.asyncio
    async def test_scales_down_after_two_consecutive_polls(self) -> None:
        ctrl = _make_controller(
            _settings(min_count=1, max_count=5, down_threshold=2),
            pending=0,
            idle=3,
        )
        mcp = _FakeMcpClientGroup()
        # Simulate already at count 3.
        ctrl._current_count = 3

        # First poll: idle_excess_count becomes 1, no scale yet.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 3

        # Second poll: consecutive count reaches 2, scale down.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

    @pytest.mark.asyncio
    async def test_does_not_go_below_min(self) -> None:
        ctrl = _make_controller(
            _settings(min_count=2, down_threshold=1),
            pending=0,
            idle=5,
        )
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 3

        # First poll: idle_excess_count -> 1
        await ctrl.poll(mcp)
        assert ctrl.current_count == 3  # not yet

        # Second poll: scales down from 3 to 2 (min)
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

        # Third + fourth polls: target would be 1, but clamped to min=2
        ctrl._last_scale_time = 0.0
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2
        ctrl._last_scale_time = 0.0
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

    @pytest.mark.asyncio
    async def test_resets_idle_excess_count_after_scaling(self) -> None:
        ctrl = _make_controller(
            _settings(down_threshold=1),
            pending=0,
            idle=5,
        )
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 3

        # Two polls to trigger scale down.
        await ctrl.poll(mcp)
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2
        assert ctrl._idle_excess_count == 0

    @pytest.mark.asyncio
    async def test_emits_pool_scaled_event_on_scale_down(self) -> None:
        writer = InMemoryEventLogWriter()
        clock = TickingClock()
        ctrl = _make_controller(
            _settings(down_threshold=1),
            pending=0,
            idle=5,
            event_writer=writer,
            clock=clock,
        )
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 3

        await ctrl.poll(mcp)  # first poll — no event
        assert len(writer.envelopes) == 0
        await ctrl.poll(mcp)  # second poll — scale down
        assert len(writer.envelopes) == 1

        env = writer.envelopes[0]
        payload = PoolScaledPayload.model_validate(env.payload)
        assert payload.old_count == 3
        assert payload.new_count == 2
        assert payload.trigger_reason == "idle_workers_exceeded"

    @pytest.mark.asyncio
    async def test_idle_counter_resets_on_non_idle_poll(self) -> None:
        ctrl = _make_controller(
            _settings(down_threshold=2),
            pending=0,
            idle=5,
        )
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 3

        # First poll: idle excess detected.
        await ctrl.poll(mcp)
        assert ctrl._idle_excess_count == 1

        # Simulate non-idle: override query to return 0 idle.
        ctrl._query_idle_count = AsyncMock(return_value=0)  # type: ignore[attr-defined]
        await ctrl.poll(mcp)
        assert ctrl._idle_excess_count == 0

        # Back to idle: counter restarts from 0.
        ctrl._query_idle_count = AsyncMock(return_value=5)  # type: ignore[attr-defined]
        await ctrl.poll(mcp)
        assert ctrl._idle_excess_count == 1


# ---------------------------------------------------------------------------
# TestBoundsAndCooldown
# ---------------------------------------------------------------------------


class TestBoundsAndCooldown:
    """min/max clamping, cooldown enforcement, no-op on same count."""

    @pytest.mark.asyncio
    async def test_min_clamping(self) -> None:
        """Scale-to below min is clamped to min, and no-op if already at min."""
        ctrl = _make_controller(_settings(min_count=3), pending=0, idle=10)
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 3

        # Idle excess triggers scale down, but target clamped to min=3.
        await ctrl.poll(mcp)  # idle_excess_count -> 1
        await ctrl.poll(mcp)  # would scale to 2, clamped to 3, no-op
        assert ctrl.current_count == 3

    @pytest.mark.asyncio
    async def test_max_clamping(self) -> None:
        ctrl = _make_controller(_settings(max_count=2), pending=10, idle=0)
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 2
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2  # clamped, no change

    @pytest.mark.asyncio
    async def test_cooldown_blocks_rapid_scale(self) -> None:
        ctrl = _make_controller(
            _settings(up_threshold=1, poll_interval_s=999.0),
            pending=10,
            idle=0,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        count_after_first = ctrl.current_count

        # Rapid second poll — cooldown blocks.
        await ctrl.poll(mcp)
        assert ctrl.current_count == count_after_first

    @pytest.mark.asyncio
    async def test_cooldown_allows_after_interval(self) -> None:
        ctrl = _make_controller(
            _settings(up_threshold=1, poll_interval_s=0.01),
            pending=10,
            idle=0,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        count_after_first = ctrl.current_count

        # Wait for cooldown to elapse.
        await asyncio.sleep(0.05)

        await ctrl.poll(mcp)
        assert ctrl.current_count > count_after_first

    @pytest.mark.asyncio
    async def test_no_scale_if_target_equals_current(self) -> None:
        writer = InMemoryEventLogWriter()
        clock = TickingClock()
        ctrl = _make_controller(
            _settings(min_count=2, max_count=5),
            pending=10,
            idle=0,
            event_writer=writer,
            clock=clock,
        )
        mcp = _FakeMcpClientGroup()
        ctrl._current_count = 5  # already at max

        await ctrl.poll(mcp)
        # pending > up_threshold but current == max, so clamped target == current
        # => no scale, no event.
        assert ctrl.current_count == 5
        assert len(writer.envelopes) == 0

    @pytest.mark.asyncio
    async def test_no_event_without_writer_or_clock(self) -> None:
        """Event emission is silently skipped when writer or clock is missing."""
        ctrl = _make_controller(
            _settings(up_threshold=1),
            pending=10,
            idle=0,
            event_writer=None,
            clock=None,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)
        # Should scale up (log-only) without error.
        assert ctrl.current_count == 2

    @pytest.mark.asyncio
    async def test_initial_count_matches_min(self) -> None:
        ctrl = _make_controller(_settings(min_count=3, max_count=10))
        assert ctrl.current_count == 3

    @pytest.mark.asyncio
    async def test_scale_up_event_actor_is_system(self) -> None:
        writer = InMemoryEventLogWriter()
        clock = TickingClock()
        ctrl = _make_controller(
            _settings(up_threshold=1),
            pending=10,
            idle=0,
            event_writer=writer,
            clock=clock,
        )
        mcp = _FakeMcpClientGroup()
        await ctrl.poll(mcp)

        assert len(writer.envelopes) == 1
        env = writer.envelopes[0]
        assert env.actor.kind == "system"
        assert env.actor.id == "autoscale-controller"

    @pytest.mark.asyncio
    async def test_scale_down_after_up_then_idle(self) -> None:
        """Full lifecycle: scale up, then work drains, scale back down."""
        writer = InMemoryEventLogWriter()
        clock = TickingClock()
        ctrl = _make_controller(
            _settings(up_threshold=2, down_threshold=2),
            pending=5,
            idle=0,
            event_writer=writer,
            clock=clock,
        )
        mcp = _FakeMcpClientGroup()

        # Scale up: 1 -> 2.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2

        # Work drains: pending=0, idle=5.
        ctrl._query_pending_count = AsyncMock(return_value=0)  # type: ignore[attr-defined]
        ctrl._query_idle_count = AsyncMock(return_value=5)  # type: ignore[attr-defined]
        ctrl._last_scale_time = 0.0  # reset cooldown

        # Two consecutive idle polls.
        await ctrl.poll(mcp)
        assert ctrl.current_count == 2  # first poll: counter=1
        await ctrl.poll(mcp)
        assert ctrl.current_count == 1  # second poll: scale down

        assert len(writer.envelopes) == 2
        down_payload = PoolScaledPayload.model_validate(writer.envelopes[1].payload)
        assert down_payload.trigger_reason == "idle_workers_exceeded"
