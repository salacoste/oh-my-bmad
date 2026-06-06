"""Tests for browser_take_screenshot — Tier-1 screenshot + artifact (Story 21.3 / FR81)."""

from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from capabilities import Tier
from mcp.types import CallToolResult, TextContent

from browser_mcp.handlers.tools import TIER_MAP

_VALID_TRACE = "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"
_VALID_TASK = "t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"

# 1x1 transparent PNG for testing
_FAKE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAABJRUEFTkSuQmCC"
)
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG_BYTES).decode("ascii")


def _make_tool_result(
    text: str = "ok",
    *,
    is_error: bool = False,
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=is_error,
    )


@contextmanager
def _register_screenshot_tools(artifact_holder=None):
    """Register tools with a mock MCP + pw_manager + artifact_holder."""
    from mcp.server.fastmcp import FastMCP

    from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager
    from browser_mcp.handlers.tools import register_tools

    mcp = FastMCP("test")

    mock_client = AsyncMock()
    mock_pw = MagicMock(spec=PlaywrightSubprocessManager)
    mock_pw.ensure_client = AsyncMock(return_value=mock_client)

    register_tools(
        mcp,
        actor_kind="operator",
        actor_id="test-op",
        emitter_holder=None,
        pw_manager=mock_pw,
        allowed_hosts=None,
        artifact_holder=artifact_holder,
    )
    yield mcp, mock_client


# ---------------------------------------------------------------------------
# TIER_MAP
# ---------------------------------------------------------------------------

class TestTierMap:
    def test_browser_take_screenshot_is_tier_one(self) -> None:
        assert TIER_MAP["browser_take_screenshot"] is Tier.ONE


# ---------------------------------------------------------------------------
# browser_take_screenshot handler
# ---------------------------------------------------------------------------

class TestBrowserTakeScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot_success_png(self) -> None:
        """Successful PNG screenshot returns artifact metadata."""
        mock_artifact = AsyncMock()
        mock_artifact.put = AsyncMock(return_value={"ok": True})
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result(_FAKE_PNG_B64)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        expected_hash = hashlib.sha256(_FAKE_PNG_BYTES).hexdigest()
        assert result.get("content_hash") == expected_hash
        assert result["format"] == "png"
        assert result["size_bytes"] == len(_FAKE_PNG_BYTES)
        assert result["artifact_ref"] is not None
        assert ".png" in result["artifact_ref"]
        # Verify artifact.put was called with the right content.
        artifact_holder.put.assert_awaited_once()
        call_kwargs = artifact_holder.put.call_args
        assert call_kwargs.kwargs["content"] == _FAKE_PNG_BYTES

    @pytest.mark.asyncio
    async def test_screenshot_success_jpeg(self) -> None:
        """JPEG format screenshot works."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result(_FAKE_PNG_B64)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                format="jpeg",
                task_id=_VALID_TASK,
            )

        assert result["format"] == "jpeg"
        assert ".jpeg" in result["artifact_ref"]

    @pytest.mark.asyncio
    async def test_screenshot_invalid_format(self) -> None:
        """Invalid format returns structured error."""
        with _register_screenshot_tools(artifact_holder=MagicMock()) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                format="gif",
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "invalid_format"

    @pytest.mark.asyncio
    async def test_screenshot_no_artifact_store(self) -> None:
        """No artifact_holder → structured error."""
        with _register_screenshot_tools(artifact_holder=None) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "no_artifact_store"

    @pytest.mark.asyncio
    async def test_screenshot_playwright_error(self) -> None:
        """Playwright returns isError=True → structured error."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result("Page not found", is_error=True)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "playwright_error"
        # artifact.put should NOT have been called on error.
        artifact_holder.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_screenshot_subprocess_error(self) -> None:
        """Subprocess RuntimeError → structured error."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(side_effect=RuntimeError("spawn failed"))

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "subprocess_error"

    @pytest.mark.asyncio
    async def test_screenshot_subprocess_timeout(self) -> None:
        """Subprocess TimeoutError → structured error."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(side_effect=TimeoutError())

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "subprocess_timeout"

    @pytest.mark.asyncio
    async def test_screenshot_empty_response(self) -> None:
        """Playwright returns empty content → structured error."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result("")
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "no_screenshot_data"

    @pytest.mark.asyncio
    async def test_screenshot_artifact_put_failure(self) -> None:
        """artifact.put raises → structured error."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(side_effect=RuntimeError("artifact store down"))

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result(_FAKE_PNG_B64)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        assert result["error"] is True
        assert result["reason"] == "artifact_put_failed"

    @pytest.mark.asyncio
    async def test_screenshot_invalid_trace_id(self) -> None:
        """Invalid caller_trace_id → ValueError before tier check."""
        with _register_screenshot_tools(artifact_holder=MagicMock()) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn

            with pytest.raises(ValueError, match="caller_trace_id"):
                await handler(
                    caller_trace_id="invalid",
                    task_id=_VALID_TASK,
                )

    @pytest.mark.asyncio
    async def test_screenshot_content_hash_sha256(self) -> None:
        """content_hash is SHA-256 of the image bytes, verified exact match."""
        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        with _register_screenshot_tools(artifact_holder=artifact_holder) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result(_FAKE_PNG_B64)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser_take_screenshot"].fn
            result = await handler(
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

        expected = hashlib.sha256(_FAKE_PNG_BYTES).hexdigest()
        assert result["content_hash"] == expected
        # NFR-B3: raw image bytes should NOT be in the result.
        result_str = str(result)
        assert _FAKE_PNG_B64 not in result_str
