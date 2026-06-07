"""Unit tests for Phase 5 per-runtime budget tracking (FR94, Story 29.1)."""

from __future__ import annotations

from types import SimpleNamespace

import structlog

from worker_wrapper.app.main import (
    BudgetExceededDuringHandoffError,
    _accumulate_runtime_tokens,
)


def _make_log() -> structlog.stdlib.BoundLogger:
    """Create a throwaway structlog logger for tests."""
    import logging

    stdlib_logger = logging.getLogger(
        f"test_runtime_budget_tracking.{id(object())}",
    )
    stdlib_logger.setLevel(logging.DEBUG)
    structlog.configure(
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        processors=[structlog.dev.ConsoleRenderer()],
    )
    return structlog.get_logger().bind(logger=stdlib_logger)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# _accumulate_runtime_tokens tests
# ---------------------------------------------------------------------------


class TestAccumulateRuntimeTokens:
    """Tests for _accumulate_runtime_tokens helper."""

    def test_accumulate_codex_result(self) -> None:
        """CodexResult-style object: input_tokens + output_tokens summed."""
        result = SimpleNamespace(input_tokens=100, output_tokens=200)
        tokens: dict[str, int] = {}
        _accumulate_runtime_tokens(result, "codex", tokens, _make_log())
        assert tokens == {"codex": 300}

    def test_accumulate_claude_code_result(self) -> None:
        """ClaudeCodeResult-style object: num_turns used as proxy."""
        result = SimpleNamespace(cost_usd=0.01, num_turns=5)
        tokens: dict[str, int] = {}
        _accumulate_runtime_tokens(result, "claude-code", tokens, _make_log())
        assert tokens == {"claude-code": 5}

    def test_accumulate_none_result(self) -> None:
        """None result must not crash."""
        tokens: dict[str, int] = {}
        _accumulate_runtime_tokens(None, "codex", tokens, _make_log())
        assert tokens == {}

    def test_accumulate_multiple_runtimes(self) -> None:
        """Two different runtime names accumulate under separate keys."""
        r1 = SimpleNamespace(input_tokens=50, output_tokens=50)
        r2 = SimpleNamespace(cost_usd=0.02, num_turns=3)
        tokens: dict[str, int] = {}
        _accumulate_runtime_tokens(r1, "codex", tokens, _make_log())
        _accumulate_runtime_tokens(r2, "claude-code", tokens, _make_log())
        assert tokens == {"codex": 100, "claude-code": 3}

    def test_accumulate_additive_same_runtime(self) -> None:
        """Repeated accumulation for same runtime sums values."""
        r1 = SimpleNamespace(input_tokens=100, output_tokens=200)
        r2 = SimpleNamespace(input_tokens=50, output_tokens=50)
        tokens: dict[str, int] = {}
        _accumulate_runtime_tokens(r1, "codex", tokens, _make_log())
        _accumulate_runtime_tokens(r2, "codex", tokens, _make_log())
        assert tokens == {"codex": 400}


# ---------------------------------------------------------------------------
# BudgetExceededDuringHandoffError tests
# ---------------------------------------------------------------------------


class TestBudgetExceededDuringHandoffError:
    """Tests for BudgetExceededDuringHandoffError sentinel exception."""

    def test_budget_exceeded_error_is_exception(self) -> None:
        """BudgetExceededDuringHandoffError is a subclass of Exception."""
        assert issubclass(BudgetExceededDuringHandoffError, Exception)

    def test_budget_exceeded_error_message(self) -> None:
        """Error message references P5-I3 for traceability."""
        # The docstring carries P5-I3; verify the class identity.
        assert "P5-I3" in (BudgetExceededDuringHandoffError.__doc__ or "")
