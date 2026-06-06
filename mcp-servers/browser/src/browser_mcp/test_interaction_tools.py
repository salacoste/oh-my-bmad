"""Tests for browser interaction tools — Tier-2 (Story 21.2 / FR80)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from capabilities import Tier
from mcp.types import CallToolResult, TextContent

from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager
from browser_mcp.handlers.tools import TIER_MAP

_VALID_TRACE = "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"

_PSM = (
    "browser_mcp.adapters.playwright_subprocess"
    ".PlaywrightSubprocessManager"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tools(**kwargs: object) -> dict[str, object]:
    """Register all browser tools and capture the decorated functions."""
    from browser_mcp.handlers.tools import register_tools

    captured: dict[str, object] = {}

    class _CaptureMCP:
        def tool(self, name: str = "", **_kw: object):
            def _deco(fn: object):
                captured[name] = fn
                return fn
            return _deco

    pw_manager = PlaywrightSubprocessManager(image="pw@sha256:test")
    mock_mcp = _CaptureMCP()
    register_tools(
        mock_mcp,  # type: ignore[arg-type]
        actor_kind="worker",
        actor_id="w-1",
        emitter_holder=kwargs.get("emitter_holder"),
        pw_manager=pw_manager,
        allowed_hosts=None,
    )
    return captured


def _mock_client(result: CallToolResult) -> AsyncMock:
    """Create a mock PlaywrightMCPClient that returns the given result."""
    client = AsyncMock()
    client.call_tool = AsyncMock(return_value=result)
    client.is_alive = True
    return client


def _success_result() -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="ok")],
        isError=False,
    )


def _error_result(msg: str = "Element not found") -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=msg)],
        isError=True,
    )


@contextmanager
def _patch_ensure(mock_client: AsyncMock) -> Generator[None, None, None]:
    """Patch PlaywrightSubprocessManager.ensure_client to return mock_client."""
    with patch.object(
        PlaywrightSubprocessManager,
        "ensure_client",
        AsyncMock(return_value=mock_client),
    ):
        yield


# ---------------------------------------------------------------------------
# TIER_MAP
# ---------------------------------------------------------------------------


class TestTierMap:
    """Verify TIER_MAP entries for interaction tools (AC #3)."""

    @pytest.mark.parametrize("name", [
        "browser.click",
        "browser.type",
        "browser.fill",
        "browser.select_option",
        "browser.press_key",
        "browser.hover",
    ])
    def test_interaction_tool_is_tier_two(self, name: str) -> None:
        assert TIER_MAP[name] is Tier.TWO

    def test_six_interaction_tools_registered(self) -> None:
        tier2_count = sum(1 for t in TIER_MAP.values() if t is Tier.TWO)
        assert tier2_count == 6


# ---------------------------------------------------------------------------
# Tool forwarding
# ---------------------------------------------------------------------------


class TestInteractionToolsForward:
    """Verify each interaction tool forwards to Playwright."""

    @pytest.mark.asyncio
    async def test_browser_click_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.click"](
                element="#submit-btn",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_click", {"element": "#submit-btn"},
        )
        assert result["success"] is True
        assert "duration_ms" in result

    @pytest.mark.asyncio
    async def test_browser_type_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.type"](
                element="#search",
                text="hello world",
                caller_trace_id=_VALID_TRACE,
                task_id="t2",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_type",
            {"element": "#search", "text": "hello world"},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_browser_fill_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.fill"](
                element="#email",
                text="user@example.com",
                caller_trace_id=_VALID_TRACE,
                task_id="t3",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_fill",
            {"element": "#email", "text": "user@example.com"},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_browser_select_option_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.select_option"](
                element="#country",
                values=["US", "CA"],
                caller_trace_id=_VALID_TRACE,
                task_id="t4",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_select_option",
            {"element": "#country", "values": ["US", "CA"]},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_browser_press_key_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.press_key"](
                key="Enter",
                caller_trace_id=_VALID_TRACE,
                task_id="t5",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_press_key", {"key": "Enter"},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_browser_hover_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.hover"](
                element="#menu-item",
                caller_trace_id=_VALID_TRACE,
                task_id="t6",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_hover", {"element": "#menu-item"},
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestInteractionToolsErrors:
    """Verify error handling for interaction tools."""

    @pytest.mark.asyncio
    async def test_playwright_error_propagated(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(
            _error_result("No element matches selector"),
        )

        with _patch_ensure(mock_client):
            result = await tools["browser.click"](
                element="#nonexistent",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        assert result["error"] is True
        assert result["reason"] == "playwright_error"
        assert "No element matches" in result["detail"]
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self) -> None:
        tools = _get_tools()
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=TimeoutError())
        mock_client.is_alive = True

        with _patch_ensure(mock_client):
            result = await tools["browser.click"](
                element="#btn",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        assert result["error"] is True
        assert result["reason"] == "subprocess_timeout"


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestInteractionToolsEvents:
    """Verify browser.action_completed event emission with duration_ms."""

    @pytest.mark.asyncio
    async def test_action_completed_event_emitted(self) -> None:
        emitter_holder = MagicMock()
        emitter_holder.emit_event = AsyncMock()

        tools = _get_tools(emitter_holder=emitter_holder)
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.click"](
                element="#btn",
                caller_trace_id=_VALID_TRACE,
                task_id="task-42",
            )

        assert result["success"] is True
        emitter_holder.emit_event.assert_awaited_once()
        call_args = emitter_holder.emit_event.call_args
        assert call_args[0][0] == "browser.action_completed"
        payload = call_args[0][1]
        assert payload["tool_name"] == "browser.click"
        assert payload["success"] is True
        assert isinstance(payload["duration_ms"], int)
        assert payload["trace_id"] == _VALID_TRACE
        assert payload["task_id"] == "task-42"
