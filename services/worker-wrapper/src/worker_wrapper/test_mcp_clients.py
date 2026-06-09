"""Tests for MCPClientGroup and verify_connectivity."""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.app.config import WorkerSettings

# ---------------------------------------------------------------------------
# Phase 10 / ADR-0022 — dual-transport tests
# ---------------------------------------------------------------------------


class TestGetAuthToken:
    """Tests for MCPClientGroup._get_auth_token()."""

    def test_reads_mcp_auth_token_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_AUTH_TOKEN", "tok_abc123")
        group = MCPClientGroup(settings=WorkerSettings())
        assert group._get_auth_token() == "tok_abc123"

    def test_returns_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        group = MCPClientGroup(settings=WorkerSettings())
        assert group._get_auth_token() is None

    def test_returns_none_when_whitespace_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_AUTH_TOKEN", "   ")
        group = MCPClientGroup(settings=WorkerSettings())
        assert group._get_auth_token() is None

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_AUTH_TOKEN", "  tok_xyz  ")
        group = MCPClientGroup(settings=WorkerSettings())
        assert group._get_auth_token() == "tok_xyz"


class TestConnectUrlValidation:
    """Tests for _connect() URL/command mutual exclusivity."""

    @pytest.mark.asyncio
    async def test_url_and_command_both_set_raises(self) -> None:
        group = MCPClientGroup(settings=WorkerSettings())
        group._stack = MagicMock()
        with pytest.raises(ValueError, match="mutually exclusive"):
            await group._connect(
                "task-registry",
                command="python",
                args=["-m", "task_registry_mcp"],
                url="http://localhost:8081/mcp",
            )


class TestConnectStreamableHttp:
    """Tests for _connect() with streamable-http transport."""

    @pytest.mark.asyncio
    async def test_connect_with_url_uses_streamable_http(self) -> None:
        """When url is set, streamable_http_client is used (not stdio_client)."""
        from contextlib import AsyncExitStack

        session = _make_mock_session()
        read_mock = MagicMock()
        write_mock = MagicMock()

        transport_entered = [False]

        class FakeTransportCtx:
            async def __aenter__(self) -> tuple[MagicMock, MagicMock]:
                transport_entered[0] = True
                return (read_mock, write_mock)

            async def __aexit__(self, *exc: object) -> None:
                pass

        class FakeSessionCtx:
            async def __aenter__(self) -> AsyncMock:
                return session

            async def __aexit__(self, *exc: object) -> None:
                pass

        group = MCPClientGroup(settings=WorkerSettings())
        group._stack = AsyncExitStack()
        await group._stack.__aenter__()

        with patch(
            "mcp.client.streamable_http.streamable_http_client",
            return_value=FakeTransportCtx(),
        ), patch(
            "worker_wrapper.adapters.mcp_clients.ClientSession",
            return_value=FakeSessionCtx(),
        ):
            result = await group._connect(
                "task-registry",
                command="",
                args=[],
                url="http://localhost:8081/mcp",
            )

        assert result is session
        assert transport_entered[0], "streamable_http_client was not used"
        session.initialize.assert_awaited_once()
        await group._stack.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_connect_without_url_uses_stdio(self) -> None:
        """When url is None, existing stdio path is used."""
        sessions = [_make_mock_session()]
        p1, p2 = _patch_stdio_client(sessions)
        with p1, p2:
            group = MCPClientGroup(settings=WorkerSettings())
            group._stack = AsyncExitStack()
            await group._stack.__aenter__()
            result = await group._connect(
                "task-registry",
                command="python",
                args=["-m", "task_registry_mcp"],
                url=None,
            )
            assert result is sessions[0]
            await group._stack.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_connect_with_url_calls_create_httpx_verify_arg(self) -> None:
        """URL-based connection passes mTLS verify arg to httpx.AsyncClient."""
        session = _make_mock_session()

        class FakeTransportCtx:
            async def __aenter__(self) -> tuple[MagicMock, MagicMock]:
                return (MagicMock(), MagicMock())

            async def __aexit__(self, *exc: object) -> None:
                pass

        class FakeSessionCtx:
            async def __aenter__(self) -> AsyncMock:
                return session

            async def __aexit__(self, *exc: object) -> None:
                pass

        group = MCPClientGroup(settings=WorkerSettings())
        group._stack = AsyncExitStack()
        await group._stack.__aenter__()

        with patch(
            "worker_wrapper.adapters.mcp_clients.create_httpx_verify_arg",
            return_value=True,
        ) as mock_verify, patch(
            "mcp.client.streamable_http.streamable_http_client",
            return_value=FakeTransportCtx(),
        ), patch(
            "worker_wrapper.adapters.mcp_clients.ClientSession",
            return_value=FakeSessionCtx(),
        ):
            result = await group._connect(
                "task-registry",
                command="",
                args=[],
                url="http://localhost:8081/mcp",
            )

        assert result is session
        mock_verify.assert_called_once()
        await group._stack.__aexit__(None, None, None)


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
