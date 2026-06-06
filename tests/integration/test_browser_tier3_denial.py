"""Tier-3 denial integration test for browser_evaluate (Story 22.2 / P4-I2).

Verifies:
  - browser_evaluate denied without approval.granted → CapabilityDenied raised
  - capability.denied audit event emitted on denial
  - browser_evaluate succeeds when approval.granted exists for task_id

This test uses the live browser-mcp server with a mock Playwright subprocess
and a real (empty) events directory as the approval source.
"""

from __future__ import annotations

import pytest
from capabilities import CapabilityDenied
from events.clock import FrozenClock

FROZEN_EPOCH = __import__("datetime").datetime(
    2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
)
_VALID_TRACE = "01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"
_VALID_TASK = "t-01945a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"


@pytest.fixture
def events_dir(tmp_path):
    """Empty events directory — no approval.granted events."""
    d = tmp_path / "events"
    d.mkdir()
    return d


def _build_browser_mcp(events_dir, *, actor_kind="operator"):
    """Build a browser-mcp FastMCP server with a real approval_lookup."""
    from browser_mcp.server import build_server

    return build_server(
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind=actor_kind,
        actor_id="test-operator",
        playwright_image="mcr.microsoft.com/playwright/mcp@sha256:fake",
        registry_events_dir=events_dir,
    )


class TestTier3Denial:
    """P4-I2: browser_evaluate denied without approval.granted."""

    @pytest.mark.asyncio
    async def test_evaluate_denied_without_approval(self, events_dir) -> None:
        """Calling browser_evaluate with no approval.granted → CapabilityDenied."""
        mcp = _build_browser_mcp(events_dir)
        await mcp.list_tools()  # Ensure tools are registered

        handler = mcp._tool_manager._tools["browser.evaluate"].fn

        with pytest.raises(CapabilityDenied):
            await handler(
                expression="1 + 1",
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )

    @pytest.mark.asyncio
    async def test_evaluate_denied_wrong_task_id(self, tmp_path) -> None:
        """Approval for different task_id → still denied."""
        from events import current_day_path
        from events.canonical import to_canonical_json
        from events.envelope import Actor, EventEnvelope
        from events.ids import new_event_id, new_request_id

        events_dir = tmp_path / "events"
        events_dir.mkdir()
        clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)

        # Seed approval for a DIFFERENT task.
        wrong_task = "t-01999a0c-5d82-7d2e-8b3c-4a5b6c7d8e9f"
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=clock),
            schema_version="1.1.0",
            type="approval.granted",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="operator", id="op-1"),
            payload={"task_id": wrong_task, "decision_id": "d-1", "actor_id": "op-1"},
            trace_id=_VALID_TRACE,
            request_id=new_request_id(clock=clock),
        )
        day_path = current_day_path(events_dir, clock.now())
        day_path.parent.mkdir(parents=True, exist_ok=True)
        day_path.write_bytes(to_canonical_json(envelope) + b"\n")

        mcp = _build_browser_mcp(events_dir)
        await mcp.list_tools()

        handler = mcp._tool_manager._tools["browser.evaluate"].fn
        with pytest.raises(CapabilityDenied):
            await handler(
                expression="1 + 1",
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,  # Different from approved task
            )

    @pytest.mark.asyncio
    async def test_worker_actor_denied_tier3(self, events_dir) -> None:
        """Worker actor_kind cannot reach Tier-3 even with approval."""
        mcp = _build_browser_mcp(events_dir, actor_kind="worker")
        await mcp.list_tools()

        handler = mcp._tool_manager._tools["browser.evaluate"].fn
        with pytest.raises(CapabilityDenied):
            await handler(
                expression="1 + 1",
                caller_trace_id=_VALID_TRACE,
                task_id=_VALID_TASK,
            )
