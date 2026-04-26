"""Tests for registry_state.domain.failure_detection — Story 2.10 (AC-7).

Originally shipped 21 tests in 4 classes. The post-1.0 review pass extends
this to ≥40 tests covering: edge-triggered emission, secret redaction,
clock regression, validator branches, distinct-ids-per-emission, EVENT_TYPES
membership, future-time heartbeats, registry idempotency, repr smoke, and
emission failure propagation.

Local fixtures (``fixed_clock``, ``writer``, ``read_envelopes``) match the
co-located convention from ``test_event_log.py`` / ``test_handlers.py``
(no new ``conftest.py``).
"""

from __future__ import annotations

import math
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from events import (
    FROZEN_EPOCH,
    EventEnvelope,
    FrozenClock,
)
from events import schema_registry as _schema_registry
from events.clock import Clock
from events.schema_registry import REGISTRY, register
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
    SinkFailureState,
    SinkFailureTracker,
    _redact_last_error,
    emit_service_crashed,
    emit_session_heartbeat_timeout,
    emit_sink_delivery_failed,
    emit_task_stop_requested,
)

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------

# Canonical UUIDv7-shaped IDs reused across tests (matching shape required by
# event_types.py validators).
_SESSION_ID = "s-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b"
_TASK_ID = "t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c"


@pytest.fixture(autouse=True)
def _ensure_failure_detection_types_registered() -> Generator[None, None, None]:
    """Snapshot the schema registry, ensure 2.10 types are registered, restore on teardown.

    Snapshotting + restoring isolates this test module from neighbours that
    call ``unregister_all()`` mid-session (e.g. ``test_event_log.py``).
    The 4 Story 2.10 types are re-registered idempotently before each test
    so payload validation always works.
    """
    # Snapshot current registry state before mutation. Story 2.1 guarantees
    # ``register()`` is idempotent for same-model rebinds, so re-registering
    # whatever was present is safe.
    snapshot = dict(REGISTRY)
    register("service.crashed", "1.0.0", ServiceCrashedPayload)
    register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
    register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
    register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
    try:
        yield
    finally:
        # Restore: clear current contents and replay the snapshot. Use the
        # registry's own ``unregister_all`` + ``register`` pair so the
        # ``EVENT_TYPES`` cache is rebuilt consistently.
        from events.schema_registry import unregister_all

        unregister_all()
        for (event_type, schema_version), model in snapshot.items():
            register(event_type, schema_version, model)


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
#
# Implements the ``Clock`` runtime-checkable Protocol so static checkers
# accept it where ``Clock`` is expected. Tracks ``_mono_ns`` independently
# (not derived from wall time) so monotonic readings match the wall-time
# advance count without needing ``time.time()``-derived seeds.
# ---------------------------------------------------------------------------


