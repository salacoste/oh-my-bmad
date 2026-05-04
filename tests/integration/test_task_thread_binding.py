"""Integration test: task thread binding + Telegram outbound sink.

Story 3.9 AC-9 (1 end-to-end test, @pytest.mark.integration):

Creates a task with chat_id=-1001 / message_id=42, writes a synthetic
task.completed event to a JSONL log file, then runs the TelegramSink for
one poll iteration and asserts that TelegramOutbound.send_to_thread was
called with the correct binding fields and a text matching
``"Task t-...: task.completed"``.

This test is @pytest.mark.integration and is excluded from the PR-gate
``just test`` run (which uses ``-m "not slow"``).  No external services
are started — registry-api is stubbed via httpx.MockTransport.
"""

from __future__ import annotations

from pathlib import Path
from random import Random
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.canonical import to_canonical_json
from events.schema_registry import register as _reg

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ensure_event_types() -> None:
    """Register payload models needed for envelope creation."""
    from events import (  # Story 2.9 AC-16
        TaskCompletedPayload,
        TaskCreatedPayload,
    )

    _reg("task.created", "1.1.0", TaskCreatedPayload)
    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)


_ACTOR = Actor(kind="system", id="integration-test")


def _write_envelope(path: Path, envelope: EventEnvelope) -> None:
    """Append *envelope* as a canonical JSONL line to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(to_canonical_json(envelope) + b"\n")


# ---------------------------------------------------------------------------
# The integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_thread_binding_end_to_end(tmp_path: Path) -> None:
    """E2E: create task with binding, emit task.completed, assert sendMessage called.

    Flow:
    1. Write a task.completed JSONL event into a temp log directory.
    2. Build a TelegramSink that:
       - reads the log directory
       - calls a stubbed registry-api GET that returns chat_id=-1001, reply_to=42
       - calls a mocked TelegramOutbound.send_to_thread
    3. Run TelegramSink._handle() directly with the envelope.
    4. Assert send_to_thread was called with chat_id=-1001, reply_to_message_id=42,
       and text matching "Task t-...: task.completed".
    """
    _ensure_event_types()

    from events import (
        TaskCompletedPayload,  # Story 2.9 AC-16
    )

    # Generate a deterministic task_id.
    rng = Random(42)
    clk = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)
    event_id = new_event_id(clock=clk, rng=rng)
    request_id = new_uuid7(clock=clk, rng=rng)

    # Build a task.completed envelope.
    completed_env = EventEnvelope.create(
        event_id=event_id,
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(task_id=task_id, summary="all done"),
        request_id=request_id,
    )

    # Write it to the JSONL log.
    log_dir = tmp_path / "events"
    log_path = log_dir / "2026-04-30.jsonl"
    _write_envelope(log_path, completed_env)

    # Stub registry-api: returns the binding for this task.
    binding_data = {"chat_id": -1001, "reply_to_message_id": 42, "status": "pending"}

    async def _registry_get(url: str, **kwargs: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        return httpx.Response(status_code=200, json=binding_data, request=req)

    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(side_effect=_registry_get)

    # Mock TelegramOutbound.send_to_thread.
    outbound = MagicMock()
    outbound.send_to_thread = AsyncMock()

    # Build and exercise the sink.
    from clawhip_daemon.adapters.sinks.telegram_sink import TelegramSink

    sink = TelegramSink(
        base_dir=log_dir,
        registry_api_url="http://registry-api:8080",
        http_client=http_client,
        outbound=outbound,
    )

    # Process the single envelope directly.
    await sink._handle(completed_env)

    # Assert send_to_thread was called with the correct binding.
    outbound.send_to_thread.assert_called_once()
    call_kwargs = outbound.send_to_thread.call_args[1]
    assert call_kwargs["chat_id"] == -1001
    assert call_kwargs["reply_to_message_id"] == 42
    text: str = call_kwargs["text"]
    assert task_id in text
    # Story 3.12 — task.completed now routes through _render_completed
    # (FR9 typed renderer), replacing the Story 3.9 placeholder shape
    # ``Task <id>: task.completed``. The new shape is
    # ``✅ Task <id> complete.\n\n<summary>``.
    #
    # Story 3.12 review M6 — exact-shape assertion. The previous form
    # (``startswith("✅ Task ")`` AND ``"complete." in text``) would also
    # accept the emergency one-liner ``✅ Task <id> complete. (message body
    # too large; ...)`` — vacuous against the typed-renderer regression
    # the test is meant to defend. Strengthen to the precise minimal
    # shape for this fixture (no PR/diff/tests/CI/blockers fields set).
    assert text == f"✅ Task {task_id} complete.\n\nall done"
