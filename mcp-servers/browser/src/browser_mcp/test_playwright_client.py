"""Tests for PlaywrightMCPClient — MCP stdio client over subprocess pipes (Story 21.1 / FR79)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from browser_mcp.adapters.playwright_client import PlaywrightMCPClient

_EXEC = "browser_mcp.adapters.playwright_subprocess.asyncio.create_subprocess_exec"
_UUID = "events.ids.new_uuid7"


def _mock_proc(pid: int = 12345) -> MagicMock:
    """Create a mock asyncio.subprocess.Process with stdin/stdout."""
    import asyncio

    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = pid
    proc.returncode = None
    proc.stdin = AsyncMock()
    proc.stdout = AsyncMock()
    return proc


class TestPlaywrightMCPClient:
    """Verify PlaywrightMCPClient lifecycle and call_tool forwarding."""

    def test_is_alive_when_process_running(self) -> None:
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)
        assert client.is_alive is True

    def test_is_alive_when_process_exited(self) -> None:
        proc = _mock_proc()
        proc.returncode = 0
        client = PlaywrightMCPClient(proc)
        assert client.is_alive is False

    def test_session_none_before_enter(self) -> None:
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)
        assert client.session is None

    @pytest.mark.asyncio
    async def test_call_tool_raises_without_session(self) -> None:
        """call_tool raises RuntimeError if session is not initialized."""
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)
        with pytest.raises(RuntimeError, match="not initialized"):
            await client.call_tool("browser_navigate", {"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_init_raises_if_no_stdin(self) -> None:
        """__aenter__ raises if proc.stdin is None."""
        proc = _mock_proc()
        proc.stdin = None
        client = PlaywrightMCPClient(proc)
        with pytest.raises(RuntimeError, match="stdin"):
            await client.__aenter__()

    @pytest.mark.asyncio
    async def test_init_raises_if_no_stdout(self) -> None:
        """__aenter__ raises if proc.stdout is None."""
        proc = _mock_proc()
        proc.stdout = None
        client = PlaywrightMCPClient(proc)
        with pytest.raises(RuntimeError, match="stdout"):
            await client.__aenter__()

    @pytest.mark.asyncio
    async def test_aenter_initializes_session(self) -> None:
        """__aenter__ creates a ClientSession over the process pipes."""
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        with patch("browser_mcp.adapters.playwright_client.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.__aenter__()

        assert client.session is mock_session

    @pytest.mark.asyncio
    async def test_aexit_clears_session(self) -> None:
        """__aexit__ clears the session without killing the process."""
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        with patch("browser_mcp.adapters.playwright_client.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.__aenter__()
            assert client.session is not None

            await client.__aexit__(None, None, None)

        assert client.session is None
        # Process should NOT be killed — lifecycle is managed by SubprocessManager.
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_forwards_to_session(self) -> None:
        """call_tool delegates to session.call_tool with correct args."""
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)

        expected_result = CallToolResult(
            content=[TextContent(type="text", text='{"url": "https://example.com"}')],
            isError=False,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=expected_result)

        with patch("browser_mcp.adapters.playwright_client.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.__aenter__()

        result = await client.call_tool("browser_navigate", {"url": "https://example.com"})

        mock_session.call_tool.assert_awaited_once_with(
            "browser_navigate", {"url": "https://example.com"}
        )
        assert result == expected_result
        assert result.isError is False

    @pytest.mark.asyncio
    async def test_call_tool_propagates_error_result(self) -> None:
        """call_tool returns isError result from Playwright."""
        proc = _mock_proc()
        client = PlaywrightMCPClient(proc)

        error_result = CallToolResult(
            content=[TextContent(type="text", text="Navigation failed")],
            isError=True,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=error_result)

        with patch("browser_mcp.adapters.playwright_client.ClientSession") as mock_session_cls:
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.__aenter__()

        result = await client.call_tool("browser_navigate", {"url": "bad-url"})
        assert result.isError is True
