"""Tests for registry_state.domain.failure_detection — Story 2.10 (AC-7).

Four test classes covering ≥18 tests:

- TestPayloadModels        (4 tests) — pydantic frozen+strict+extra=forbid contracts.
- TestEmissionFunctions    (6 tests) — emit_* writes a valid envelope to the log.
- TestHeartbeatMonitor     (6 tests) — strict ``> 2 × interval`` overdue boundary.
- TestSinkFailureTracker   (5 tests) — ``>= threshold`` emit gate + reset semantics.

Local fixtures (``fixed_clock``, ``writer``, ``read_envelopes``) match the
co-located convention from ``test_event_log.py`` / ``test_handlers.py``
(no new ``conftest.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from events import (
    FROZEN_EPOCH,
    EventEnvelope,
    FrozenClock,
)
from events.schema_registry import register
from pydantic import ValidationError

from registry_state.adapters.event_log import EventLogWriter, current_day_path, read_log_lines
from registry_state.domain.event_types import (
    ServiceCrashedPayload,
    SessionHeartbeatTimeoutPayload,
    SinkDeliveryFailedPayload,
    TaskStopRequestedPayload,
)
from registry_state.domain.failure_detection import (
    HeartbeatMonitor,
    SinkFailureTracker,
    emit_service_crashed,
    emit_session_heartbeat_timeout,
    emit_sink_delivery_failed,
    emit_task_stop_requested,
)

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_failure_detection_types_registered() -> Generator[None, None, None]:
    """Re-register the 4 failure-detection types before each test.

    Mirrors the autouse fixture in ``test_handlers.py`` — ``test_event_log.py``
    has a ``unregister_all()`` teardown that may run between cross-file tests
    in the full suite, so we re-register idempotently here. ``register()`` is
    a no-op when the same model is registered for the same key.
    """
    register("service.crashed", "1.0.0", ServiceCrashedPayload)
    register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
    register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
    register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
    yield


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


# ---------------------------------------------------------------------------
# AdvancingClock — manual-tick test double for HeartbeatMonitor tests.
#
# HeartbeatMonitor uses ``clock.now()`` for both heartbeat recording AND
# overdue checks. ``FrozenClock`` is too rigid (now() never changes); the
# upstream ``TickingClock`` advances on every call which makes per-test
# assertions brittle. AdvancingClock is the simplest test double that
# advances only when the test calls ``advance(seconds)``.
# ---------------------------------------------------------------------------


class _AdvancingClock:
    """Test double: ``now()`` only advances when ``advance()`` is called."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return int(self._now.timestamp() * 1_000_000_000)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Helper: read the envelopes a writer just appended to its base_dir.
# ---------------------------------------------------------------------------


def _read_envelopes(base_dir: Path, when: datetime) -> list[EventEnvelope]:
    return list(read_log_lines(current_day_path(base_dir, when)))


# ===========================================================================
# TestPayloadModels — AC-1 (pydantic discipline)
# ===========================================================================


class TestPayloadModels:
    def test_service_crashed_payload_validates_correctly(self) -> None:
        p = ServiceCrashedPayload(service="worker-wrapper", exit_code=137)
        assert p.service == "worker-wrapper"
        assert p.exit_code == 137
        # frozen=True
        with pytest.raises(ValidationError):
            p.service = "other"
        # extra="forbid"
        with pytest.raises(ValidationError):
            ServiceCrashedPayload(
                service="x",
                exit_code=1,
                surplus="boom",  # type: ignore[call-arg]
            )

    def test_session_heartbeat_timeout_payload_validates_correctly(self) -> None:
        last_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
        p = SessionHeartbeatTimeoutPayload(
            session_id="s-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b",
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
            last_heartbeat_at=last_at,
            timeout_threshold_s=60.0,
        )
        assert p.last_heartbeat_at == last_at
        assert p.timeout_threshold_s == 60.0
        # extra="forbid"
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id="s-x",
                task_id="t-x",
                last_heartbeat_at=last_at,
                timeout_threshold_s=1.0,
                surplus=True,  # type: ignore[call-arg]
            )

    def test_sink_delivery_failed_payload_validates_correctly(self) -> None:
        p = SinkDeliveryFailedPayload(
            sink_name="telegram",
            consecutive_failures=3,
            last_error="HTTP 503",
        )
        assert p.sink_name == "telegram"
        assert p.consecutive_failures == 3
        assert p.last_error == "HTTP 503"
        # last_error optional with default None
        p2 = SinkDeliveryFailedPayload(sink_name="telegram", consecutive_failures=4)
        assert p2.last_error is None
        # extra="forbid"
        with pytest.raises(ValidationError):
            SinkDeliveryFailedPayload(
                sink_name="x",
                consecutive_failures=1,
                surplus="leak",  # type: ignore[call-arg]
            )

    def test_task_stop_requested_payload_validates_correctly(self) -> None:
        p = TaskStopRequestedPayload(
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b",
            actor_id="telegram:12345678",
        )
        assert p.actor_id == "telegram:12345678"
        # frozen=True
        with pytest.raises(ValidationError):
            p.actor_id = "console"
        # extra="forbid"
        with pytest.raises(ValidationError):
            TaskStopRequestedPayload(
                task_id="t-x",
                actor_id="console",
                surplus=1,  # type: ignore[call-arg]
            )


