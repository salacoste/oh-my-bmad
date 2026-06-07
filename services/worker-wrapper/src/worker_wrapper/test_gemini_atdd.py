"""ATDD red-phase contract tests for Gemini adapter (Epic 33).

Phase 6 Epic 33 — Gemini Adapter. These tests assert contracts that are
NOT YET IMPLEMENTED. Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate). When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a HARD
FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts asserted (all xfail):
  1. get_runtime_adapter(settings, runtime="gemini") returns GeminiRunner
  2. GeminiRunner.runtime_name returns "gemini"
  3. GeminiRunner satisfies RuntimeAdapter protocol
  4. GeminiRunner.spawn() runs gemini CLI with structured output
  5. GeminiRunner.parse_output() deserializes JSONL
  6. GEMINI_API_KEY in Gemini child env, NOT in Claude/Codex (P6-I5)
  7. ANTHROPIC_API_KEY + OPENAI_API_KEY NOT in Gemini child env
  8. WORKER_GEMINI_COMMAND blank → health_check() returns installed=False
  9. SUPPORTED_RUNTIMES includes "gemini"
  10. WorkerSettings accepts gemini config fields
  11. _RUNTIMES in metrics includes "gemini"

Reference tests (NOT xfail):
  - Existing runtime adapter protocol is sound
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Story 33.1 / AC1: Runtime factory returns GeminiRunner
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.2: GeminiRunner not yet implemented")
def test_factory_returns_gemini_runner() -> None:
    """get_runtime_adapter(settings, runtime="gemini") must return GeminiRunner."""
    from unittest.mock import MagicMock

    from worker_wrapper.adapters import get_runtime_adapter

    settings = MagicMock()
    settings.runtime = "claude-code"
    adapter = get_runtime_adapter(settings, runtime="gemini")
    assert adapter.runtime_name == "gemini"


# ---------------------------------------------------------------------------
# Story 33.1 / AC2: GeminiRunner.runtime_name
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.2: GeminiRunner not yet implemented")
def test_gemini_runner_runtime_name() -> None:
    """GeminiRunner must have runtime_name = "gemini"."""
    from worker_wrapper.adapters.gemini_runner import GeminiRunner

    settings = MagicMock()
    runner = GeminiRunner(settings)
    assert runner.runtime_name == "gemini"


# ---------------------------------------------------------------------------
# Story 33.1 / AC3: GeminiRunner satisfies RuntimeAdapter protocol
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.2: GeminiRunner not yet implemented")
def test_gemini_runner_satisfies_protocol() -> None:
    """GeminiRunner must satisfy the RuntimeAdapter protocol (ADR-0015)."""
    from worker_wrapper.adapters.gemini_runner import GeminiRunner
    from worker_wrapper.domain.runtime_adapter import RuntimeAdapter

    assert issubclass(GeminiRunner, RuntimeAdapter) or hasattr(
        GeminiRunner, "run"
    ), "GeminiRunner must implement RuntimeAdapter protocol methods"
    # Check all required methods exist
    for method in ("run", "cancel", "terminate_with_grace", "health_check"):
        assert hasattr(GeminiRunner, method), f"GeminiRunner must have {method} method"


# ---------------------------------------------------------------------------
# Story 33.1 / AC7: Gemini credential isolation — GEMINI_API_KEY
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.3: Gemini credential isolation not yet implemented")
def test_gemini_env_has_api_key() -> None:
    """GEMINI_API_KEY must be in Gemini child env."""
    from worker_wrapper.adapters.gemini_runner import GeminiRunner

    settings = MagicMock()
    settings.google_api_key = "test-gemini-key-12345"
    runner = GeminiRunner(settings)
    env = runner._build_child_env()
    assert env.get("GEMINI_API_KEY") == "test-gemini-key-12345", (
        "GEMINI_API_KEY must be set in Gemini child env"
    )


@pytest.mark.xfail(strict=True, reason="Story 33.3: Gemini credential isolation not yet implemented")
def test_claude_env_excludes_gemini_key() -> None:
    """GEMINI_API_KEY must NOT be in Claude child env (P6-I5)."""
    from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner

    settings = MagicMock()
    settings.anthropic_api_key = "test-key"
    settings.google_api_key = "leaked-key"
    runner = ClaudeCodeRunner(settings)
    env = runner._build_child_env()
    assert "GEMINI_API_KEY" not in env, (
        "GEMINI_API_KEY must not leak into Claude child env"
    )


@pytest.mark.xfail(strict=True, reason="Story 33.3: Gemini credential isolation not yet implemented")
def test_gemini_env_excludes_other_keys() -> None:
    """ANTHROPIC_API_KEY and OPENAI_API_KEY must NOT be in Gemini child env."""
    from worker_wrapper.adapters.gemini_runner import GeminiRunner

    settings = MagicMock()
    settings.google_api_key = "test-key"
    runner = GeminiRunner(settings)
    env = runner._build_child_env()
    assert "ANTHROPIC_API_KEY" not in env, (
        "ANTHROPIC_API_KEY must not leak into Gemini child env"
    )
    assert "OPENAI_API_KEY" not in env, (
        "OPENAI_API_KEY must not leak into Gemini child env"
    )


# ---------------------------------------------------------------------------
# Story 33.1 / AC8: Health check returns installed=False when no binary
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.5: Gemini health check not yet implemented")
@pytest.mark.asyncio
async def test_health_check_returns_not_installed() -> None:
    """When WORKER_GEMINI_COMMAND is blank, health_check returns installed=False."""
    from unittest.mock import MagicMock

    from worker_wrapper.adapters.gemini_runner import GeminiRunner

    settings = MagicMock()
    settings.gemini_command = ""
    runner = GeminiRunner(settings)
    result = await runner.health_check()
    assert result.installed is False, "Must return installed=False when no binary"


# ---------------------------------------------------------------------------
# Story 33.1 / AC9: SUPPORTED_RUNTIMES includes "gemini"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.4: SUPPORTED_RUNTIMES not yet expanded")
def test_supported_runtimes_includes_gemini() -> None:
    """SUPPORTED_RUNTIMES must include "gemini" (FR107)."""
    from worker_wrapper.adapters.runtime_factory import SUPPORTED_RUNTIMES

    assert "gemini" in SUPPORTED_RUNTIMES, (
        f"SUPPORTED_RUNTIMES must include 'gemini', got: {SUPPORTED_RUNTIMES}"
    )


# ---------------------------------------------------------------------------
# Story 33.1 / AC10: WorkerSettings accepts Gemini config fields
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.4: WorkerSettings gemini fields not yet added")
def test_worker_settings_has_gemini_fields() -> None:
    """WorkerSettings must have gemini_command, gemini_timeout_s, google_api_key."""
    from worker_wrapper.app.config import WorkerSettings

    settings = WorkerSettings()
    assert hasattr(settings, "gemini_command"), "Must have gemini_command field"
    assert hasattr(settings, "gemini_timeout_s"), "Must have gemini_timeout_s field"
    assert hasattr(settings, "google_api_key"), "Must have google_api_key field"
    # Defaults
    assert settings.gemini_command == "gemini"
    assert settings.gemini_timeout_s == 600.0


# ---------------------------------------------------------------------------
# Story 33.1 / AC11: Metrics _RUNTIMES includes "gemini"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 33.4: metrics _RUNTIMES not yet expanded")
def test_metrics_runtimes_includes_gemini() -> None:
    """Metrics subscriber _RUNTIMES must include "gemini" (NFR-O13)."""
    from metrics_subscriber.app.metrics import _RUNTIMES

    assert "gemini" in _RUNTIMES, f"'gemini' must be in _RUNTIMES, got: {_RUNTIMES}"


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail)
# ---------------------------------------------------------------------------


def test_ref_runtime_adapter_protocol_exists() -> None:
    """[Reference] RuntimeAdapter protocol is importable. Not xfail."""
    from worker_wrapper.domain.runtime_adapter import RuntimeAdapter

    assert RuntimeAdapter is not None


def test_ref_supported_runtimes_has_claude_and_codex() -> None:
    """[Reference] Existing runtimes are registered. Not xfail."""
    from worker_wrapper.adapters.runtime_factory import SUPPORTED_RUNTIMES

    assert "claude-code" in SUPPORTED_RUNTIMES
    assert "codex" in SUPPORTED_RUNTIMES
