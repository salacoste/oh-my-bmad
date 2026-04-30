"""Unit tests for TelegramSink — Story 3.9 AC-7 / AC-9.

5 tests:
1. Happy-path dispatch — task.* event with binding → send_to_thread called.
2. Skip on missing chat_id — binding has reply_to but chat_id is None.
3. Skip on missing reply_to_message_id — binding has chat_id but reply_to is None.
4. Skip non-task event — event type does not start with "task." → no dispatch.
5. Placeholder renderer output shape — text is "Task <id>: <type>" HTML-escaped.
"""

from __future__ import annotations

from pathlib import Path
from random import Random
from unittest.mock import AsyncMock, MagicMock

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
from events.schema_registry import register as _reg

from clawhip_daemon.adapters.sinks.telegram_sink import TelegramSink, _render

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-sink")


def _ensure_task_created_registered() -> None:
    """Register task.created 1.1.0 so EventEnvelope.create succeeds in tests."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        ServiceCrashedPayload,
        TaskCompletedPayload,
        TaskCreatedPayload,
    )

    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.created", "1.1.0", TaskCreatedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)
    _reg("service.crashed", "1.0.0", ServiceCrashedPayload)


def _task_created_envelope(task_id: str, *, mono_ns: int = 1_000_000) -> EventEnvelope:
    """Build a task.created envelope."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import TaskCreatedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(42)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=task_id, title="test"),
        request_id=rid,
    )


def _task_completed_envelope(task_id: str, *, mono_ns: int = 2_000_000) -> EventEnvelope:
    """Build a task.completed envelope."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import TaskCompletedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(77)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(task_id=task_id, summary="done"),
        request_id=rid,
    )


def _service_crashed_envelope(*, mono_ns: int = 3_000_000) -> EventEnvelope:
    """Build a service.crashed envelope (non-task event)."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import ServiceCrashedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(11)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="service.crashed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ServiceCrashedPayload(service="worker", exit_code=1),
        request_id=rid,
    )


def _make_sink(
    *,
    outbound: object | None = None,
    registry_response: dict[str, object] | None = None,
    registry_status: int = 200,
    base_dir: Path | None = None,
) -> TelegramSink:
    """Build a TelegramSink with mocked outbound + http_client."""
    import httpx

    outbound_mock = outbound or MagicMock()
    outbound_mock.send_to_thread = AsyncMock()

    resp_data = registry_response or {"chat_id": -1001, "reply_to_message_id": 42}

    async def _registry_get(url: str, **kwargs: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        return httpx.Response(
            status_code=registry_status,
            json=resp_data,
            request=req,
        )

    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(side_effect=_registry_get)

    return TelegramSink(
        base_dir=base_dir or Path("/nonexistent"),
        registry_api_url="http://registry-api:8080",
        http_client=http_client,
        outbound=outbound_mock,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. Happy-path dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_dispatches_on_task_event() -> None:
    """AC-7: task.completed event with binding → send_to_thread called with correct args."""
    rng = Random(1)
    clk = FrozenClock(mono_ns=1, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": -1001, "reply_to_message_id": 42},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_called_once()
    call_kwargs = outbound_mock.send_to_thread.call_args[1]
    assert call_kwargs["chat_id"] == -1001
    assert call_kwargs["reply_to_message_id"] == 42
    assert task_id in call_kwargs["text"]
    assert "task.completed" in call_kwargs["text"]


# ---------------------------------------------------------------------------
# 2. Skip on missing chat_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_when_chat_id_is_none() -> None:
    """AC-7: registry returns chat_id=null → send_to_thread NOT called (no binding)."""
    rng = Random(2)
    clk = FrozenClock(mono_ns=2, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": None, "reply_to_message_id": 42},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Skip on missing reply_to_message_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_when_reply_to_is_none() -> None:
    """AC-7: registry returns reply_to_message_id=null → send_to_thread NOT called."""
    rng = Random(3)
    clk = FrozenClock(mono_ns=3, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": -1001, "reply_to_message_id": None},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Skip non-task event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_non_task_event() -> None:
    """AC-7: service.crashed event does not start with 'task.' → no dispatch."""
    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(outbound=outbound_mock)

    env = _service_crashed_envelope()
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Placeholder renderer output shape
# ---------------------------------------------------------------------------


def test_render_placeholder_output_shape() -> None:
    """AC-7: _render returns 'Task {task_id}: {event_type}' with HTML escaping."""
    result = _render("t-00000000-0000-7000-8000-000000000001", "task.completed")
    assert result == "Task t-00000000-0000-7000-8000-000000000001: task.completed"


def test_render_html_escapes_special_chars() -> None:
    """AC-7: _render HTML-escapes task_id and event_type (Story 3.5 H5 carry-forward)."""
    result = _render("t-<hack>", "task.<evil>")
    assert "<hack>" not in result
    assert "&lt;hack&gt;" in result
    assert "&lt;evil&gt;" in result
