"""Browser container cleanup integration test scaffold (NFR-R9 / Ship-blocker #10).

Verifies that Playwright subprocess containers are cleaned up (killed) when:
- A task session ends
- The server shuts down
- No zombie processes survive

Requires Docker. Skipped when not available.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Container cleanup tests require Docker",
)


class TestBrowserContainerCleanup:
    """NFR-R9: zero zombie containers after session end."""

    @pytest.mark.asyncio
    async def test_kill_session_removes_container(self) -> None:
        """After kill_session, the Docker container for that task is gone."""
        pytest.skip("Requires Playwright Docker image in CI")

    @pytest.mark.asyncio
    async def test_kill_all_on_shutdown(self) -> None:
        """Server shutdown kills all remaining Playwright containers."""
        pytest.skip("Requires Playwright Docker image in CI")

    def test_kill_all_api_exists(self) -> None:
        """PlaywrightSubprocessManager.kill_all() is callable."""
        from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

        mgr = PlaywrightSubprocessManager(image="pw@sha256:test")
        assert hasattr(mgr, "kill_all")
        assert callable(mgr.kill_all)

    def test_kill_session_api_exists(self) -> None:
        """PlaywrightSubprocessManager.kill_session() is callable."""
        from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

        mgr = PlaywrightSubprocessManager(image="pw@sha256:test")
        assert hasattr(mgr, "kill_session")
        assert callable(mgr.kill_session)

    def test_graceful_timeout_constants(self) -> None:
        """Graceful/hard kill timeouts are documented and bounded."""
        from browser_mcp.adapters.playwright_subprocess import (
            _GRACEFUL_TIMEOUT,
            _HARD_KILL_TIMEOUT,
        )

        assert _GRACEFUL_TIMEOUT == 10.0
        assert _HARD_KILL_TIMEOUT == 30.0
        assert _HARD_KILL_TIMEOUT > _GRACEFUL_TIMEOUT
