"""Runtime adapter contract tests — ADR-0015 ATDD contracts (FR89 / FR95).

Verifies the structural and behavioral contracts for all registered runtime
adapters.  These tests enforce the invariants from ADR-0015 D1-D6:

- Every registered adapter satisfies RuntimeAdapter protocol.
- Factory returns correct adapter for each registered name.
- Factory raises ValueError for unknown names.
- runtime_name returns a value in the closed SUPPORTED_RUNTIMES set.
- health_check returns HealthCheckResult.
- terminate_with_grace returns TerminationResult-compatible value.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeRunner,
    TerminationResult,
)
from worker_wrapper.adapters.codex_runner import CodexRunner
from worker_wrapper.adapters.runtime_factory import (
    SUPPORTED_RUNTIMES,
    get_runtime_adapter,
)
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.runtime_adapter import HealthCheckResult, RuntimeAdapter


class TestRuntimeAdapterFactory:
    """ADR-0015 D2: Factory completeness and correctness."""

    def test_supported_runtimes_is_frozenset(self) -> None:
        assert isinstance(SUPPORTED_RUNTIMES, frozenset)

    def test_supported_runtenes_contains_claude_code(self) -> None:
        assert "claude-code" in SUPPORTED_RUNTIMES

    def test_supported_runtimes_contains_codex(self) -> None:
        assert "codex" in SUPPORTED_RUNTIMES

    def test_factory_claude_code_returns_claude_runner(self) -> None:
        s = WorkerSettings()
        adapter = get_runtime_adapter(s)
        assert isinstance(adapter, ClaudeCodeRunner)

    def test_factory_codex_returns_codex_runner(self) -> None:
        s = WorkerSettings(runtime="codex")
        adapter = get_runtime_adapter(s)
        assert isinstance(adapter, CodexRunner)

    def test_factory_override_takes_priority(self) -> None:
        s = WorkerSettings(runtime="claude-code")
        adapter = get_runtime_adapter(s, runtime="codex")
        assert isinstance(adapter, CodexRunner)

    def test_factory_none_override_uses_settings(self) -> None:
        s = WorkerSettings(runtime="codex")
        adapter = get_runtime_adapter(s, runtime=None)
        assert isinstance(adapter, CodexRunner)

    def test_factory_empty_override_uses_settings(self) -> None:
        s = WorkerSettings(runtime="codex")
        adapter = get_runtime_adapter(s, runtime="")
        assert isinstance(adapter, CodexRunner)

    def test_factory_unknown_raises_value_error(self) -> None:
        s = WorkerSettings()
        with pytest.raises(ValueError, match="Unknown runtime"):
            get_runtime_adapter(s, runtime="gemini")

    def test_factory_empty_settings_defaults_to_claude(self) -> None:
        s = WorkerSettings()
        adapter = get_runtime_adapter(s)
        assert isinstance(adapter, ClaudeCodeRunner)

    @pytest.mark.parametrize("name", list(SUPPORTED_RUNTIMES))
    def test_factory_round_trip_for_all_registered(self, name: str) -> None:
        """Every name in SUPPORTED_RUNTIMES resolves without error."""
        s = WorkerSettings(runtime=name)
        adapter = get_runtime_adapter(s)
        assert isinstance(adapter, RuntimeAdapter)


class TestRuntimeAdapterProtocol:
    """ADR-0015 D1: Protocol compliance for all adapters."""

    @pytest.mark.parametrize(
        "runner_factory",
        [
            lambda: ClaudeCodeRunner(WorkerSettings()),
            lambda: CodexRunner(WorkerSettings()),
        ],
        ids=["claude-code", "codex"],
    )
    def test_isinstance_runtime_adapter(self, runner_factory: object) -> None:
        runner = runner_factory()  # type: ignore[operator]
        assert isinstance(runner, RuntimeAdapter)

    @pytest.mark.parametrize(
        "runner_factory,expected_name",
        [
            (lambda: ClaudeCodeRunner(WorkerSettings()), "claude-code"),
            (lambda: CodexRunner(WorkerSettings()), "codex"),
        ],
        ids=["claude-code", "codex"],
    )
    def test_runtime_name_in_closed_set(
        self,
        runner_factory: object,
        expected_name: str,
    ) -> None:
        runner = runner_factory()  # type: ignore[operator]
        assert runner.runtime_name == expected_name
        assert runner.runtime_name in SUPPORTED_RUNTIMES


class TestRuntimeAdapterHealthCheck:
    """FR95: Health check returns HealthCheckResult."""

    @pytest.mark.asyncio
    async def test_claude_health_check_returns_result(self) -> None:
        s = WorkerSettings(claude_command="echo")  # not a real claude binary
        runner = ClaudeCodeRunner(s)
        result = await runner.health_check()
        assert isinstance(result, HealthCheckResult)
        # "echo" is installed but won't have a useful version
        assert result.installed is True

    @pytest.mark.asyncio
    async def test_codex_health_check_returns_result(self) -> None:
        s = WorkerSettings()
        runner = CodexRunner(s)
        result = await runner.health_check()
        assert isinstance(result, HealthCheckResult)
        # Binary may or may not be installed; just check the shape is correct.

    @pytest.mark.asyncio
    async def test_health_check_missing_binary(self) -> None:
        s = WorkerSettings(claude_command="/nonexistent/binary")
        runner = ClaudeCodeRunner(s)
        result = await runner.health_check()
        assert isinstance(result, HealthCheckResult)
        assert result.installed is False

    @pytest.mark.asyncio
    async def test_codex_health_check_missing_binary(self) -> None:
        s = WorkerSettings(codex_command="/nonexistent/codex")
        runner = CodexRunner(s)
        result = await runner.health_check()
        assert isinstance(result, HealthCheckResult)
        assert result.installed is False


class TestRuntimeAdapterKill:
    """P5-I3: terminate_with_grace returns TerminationResult-compatible value."""

    @pytest.mark.asyncio
    async def test_claude_noop_terminate(self) -> None:
        """No live subprocess → noop termination."""
        runner = ClaudeCodeRunner(WorkerSettings())
        result = await runner.terminate_with_grace()
        assert isinstance(result, TerminationResult)
        assert result.method == "noop"

    @pytest.mark.asyncio
    async def test_codex_noop_terminate(self) -> None:
        """No live subprocess → noop termination."""
        runner = CodexRunner(WorkerSettings())
        result = await runner.terminate_with_grace()
        assert isinstance(result, TerminationResult)
        assert result.method == "noop"


class TestCodexRunnerParsing:
    """P5-I2: Structured output parsing — JSON only, no regex."""

    def test_classify_tool_write(self) -> None:
        event = CodexRunner._classify_tool_use("write", {"file": "x.py"})
        assert event is not None
        assert event.event_type == "file.edited"

    def test_classify_tool_edit(self) -> None:
        event = CodexRunner._classify_tool_use("edit", {"file": "x.py"})
        assert event is not None
        assert event.event_type == "file.edited"

    def test_classify_tool_bash_git_push(self) -> None:
        event = CodexRunner._classify_tool_use(
            "bash", {"command": "git push origin main"},
        )
        assert event is not None
        assert event.event_type == "git.push"

    def test_classify_tool_bash_git_commit(self) -> None:
        event = CodexRunner._classify_tool_use(
            "bash", {"command": "git commit -m 'fix'"},
        )
        assert event is not None
        assert event.event_type == "commit.created"

    def test_classify_tool_bash_pytest(self) -> None:
        event = CodexRunner._classify_tool_use(
            "bash", {"command": "pytest tests/"},
        )
        assert event is not None
        assert event.event_type == "test.run"

    def test_classify_tool_unknown_returns_none(self) -> None:
        event = CodexRunner._classify_tool_use("unknown_tool", {})
        assert event is None

    def test_extract_usage(self) -> None:
        runner = CodexRunner(WorkerSettings())
        runner._extract_usage({
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        assert runner._input_tokens == 100
        assert runner._output_tokens == 50

    def test_extract_events_from_turn(self) -> None:
        runner = CodexRunner(WorkerSettings())
        runner._extract_events({
            "tool_calls": [
                {"name": "write", "input": {"file": "a.py"}},
                {"name": "bash", "input": {"command": "pytest"}},
            ],
        })
        assert len(runner._events) == 2
        assert runner._events[0].event_type == "file.edited"
        assert runner._events[1].event_type == "test.run"

    def test_build_result_exit_0(self) -> None:
        runner = CodexRunner(WorkerSettings())
        result = runner._build_result(0, "")
        assert result.exit_code == 0
        assert result.error is None

    def test_build_result_exit_1(self) -> None:
        runner = CodexRunner(WorkerSettings())
        result = runner._build_result(1, "Error occurred")
        assert result.error == "Error occurred"

    def test_build_result_exit_130(self) -> None:
        runner = CodexRunner(WorkerSettings())
        result = runner._build_result(130, "")
        assert result.exit_code == 130
        assert result.error is None  # SIGTERM, not an error
