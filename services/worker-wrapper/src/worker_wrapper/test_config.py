"""Tests for WorkerSettings defaults and env overrides."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import structlog.testing
from events.envelope import is_valid_trace_id
from events.ids import new_uuid7

from worker_wrapper.app.config import WorkerSettings

# Story 9.4 / 9.5 lesson: keep DeprecationWarnings strict at the test-module
# boundary so any new callsite-warning ingress is caught immediately.
pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")


class TestWorkerSettingsDefaults:
    def test_task_registry_defaults(self) -> None:
        s = WorkerSettings()
        assert s.task_registry_command == "python"
        assert s.task_registry_args == ["-m", "task_registry_mcp"]

    def test_session_registry_defaults(self) -> None:
        s = WorkerSettings()
        assert s.session_registry_command == "python"
        assert s.session_registry_args == ["-m", "session_registry_mcp"]

    def test_clawhip_bridge_defaults(self) -> None:
        s = WorkerSettings()
        assert s.clawhip_bridge_command == "python"
        assert s.clawhip_bridge_args == ["-m", "clawhip_bridge_mcp"]

    def test_registry_db_path_default_empty(self) -> None:
        s = WorkerSettings()
        assert s.registry_db_path == ""

    def test_ready_file_path_default(self) -> None:
        s = WorkerSettings()
        assert s.ready_file_path == ""


class TestWorkerSettingsEnvOverrides:
    def test_task_registry_command_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_REGISTRY_COMMAND": "/usr/bin/python3"}):
            s = WorkerSettings()
            assert s.task_registry_command == "/usr/bin/python3"

    def test_task_registry_args_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_REGISTRY_ARGS": '["-m", "custom_mcp"]'}):
            s = WorkerSettings()
            assert s.task_registry_args == ["-m", "custom_mcp"]

    def test_clawhip_bridge_command_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_CLAWHIP_BRIDGE_COMMAND": "node"}):
            s = WorkerSettings()
            assert s.clawhip_bridge_command == "node"
            assert s.session_registry_command == "python"


# ---------------------------------------------------------------------------
# Story 9.6 / FR59 — trace_id propagation
# ---------------------------------------------------------------------------


class TestWorkerSettingsTraceId:
    """trace_id field + resolve_trace_id behaviour (Story 9.6 AC1, AC2, AC5)."""

    def test_settings_accepts_valid_uuidv7_trace_id(self) -> None:
        """AC1: a bare UUIDv7 from WORKER_TRACE_ID is accepted verbatim."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        assert s.trace_id == tid
        assert is_valid_trace_id(s.trace_id) is True

    def test_settings_accepts_valid_tg_form_trace_id(self) -> None:
        """AC1: ``tg:<update_id>`` Telegram-derived form is accepted."""
        with patch.dict(os.environ, {"WORKER_TRACE_ID": "tg:42"}):
            s = WorkerSettings()
        assert s.trace_id == "tg:42"

    def test_settings_rejects_invalid_trace_id_with_warning(self) -> None:
        """AC2: invalid shape → WARNING log + field reset to None (no crash)."""
        with (
            structlog.testing.capture_logs() as cap,
            patch.dict(os.environ, {"WORKER_TRACE_ID": "bad-format"}),
        ):
            s = WorkerSettings()
        assert s.trace_id is None
        # WARNING about the invalid trace_id must be present.
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["value_preview"] == "bad-format"

    def test_settings_silent_when_trace_id_absent(self) -> None:
        """AC2: absent env var → no warning, trace_id is None silently."""
        # Make sure the env var is unset for this test.
        env = {k: v for k, v in os.environ.items() if k != "WORKER_TRACE_ID"}
        with (
            structlog.testing.capture_logs() as cap,
            patch.dict(os.environ, env, clear=True),
        ):
            s = WorkerSettings()
        assert s.trace_id is None
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert warnings == []

    def test_settings_rejects_trace_id_with_crlf(self) -> None:
        """Story 9.4 pass-2 S1: shape validator must reject CRLF (smuggling)."""
        with (
            structlog.testing.capture_logs(),
            patch.dict(os.environ, {"WORKER_TRACE_ID": "tg:42\r\n"}),
        ):
            s = WorkerSettings()
        # Trailing CRLF is not a valid UUIDv7 nor a valid tg: form per
        # Story 9.1 — must be rejected and silently dropped (no crash).
        assert s.trace_id is None

    def test_resolve_trace_id_returns_set_value(self) -> None:
        """AC5: when a valid trace_id is supplied, resolve returns it verbatim."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        assert s.resolve_trace_id() == tid

    def test_resolve_trace_id_mints_uuidv7_when_absent(self) -> None:
        """AC2/AC5: absent input → fresh UUIDv7 minted on demand."""
        env = {k: v for k, v in os.environ.items() if k != "WORKER_TRACE_ID"}
        with patch.dict(os.environ, env, clear=True):
            s = WorkerSettings()
        resolved = s.resolve_trace_id()
        assert is_valid_trace_id(resolved) is True

    def test_resolve_trace_id_caches_minted_value(self) -> None:
        """AC5: minted ONCE per WorkerSettings instance (per-invocation singleton)."""
        env = {k: v for k, v in os.environ.items() if k != "WORKER_TRACE_ID"}
        with patch.dict(os.environ, env, clear=True):
            s = WorkerSettings()
        first = s.resolve_trace_id()
        second = s.resolve_trace_id()
        third = s.resolve_trace_id()
        assert first == second == third

    def test_resolve_trace_id_caches_supplied_value(self) -> None:
        """AC5: supplied-value path also caches (no re-validation cost)."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        first = s.resolve_trace_id()
        second = s.resolve_trace_id()
        assert first == second == tid
