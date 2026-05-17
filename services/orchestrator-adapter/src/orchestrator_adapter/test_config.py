"""Tests for OrchestratorSettings defaults and env-var overrides (Story 5.10 AC-9).

Story 9.6 review pass-3 TH1: added regression tests for the trace_id
shape contract — validator + post_init eager resolve + alias-fallthrough
+ resolver narrow-raise.
"""

from __future__ import annotations

import pytest

from orchestrator_adapter.app.config import OrchestratorSettings


@pytest.fixture(autouse=True)
def _clean_orchestrator_trace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Story 9.6 review pass-3 TH1: strip ambient trace_id env vars between
    tests so CI / dev shell exports cannot pollute alias-fallthrough logic.
    """
    for name in (
        "ORCHESTRATOR_TRACE_ID",
        "OMB_ORCHESTRATOR_TRACE_ID",
        "OMB_TRACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


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


# ---------------------------------------------------------------------------
# Story 9.6 review pass-3 TH1 — trace_id shape contract (mirror worker side).
# ---------------------------------------------------------------------------


_VALID_UUIDV7 = "01917e5c-a7d1-7000-8abc-0123456789ab"


def test_trace_id_accepts_valid_uuidv7(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_TRACE_ID", _VALID_UUIDV7)
    s = OrchestratorSettings()
    assert s.trace_id == _VALID_UUIDV7
    assert s.resolve_trace_id() == _VALID_UUIDV7


def test_trace_id_accepts_telegram_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_TRACE_ID", "tg:42")
    s = OrchestratorSettings()
    assert s.trace_id == "tg:42"
    assert s.resolve_trace_id() == "tg:42"


def test_trace_id_invalid_value_mints_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_TRACE_ID", "not-a-uuid")
    s = OrchestratorSettings()
    assert s.trace_id is None
    # Resolver returns a freshly-minted UUIDv7 (non-empty, shape-valid).
    from events.envelope import is_valid_trace_id

    assert is_valid_trace_id(s.resolve_trace_id())


def test_trace_id_absent_mints_fresh_silently() -> None:
    s = OrchestratorSettings()
    assert s.trace_id is None
    from events.envelope import is_valid_trace_id

    assert is_valid_trace_id(s.resolve_trace_id())


def test_trace_id_alias_fallthrough_when_canonical_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TH1 + PH1 parity: empty canonical falls through to remaining aliases."""
    monkeypatch.setenv("ORCHESTRATOR_TRACE_ID", "")
    monkeypatch.setenv("OMB_TRACE_ID", _VALID_UUIDV7)
    s = OrchestratorSettings()
    assert s.trace_id == _VALID_UUIDV7


def test_trace_id_via_omb_orchestrator_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMB_ORCHESTRATOR_TRACE_ID", _VALID_UUIDV7)
    s = OrchestratorSettings()
    assert s.trace_id == _VALID_UUIDV7


def test_trace_id_via_omb_trace_id_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMB_TRACE_ID", _VALID_UUIDV7)
    s = OrchestratorSettings()
    assert s.trace_id == _VALID_UUIDV7


def test_trace_id_ctor_kwarg_via_populate_by_name() -> None:
    """TH1: ``populate_by_name=True`` lets ctor kwarg use the field name."""
    s = OrchestratorSettings(trace_id=_VALID_UUIDV7)
    assert s.trace_id == _VALID_UUIDV7


def test_resolve_trace_id_raises_when_post_init_skipped() -> None:
    """TH6 parity: bypassing model_post_init surfaces a typed RuntimeError
    (not an assert) so production runs under ``python -O`` still fail loud.
    """
    s = OrchestratorSettings()
    # Simulate the invariant violation by clearing the cache.
    s._resolved_trace_id = None
    with pytest.raises(RuntimeError, match="model_post_init must have populated"):
        s.resolve_trace_id()
