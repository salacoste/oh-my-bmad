"""Tests for MCPClientGroup lifecycle and verify_connectivity (Story 5.10 AC-9)."""

from __future__ import annotations

from unittest.mock import AsyncMock  # noqa: I001

import pytest

from orchestrator_adapter.adapters.mcp_clients import (
    MCPClientGroup,
    _check_one,
    verify_connectivity,
)
from orchestrator_adapter.app.config import OrchestratorSettings


def _settings() -> OrchestratorSettings:
    return OrchestratorSettings(
        task_registry_command="echo",
        task_registry_args=[],
        session_registry_command="echo",
        session_registry_args=[],
        clawhip_bridge_command="echo",
        clawhip_bridge_args=[],
    )


class TestCheckOne:
    @pytest.mark.asyncio
    async def test_none_session_returns_false(self) -> None:
        name, ok = await _check_one("test", None)
        assert name == "test"
        assert ok is False

    @pytest.mark.asyncio
    async def test_healthy_session_returns_true(self) -> None:
        session = AsyncMock()
        session.list_tools = AsyncMock(return_value=[])
        name, ok = await _check_one("test", session)
        assert ok is True

    @pytest.mark.asyncio
    async def test_failing_session_returns_false(self) -> None:
        session = AsyncMock()
        session.list_tools = AsyncMock(side_effect=RuntimeError("broken"))
        name, ok = await _check_one("test", session)
        assert ok is False


class TestVerifyConnectivity:
    @pytest.mark.asyncio
    async def test_all_healthy(self) -> None:
        clients = MCPClientGroup(settings=_settings())
        session = AsyncMock()
        session.list_tools = AsyncMock(return_value=[])
        clients.task_registry = session
        clients.session_registry = session
        clients.clawhip_bridge = session

        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }

    @pytest.mark.asyncio
    async def test_partial_failure(self) -> None:
        clients = MCPClientGroup(settings=_settings())
        good = AsyncMock()
        good.list_tools = AsyncMock(return_value=[])
        bad = AsyncMock()
        bad.list_tools = AsyncMock(side_effect=RuntimeError("down"))

        clients.task_registry = good
        clients.session_registry = bad
        clients.clawhip_bridge = None

        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is False
        assert results["clawhip-bridge"] is False
