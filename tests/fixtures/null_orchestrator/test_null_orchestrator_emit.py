"""Regression tests for the null-orchestrator fixture's lifecycle emit (Story 11.3.4).

S-3 separability stalled because Story 9.7 made ``EventEnvelope.create(trace_id=...)``
a REQUIRED kwarg, but ``_emit_lifecycle_for_task`` called ``create()`` without it →
``TypeError`` on the first detected ``task.created`` → the orchestrator crashed after
touching ``/tmp/ready`` (so the stack booted healthy but the task never progressed past
``task.created``). The fix propagates ``task_created_env.trace_id`` into all 4 lifecycle
events. These tests fail against the pre-fix code (TypeError) — AI-7 realism check.
"""

from __future__ import annotations

import pytest
from events import (
    Actor,
    EventEnvelope,
    SystemClock,
    TaskCreatedPayload,
    new_request_id,
    new_task_id,
)
from events.ids import new_event_id
from registry_state.adapters.event_log import EventLogWriter, read_log_lines
from registry_state.domain.event_types import ensure_registered

from tests.fixtures.null_orchestrator.null_orchestrator import _emit_lifecycle_for_task

_TRACE_ID = "0190aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
_LIFECYCLE_TYPES = [
    "task.planning.started",
    "task.plan.ready",
    "task.execution.started",
    "task.completed",
]


def _make_task_created(clock: SystemClock) -> EventEnvelope:
    ensure_registered()
    task_id = new_task_id(clock=clock)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.1.0",
        type="task.created",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="operator", id="op"),
        payload=TaskCreatedPayload(task_id=task_id),
        request_id=new_request_id(clock=clock),
        trace_id=_TRACE_ID,
    )


@pytest.mark.asyncio
async def test_emit_lifecycle_propagates_trace_id(tmp_path) -> None:
    """All 4 lifecycle events are emitted and chain trace_id + parent from task.created."""
    clock = SystemClock()
    created = _make_task_created(clock)

    writer = EventLogWriter(base_dir=tmp_path, clock=clock)
    try:
        await _emit_lifecycle_for_task(writer=writer, task_created_env=created, clock=clock)
    finally:
        await writer.close()

    envs = [e for p in sorted(tmp_path.rglob("*.jsonl")) for e in read_log_lines(p)]
    lifecycle = [e for e in envs if e.type in _LIFECYCLE_TYPES]

    assert [e.type for e in lifecycle] == _LIFECYCLE_TYPES
    # Story 9.7: trace_id is required and must chain from the originating event.
    assert all(e.trace_id == _TRACE_ID for e in lifecycle)
    # Causal chain: every lifecycle event parents to the task.created event_id.
    assert all(e.parent_event_id == created.event_id for e in lifecycle)
