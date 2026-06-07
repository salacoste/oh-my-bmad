"""Integration test: budget-aware runtime handoff rejection (Story 29.2 / FR94, P5-I3).

Exercises the budget gate inside :func:`perform_runtime_handoff`:

* Cumulative token accounting across runtimes — the budget limit applies to
  ``sum(tokens_consumed_by_runtime.values())``, not to a single runtime.
* Rejection raises :class:`BudgetExceededDuringHandoffError` with P5-I3 tag.
* Accepted handoff terminates the source adapter and returns a new one.
* Missing ``tokens_consumed_by_runtime`` or ``budget_token_limit`` skips the
  check (backwards-compatible passthrough).

No subprocess spawning — this is a unit-level integration test for the
handoff function's budget logic. ``get_runtime_adapter`` is patched so no
real adapters are constructed.

``@pytest.mark.integration`` — excluded from the PR-gate ``just test`` run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.app.main import (
    BudgetExceededDuringHandoffError,
    perform_runtime_handoff,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_adapter(runtime_name: str) -> MagicMock:
    adapter = MagicMock()
    adapter.runtime_name = runtime_name
    adapter.terminate_with_grace = AsyncMock()
    # Return a mock TerminationResult
    result = MagicMock()
    result.method = "sigterm"
    result.exit_code = 0
    adapter.terminate_with_grace.return_value = result
    return adapter


def _mock_clients() -> MagicMock:
    clients = MagicMock()
    clients.clawhip_bridge = AsyncMock()
    return clients


def _settings() -> WorkerSettings:
    return WorkerSettings(
        claude_command="claude",
        anthropic_api_key="dummy-key",
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_rejected_when_budget_exceeded() -> None:
    """P5-I3: handoff raises BudgetExceededDuringHandoffError when cumulative >= limit."""
    adapter = _mock_adapter("claude-code")
    settings = _settings()
    clients = _mock_clients()

    tokens_consumed_by_runtime = {"claude-code": 90000}
    budget_token_limit = 80000

    with pytest.raises(BudgetExceededDuringHandoffError) as exc_info:
        await perform_runtime_handoff(
            current_adapter=adapter,
            settings=settings,
            target_runtime="codex",
            trace_id="trace-001",
            task_id="task-001",
            session_id="session-001",
            clients=clients,
            worktree_path=Path("/tmp/wt"),
            tokens_consumed_by_runtime=tokens_consumed_by_runtime,
            budget_token_limit=budget_token_limit,
        )

    assert "P5-I3" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_accepted_when_under_budget() -> None:
    """Under-budget handoff succeeds: terminates source, returns new adapter."""
    adapter = _mock_adapter("claude-code")
    settings = _settings()
    clients = _mock_clients()
    new_adapter = _mock_adapter("codex")

    tokens_consumed_by_runtime = {"claude-code": 30000}
    budget_token_limit = 80000

    with patch(
        "worker_wrapper.app.main.get_runtime_adapter",
        return_value=new_adapter,
    ):
        result = await perform_runtime_handoff(
            current_adapter=adapter,
            settings=settings,
            target_runtime="codex",
            trace_id="trace-002",
            task_id="task-002",
            session_id="session-002",
            clients=clients,
            worktree_path=Path("/tmp/wt"),
            tokens_consumed_by_runtime=tokens_consumed_by_runtime,
            budget_token_limit=budget_token_limit,
        )

    # No error raised — the function returns the new adapter.
    assert result is new_adapter
    # Source adapter was terminated.
    adapter.terminate_with_grace.assert_awaited_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_rejected_with_multi_runtime_tokens() -> None:
    """P5-I3: cumulative enforcement across runtimes — 40k + 50k = 90k > 80k."""
    adapter = _mock_adapter("claude-code")
    settings = _settings()
    clients = _mock_clients()

    tokens_consumed_by_runtime = {"claude-code": 40000, "codex": 50000}
    budget_token_limit = 80000

    with pytest.raises(BudgetExceededDuringHandoffError) as exc_info:
        await perform_runtime_handoff(
            current_adapter=adapter,
            settings=settings,
            target_runtime="codex",
            trace_id="trace-003",
            task_id="task-003",
            session_id="session-003",
            clients=clients,
            worktree_path=Path("/tmp/wt"),
            tokens_consumed_by_runtime=tokens_consumed_by_runtime,
            budget_token_limit=budget_token_limit,
        )

    assert "P5-I3" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_skips_budget_check_when_params_none() -> None:
    """When tokens/budget are None, the budget check is skipped entirely."""
    adapter = _mock_adapter("claude-code")
    settings = _settings()
    clients = _mock_clients()
    new_adapter = _mock_adapter("codex")

    with patch(
        "worker_wrapper.app.main.get_runtime_adapter",
        return_value=new_adapter,
    ):
        result = await perform_runtime_handoff(
            current_adapter=adapter,
            settings=settings,
            target_runtime="codex",
            trace_id="trace-004",
            task_id="task-004",
            session_id="session-004",
            clients=clients,
            worktree_path=Path("/tmp/wt"),
            # Both budget params are None (defaults) — check is skipped.
            tokens_consumed_by_runtime=None,
            budget_token_limit=None,
        )

    assert result is new_adapter
    adapter.terminate_with_grace.assert_awaited_once()
