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


# ---------------------------------------------------------------------------
# Story 12.3c (FR68) — orchestrator override re-arm tests.
# ---------------------------------------------------------------------------

_T5 = "t-22222222-3333-7444-8555-666666666666"
_T6 = "t-33333333-4444-7555-8666-777777777777"


@pytest.mark.asyncio
async def test_budget_exceeded_fail_closed_when_no_override() -> None:
    """AC2 — no override within the bounded wait → loop breaks (terminate),
    exactly the pre-12.3c behavior: a single ``task.budget_exceeded`` and NO
    further steps / no resume. Drives the override-wait to expiry by stubbing
    the persisted-limit read to always return the ORIGINAL limit (no raise)."""
    emitted: list[str] = []

    async def _capture_emit(clients, event_type, payload, *, label, caller_trace_id):
        emitted.append(event_type)

    runner = _make_sequential_runner(
        plan_stdout="1. Step one\n2. Step two",
        step_results=[
            {"stdout": "5 passed in 1s\n1100 tokens used", "error": None},
            {"stdout": "5 passed in 1s\n1100 tokens used", "error": None},
        ],
    )
    # Tiny wait window + tiny poll so the monotonic deadline elapses fast.
    settings = _make_settings(
        task_token_budget=1000,
        override_wait_s=0.05,
        override_poll_interval_s=0.01,
    )
    task = {"id": _T5, "title": "Fail closed"}

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_capture_emit),
        # Persisted limit never rises → re-arm returns None → fail-closed break.
        patch(
            "orchestrator_adapter.app.main._read_task_budget_limit",
            return_value=1000,
        ),
    ):
        await process_task(AsyncMock(), runner, settings, task)

    # Exactly one budget_exceeded and NO second step.completed after it.
    assert emitted.count("task.budget_exceeded") == 1
    # Only the first step completed before the breach broke the loop.
    assert emitted.count("task.step.completed") == 1


@pytest.mark.asyncio
async def test_budget_override_rearm_resumes_and_rebreaches() -> None:
    """AC1/AC3 — limit 1k → step1 spends 1100 (breach) → override raises ceiling
    to 5k → tracker re-arms, loop RESUMES → step2 spends 5000 → cumulative 6100
    exceeds the NEW 5k ceiling → a SECOND ``task.budget_exceeded`` is emitted."""
    emitted: list[str] = []

    async def _capture_emit(clients, event_type, payload, *, label, caller_trace_id):
        emitted.append(event_type)

    runner = _make_sequential_runner(
        plan_stdout="1. Step one\n2. Step two",
        step_results=[
            {"stdout": "5 passed in 1s\n1100 tokens used", "error": None},
            {"stdout": "5 passed in 1s\n5000 tokens used", "error": None},
        ],
    )
    # Tiny wait window: the SECOND breach's override-wait is not under test
    # (the patched read returns 5000 == the re-armed ceiling, so it never
    # re-arms again) — keep it short so it fails closed fast instead of
    # blocking the suite for the full window.
    settings = _make_settings(
        task_token_budget=1000,
        override_wait_s=0.05,
        override_poll_interval_s=0.01,
    )
    task = {"id": _T6, "title": "Re-arm then re-breach"}

    with (
        patch("orchestrator_adapter.app.main._emit_event", side_effect=_capture_emit),
        # Operator override has raised the persisted ceiling to 5000.
        patch(
            "orchestrator_adapter.app.main._read_task_budget_limit",
            return_value=5000,
        ),
    ):
        await process_task(AsyncMock(), runner, settings, task)

    # Two breaches: the original 1k ceiling, then the re-armed 5k ceiling.
    assert emitted.count("task.budget_exceeded") == 2
    # Both steps completed (the loop resumed after the first breach).
    assert emitted.count("task.step.completed") == 2


