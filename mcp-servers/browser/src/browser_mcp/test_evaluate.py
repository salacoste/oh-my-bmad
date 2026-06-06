"""Tests for browser_evaluate — Tier-3 JS execution (Story 21.4 / FR82)."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from capabilities import Tier
from mcp.types import CallToolResult, TextContent

from browser_mcp.handlers.tools import TIER_MAP

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_APPROVED_LOOKUP: Callable[[str, str], Awaitable[bool]] = staticmethod(
    lambda _tid, _act: _approved()
)


async def _approved() -> bool:
    return True


async def _denied() -> bool:
    return False


def _denied_lookup(_tid: str, _act: str) -> Awaitable[bool]:
    return _denied()


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
def _register_browser_evaluate(approval_lookup=None):
    """Register tools with a mock MCP + pw_manager, yield the MCP for call_tool."""
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
        approval_lookup=approval_lookup,
    )
    yield mcp, mock_client


# ---------------------------------------------------------------------------
# TIER_MAP
# ---------------------------------------------------------------------------


class TestTierMap:
    def test_browser_evaluate_is_tier_three(self) -> None:
        assert TIER_MAP["browser.evaluate"] is Tier.THREE

    def test_tier_three_count(self) -> None:
        """Only browser.evaluate is Tier-3."""
        tier3 = [k for k, v in TIER_MAP.items() if v is Tier.THREE]
        assert tier3 == ["browser.evaluate"]

    def test_total_tool_count(self) -> None:
        """15 tools total: 6 Tier-1 + 8 Tier-2 + 1 Tier-3."""
        assert len(TIER_MAP) == 15


# ---------------------------------------------------------------------------
# Approval lookup
# ---------------------------------------------------------------------------


class TestApprovalLookup:
    @pytest.mark.asyncio
    async def test_approval_lookup_finds_granted(self, tmp_path) -> None:
        """make_approval_lookup returns True when approval.granted exists."""
        from events import current_day_path
        from events.canonical import to_canonical_json
        from events.clock import FrozenClock
        from events.envelope import Actor, EventEnvelope
        from events.ids import new_event_id, new_request_id

        from browser_mcp.handlers.tools import make_approval_lookup

        clock = FrozenClock(mono_ns=1_000_000)
        log_dir = tmp_path / "events"
        log_dir.mkdir()

        # Write an approval.granted event using the canonical envelope schema.
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=clock),
            schema_version="1.1.0",
            type="approval.granted",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="operator", id="test-op"),
            payload={
                "task_id": "t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                "decision_id": "d-1",
                "actor_id": "test-op",
            },
            trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            request_id=new_request_id(clock=clock),
        )
        day_path = current_day_path(log_dir, clock.now())
        day_path.parent.mkdir(parents=True, exist_ok=True)
        day_path.write_bytes(to_canonical_json(envelope) + b"\n")

        lookup = make_approval_lookup(log_dir, clock)
        assert await lookup("t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f", "browser.evaluate") is True
        assert await lookup("t-01999a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f", "browser.evaluate") is False

    @pytest.mark.asyncio
    async def test_approval_lookup_no_file(self, tmp_path) -> None:
        """Returns False when no event log exists."""
        from events.clock import FrozenClock

        from browser_mcp.handlers.tools import make_approval_lookup

        clock = FrozenClock()
        lookup = make_approval_lookup(tmp_path / "nonexistent", clock)
        assert await lookup("t-01111a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f", "browser.evaluate") is False


# ---------------------------------------------------------------------------
# browser_evaluate handler
# ---------------------------------------------------------------------------


class TestBrowserEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_approved_success(self) -> None:
        """Approved call returns structured result with expression_hash."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(return_value=_make_tool_result("42"))

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="1 + 1",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        expected_hash = hashlib.sha256(b"1 + 1").hexdigest()
        assert result["success"] is True
        assert result["expression_hash"] == expected_hash
        assert result["result_type"] == "string"
        assert result["result_preview"] == "42"
        assert result["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_evaluate_denied_no_approval(self) -> None:
        """No approval_lookup → ValueError raised (Tier-3 requires lookup)."""
        with _register_browser_evaluate(approval_lookup=None) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn

            with pytest.raises(ValueError, match="approval_lookup"):
                await handler(
                    expression="document.cookie",
                    caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                    task_id="t-01111a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                )

    @pytest.mark.asyncio
    async def test_evaluate_denied_lookup_returns_false(self) -> None:
        """approval_lookup returns False → CapabilityDenied raised."""
        from capabilities import CapabilityDenied

        with _register_browser_evaluate(approval_lookup=_denied_lookup) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn

            with pytest.raises(CapabilityDenied):
                await handler(
                    expression="fetch('/api')",
                    caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                    task_id="t-01111a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                )

    @pytest.mark.asyncio
    async def test_evaluate_playwright_error(self) -> None:
        """Playwright returns isError=True → error in response."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(
                return_value=_make_tool_result("ReferenceError: x is not defined", is_error=True)
            )

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="x",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        assert result["success"] is False
        assert result["error"] is True
        assert result["reason"] == "playwright_error"
        assert result["expression_hash"] == hashlib.sha256(b"x").hexdigest()

    @pytest.mark.asyncio
    async def test_evaluate_subprocess_error(self) -> None:
        """Subprocess RuntimeError → structured error."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(side_effect=RuntimeError("spawn failed"))

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="1",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        assert result["error"] is True
        assert result["reason"] == "subprocess_error"

    @pytest.mark.asyncio
    async def test_evaluate_subprocess_timeout(self) -> None:
        """Subprocess TimeoutError → structured error."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(side_effect=TimeoutError())

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="while(true){}",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        assert result["error"] is True
        assert result["reason"] == "subprocess_timeout"

    @pytest.mark.asyncio
    async def test_evaluate_result_preview_truncated(self) -> None:
        """Long result is truncated to 500 chars."""
        long_text = "x" * 1000
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(return_value=_make_tool_result(long_text))

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="longResult",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        assert result["success"] is True
        assert len(result["result_preview"]) == 500  # truncated
        assert result["result_preview"] == long_text[:500]

    @pytest.mark.asyncio
    async def test_evaluate_expression_hash_sha256(self) -> None:
        """expression_hash is SHA-256 of the expression, not the expression itself."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            mock_client.call_tool = AsyncMock(return_value=_make_tool_result("ok"))

            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn
            result = await handler(
                expression="secret_value",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            )

        expected = hashlib.sha256(b"secret_value").hexdigest()
        assert result["expression_hash"] == expected
        # Verify the raw expression is NOT in the result (NFR-S13).
        assert "secret_value" not in str(result)

    @pytest.mark.asyncio
    async def test_evaluate_invalid_trace_id(self) -> None:
        """Invalid caller_trace_id → ValueError before tier check."""
        with _register_browser_evaluate(approval_lookup=_APPROVED_LOOKUP) as (mcp, mock_client):
            tools = mcp._tool_manager._tools
            handler = tools["browser.evaluate"].fn

            with pytest.raises(ValueError, match="caller_trace_id"):
                await handler(
                    expression="1",
                    caller_trace_id="invalid",
                    task_id="t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                )
