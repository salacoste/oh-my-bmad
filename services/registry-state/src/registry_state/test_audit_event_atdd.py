"""ATDD contract tests for audit event emission (Epic 35, Story 35.2).

Phase 7 Epic 35 — Audit Trail Completion.  These tests assert contracts that
verify handler functions emit ``task.state_transition`` audit events via the
``_audit_transition`` / ``_emit_state_transition`` helpers.  The production
code (Stories 35.3-35.4) implemented this using a module-global
``_audit_writer`` set by ``_set_audit_writer()``.

Contracts tested:
  1. _emit_state_transition helper exists and constructs valid envelope
  2. _emit_state_transition payload has required fields
  3. _emit_state_transition appends via EventLogWriter
  4. _emit_state_transition uses parent monotonic_ns + 1 for ordering
  5. planning_started handler emits audit event
  6. plan_ready handler emits audit event
  7. execution_started handler emits audit event
  8. blocker_raised handler emits audit event
  9. completed handler emits audit event
 10. stop_requested handler emits audit event
 11. retry_requested handler emits audit event
 12. budget_exceeded handler emits audit event
 13. budget_override handler emits audit event

Reference tests (NOT xfail):
  - task.state_transition event type registered
  - TaskStateTransitionPayload model has required fields
"""

from __future__ import annotations

from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    FrozenClock,
    TickingClock,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.envelope import EventEnvelope


def _payload_dict(env: EventEnvelope) -> dict[str, object]:
    """Extract envelope payload as a dict (for assertion access)."""
    p = env.payload
    assert isinstance(p, dict), f"Expected dict payload, got {type(p)}"
    return p


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail) — existing registrations and payload shape
# ---------------------------------------------------------------------------


def test_task_state_transition_event_registered() -> None:
    """The ``task.state_transition`` event type must be registered in event_types.py.

    Born at schema 1.1.0 (Phase 6, NEW event — no v1.0.0 predecessor).
    """
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("task.state_transition", "1.1.0") in REGISTRY


def test_task_state_transition_payload_has_required_fields() -> None:
    """TaskStateTransitionPayload must have task_id, from_state, to_state,
    trigger_event, worker_id, and timestamp fields."""
    from events.payloads import TaskStateTransitionPayload

    payload = TaskStateTransitionPayload(
        task_id="t-17e5ca7d-a7d1-7000-8abc-000000000001",
        from_state="pending",
        to_state="planning",
        trigger_event="task.planning.started",
        worker_id="worker-01-12345",
        timestamp="2026-06-08T00:00:00Z",
    )
    assert payload.task_id == "t-17e5ca7d-a7d1-7000-8abc-000000000001"
    assert payload.from_state == "pending"
    assert payload.to_state == "planning"
    assert payload.trigger_event == "task.planning.started"
    assert payload.worker_id == "worker-01-12345"
    assert payload.timestamp == "2026-06-08T00:00:00Z"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-atdd")


def _make_task_id(mono_ns: int = 1_000_000, seed: int = 42) -> str:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    return new_task_id(clock=clk, rng=rng)


