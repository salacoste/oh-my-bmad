"""Tests for process_task — empty-plan early-return, blocker path (Story 5.12 review)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from orchestrator_adapter.adapters.github_adapter import PRDraftResult
from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.app.main import process_task


def _make_settings(**overrides: object) -> OrchestratorSettings:
    return OrchestratorSettings(**overrides)


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


def _make_sequential_runner(
    plan_stdout: str,
    step_results: list[dict[str, str | None]],
) -> AsyncMock:
    """Runner that returns *plan_stdout* on first call, then *step_results* in order."""
    runner = AsyncMock()
    call_idx = 0

    async def _run(_prompt: object) -> AsyncMock:
        nonlocal call_idx
        r = AsyncMock()
        if call_idx == 0:
            r.stdout = plan_stdout
            r.error = None
        else:
            idx = call_idx - 1
            step = step_results[idx] if idx < len(step_results) else {}
            r.stdout = step.get("stdout", "")
            r.error = step.get("error")
        r.stderr = ""
        r.exit_code = -1 if r.error else 0
        r.duration_ms = 100
        call_idx += 1
        return r

    runner.run = AsyncMock(side_effect=_run)
    return runner


# ---------------------------------------------------------------------------
# Existing test
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# PR creation guard regression tests (Story 5.14 review finding)
# ---------------------------------------------------------------------------


async def _fake_emit(_clients: object, _event_type: str, _payload: object, *, label: str) -> None:
    pass


@pytest.mark.asyncio
async def test_pr_not_created_when_blockers_exist() -> None:
    """PR auto-creation must be suppressed when a blocker was raised during execution.

    Two-step plan: step 1 succeeds with passing tests (ci_state=green), step 2
    fails triggering a blocker.  The PR guard must reject because blockers_count > 0.
    """
    runner = _make_sequential_runner(
        plan_stdout="1. Write code\n2. Fix edge case",
        step_results=[
            {"stdout": "5 passed in 1.2s", "error": None},
            {"stdout": "", "error": "timeout"},
        ],
    )
    settings = _make_settings()
    task = {"id": "T-BLOCK", "title": "Blocked task", "repo": "owner/repo"}

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_fake_emit),
        patch("orchestrator_adapter.app.main._create_pr_draft") as mock_pr,
    ):
        await process_task(AsyncMock(), runner, settings, task)
        mock_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_not_created_when_budget_exceeded() -> None:
    """PR auto-creation must be suppressed when token budget is exceeded.

    Single-step plan with low budget (50).  Step output reports 100 tokens used
    and passing tests.  Budget exceeded → break, PR guard rejects.
    """
    runner = _make_sequential_runner(
        plan_stdout="1. Implement feature",
        step_results=[
            {"stdout": "5 passed in 1.2s\n100 tokens used", "error": None},
        ],
    )
    settings = _make_settings(task_token_budget=50)
    task = {"id": "T-EXCEEDED", "title": "Over budget", "repo": "owner/repo"}

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_fake_emit),
        patch("orchestrator_adapter.app.main._create_pr_draft") as mock_pr,
    ):
        await process_task(AsyncMock(), runner, settings, task)
        mock_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_created_when_all_guards_pass() -> None:
    """PR auto-creation proceeds when CI is green, no blockers, budget OK."""
    runner = _make_sequential_runner(
        plan_stdout="1. Implement feature",
        step_results=[
            {"stdout": "5 passed in 1.2s", "error": None},
        ],
    )
    # task_token_budget=0 disables budget tracking → tracker is None → guard passes.
    settings = _make_settings(task_token_budget=0)
    task = {"id": "T-GREEN", "title": "Green task", "repo": "owner/repo"}

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_fake_emit),
        patch(
            "orchestrator_adapter.app.main._create_pr_draft",
            return_value=PRDraftResult(success=True, url="https://pr/1", number=1, branch="task/T-GREEN"),
        ) as mock_pr,
    ):
        await process_task(AsyncMock(), runner, settings, task)
        mock_pr.assert_called_once()
