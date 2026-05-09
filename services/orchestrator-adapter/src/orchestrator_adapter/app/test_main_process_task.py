"""Tests for process_task — empty-plan early-return, blocker path (Story 5.12 review)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.app.main import process_task


def _make_settings() -> OrchestratorSettings:
    return OrchestratorSettings()


def _make_runner(stdout: str = "", error: str | None = None) -> AsyncMock:
    runner = AsyncMock()
    result = AsyncMock()
    result.stdout = stdout
    result.stderr = ""
    result.error = error
    result.exit_code = -1 if error else 0
    result.duration_ms = 100
    runner.run = AsyncMock(return_value=result)
    return runner


@pytest.mark.asyncio
async def test_empty_plan_emits_completed_without_execution_started() -> None:
    """Zero-step plan should emit task.completed but NOT task.execution.started."""
    emitted_events: list[str] = []

    async def fake_emit(clients, event_type, payload, *, label):
        emitted_events.append(event_type)

    runner = _make_runner(stdout="")
    settings = _make_settings()
    task = {"id": "T-001", "title": "Do nothing"}

    with patch("orchestrator_adapter.app.main._emit_event", side_effect=fake_emit):
        await process_task(AsyncMock(), runner, settings, task)

    assert "task.planning.started" in emitted_events
    assert "task.plan.ready" in emitted_events
    assert "task.completed" in emitted_events
    assert "task.execution.started" not in emitted_events
    completed_idx = emitted_events.index("task.completed")
    plan_ready_idx = emitted_events.index("task.plan.ready")
    assert completed_idx > plan_ready_idx