# ---------------------------------------------------------------------------
# Contract tests — _emit_state_transition helper (Story 35.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_state_transition_helper_exists() -> None:
    """The _emit_state_transition helper must exist in handlers.py."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401

    assert callable(_emit_state_transition)


@pytest.mark.asyncio
async def test_emit_state_transition_constructs_valid_envelope(tmp_path: Path) -> None:
    """_emit_state_transition must construct a task.state_transition envelope
    with all required payload fields."""
    from unittest.mock import AsyncMock

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.domain.event_types import ensure_registered
    from registry_state.domain.handlers import _set_audit_writer

    ensure_registered()

    writer = AsyncMock(spec=EventLogWriter)
    _set_audit_writer(writer)
    try:
        clock = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
        rng = Random(42)

        # Parent envelope that triggers the transition
        parent = EventEnvelope.create(
            event_id=new_event_id(clock=clock, rng=rng),
            type="task.planning.started",
            schema_version="1.0.0",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=_ACTOR,
            payload={"task_id": _make_task_id()},
            trace_id="01917e5c-a7d1-7000-8abc-000000000099",
            request_id=new_uuid7(clock=TickingClock(start_now=FROZEN_EPOCH), rng=Random(43)),
        )

        from registry_state.domain.handlers import _emit_state_transition

        await _emit_state_transition(
            task_id=_make_task_id(),
            from_state="pending",
            to_state="planning",
            trigger_event="task.planning.started",
            worker_id="",
            parent_envelope=parent,
            clock=clock,
        )

        # The writer must have been called with an EventEnvelope
        assert writer.append.call_count == 1
        emitted: EventEnvelope = writer.append.call_args[0][0]
        assert emitted.type == "task.state_transition"
        assert emitted.schema_version == "1.1.0"
        payload = emitted.payload
        assert isinstance(payload, dict) or hasattr(payload, "from_state")
        assert getattr(payload, "from_state", None) == "pending"
        assert getattr(payload, "to_state", None) == "planning"
        assert getattr(payload, "trigger_event", None) == "task.planning.started"
        assert getattr(payload, "task_id", None) == _make_task_id()
        assert getattr(payload, "worker_id", None) == ""
    finally:
        _set_audit_writer(None)


@pytest.mark.asyncio
async def test_emit_state_transition_uses_parent_monotonic_ns_plus_one() -> None:
    """The child event's monotonic_ns must be parent's monotonic_ns + 1 for ordering."""
    from unittest.mock import AsyncMock

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.domain.event_types import ensure_registered
    from registry_state.domain.handlers import _set_audit_writer

    ensure_registered()

    writer = AsyncMock(spec=EventLogWriter)
    _set_audit_writer(writer)
    try:
        clock = FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH)
        rng = Random(42)

        parent_ns = 2_000_000
        parent = EventEnvelope.create(
            event_id=new_event_id(clock=clock, rng=rng),
            type="task.execution.started",
            schema_version="1.0.0",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=parent_ns,
            actor=_ACTOR,
            payload={"task_id": _make_task_id(), "session_id": "s-test-session"},
            trace_id="01917e5c-a7d1-7000-8abc-000000000098",
            request_id=new_uuid7(clock=TickingClock(start_now=FROZEN_EPOCH), rng=Random(43)),
        )

        from registry_state.domain.handlers import _emit_state_transition

        await _emit_state_transition(
            task_id=_make_task_id(),
            from_state="plan_ready",
            to_state="executing",
            trigger_event="task.execution.started",
            worker_id="worker-01-12345",
            parent_envelope=parent,
            clock=clock,
        )

        emitted: EventEnvelope = writer.append.call_args[0][0]
        assert emitted.emitted_at_monotonic_ns == parent_ns + 1
    finally:
        _set_audit_writer(None)


# ---------------------------------------------------------------------------
# Contract tests — handler emission sites (Story 35.4)
#
# Each test creates a task, runs transition handlers with a real
# EventLogWriter injected via _set_audit_writer, then reads the event log
# to verify a task.state_transition audit event was emitted.
#
# Payloads deserialized from disk are _FrozenDict, so dict-style access
# (_payload_dict(evt)["field"]) is used instead of attribute access.
# ---------------------------------------------------------------------------


def _make_created_envelope(
    task_id: str,
    mono_ns: int = 1_000_000,
    seed: int = 42,
    title: str = "Test",
) -> EventEnvelope:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": task_id, "title": title},
        trace_id="01917e5c-a7d1-7000-8abc-000000000000",
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _make_transition_envelope(
    event_type: str,
    task_id: str,
    payload: dict[str, object],
    mono_ns: int = 1_100_000,
    seed: int = 99,
) -> EventEnvelope:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type=event_type,
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        trace_id="01917e5c-a7d1-7000-8abc-000000000001",
        request_id=new_uuid7(clock=clk, rng=rng),
    )


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Register all event types before each test."""
    from events.payloads import TaskStateTransitionPayload
    from events.schema_registry import register as _reg

    from registry_state.domain.event_types import (
        TaskBlockerRaisedPayload,
        TaskBudgetExceededPayload,
        TaskCompletedPayload,
        TaskCreatedPayload,
        TaskExecutionStartedPayload,
        TaskPlanningStartedPayload,
        TaskPlanReadyPayload,
        TaskRetryRequestedPayload,
        TaskStopRequestedPayload,
        ensure_registered,
    )

    ensure_registered()
    # Ensure core types needed by these tests are registered
    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
    _reg("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
    _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    _reg("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)
    _reg("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
    _reg("task.retry_requested", "1.0.0", TaskRetryRequestedPayload)
    _reg("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    _reg("task.state_transition", "1.1.0", TaskStateTransitionPayload)


async def _read_audit_events(tmp_path: Path) -> list[EventEnvelope]:
    """Read all task.state_transition audit events from the event log."""
    from events.log_reader import current_day_path, read_log_lines

    log_path = current_day_path(tmp_path, FROZEN_EPOCH)
    if not log_path.exists():
        return []
    return [env for env in read_log_lines(log_path) if env.type == "task.state_transition"]


@pytest.mark.asyncio
async def test_planning_started_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_planning_started must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_created,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                ),
            )

        await writer.close()

        # Read back events from the log
        state_transitions = await _read_audit_events(tmp_path)
        assert len(state_transitions) >= 1, "Expected at least one task.state_transition event"
        evt = state_transitions[0]
        assert _payload_dict(evt)["from_state"] == "pending"
        assert _payload_dict(evt)["to_state"] == "planning"
        assert _payload_dict(evt)["trigger_event"] == "task.planning.started"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_plan_ready_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_plan_ready must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_created,
        handle_task_plan_ready,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )

        await writer.close()

        # Verify state is plan_ready (sanity)
        from sqlalchemy import select

        from registry_state.schema import Task

        async with sm() as session:
            result = await session.execute(select(Task.status).where(Task.id == task_id))
            status = result.scalar_one()
            assert status == "plan_ready"

        # Check audit event
        state_transitions = await _read_audit_events(tmp_path)
        plan_ready_events = [
            e for e in state_transitions if _payload_dict(e)["to_state"] == "plan_ready"
        ]
        assert len(plan_ready_events) >= 1, (
            "Expected at least one task.state_transition to plan_ready"
        )
        evt = plan_ready_events[0]
        assert _payload_dict(evt)["from_state"] == "planning"
        assert _payload_dict(evt)["to_state"] == "plan_ready"
        assert _payload_dict(evt)["trigger_event"] == "task.plan.ready"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_execution_started_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_execution_started must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-exec-session"},
                    mono_ns=1_300_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        exec_events = [e for e in state_transitions if _payload_dict(e)["to_state"] == "executing"]
        assert len(exec_events) >= 1, "Expected at least one task.state_transition to executing"
        evt = exec_events[0]
        assert _payload_dict(evt)["from_state"] == "plan_ready"
        assert _payload_dict(evt)["to_state"] == "executing"
        assert _payload_dict(evt)["trigger_event"] == "task.execution.started"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_blocker_raised_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_blocker_raised must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_blocker_raised,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-blocker-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_blocker_raised(
                session,
                _make_transition_envelope(
                    "task.blocker_raised",
                    task_id,
                    {"task_id": task_id, "reason": "test blocker"},
                    mono_ns=1_400_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        blocked_events = [e for e in state_transitions if _payload_dict(e)["to_state"] == "blocked"]
        assert len(blocked_events) >= 1, "Expected at least one task.state_transition to blocked"
        evt = blocked_events[0]
        assert _payload_dict(evt)["from_state"] == "executing"
        assert _payload_dict(evt)["to_state"] == "blocked"
        assert _payload_dict(evt)["trigger_event"] == "task.blocker_raised"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_completed_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_completed must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_completed,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-completed-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_completed(
                session,
                _make_transition_envelope(
                    "task.completed",
                    task_id,
                    {"task_id": task_id, "summary": "done"},
                    mono_ns=1_400_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        completed_events = [
            e for e in state_transitions if _payload_dict(e)["to_state"] == "completed"
        ]
        assert len(completed_events) >= 1, (
            "Expected at least one task.state_transition to completed"
        )
        evt = completed_events[0]
        assert _payload_dict(evt)["from_state"] == "executing"
        assert _payload_dict(evt)["to_state"] == "completed"
        assert _payload_dict(evt)["trigger_event"] == "task.completed"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_stop_requested_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_stop_requested must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
        handle_task_stop_requested,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-stop-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_stop_requested(
                session,
                _make_transition_envelope(
                    "task.stop_requested",
                    task_id,
                    {"task_id": task_id, "actor_id": "test-operator"},
                    mono_ns=1_400_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        stopped_events = [e for e in state_transitions if _payload_dict(e)["to_state"] == "stopped"]
        assert len(stopped_events) >= 1, "Expected at least one task.state_transition to stopped"
        evt = stopped_events[0]
        assert _payload_dict(evt)["from_state"] == "executing"
        assert _payload_dict(evt)["to_state"] == "stopped"
        assert _payload_dict(evt)["trigger_event"] == "task.stop_requested"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_retry_requested_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_retry_requested must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_blocker_raised,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
        handle_task_retry_requested,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-retry-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_blocker_raised(
                session,
                _make_transition_envelope(
                    "task.blocker_raised",
                    task_id,
                    {"task_id": task_id, "reason": "need retry"},
                    mono_ns=1_400_000,
                ),
            )
            await handle_task_retry_requested(
                session,
                _make_transition_envelope(
                    "task.retry_requested",
                    task_id,
                    {
                        "task_id": task_id,
                        "decision_id": "d-test-decision",
                        "actor_id": "test-operator",
                    },
                    mono_ns=1_500_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        retry_events = [
            e
            for e in state_transitions
            if _payload_dict(e)["to_state"] == "pending"
            and _payload_dict(e)["trigger_event"] == "task.retry_requested"
        ]
        assert len(retry_events) >= 1, (
            "Expected at least one task.state_transition to pending via retry"
        )
        evt = retry_events[0]
        assert _payload_dict(evt)["from_state"] == "blocked"
        assert _payload_dict(evt)["to_state"] == "pending"
        assert _payload_dict(evt)["trigger_event"] == "task.retry_requested"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_budget_exceeded_emits_audit_event(tmp_path: Path) -> None:
    """handle_task_budget_exceeded must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_budget_exceeded,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-budget-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_budget_exceeded(
                session,
                _make_transition_envelope(
                    "task.budget_exceeded",
                    task_id,
                    {"task_id": task_id, "token_limit": 100000, "tokens_used": 150000, "step": 5},
                    mono_ns=1_400_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        budget_events = [
            e
            for e in state_transitions
            if _payload_dict(e)["to_state"] == "blocked"
            and _payload_dict(e)["trigger_event"] == "task.budget_exceeded"
        ]
        assert len(budget_events) >= 1, (
            "Expected at least one task.state_transition to blocked via budget_exceeded"
        )
        evt = budget_events[0]
        assert _payload_dict(evt)["from_state"] == "executing"
        assert _payload_dict(evt)["to_state"] == "blocked"
        assert _payload_dict(evt)["trigger_event"] == "task.budget_exceeded"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()


@pytest.mark.asyncio
async def test_budget_override_emits_audit_event(tmp_path: Path) -> None:
    """handle_tier3_budget_override must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        _set_audit_writer,
        handle_task_budget_exceeded,
        handle_task_created,
        handle_task_execution_started,
        handle_task_plan_ready,
        handle_task_planning_started,
        handle_tier3_budget_override,
    )
    from registry_state.schema import Base

    task_id = _make_task_id()
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    await writer.recover()
    _set_audit_writer(writer)
    try:
        sm = get_session(eng)
        async with sm() as session, session.begin():
            await handle_task_created(session, _make_created_envelope(task_id))
            await handle_task_planning_started(
                session,
                _make_transition_envelope(
                    "task.planning.started",
                    task_id,
                    {"task_id": task_id},
                    mono_ns=1_100_000,
                ),
            )
            await handle_task_plan_ready(
                session,
                _make_transition_envelope(
                    "task.plan.ready",
                    task_id,
                    {"task_id": task_id, "estimated_steps": 3, "plan_summary": "test"},
                    mono_ns=1_200_000,
                ),
            )
            await handle_task_execution_started(
                session,
                _make_transition_envelope(
                    "task.execution.started",
                    task_id,
                    {"task_id": task_id, "session_id": "s-test-override-session"},
                    mono_ns=1_300_000,
                ),
            )
            await handle_task_budget_exceeded(
                session,
                _make_transition_envelope(
                    "task.budget_exceeded",
                    task_id,
                    {"task_id": task_id, "token_limit": 100000, "tokens_used": 150000, "step": 5},
                    mono_ns=1_400_000,
                ),
            )
            await handle_tier3_budget_override(
                session,
                _make_transition_envelope(
                    "tier3.budget_override",
                    task_id,
                    {
                        "task_id": task_id,
                        "decision_id": "d-test-override",
                        "actor_id": "test-operator",
                        "old_limit": 100000,
                        "new_limit": 200000,
                    },
                    mono_ns=1_500_000,
                ),
            )

        await writer.close()

        state_transitions = await _read_audit_events(tmp_path)
        override_events = [
            e
            for e in state_transitions
            if _payload_dict(e)["to_state"] == "executing"
            and _payload_dict(e)["trigger_event"] == "tier3.budget_override"
        ]
        assert len(override_events) >= 1, (
            "Expected at least one task.state_transition to executing via budget_override"
        )
        evt = override_events[0]
        assert _payload_dict(evt)["from_state"] == "blocked"
        assert _payload_dict(evt)["to_state"] == "executing"
        assert _payload_dict(evt)["trigger_event"] == "tier3.budget_override"
        assert _payload_dict(evt)["task_id"] == task_id
    finally:
        _set_audit_writer(None)
        await eng.dispose()
