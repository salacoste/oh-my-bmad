"""Screenshot artifact round-trip integration test scaffold (FR81 / Ship-blocker #5).

Verifies that a screenshot taken via browser_take_screenshot can be
retrieved via artifact.get with the same content hash.

Requires a running artifact-mcp server. Skipped when not available.
"""

from __future__ import annotations

import base64
import hashlib
import shutil

import pytest

pytestmark = pytest.mark.skipif(
    not shutil.which("docker"),
    reason="Screenshot artifact round-trip requires Docker (artifact-mcp + Playwright)",
)

_FAKE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12Ng"
    "AAIABQABNjN9GQAAAABJRUEFTkSuQmCC"
)
_FAKE_PNG_BYTES = base64.b64decode(_FAKE_PNG_B64)
_FAKE_PNG_HASH = hashlib.sha256(_FAKE_PNG_BYTES).hexdigest()
_VALID_TRACE = "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"
_VALID_TASK = "t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"


class TestScreenshotArtifactRoundTrip:
    """FR81: screenshot → artifact.put → artifact.get round-trip."""

    @pytest.mark.asyncio
    async def test_screenshot_stored_and_retrievable(self) -> None:
        """Screenshot bytes stored in artifact-mcp are retrievable via hash.

        Full round-trip:
        1. browser_take_screenshot captures a screenshot
        2. ArtifactClient.put stores bytes under SHA-256 hash
        3. artifact.get with the hash returns the original bytes
        """
        # TODO: Implement with real artifact-mcp server when CI has Docker.
        pytest.skip("Requires artifact-mcp server + Playwright Docker image in CI")

    @pytest.mark.asyncio
    async def test_screenshot_metadata_only_response(self) -> None:
        """Tool response contains artifact_ref and content_hash, NOT raw bytes."""
        from unittest.mock import AsyncMock, MagicMock

        from browser_mcp.handlers.tools import register_tools
        from mcp.server.fastmcp import FastMCP

        artifact_holder = MagicMock()
        artifact_holder.put = AsyncMock(return_value={"ok": True})

        mcp = FastMCP("test")
        mock_client = AsyncMock()
        mock_pw = MagicMock()
        mock_pw.ensure_client = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(
            return_value=MagicMock(
                isError=False,
                content=[MagicMock(text=_FAKE_PNG_B64)],
            )
        )

        register_tools(
            mcp,
            actor_kind="operator",
            actor_id="test-op",
            emitter_holder=None,
            pw_manager=mock_pw,
            artifact_holder=artifact_holder,
        )

        handler = mcp._tool_manager._tools["browser_take_screenshot"].fn
        result = await handler(
            caller_trace_id=_VALID_TRACE,
            task_id=_VALID_TASK,
        )

        # Metadata present.
        assert result["content_hash"] == _FAKE_PNG_HASH
        assert result["artifact_ref"] is not None
        assert result["format"] == "png"
        assert result["size_bytes"] == len(_FAKE_PNG_BYTES)

        # Raw bytes NOT in response (NFR-B3).
        result_str = str(result)
        assert _FAKE_PNG_B64 not in result_str
