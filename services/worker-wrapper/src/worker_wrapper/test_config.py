"""Tests for WorkerSettings defaults and env overrides."""

from __future__ import annotations

import os
from unittest.mock import patch

from worker_wrapper.app.config import WorkerSettings


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
