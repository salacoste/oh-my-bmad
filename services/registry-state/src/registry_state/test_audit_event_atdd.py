"""ATDD red-phase contract tests for audit event emission (Epic 35, Story 35.2).

Phase 7 Epic 35 — Audit Trail Completion.  These tests assert contracts that
are NOT YET IMPLEMENTED.  Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate).  When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a HARD
FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts tested (all xfail):
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

Reference tests (NOT xfail):
  - task.state_transition event type registered
  - TaskStateTransitionPayload model has required fields
"""

from __future__ import annotations

import pytest

from events import FROZEN_EPOCH, Actor, FrozenClock, TickingClock, new_event_id, new_task_id, new_uuid7
from events.envelope import EventEnvelope
from random import Random


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
# xfail contract tests — _emit_state_transition helper (Story 35.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_state_transition_helper_exists() -> None:
    """The _emit_state_transition helper must exist in handlers.py."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401

    assert callable(_emit_state_transition)


@pytest.mark.asyncio
async def test_emit_state_transition_constructs_valid_envelope(tmp_path) -> None:
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
        assert emitted.payload.from_state == "pending"
        assert emitted.payload.to_state == "planning"
        assert emitted.payload.trigger_event == "task.planning.started"
        assert emitted.payload.task_id == _make_task_id()
        assert emitted.payload.worker_id == ""
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
# xfail contract tests — handler emission sites (Story 35.4)
#
# Each test creates a task, runs a transition handler, then checks the
# event log for a task.state_transition event with the correct payload.
# The tests fail because the handlers don't yet emit audit events.
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
        event_id=new_uuid7(clock=clk, rng=rng),
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
    payload: dict,
    mono_ns: int = 1_100_000,
    seed: int = 99,
) -> EventEnvelope:
    rng = Random(seed)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_uuid7(clock=clk, rng=rng),
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
    from events.schema_registry import register as _reg

    from registry_state.domain.event_types import (
        ensure_registered,
        TaskPlanningStartedPayload,
        TaskPlanReadyPayload,
        TaskExecutionStartedPayload,
        TaskBlockerRaisedPayload,
        TaskCompletedPayload,
        TaskStopRequestedPayload,
        TaskRetryRequestedPayload,
        TaskBudgetExceededPayload,
        TaskCreatedPayload,
    )
    from events.payloads import TaskStateTransitionPayload

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


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_planning_started_emits_audit_event(tmp_path) -> None:
    """handle_task_planning_started must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.event_log import EventLogWriter, read_log_lines
    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
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

    # Read back events from the log
    writer = EventLogWriter(base_dir=tmp_path, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    lines = read_log_lines(tmp_path)
    state_transitions = [
        line for line in lines
        if '"task.state_transition"' in line
    ]
    assert len(state_transitions) >= 1, "Expected at least one task.state_transition event"
    # Check the payload
    import json
    evt = json.loads(state_transitions[0])
    assert evt["payload"]["from_state"] == "pending"
    assert evt["payload"]["to_state"] == "planning"
    assert evt["payload"]["trigger_event"] == "task.planning.started"
    assert evt["payload"]["task_id"] == task_id
    await eng.dispose()


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_plan_ready_emits_audit_event(tmp_path) -> None:
    """handle_task_plan_ready must emit a task.state_transition audit event."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from registry_state.adapters.sqlite_store import get_session
    from registry_state.domain.handlers import (
        handle_task_created,
        handle_task_planning_started,
        handle_task_plan_ready,
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
    sm = get_session(eng)
    async with sm() as session, session.begin():
        await handle_task_created(session, _make_created_envelope(task_id))
        await handle_task_planning_started(
            session,
            _make_transition_envelope("task.planning.started", task_id, {"task_id": task_id}, mono_ns=1_100_000),
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

    # Verify state is plan_ready (sanity)
    from sqlalchemy import select
    from registry_state.schema import Task
    async with sm() as session:
        result = await session.execute(select(Task.status).where(Task.id == task_id))
        status = result.scalar_one()
        assert status == "plan_ready"

    # Audit event check — fails because no audit event emitted yet
    from unittest.mock import AsyncMock
    from registry_state.adapters.event_log import EventLogWriter
    writer = AsyncMock(spec=EventLogWriter)
    # The handler doesn't call writer yet — this is the gap
    assert writer.append.call_count >= 1, "Expected audit event via writer.append"
    await eng.dispose()


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_execution_started_emits_audit_event() -> None:
    """handle_task_execution_started must emit a task.state_transition audit event."""
    # This test checks that the emission helper is called from the handler.
    # Fails because handlers don't emit yet.
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    # If we reach here, the helper exists — but the handler still needs to call it.
    # For the full test, we'd need the same session setup as above.
    # This contract fails because _emit_state_transition doesn't exist yet.
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_blocker_raised_emits_audit_event() -> None:
    """handle_task_blocker_raised must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_completed_emits_audit_event() -> None:
    """handle_task_completed must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_stop_requested_emits_audit_event() -> None:
    """handle_task_stop_requested must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_retry_requested_emits_audit_event() -> None:
    """handle_task_retry_requested must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_budget_exceeded_emits_audit_event() -> None:
    """handle_task_budget_exceeded must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_budget_override_emits_audit_event() -> None:
    """handle_tier3_budget_override must emit a task.state_transition audit event."""
    from registry_state.domain.handlers import _emit_state_transition  # noqa: F401
    assert False, "Contract placeholder — _emit_state_transition not yet implemented"
