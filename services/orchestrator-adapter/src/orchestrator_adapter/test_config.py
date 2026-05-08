"""Tests for OrchestratorSettings defaults and env-var overrides (Story 5.10 AC-9)."""

from __future__ import annotations

import pytest

from orchestrator_adapter.app.config import OrchestratorSettings


def test_default_mcp_commands() -> None:
    """Default MCP server commands point at Python modules."""
    s = OrchestratorSettings()
    assert s.task_registry_command == "python"
    assert s.task_registry_args == ["-m", "task_registry_mcp"]
    assert s.session_registry_command == "python"
    assert s.session_registry_args == ["-m", "session_registry_mcp"]
    assert s.clawhip_bridge_command == "python"
    assert s.clawhip_bridge_args == ["-m", "clawhip_bridge_mcp"]


def test_default_omc_settings() -> None:
    """Default OMC path and timeout."""
    s = OrchestratorSettings()
    assert s.omc_path == "upstream/omc"
    assert s.omc_timeout_s == 120.0
    assert s.poll_interval_s == 5.0


def test_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ORCHESTRATOR_ prefix env-vars override defaults."""
    monkeypatch.setenv("ORCHESTRATOR_OMC_PATH", "/custom/omc")
    monkeypatch.setenv("ORCHESTRATOR_OMC_TIMEOUT_S", "60")
    monkeypatch.setenv("ORCHESTRATOR_POLL_INTERVAL_S", "10")
    s = OrchestratorSettings()
    assert s.omc_path == "/custom/omc"
    assert s.omc_timeout_s == 60.0
    assert s.poll_interval_s == 10.0


def test_resolve_actor_id_generates_uuid() -> None:
    """Empty actor_id auto-generates a UUID."""
    s = OrchestratorSettings(actor_id="")
    resolved = s.resolve_actor_id()
    assert resolved != ""
    # Cached — second call returns same value.
    assert s.resolve_actor_id() == resolved


def test_resolve_actor_id_uses_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit actor_id is returned as-is."""
    monkeypatch.setenv("ORCHESTRATOR_ACTOR_ID", "my-actor-42")
    s = OrchestratorSettings()
    assert s.resolve_actor_id() == "my-actor-42"


def test_omc_timeout_must_be_positive() -> None:
    """Zero or negative timeout is rejected by pydantic."""
    with pytest.raises(ValueError):
        OrchestratorSettings(omc_timeout_s=0)
    with pytest.raises(ValueError):
        OrchestratorSettings(omc_timeout_s=-1)


def test_poll_interval_must_be_positive() -> None:
    """Zero or negative poll interval is rejected."""
    with pytest.raises(ValueError):
        OrchestratorSettings(poll_interval_s=0)
    with pytest.raises(ValueError):
        OrchestratorSettings(poll_interval_s=-5)


def test_ready_file_path_default() -> None:
    """Default ready-file path."""
    s = OrchestratorSettings()
    assert s.ready_file_path == "/tmp/ready"
