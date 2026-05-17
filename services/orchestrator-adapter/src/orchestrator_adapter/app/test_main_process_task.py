"""Tests for process_task — empty-plan early-return, blocker path (Story 5.12 review)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from orchestrator_adapter.adapters.github_adapter import PRDraftResult
from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.app.main import _emit_event, process_task

# Valid UUIDv7-format task IDs matching payload pattern constraints.
_T1 = "t-01234567-89ab-7def-8abc-0123456789ab"
_T2 = "t-aaaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
_T3 = "t-11111111-2222-7333-8444-555555555555"
_T4 = "t-ffffffff-0000-7000-a000-000000000000"


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

    async def _run(_prompt: object, *, trace_id: str | None = None) -> AsyncMock:
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

    async def fake_emit(clients, event_type, payload, *, label, caller_trace_id):
        emitted_events.append(event_type)

    runner = _make_runner(stdout="")
    settings = _make_settings()
    task = {"id": _T1, "title": "Do nothing"}

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


async def _fake_emit(
    _clients: object,
    _event_type: str,
    _payload: object,
    *,
    label: str,
    caller_trace_id: str,
) -> None:
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
    task = {
        "id": _T2,
        "title": "Blocked task",
        "repo": "owner/repo",
    }

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
    and passing tests.  Budget exceeded -> break, PR guard rejects.
    """
    runner = _make_sequential_runner(
        plan_stdout="1. Implement feature",
        step_results=[
            {"stdout": "5 passed in 1.2s\n100 tokens used", "error": None},
        ],
    )
    settings = _make_settings(task_token_budget=50)
    task = {
        "id": _T3,
        "title": "Over budget",
        "repo": "owner/repo",
    }

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
    # task_token_budget=0 disables budget tracking -> tracker is None -> guard passes.
    settings = _make_settings(task_token_budget=0)
    task = {
        "id": _T4,
        "title": "Green task",
        "repo": "owner/repo",
    }

    pr_result = PRDraftResult(
        success=True,
        url="https://pr/1",
        number=1,
        branch=f"task/{_T4}",
    )

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_fake_emit),
        patch(
            "orchestrator_adapter.app.main._create_pr_draft",
            return_value=pr_result,
        ) as mock_pr,
    ):
        await process_task(AsyncMock(), runner, settings, task)
        mock_pr.assert_called_once()


# ---------------------------------------------------------------------------
# Story 9.6 review pass-3 TH0 — caller_trace_id contract tests
# (real schema validation, not mocked-emit_event).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_event_threads_caller_trace_id_to_clawhip_call() -> None:
    """TH0 regression: ``_emit_event`` includes ``caller_trace_id`` in the
    arguments dict passed to the clawhip-bridge ``call_tool``.

    Asserts the FastMCP server contract (clawhip-bridge ``emit_event``
    requires ``caller_trace_id``) is satisfied by the producer side.
    """
    captured: dict[str, object] = {}

    async def _fake_call_tool(name: str, *, arguments: dict[str, object]) -> object:
        captured["name"] = name
        captured["arguments"] = arguments
        return None

    clients = AsyncMock()
    clients.clawhip_bridge = AsyncMock()
    clients.clawhip_bridge.call_tool = _fake_call_tool

    # Patch isinstance(session, ClientSession) check inside _call_tool.
    with patch("orchestrator_adapter.app.main.isinstance", return_value=True):
        await _emit_event(
            clients,
            "task.planning.started",
            {"task_id": _T1},
            label="planning_started_test",
            caller_trace_id="01917e5c-a7d1-7000-8abc-0123456789ab",
        )

    assert captured["name"] == "emit_event"
    args = captured["arguments"]
    assert isinstance(args, dict)
    assert args["caller_trace_id"] == "01917e5c-a7d1-7000-8abc-0123456789ab"
    assert args["type"] == "task.planning.started"


def test_emit_event_caller_trace_id_passes_validate_caller_trace_id() -> None:
    """TH0 schema contract: the trace_id produced by ``OrchestratorSettings.resolve_trace_id``
    passes the clawhip-bridge ``validate_caller_trace_id`` shape oracle.

    Asserts producer-side and consumer-side contracts agree on shape.
    """
    from clawhip_bridge_mcp.server import validate_caller_trace_id

    settings = OrchestratorSettings()
    tid = settings.resolve_trace_id()
    # Must not raise.
    validate_caller_trace_id(tid)