class _AdvancingClock(Clock):
    """Test double: ``now()`` only advances when ``advance()`` is called."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self._mono_ns = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._mono_ns

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono_ns += int(seconds * 1_000_000_000)


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

    def test_service_crashed_rejects_zero_exit_code(self) -> None:
        """exit_code=0 is a clean exit, not a crash; payload validator rejects."""
        with pytest.raises(ValidationError) as ei:
            ServiceCrashedPayload(service="worker-wrapper", exit_code=0)
        assert "exit_code" in str(ei.value)

    def test_service_crashed_rejects_empty_service(self) -> None:
        with pytest.raises(ValidationError):
            ServiceCrashedPayload(service="", exit_code=1)

    def test_service_crashed_rejects_oversized_service(self) -> None:
        with pytest.raises(ValidationError):
            ServiceCrashedPayload(service="x" * 129, exit_code=1)

    def test_session_heartbeat_timeout_payload_validates_correctly(self) -> None:
        last_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
        p = SessionHeartbeatTimeoutPayload(
            session_id=_SESSION_ID,
            task_id=_TASK_ID,
            last_heartbeat_at=last_at,
            timeout_threshold_s=60.0,
        )
        assert p.last_heartbeat_at == last_at
        assert p.timeout_threshold_s == 60.0
        # extra="forbid"
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id=_SESSION_ID,
                task_id=_TASK_ID,
                last_heartbeat_at=last_at,
                timeout_threshold_s=1.0,
                surplus=True,  # type: ignore[call-arg]
            )

    def test_session_heartbeat_timeout_rejects_naive_datetime(self) -> None:
        """``last_heartbeat_at`` is AwareDatetime — naive is rejected."""
        naive = datetime(2026, 4, 24, 12, 0, 0)  # no tzinfo
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id=_SESSION_ID,
                task_id=_TASK_ID,
                last_heartbeat_at=naive,
                timeout_threshold_s=60.0,
            )

    def test_session_heartbeat_timeout_rejects_inf_threshold(self) -> None:
        last_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id=_SESSION_ID,
                task_id=_TASK_ID,
                last_heartbeat_at=last_at,
                timeout_threshold_s=float("inf"),
            )

    def test_session_heartbeat_timeout_rejects_malformed_session_id(self) -> None:
        last_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id="s-not-a-uuid",
                task_id=_TASK_ID,
                last_heartbeat_at=last_at,
                timeout_threshold_s=60.0,
            )

    def test_session_heartbeat_timeout_rejects_malformed_task_id(self) -> None:
        last_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError):
            SessionHeartbeatTimeoutPayload(
                session_id=_SESSION_ID,
                task_id="bogus",
                last_heartbeat_at=last_at,
                timeout_threshold_s=60.0,
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

    def test_sink_delivery_failed_rejects_zero_consecutive_failures(self) -> None:
        with pytest.raises(ValidationError):
            SinkDeliveryFailedPayload(sink_name="telegram", consecutive_failures=0)

    def test_sink_delivery_failed_rejects_oversized_last_error(self) -> None:
        with pytest.raises(ValidationError):
            SinkDeliveryFailedPayload(
                sink_name="telegram",
                consecutive_failures=3,
                last_error="x" * 4097,
            )

    def test_task_stop_requested_payload_validates_correctly(self) -> None:
        p = TaskStopRequestedPayload(
            task_id=_TASK_ID,
            actor_id="telegram:12345678",
        )
        assert p.actor_id == "telegram:12345678"
        # frozen=True
        with pytest.raises(ValidationError):
            p.actor_id = "console"
        # extra="forbid"
        with pytest.raises(ValidationError):
            TaskStopRequestedPayload(
                task_id=_TASK_ID,
                actor_id="console",
                surplus=1,  # type: ignore[call-arg]
            )

    def test_task_stop_requested_rejects_malformed_task_id(self) -> None:
        with pytest.raises(ValidationError):
            TaskStopRequestedPayload(task_id="not-a-task", actor_id="console")

    def test_task_stop_requested_rejects_empty_actor(self) -> None:
        with pytest.raises(ValidationError):
            TaskStopRequestedPayload(task_id=_TASK_ID, actor_id="")


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
    async def test_emit_service_crashed_actor_id_defaults_to_service(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """The crashing service is the actor; registry-state is only the detector."""
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_service_crashed(
            writer,
            clock=fixed_clock,
            service="worker-wrapper",
            exit_code=1,
        )
        await writer.close()
        assert env.actor.id == "worker-wrapper"

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
            session_id=_SESSION_ID,
            task_id=_TASK_ID,
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
    async def test_emit_sink_delivery_failed_redacts_telegram_token(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_sink_delivery_failed(
            writer,
            clock=fixed_clock,
            sink_name="telegram",
            consecutive_failures=3,
            last_error="auth failed for token 123456789:ABCDEF1234567890ghijklmnopqrstuvwxyz",
        )
        await writer.close()
        assert isinstance(env.payload, SinkDeliveryFailedPayload)
        assert env.payload.last_error is not None
        assert "<redacted-telegram-token>" in env.payload.last_error
        assert "ABCDEF1234567890" not in env.payload.last_error

    @pytest.mark.asyncio
    async def test_emit_sink_delivery_failed_redacts_bearer_token(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_sink_delivery_failed(
            writer,
            clock=fixed_clock,
            sink_name="telegram",
            consecutive_failures=3,
            last_error="rejected: Bearer abc.def-ghi_jkl",
        )
        await writer.close()
        assert isinstance(env.payload, SinkDeliveryFailedPayload)
        assert env.payload.last_error is not None
        assert "Bearer <redacted>" in env.payload.last_error
        assert "abc.def-ghi_jkl" not in env.payload.last_error

    @pytest.mark.asyncio
    async def test_emit_sink_delivery_failed_redacts_password_kv(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_sink_delivery_failed(
            writer,
            clock=fixed_clock,
            sink_name="postgres",
            consecutive_failures=3,
            last_error="conn failed password=hunter2 host=db",
        )
        await writer.close()
        assert isinstance(env.payload, SinkDeliveryFailedPayload)
        assert env.payload.last_error is not None
        assert "password=<redacted>" in env.payload.last_error
        assert "hunter2" not in env.payload.last_error

    @pytest.mark.asyncio
    async def test_emit_task_stop_requested_writes_envelope(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id=_TASK_ID,
            actor_id="console",
        )
        await writer.close()

        recovered = _read_envelopes(tmp_path, fixed_clock.now())
        assert len(recovered) == 1
        assert recovered[0].type == "task.stop_requested"
        assert env.type == "task.stop_requested"
        assert isinstance(env.payload, TaskStopRequestedPayload)
        assert env.payload.task_id == _TASK_ID

    @pytest.mark.asyncio
    async def test_emit_task_stop_requested_actor_id_preserved(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id=_TASK_ID,
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

    @pytest.mark.asyncio
    async def test_emit_task_stop_requested_with_operator_kind(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Operator-initiated stops set Actor.kind='operator'."""
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id=_TASK_ID,
            actor_id="telegram:99887766",
            actor_kind="operator",
        )
        await writer.close()
        assert env.actor.kind == "operator"

    @pytest.mark.asyncio
    async def test_emit_with_explicit_request_id(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Caller can pin request_id to correlate emissions on the same tick."""
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        rid = "018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b"
        env = await emit_service_crashed(
            writer,
            clock=fixed_clock,
            service="worker-wrapper",
            exit_code=1,
            request_id=rid,
        )
        await writer.close()
        assert env.request_id == rid

    @pytest.mark.asyncio
    async def test_emit_with_parent_event_id(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        parent = "e-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b"
        env = await emit_task_stop_requested(
            writer,
            clock=fixed_clock,
            task_id=_TASK_ID,
            actor_id="console",
            parent_event_id=parent,
        )
        await writer.close()
        assert env.parent_event_id == parent

    @pytest.mark.asyncio
    async def test_emit_generates_distinct_event_and_request_ids(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Two emissions produce different event_ids AND different request_ids
        even under the FrozenClock (entropy comes from the rand bits, not time).
        """
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        env1 = await emit_service_crashed(
            writer, clock=fixed_clock, service="worker-wrapper", exit_code=1
        )
        env2 = await emit_service_crashed(
            writer, clock=fixed_clock, service="worker-wrapper", exit_code=1
        )
        await writer.close()
        assert env1.event_id != env2.event_id
        assert env1.request_id != env2.request_id

    @pytest.mark.asyncio
    async def test_emit_propagates_writer_errors(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """If the writer raises during append, the error propagates to the caller."""
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.close()
        # Writing through a closed writer raises RuntimeError.
        with pytest.raises(RuntimeError):
            await emit_service_crashed(
                writer, clock=fixed_clock, service="worker-wrapper", exit_code=1
            )


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
        # Capture last_at via the public accessor (review-pass F38).
        last_at = monitor.last_heartbeat_at("s-1")
        assert last_at is not None
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
        # accessor returns None after removal
        assert monitor.last_heartbeat_at("s-1") is None

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

    def test_heartbeat_monitor_rejects_non_positive_interval(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        with pytest.raises(ValueError):
            HeartbeatMonitor(heartbeat_interval_s=0, clock=clock)
        with pytest.raises(ValueError):
            HeartbeatMonitor(heartbeat_interval_s=-1, clock=clock)

    def test_heartbeat_monitor_rejects_nan_interval(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        with pytest.raises(ValueError):
            HeartbeatMonitor(heartbeat_interval_s=float("nan"), clock=clock)
        with pytest.raises(ValueError):
            HeartbeatMonitor(heartbeat_interval_s=float("inf"), clock=clock)

    def test_heartbeat_monitor_rejects_naive_at(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        with pytest.raises(ValueError):
            monitor.record_heartbeat("s-1", at=datetime(2026, 1, 1))  # naive

    def test_heartbeat_monitor_accepts_external_at(self) -> None:
        """``at=`` lets callers seed timestamps from event-log replay."""
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        seeded = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        monitor.record_heartbeat("s-1", at=seeded)
        assert monitor.last_heartbeat_at("s-1") == seeded

    def test_heartbeat_monitor_accepts_future_last_at(self) -> None:
        """A future heartbeat (clock skew) is stored; not-overdue until now overtakes 2x."""
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        future = FROZEN_EPOCH + timedelta(seconds=60)
        monitor.record_heartbeat("s-1", at=future)
        # now is FROZEN_EPOCH; future > now → elapsed is negative → not overdue
        assert monitor.overdue_sessions() == []
        assert monitor.last_heartbeat_at("s-1") == future

    def test_heartbeat_monitor_clock_regression_ignored(self) -> None:
        """Out-of-order heartbeat (older than stored) is ignored, not stored."""
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        clock.advance(30)
        monitor.record_heartbeat("s-1")
        stored = monitor.last_heartbeat_at("s-1")
        assert stored is not None
        # Now feed an OLDER timestamp; should be ignored.
        old = FROZEN_EPOCH + timedelta(seconds=5)
        monitor.record_heartbeat("s-1", at=old)
        assert monitor.last_heartbeat_at("s-1") == stored

    def test_heartbeat_monitor_overdue_edge_triggered(self) -> None:
        """``overdue_sessions`` does not re-report after ``mark_emitted``."""
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        clock.advance(20.001)
        first = monitor.overdue_sessions()
        assert [sid for sid, _ in first] == ["s-1"]
        monitor.mark_emitted("s-1")
        # Subsequent calls do not include the already-emitted session.
        assert monitor.overdue_sessions() == []
        # Refresh clears the mark; overdue reports again after another timeout.
        monitor.record_heartbeat("s-1")
        clock.advance(20.001)
        again = monitor.overdue_sessions()
        assert [sid for sid, _ in again] == ["s-1"]

    def test_heartbeat_monitor_overdue_and_mark_atomic(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        clock.advance(20.001)
        first = monitor.overdue_sessions_and_mark()
        assert [sid for sid, _ in first] == ["s-1"]
        # Same call again returns empty without an explicit mark_emitted.
        assert monitor.overdue_sessions_and_mark() == []

    def test_heartbeat_monitor_repr_smoke(self) -> None:
        clock = _AdvancingClock(FROZEN_EPOCH)
        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)
        monitor.record_heartbeat("s-1")
        r = repr(monitor)
        assert "HeartbeatMonitor" in r
        assert "sessions=1" in r


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

    def test_sink_failure_tracker_success_resets_counter_preserves_last_error(self) -> None:
        """``record_success`` resets count to 0 but preserves ``last_error``.

        Operational signal: operators inspecting an idle sink can still see
        what caused the most-recent failure streak.
        """
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "boom")
        tracker.record_failure("telegram", "boom2")
        tracker.record_success("telegram")
        assert tracker.get_state("telegram") == SinkFailureState(count=0, last_error="boom2")
        # one more failure → still below threshold
        tracker.record_failure("telegram", "fresh")
        assert tracker.should_emit("telegram") is False

    def test_sink_failure_tracker_last_error_preserved_on_none(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "first")
        tracker.record_failure("telegram", "second")
        # passing error=None preserves the prior most-recent error
        tracker.record_failure("telegram", None)
        state = tracker.get_state("telegram")
        assert state.count == 3
        assert state.last_error == "second"

    def test_sink_failure_tracker_independent_sinks(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=2)
        tracker.record_failure("telegram", "tg-err")
        tracker.record_failure("telegram", "tg-err2")
        tracker.record_failure("discord", "ds-err")
        assert tracker.should_emit("telegram") is True
        assert tracker.should_emit("discord") is False
        # success on telegram does not affect discord; preserves discord state
        tracker.record_success("telegram")
        assert tracker.should_emit("telegram") is False
        assert tracker.get_state("discord") == SinkFailureState(count=1, last_error="ds-err")

    def test_sink_failure_tracker_rejects_threshold_below_one(self) -> None:
        with pytest.raises(ValueError):
            SinkFailureTracker(failure_threshold=0)
        with pytest.raises(ValueError):
            SinkFailureTracker(failure_threshold=-3)

    def test_sink_failure_tracker_should_emit_edge_triggered(self) -> None:
        """should_emit returns True only when count > emitted_at_count."""
        tracker = SinkFailureTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_failure("telegram", "boom")
        assert tracker.should_emit("telegram") is True
        tracker.mark_emitted("telegram")
        # Still at count=3, but mark_emitted noted that — gate is closed.
        assert tracker.should_emit("telegram") is False
        # New failure advances the count past the mark; gate re-opens.
        tracker.record_failure("telegram", "next")
        assert tracker.should_emit("telegram") is True

    def test_sink_failure_tracker_should_emit_and_mark_atomic(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        for _ in range(3):
            tracker.record_failure("telegram", "boom")
        assert tracker.should_emit_and_mark("telegram") is True
        # Second call without a new failure returns False — already marked.
        assert tracker.should_emit_and_mark("telegram") is False

    def test_sink_failure_tracker_count_caps_at_9999(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        # Synthesise saturation — record_failure caps at 9999.
        for _ in range(10005):
            tracker.record_failure("telegram", "boom")
        assert tracker.get_state("telegram").count == 9999

    def test_sink_failure_tracker_repr_smoke(self) -> None:
        tracker = SinkFailureTracker(failure_threshold=3)
        tracker.record_failure("telegram", "boom")
        r = repr(tracker)
        assert "SinkFailureTracker" in r
        assert "sinks=1" in r
        assert "threshold=3" in r


# ===========================================================================
# TestRedaction — F18 defense-in-depth
# ===========================================================================


class TestRedaction:
    def test_redact_none_returns_none(self) -> None:
        assert _redact_last_error(None) is None

    def test_redact_clean_string_unchanged(self) -> None:
        s = "HTTP 503 from upstream"
        assert _redact_last_error(s) == s

    def test_redact_telegram_token(self) -> None:
        s = "auth failed for 123456789:ABCDEF1234567890ghijklmnopqrstuvwxyz"
        out = _redact_last_error(s)
        assert out is not None
        assert "<redacted-telegram-token>" in out
        assert "ABCDEF" not in out

    def test_redact_bearer(self) -> None:
        s = "rejected: Bearer abc.def-ghi"
        out = _redact_last_error(s)
        assert out == "rejected: Bearer <redacted>"

    def test_redact_kv_secret_password(self) -> None:
        s = "conn failed password=hunter2 host=db"
        out = _redact_last_error(s)
        assert out is not None
        assert "password=<redacted>" in out
        assert "hunter2" not in out

    def test_redact_kv_secret_api_key(self) -> None:
        s = "denied: api_key=sk-1234567890"
        out = _redact_last_error(s)
        assert out is not None
        assert "api_key=<redacted>" in out

    def test_redact_kv_secret_token_dashed(self) -> None:
        s = "blocked: api-key=ZZZ"
        out = _redact_last_error(s)
        assert out is not None
        assert "api-key=<redacted>" in out


# ===========================================================================
# TestRegistryAndEvents — AC-8 / AC-2 verification via public API
# ===========================================================================


class TestRegistryAndEvents:
    def test_all_2_10_event_types_in_registry(self) -> None:
        """Closes the AC-8 scanner gap: explicitly assert EVENT_TYPES membership.

        Reads ``EVENT_TYPES`` via the live module attribute (rather than a
        from-import) because ``register()`` rebinds the cache on every call.
        """
        event_types = _schema_registry.EVENT_TYPES
        assert "service.crashed" in event_types
        assert "session.heartbeat_timeout" in event_types
        assert "sink.delivery_failed" in event_types
        assert "task.stop_requested" in event_types

    def test_register_new_event_types_is_idempotent(self) -> None:
        """Repeated registration with the same model is a no-op (no exception)."""
        for _ in range(3):
            register("service.crashed", "1.0.0", ServiceCrashedPayload)
            register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
            register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
            register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)

    def test_assert_aware_helper_on_naive_clock(self) -> None:
        """Defense-in-depth: even if a test passed a naive-now clock, monitor
        rejects at record_heartbeat / overdue_sessions boundaries.

        (We can't directly construct a naive-now ``Clock`` via FrozenClock —
        FrozenClock validates UTC at __init__ — so use a small inline double.)
        """

        class _NaiveClock:
            def now(self) -> datetime:
                return datetime(2026, 1, 1)  # naive

            def monotonic_ns(self) -> int:
                return 0

        monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=_NaiveClock())
        with pytest.raises(ValueError):
            monitor.record_heartbeat("s-1")

    def test_finite_check_uses_math_isfinite(self) -> None:
        """Smoke: math.isfinite is the gate (covers nan/inf rejection branch)."""
        assert math.isfinite(10.0) is True
        assert math.isfinite(float("nan")) is False
        assert math.isfinite(float("inf")) is False