# ===========================================================================
# TestEmissionFunctions — AC-3a
# ===========================================================================


class TestEmissionFunctions:
    @pytest.mark.asyncio
    async def test_emit_service_crashed_writes_envelope_to_log(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_service_crashed(
            writer,
            clock=fixed_clock,
            service="worker-wrapper",
            exit_code=137,
        )
        await writer.close()

        recovered = _read_envelopes(tmp_path, fixed_clock.now())
        assert len(recovered) == 1
        assert recovered[0].event_id == env.event_id

    @pytest.mark.asyncio
    async def test_emit_service_crashed_envelope_has_correct_type_and_payload(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_service_crashed(
            writer,
            clock=fixed_clock,
            service="registry-api",
            exit_code=1,
            actor_id="supervisor",
        )
        await writer.close()
        assert env.type == "service.crashed"
        assert env.schema_version == "1.0.0"
        assert env.actor.kind == "system"
        assert env.actor.id == "supervisor"
        assert isinstance(env.payload, ServiceCrashedPayload)
        assert env.payload.service == "registry-api"
        assert env.payload.exit_code == 1

    @pytest.mark.asyncio
    async def test_emit_session_heartbeat_timeout_writes_envelope(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        last_at = datetime(2026, 4, 24, 11, 59, 0, tzinfo=UTC)
        env = await emit_session_heartbeat_timeout(
            writer,
            clock=fixed_clock,
            session_id="s-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b",
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
            last_heartbeat_at=last_at,
            timeout_threshold_s=60.0,
        )
        await writer.close()

        recovered = _read_envelopes(tmp_path, fixed_clock.now())
        assert len(recovered) == 1
        assert recovered[0].type == "session.heartbeat_timeout"
        assert env.type == "session.heartbeat_timeout"
        assert isinstance(env.payload, SessionHeartbeatTimeoutPayload)
        assert env.payload.last_heartbeat_at == last_at
        assert env.payload.timeout_threshold_s == 60.0

    @pytest.mark.asyncio
    async def test_emit_sink_delivery_failed_writes_envelope(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_sink_delivery_failed(
            writer,
            clock=fixed_clock,
            sink_name="telegram",
            consecutive_failures=3,
            last_error="HTTP 502 from upstream",
        )
        await writer.close()

        recovered = _read_envelopes(tmp_path, fixed_clock.now())
        assert len(recovered) == 1
        assert recovered[0].type == "sink.delivery_failed"
        assert isinstance(env.payload, SinkDeliveryFailedPayload)
        assert env.payload.sink_name == "telegram"
        assert env.payload.consecutive_failures == 3
        assert env.payload.last_error == "HTTP 502 from upstream"

    @pytest.mark.asyncio
    async def test_emit_task_stop_requested_writes_envelope(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b",
            actor_id="console",
        )
        await writer.close()

        recovered = _read_envelopes(tmp_path, fixed_clock.now())
        assert len(recovered) == 1
        assert recovered[0].type == "task.stop_requested"
        assert env.type == "task.stop_requested"
        assert isinstance(env.payload, TaskStopRequestedPayload)
        assert env.payload.task_id == "t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b"

    @pytest.mark.asyncio
    async def test_emit_task_stop_requested_actor_id_preserved(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b",
            actor_id="telegram:99887766",
        )
        await writer.close()
        # actor_id flows BOTH to the envelope's Actor.id AND the payload's
        # actor_id field — TaskStopRequestedPayload.actor_id is the operator
        # identity carried with the typed payload itself, while envelope.actor
        # is the standard envelope-level emitter identity.
        assert env.actor.id == "telegram:99887766"
        assert isinstance(env.payload, TaskStopRequestedPayload)
        assert env.payload.actor_id == "telegram:99887766"


# ===========================================================================
# TestHeartbeatMonitor — AC-3b
# ===========================================================================


class TestHeartbeatMonitor:
    def test_heartbeat_monitor_no_overdue_when_fresh(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        # zero elapsed time → not overdue
        assert monitor.overdue_sessions() == []

    def test_heartbeat_monitor_session_overdue_after_2x_interval(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        last_at = clock.now()
        # > 2 × 10 s — must be overdue
        clock.advance(20.001)
        overdue = monitor.overdue_sessions()
        assert len(overdue) == 1
        assert overdue[0][0] == "s-1"
        assert overdue[0][1] == last_at

    def test_heartbeat_monitor_refreshed_session_not_overdue(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        clock.advance(15.0)
        # refresh resets the last-seen timestamp
        monitor.record_heartbeat("s-1")
        clock.advance(5.0)
        # 5 s elapsed since refresh → far below 20 s threshold
        assert monitor.overdue_sessions() == []

    def test_heartbeat_monitor_remove_session_clears_tracking(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        monitor.remove_session("s-1")
        clock.advance(60.0)
        assert monitor.overdue_sessions() == []
        # idempotent
        monitor.remove_session("s-unknown")

    def test_heartbeat_monitor_multiple_sessions_independent(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-old")
        clock.advance(15.0)
        monitor.record_heartbeat("s-fresh")
        clock.advance(6.0)
        # s-old: 21 s elapsed → overdue (> 20 s)
        # s-fresh: 6 s elapsed → not overdue
        overdue = monitor.overdue_sessions()
        assert [sid for sid, _ in overdue] == ["s-old"]

    def test_heartbeat_monitor_boundary_exactly_2x_not_overdue(self) -> None:
        """At exactly 2 × interval the session is NOT yet overdue (strict ``>``)."""
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        clock.advance(20.0)  # exactly 2 × interval
        assert monitor.overdue_sessions() == []
        # Now nudge past the boundary
        clock.advance(0.001)
        overdue = monitor.overdue_sessions()
        assert [sid for sid, _ in overdue] == ["s-1"]


# ===========================================================================
# TestSinkFailureTracker — AC-3c
# ===========================================================================


class TestSinkFailureTracker:
    def test_sink_failure_tracker_no_emit_before_threshold(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "err 1")
        tracker.record_failure("telegram", "err 2")
        # 2 < 3
        assert tracker.should_emit("telegram") is False

    def test_sink_failure_tracker_emits_at_threshold(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram")
        tracker.record_failure("telegram")
        count = tracker.record_failure("telegram", "boom")
        assert count == 3
        assert tracker.should_emit("telegram") is True

    def test_sink_failure_tracker_success_resets_counter(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "boom")
        tracker.record_failure("telegram", "boom2")
        tracker.record_success("telegram")
        # counter back to 0; last_error cleared
        assert tracker.get_state("telegram") == (0, None)
        # one more failure → still below threshold
        tracker.record_failure("telegram", "fresh")
        assert tracker.should_emit("telegram") is False

    def test_sink_failure_tracker_last_error_preserved(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "first")
        tracker.record_failure("telegram", "second")
        # passing error=None preserves the prior most-recent error
        tracker.record_failure("telegram", None)
        count, last_error = tracker.get_state("telegram")
        assert count == 3
        assert last_error == "second"

    def test_sink_failure_tracker_independent_sinks(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=2)
        tracker.record_failure("telegram", "tg-err")
        tracker.record_failure("telegram", "tg-err2")
        tracker.record_failure("discord", "ds-err")
        assert tracker.should_emit("telegram") is True
        assert tracker.should_emit("discord") is False
        # success on telegram does not affect discord
        tracker.record_success("telegram")
        assert tracker.should_emit("telegram") is False
        assert tracker.get_state("discord") == (1, "ds-err")
