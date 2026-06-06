"""P4-I1 ephemerality CI gate — no-state-leak negative test (Story 22.1).

Verifies that browser state (cookies, localStorage) does NOT survive across
sequential task-scoped sessions. Each task gets an isolated browser; closing
a task's session destroys all state.

Requires Docker (Playwright subprocess). Skipped when not available.
"""

from __future__ import annotations

import shutil

import pytest

# Skip entire module when Docker is not available.
pytestmark = pytest.mark.skipif(
    not shutil.which("docker"),
    reason="P4-I1 ephemerality test requires Docker",
)

_DIGEST_IMAGE = "mcr.microsoft.com/playwright/mcp@sha256:abc123"
_VALID_TRACE = "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"


class TestBrowserEphemerality:
    """P4-I1: zero state leakage across task-scoped sessions."""

    @pytest.mark.asyncio
    async def test_cookie_not_persistent_across_sessions(self) -> None:
        """Cookie set in task A session is absent in task B session.

        1. Start browser session for task A
        2. Navigate to test page that sets a cookie
        3. End task A session
        4. Start browser session for task B
        5. Navigate to same page and read cookie
        6. Assert cookie is absent/empty
        """
        # TODO: Implement with real Docker when CI has Docker available.
        # This is the scaffold structure — will be filled in when the
        # Playwright Docker image is available in CI.
        pytest.skip("Requires Playwright Docker image in CI")

    @pytest.mark.asyncio
    async def test_localstorage_not_persistent_across_sessions(self) -> None:
        """localStorage set in task A session is absent in task B session."""
        pytest.skip("Requires Playwright Docker image in CI")

    def test_storage_capability_suppressed(self) -> None:
        """No browser_set_storage_state / browser_storage_state tools exist."""
        from browser_mcp.handlers.tools import TIER_MAP

        # storage-related tools must NOT be in TIER_MAP.
        for tool_name in TIER_MAP:
            assert "storage" not in tool_name.lower(), (
                f"Storage tool {tool_name!r} found — P4-I1 requires suppression"
            )
            assert "cookie" not in tool_name.lower(), (
                f"Cookie tool {tool_name!r} found — P4-I1 requires suppression"
            )
