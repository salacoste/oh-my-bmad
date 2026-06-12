"""Regression tests for the null-orchestrator fixture's lifecycle emit (Story 11.3.4).

S-3 separability stalled because Story 9.7 made ``EventEnvelope.create(trace_id=...)``
a REQUIRED kwarg, but ``_emit_lifecycle_for_task`` called ``create()`` without it →
``TypeError`` on the first detected ``task.created`` → the orchestrator crashed after
touching ``/tmp/ready`` (so the stack booted healthy but the task never progressed past
``task.created``). The fix propagates ``task_created_env.trace_id`` into all 4 lifecycle
events. These tests fail against the pre-fix code (TypeError) — AI-7 realism check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from events import (
    Actor,
    EventEnvelope,
    SystemClock,
    TaskCreatedPayload,
    new_request_id,
    new_task_id,
    schema_registry,
)
from events.event_log_writer import EventLogWriter
from events.ids import new_event_id
from events.log_reader import read_log_lines
from registry_state.domain.event_types import ensure_registered

from tests.fixtures.null_orchestrator.null_orchestrator import (
    _async_main,
    _emit_lifecycle_for_task,
)

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
async def test_emit_lifecycle_propagates_trace_id(tmp_path: Path) -> None:
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
    raw_lines = [
        json.loads(line)
        for p in sorted(tmp_path.rglob("*.jsonl"))
        for line in p.read_text(encoding="utf-8").splitlines()
    ]
    raw_lifecycle = [e for e in raw_lines if e["type"] in _LIFECYCLE_TYPES]
    assert [e["schema_version"] for e in raw_lifecycle] == [
        "1.0.0",
        "1.1.0",
        "1.2.0",
        "1.3.0",
    ]
    # Story 9.7: trace_id is required and must chain from the originating event.
    assert all(e.trace_id == _TRACE_ID for e in lifecycle)
    # Causal chain: every lifecycle event parents to the task.created event_id.
    assert all(e.parent_event_id == created.event_id for e in lifecycle)


def test_async_main_installs_canonical_event_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container entrypoint must self-register schemas before emitting lifecycle events."""
    schema_registry.unregister_all()

    async def _fake_run_null_orchestrator(**_: object) -> None:
        return None

    monkeypatch.setattr(
        "tests.fixtures.null_orchestrator.null_orchestrator.run_null_orchestrator",
        _fake_run_null_orchestrator,
    )
    monkeypatch.setenv("EVENT_LOG_DIR", "/tmp/null-orchestrator-test-events")

    try:
        import asyncio

        asyncio.run(_async_main())
        assert ("task.planning.started", "1.0.0") in schema_registry.REGISTRY
        assert ("task.completed", "1.0.0") in schema_registry.REGISTRY
    finally:
        ensure_registered()
