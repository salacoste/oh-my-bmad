"""Tests for origin control — _is_host_allowed + blocked navigation (Story 20.4 / FR85).

Unit tests for the host-allowlist checking and ``browser.navigation_blocked``
event emission wired into ``browser_navigate``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from events.clock import FrozenClock

from browser_mcp.handlers.tools import _is_host_allowed

# Patch targets
_EXEC = "browser_mcp.adapters.playwright_subprocess.asyncio.create_subprocess_exec"
_UUID = "events.ids.new_uuid7"


# ---------------------------------------------------------------------------
# _is_host_allowed — host allowlist logic
# ---------------------------------------------------------------------------


class TestIsHostAllowed:
    """Verify ``_is_host_allowed`` correctly filters URLs by hostname."""

    def test_returns_true_when_allowed_hosts_none(self) -> None:
        """No allowlist → allow all (AC #3)."""
        assert _is_host_allowed("https://example.com", None) is True

    def test_returns_true_for_matching_host(self) -> None:
        """Exact hostname match → allowed (AC #1)."""
        assert _is_host_allowed("http://localhost:8080/page", ["localhost"]) is True

    def test_returns_false_for_non_matching_host(self) -> None:
        """Hostname not in allowlist → blocked (AC #2)."""
        assert _is_host_allowed("https://example.com", ["localhost"]) is False

    def test_port_not_part_of_hostname(self) -> None:
        """Port is stripped by urlparse — :8080 does not affect matching."""
        assert _is_host_allowed("http://localhost:8080/page", ["localhost"]) is True
        assert _is_host_allowed("http://localhost/page", ["localhost"]) is True

    def test_empty_allowlist_blocks_everything(self) -> None:
        """Empty list (not None) → everything blocked."""
        assert _is_host_allowed("http://localhost", []) is False

    def test_unparseable_url_returns_false(self) -> None:
        """URL with no parseable hostname → fail-safe block."""
        assert _is_host_allowed("not-a-url", ["localhost"]) is False

    def test_multiple_allowed_hosts(self) -> None:
        """Multiple hosts in allowlist — any match passes."""
        allowed = ["localhost", "example.com", "test.local"]
        assert _is_host_allowed("https://example.com/path", allowed) is True
        assert _is_host_allowed("http://test.local/page", allowed) is True
        assert _is_host_allowed("https://other.com", allowed) is False

    def test_scheme_does_not_affect_host_check(self) -> None:
        """http vs https does not matter — only hostname is compared."""
        assert _is_host_allowed("http://localhost", ["localhost"]) is True
        assert _is_host_allowed("https://localhost", ["localhost"]) is True

    # -- Normalisation edge cases (code-review findings) ---------------------

    def test_trailing_dot_normalised(self) -> None:
        """Trailing DNS dot is stripped — example.com. matches example.com."""
        assert _is_host_allowed("http://example.com./path", ["example.com"]) is True
        assert _is_host_allowed("http://example.com/path", ["example.com."]) is True

    def test_case_insensitive_matching(self) -> None:
        """Allowlist comparison is case-insensitive on both sides."""
        assert _is_host_allowed("http://LOCALHOST", ["localhost"]) is True
        assert _is_host_allowed("http://localhost", ["LOCALHOST"]) is True
        assert _is_host_allowed("http://LocalHost", ["localhost"]) is True

    def test_subdomain_does_not_match_parent(self) -> None:
        """Exact-match only — sub.example.com does NOT match example.com."""
        assert _is_host_allowed("https://sub.example.com", ["example.com"]) is False

    def test_special_schemes_blocked(self) -> None:
        """data:, javascript:, file: schemes have no hostname → blocked."""
        assert _is_host_allowed("data:text/html,<script>x</script>", ["example.com"]) is False
        assert _is_host_allowed("javascript:alert(1)", ["example.com"]) is False
        assert _is_host_allowed("file:///etc/passwd", ["example.com"]) is False

    def test_empty_string_url_blocked(self) -> None:
        """Empty URL has no hostname → fail-safe block."""
        assert _is_host_allowed("", ["localhost"]) is False


# ---------------------------------------------------------------------------
# browser_navigate — origin control integration
# ---------------------------------------------------------------------------


def _mock_proc(pid: int = 12345) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    import asyncio

    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = pid
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    return proc


def _build_mcp(allowed_hosts: list[str] | None = None):
    """Build a browser FastMCP server with the given allowed_hosts."""
    from browser_mcp.server import build_server

    clock = FrozenClock()
    return build_server(
        clock=clock,
        actor_kind="worker",
        actor_id="w-1",
        playwright_image="pw@sha256:test",
        allowed_hosts=allowed_hosts,
    )


def _get_navigate_fn(
    allowed_hosts: list[str] | None = None,
    *,
    mock_call_tool_result: dict[str, object] | None = None,
):
    """Extract the browser_navigate tool function from a built server.

    ``register_tools`` creates closures via ``@mcp.tool()``; we capture
    the decorated function by inspecting the FastMCP internal tool map.

    When *mock_call_tool_result* is provided, the ``ensure_client`` method
    is patched to return a mock client whose ``call_tool`` returns a
    ``CallToolResult`` with the given content.
    """
    from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

    captured: dict[str, object] = {}

    class _CaptureMCP:
        """Minimal FastMCP stand-in that captures tool registrations."""

        def tool(self, name: str = "", **_kw: object):
            def _deco(fn: object):
                captured[name] = fn
                return fn

            return _deco

    from browser_mcp.handlers.tools import register_tools

    pw_manager = PlaywrightSubprocessManager(image="pw@sha256:test")
    mock_mcp = _CaptureMCP()
    register_tools(
        mock_mcp,  # type: ignore[arg-type]
        actor_kind="worker",
        actor_id="w-1",
        emitter_holder=None,
        pw_manager=pw_manager,
        allowed_hosts=allowed_hosts,
    )

    # If a mock result is provided, inject a mock client into the pw_manager.
    if mock_call_tool_result is not None:
        from mcp.types import CallToolResult, TextContent

        mock_result = CallToolResult(
            content=[TextContent(type="text", text=str(mock_call_tool_result))],
            isError=False,
        )
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.is_alive = True
        # Patch ensure_client to return our mock.
        pw_manager.ensure_client = AsyncMock(return_value=mock_client)  # type: ignore[assignment]

    return captured["browser.navigate"]


class TestBrowserNavigateBlocked:
    """Verify browser_navigate blocks unauthorized hosts and emits event."""

    @pytest.mark.asyncio
    async def test_blocked_returns_structured_error(self) -> None:
        """Blocked navigation returns {blocked: true, ...} (AC #2)."""
        navigate = _get_navigate_fn(allowed_hosts=["localhost"])

        result = await navigate(
            url="https://example.com",
            caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            task_id="task-1",
        )

        assert result["blocked"] is True
        assert result["reason"] == "origin_not_allowed"
        assert result["requested_url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_allowed_navigation_succeeds(self) -> None:
        """Navigation to allowed host succeeds (AC #1)."""
        navigate = _get_navigate_fn(
            allowed_hosts=["localhost"],
            mock_call_tool_result={"url": "http://localhost:8080/page"},
        )

        result = await navigate(
            url="http://localhost:8080/page",
            caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            task_id="task-1",
        )

        assert result.get("blocked") is not True
        assert result.get("url") == "http://localhost:8080/page"

    @pytest.mark.asyncio
    async def test_no_allowlist_allows_all(self) -> None:
        """No allowed_hosts → all origins permitted (AC #3)."""
        navigate = _get_navigate_fn(
            allowed_hosts=None,
            mock_call_tool_result={"url": "https://any-site.example.com"},
        )

        result = await navigate(
            url="https://any-site.example.com",
            caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
            task_id="task-1",
        )

        assert result.get("blocked") is not True

    @pytest.mark.asyncio
    async def test_blocked_does_not_spawn_subprocess(self) -> None:
        """Blocked navigation must NOT call ensure_client."""
        navigate = _get_navigate_fn(allowed_hosts=["localhost"])

        # Patch ensure_client on the manager — it should NOT be called.
        from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

        with patch.object(PlaywrightSubprocessManager, "ensure_client", AsyncMock()) as mock_ensure:
            result = await navigate(
                url="https://evil.example.com",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="task-1",
            )

        assert result["blocked"] is True
        mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_emits_navigation_blocked_event(self) -> None:
        """Blocked navigation emits browser.navigation_blocked with correct payload (AC #2)."""
        from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

        captured: dict[str, object] = {}
        emitter_holder = MagicMock()
        emitter_holder.emit_event = AsyncMock()

        class _CaptureMCP:
            def tool(self, name: str = "", **_kw: object):
                def _deco(fn: object):
                    captured[name] = fn
                    return fn

                return _deco

        from browser_mcp.handlers.tools import register_tools

        mock_mcp = _CaptureMCP()
        register_tools(
            mock_mcp,  # type: ignore[arg-type]
            actor_kind="worker",
            actor_id="w-1",
            emitter_holder=emitter_holder,
            pw_manager=PlaywrightSubprocessManager(image="pw@sha256:test"),
            allowed_hosts=["localhost"],
        )
        navigate = captured["browser.navigate"]

        with patch(_EXEC, AsyncMock(return_value=_mock_proc())):
            result = await navigate(
                url="https://evil.example.com",
                caller_trace_id="01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f",
                task_id="task-42",
            )

        assert result["blocked"] is True
        emitter_holder.emit_event.assert_awaited_once()
        call_args = emitter_holder.emit_event.call_args
        assert call_args[0][0] == "browser.navigation_blocked"
        payload = call_args[0][1]
        assert payload["task_id"] == "task-42"
        assert payload["requested_url"] == "https://evil.example.com"
        assert payload["reason"] == "origin_not_allowed"
        assert payload["trace_id"] == "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"