def test_resolve_budget_limit_reloads_persisted_raised_ceiling() -> None:
    """AC4 — after a restart, ``_resolve_budget_limit`` picks up the persisted
    raised ``budget_token_limit`` (highest precedence) rather than the default.

    Plain sync test — ``_resolve_budget_limit`` is not a coroutine.
    """
    from orchestrator_adapter.app.main import _resolve_budget_limit

    settings = _make_settings(task_token_budget=1000)
    # Simulate the task row reloaded post-restart with the raised ceiling.
    task = {"id": _T6, "budget_token_limit": 5000}
    assert _resolve_budget_limit(task, settings) == 5000
    # Defense-in-depth: an out-of-contract over-bound value is IGNORED (falls
    # back to the default) so a corrupted row cannot disable enforcement.
    over = {"id": _T6, "budget_token_limit": 1_000_000_001}
    assert _resolve_budget_limit(over, settings) == 1000


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
    pytest.importorskip("clawhip_bridge_mcp")
    from clawhip_bridge_mcp.server import validate_caller_trace_id

    settings = OrchestratorSettings()
    tid = settings.resolve_trace_id()
    # Must not raise.
    validate_caller_trace_id(tid)


# ---------------------------------------------------------------------------
# Story 12.3c — _read_task_budget_limit UNPATCHED read path (security review:
# the re-arm tests patch this fn, so exercise the REAL task://detail parse +
# the defense-in-depth bounds here so the wiring can't silently rot).
# ---------------------------------------------------------------------------

_T7 = "t-44444444-5555-7666-8777-888888888888"


def _fake_task_registry(*, text: str | None = None, raises: bool = False) -> object:
    """Build a fake MCPClientGroup whose task_registry.read_resource returns a
    task://detail result with *text* as a single text-content block (or raises).
    """
    import types
    from unittest.mock import AsyncMock

    registry = AsyncMock()
    if raises:
        registry.read_resource = AsyncMock(side_effect=RuntimeError("transient MCP read failure"))
    else:
        content = types.SimpleNamespace(text=text)
        result = types.SimpleNamespace(contents=[content] if text is not None else [])
        registry.read_resource = AsyncMock(return_value=result)
    return types.SimpleNamespace(task_registry=registry)


@pytest.mark.asyncio
async def test_read_task_budget_limit_parses_detail_resource() -> None:
    """Happy path: a task://detail JSON body with a positive budget_token_limit
    within bound is parsed and returned (proves the real serialize→read wiring,
    not a patched stub)."""
    import json

    from orchestrator_adapter.app.main import _read_task_budget_limit

    clients = _fake_task_registry(text=json.dumps({"id": _T7, "budget_token_limit": 5000}))
    assert await _read_task_budget_limit(clients, _T7) == 5000


@pytest.mark.asyncio
async def test_read_task_budget_limit_rejects_over_bound_value() -> None:
    """Defense-in-depth (security MEDIUM): an over-bound value (> 1e9) is
    rejected (None) so a corrupted row cannot re-arm to a runaway ceiling."""
    import json

    from orchestrator_adapter.app.main import _read_task_budget_limit

    clients = _fake_task_registry(text=json.dumps({"id": _T7, "budget_token_limit": 1_000_000_001}))
    assert await _read_task_budget_limit(clients, _T7) is None


@pytest.mark.asyncio
async def test_read_task_budget_limit_null_and_notfound_and_transient_return_none() -> None:
    """NULL/absent column, task-not-found (empty body), and a transient read
    error all return None → caller keeps polling → fails closed at deadline."""
    import json

    from orchestrator_adapter.app.main import _read_task_budget_limit

    # NULL column.
    c_null = _fake_task_registry(text=json.dumps({"id": _T7, "budget_token_limit": None}))
    assert await _read_task_budget_limit(c_null, _T7) is None
    # Task not found → task-registry returns "" (empty body).
    c_missing = _fake_task_registry(text="")
    assert await _read_task_budget_limit(c_missing, _T7) is None
    # Transient MCP read failure → swallowed, returns None (not an exception).
    c_err = _fake_task_registry(raises=True)
    assert await _read_task_budget_limit(c_err, _T7) is None
    # task-registry not connected (None client) → None, fail-closed (critic note).
    import types

    c_none = types.SimpleNamespace(task_registry=None)
    assert await _read_task_budget_limit(c_none, _T7) is None
