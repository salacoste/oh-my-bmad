"""Tests for browser tab management tools — Tier-1/2 (Story 21.5 / FR83)."""

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


@contextmanager
def _patch_ensure(mock_client: AsyncMock) -> Generator[None, None, None]:
    """Patch ensure_client to return mock_client."""
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
    """Verify TIER_MAP entries for tab management tools."""

    @pytest.mark.parametrize("name,expected_tier", [
        ("browser.tab_list", Tier.ONE),
        ("browser.tab_select", Tier.ONE),
        ("browser.tab_create", Tier.TWO),
        ("browser.tab_close", Tier.TWO),
    ])
    def test_tab_tier_mapping(
        self, name: str, expected_tier: Tier,
    ) -> None:
        assert TIER_MAP[name] is expected_tier

    def test_four_tab_tools_registered(self) -> None:
        tab_count = sum(1 for k in TIER_MAP if k.startswith("browser.tab_"))
        assert tab_count == 4


# ---------------------------------------------------------------------------
# Tool forwarding
# ---------------------------------------------------------------------------


class TestTabToolsForward:
    """Verify each tab tool forwards to Playwright with correct args."""

    @pytest.mark.asyncio
    async def test_tab_list_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.tab_list"](
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_tab_list", {},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_tab_select_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.tab_select"](
                tab_id="tab-2",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_tab_select", {"tab_id": "tab-2"},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_tab_create_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.tab_create"](
                url="https://example.com",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_tab_create",
            {"url": "https://example.com"},
        )
        assert result["success"] is True
        assert "duration_ms" in result

    @pytest.mark.asyncio
    async def test_tab_close_forwards(self) -> None:
        tools = _get_tools()
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.tab_close"](
                tab_id="tab-3",
                caller_trace_id=_VALID_TRACE,
                task_id="t1",
            )

        mock_client.call_tool.assert_awaited_once_with(
            "browser_tab_close", {"tab_id": "tab-3"},
        )
        assert result["success"] is True
        assert "duration_ms" in result


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestTabToolsEvents:
    """Verify browser.action_completed event emission."""

    @pytest.mark.asyncio
    async def test_tab_create_emits_event(self) -> None:
        emitter_holder = MagicMock()
        emitter_holder.emit_event = AsyncMock()

        tools = _get_tools(emitter_holder=emitter_holder)
        mock_client = _mock_client(_success_result())

        with _patch_ensure(mock_client):
            result = await tools["browser.tab_create"](
                url="https://example.com",
                caller_trace_id=_VALID_TRACE,
                task_id="task-42",
            )

        assert result["success"] is True
        emitter_holder.emit_event.assert_awaited_once()
        call_args = emitter_holder.emit_event.call_args
        assert call_args[0][0] == "browser.action_completed"
        payload = call_args[0][1]
        assert payload["tool_name"] == "browser.tab_create"
        assert payload["success"] is True
        assert payload["trace_id"] == _VALID_TRACE
        assert payload["task_id"] == "task-42"
