"""Tests for MCPClientGroup and verify_connectivity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.app.config import WorkerSettings


def _make_mock_session() -> AsyncMock:
    """Create a mock ClientSession with initialize() and list_tools()."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
    return session


def _patch_stdio_client(sessions: list[AsyncMock]) -> Any:
    """Patch stdio_client to return (read, write) pairs for each session."""

    class FakeStream:
        async def __aenter__(self) -> tuple[MagicMock, MagicMock]:
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc: object) -> None:
            pass

    class FakeSessionCtx:
        def __init__(self, idx: int) -> None:
            self._idx = idx

        async def __aenter__(self) -> AsyncMock:
            return sessions[self._idx]

        async def __aexit__(self, *exc: object) -> None:
            pass

    ctx_counter = [0]

    def _fake_stdio_client(params: object) -> FakeStream:
        return FakeStream()

    def _fake_client_session(read: object, write: object) -> FakeSessionCtx:
        idx = ctx_counter[0]
        ctx_counter[0] += 1
        return FakeSessionCtx(idx)

    return (
        patch(
            "worker_wrapper.adapters.mcp_clients.stdio_client",
            side_effect=_fake_stdio_client,
        ),
        patch(
            "worker_wrapper.adapters.mcp_clients.ClientSession",
            side_effect=_fake_client_session,
        ),
    )


@pytest.mark.asyncio
async def test_mcp_client_group_connects_all_three() -> None:
    sessions = [_make_mock_session() for _ in range(3)]
    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        async with MCPClientGroup(settings) as clients:
            assert clients.task_registry is sessions[0]
            assert clients.session_registry is sessions[1]
            assert clients.clawhip_bridge is sessions[2]

    for s in sessions:
        s.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_client_group_shutdown_clears_refs() -> None:
    sessions = [_make_mock_session() for _ in range(3)]
    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        group = MCPClientGroup(settings)
        async with group:
            pass
        assert group.task_registry is None
        assert group.session_registry is None
        assert group.clawhip_bridge is None


@pytest.mark.asyncio
async def test_mcp_client_group_double_exit_safe() -> None:
    sessions = [_make_mock_session() for _ in range(3)]
    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        group = MCPClientGroup(settings)
        async with group:
            pass
        await group.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_mcp_client_group_connect_failure_cleans_up() -> None:
    """If the second _connect() fails, all fields reset to None."""
    sessions = [_make_mock_session() for _ in range(2)]

    # Third session's initialize will raise
    fail_session = _make_mock_session()
    fail_session.initialize = AsyncMock(side_effect=RuntimeError("subprocess died"))
    sessions.append(fail_session)

    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        group = MCPClientGroup(settings)
        with pytest.raises(RuntimeError, match="subprocess died"):
            await group.__aenter__()
        assert group.task_registry is None
        assert group.session_registry is None
        assert group.clawhip_bridge is None


@pytest.mark.asyncio
async def test_verify_connectivity_all_ok() -> None:
    sessions = [_make_mock_session() for _ in range(3)]
    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        async with MCPClientGroup(settings) as clients:
            results = await verify_connectivity(clients)
            assert results == {
                "task-registry": True,
                "session-registry": True,
                "clawhip-bridge": True,
            }


@pytest.mark.asyncio
async def test_verify_connectivity_one_fails() -> None:
    sessions = [_make_mock_session() for _ in range(3)]
    sessions[1].list_tools = AsyncMock(side_effect=RuntimeError("boom"))
    p1, p2 = _patch_stdio_client(sessions)
    with p1, p2:
        settings = WorkerSettings()
        async with MCPClientGroup(settings) as clients:
            results = await verify_connectivity(clients)
            assert results["task-registry"] is True
            assert results["session-registry"] is False
            assert results["clawhip-bridge"] is True


@pytest.mark.asyncio
async def test_verify_connectivity_null_session() -> None:
    group = MCPClientGroup(settings=WorkerSettings())
    group.task_registry = None
    group.session_registry = None
    group.clawhip_bridge = None
    results = await verify_connectivity(group)
    assert results == {
        "task-registry": False,
        "session-registry": False,
        "clawhip-bridge": False,
    }
