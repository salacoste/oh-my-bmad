"""Tests for /status command handler (Story 3.14 AC-8).

Coverage (>=18 tests):
RegistryAPIClient.get_task tests (4):
- test_get_task_success — mock transport returns 200 with valid TaskResponse JSON
- test_get_task_404_raises — mock transport returns 404; HTTPStatusError raised
- test_get_task_malformed_json_raises_registry_response_error — 200 with invalid body
- test_get_task_sends_request_id_header — verify X-Request-ID header present

Handler tests (14+):
- test_handle_status_success_renders_all_fields — all rendered fields + datetime labels
- test_handle_status_no_title_shows_none — title=None → "(none)"
- test_handle_status_no_last_event_shows_none — last_event=None → "(none)"
- test_handle_status_empty_next_commands_shows_none — next_commands=[] → "(none)"
- test_handle_status_multiple_commands_renders_comma_separated — multi cmds
- test_handle_status_no_args_shows_usage — "/status" → usage reply
- test_handle_status_invalid_task_id_shows_usage — "/status bad" → usage with example
- test_handle_status_task_not_found_404 — HTTPStatusError 404 → "Task not found"
- test_handle_status_network_error — ReadTimeout → "Could not reach registry"
- test_handle_status_too_many_redirects — TooManyRedirects → "Registry unreachable"
- test_handle_status_5xx_replies_retry_message — 500 via format_http_error
- test_handle_status_malformed_response — RegistryResponseError → "malformed response"
- test_handle_status_unexpected_exception — RuntimeError backstop → "Unexpected error"
- test_handle_status_html_chars_are_escaped — all string fields escaped

Router tests (1):
- test_make_status_router_returns_fresh_routers — factory produces distinct instances
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
    RegistryResponseError,
    TaskResponseLocal,
)
from telegram_gateway.handlers.status_command import handle_status, make_status_router

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"

_VALID_TASK_JSON = json.dumps(
    {
        "task_id": _TASK_ID,
        "status": "executing",
        "title": "Deploy staging",
        "created_at": "2026-05-01T12:00:00+00:00",
        "updated_at": "2026-05-01T12:30:00+00:00",
        "actor": {"kind": "operator", "id": "12345"},
        "last_event": {
            "id": "e-abc123",
            "type": "task.started",
            "emitted_at": "2026-05-01T12:30:00+00:00",
        },
        "next_commands": ["stop"],
        "chat_id": None,
        "reply_to_message_id": None,
    }
)


def _make_message(
    *,
    text: str = "/status",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /status tests."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.reply = AsyncMock(return_value=None)
    return msg


@asynccontextmanager
async def _make_registry_client(
    *,
    status_code: int = 200,
    body: str = _VALID_TASK_JSON,
    raise_exc: Exception | None = None,
) -> AsyncIterator[RegistryAPIClient]:
    """Build a RegistryAPIClient backed by a fake httpx transport (proper teardown)."""
    if raise_exc is not None:

        async def _transport_raise(request: httpx.Request) -> httpx.Response:
            raise raise_exc

        transport_fn = _transport_raise
    else:

        async def _transport_ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=status_code,
                content=body.encode(),
                request=request,
            )

        transport_fn = _transport_ok

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(transport_fn),
    ) as http_client:
        yield RegistryAPIClient(http_client=http_client)


# ---------------------------------------------------------------------------
# RegistryAPIClient.get_task tests (4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_success() -> None:
    """Mock transport returns 200 with valid TaskResponse JSON; assert parsed fields match."""
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=_VALID_TASK_JSON.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        result = await client.get_task(task_id=_TASK_ID)

    assert isinstance(result, TaskResponseLocal)
    assert result.task_id == _TASK_ID
    assert result.status == "executing"
    assert result.title == "Deploy staging"
    assert result.actor.kind == "operator"
    assert result.actor.id == "12345"
    assert result.last_event is not None
    assert result.last_event.type == "task.started"
    assert result.next_commands == ["stop"]


@pytest.mark.asyncio
async def test_get_task_404_raises() -> None:
    """Mock transport returns 404; assert HTTPStatusError raised."""
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=404,
                content=json.dumps({"detail": "not found"}).encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_task(task_id=_TASK_ID)
        assert exc_info.value.response.status_code == 404


@pytest.mark.asyncio
async def test_get_task_malformed_json_raises_registry_response_error() -> None:
    """200 with invalid JSON body; assert RegistryResponseError raised."""
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=b"{}",
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(RegistryResponseError):
            await client.get_task(task_id=_TASK_ID)


@pytest.mark.asyncio
async def test_get_task_sends_request_id_header() -> None:
    """Verify X-Request-ID header present when provided."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=200,
            content=_VALID_TASK_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        await client.get_task(task_id=_TASK_ID, request_id="test-rid-12345")

    assert captured["rid"] == "test-rid-12345", (
        f"Expected X-Request-ID 'test-rid-12345', got {captured['rid']!r}"
    )


# ---------------------------------------------------------------------------
# Handler tests (14+)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_status_success_renders_all_fields() -> None:
    """Mock client returns full TaskResponseLocal; assert reply contains all rendered fields."""
    async with _make_registry_client() as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert f"<code>{_TASK_ID}</code>" in reply_text, (
        f"task_id not wrapped in <code>: {reply_text!r}"
    )
    assert "executing" in reply_text, f"status missing from reply: {reply_text!r}"
    assert "Deploy staging" in reply_text, f"title missing from reply: {reply_text!r}"
    assert "Created: 2026-05-01" in reply_text, f"Created label/date missing: {reply_text!r}"
    assert "Updated: 2026-05-01" in reply_text, f"Updated label/date missing: {reply_text!r}"
    assert "operator/12345" in reply_text, f"actor missing from reply: {reply_text!r}"
    assert "task.started at 2026-05-01" in reply_text, f"last_event missing: {reply_text!r}"
    assert "/stop" in reply_text, f"next_commands missing: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_no_title_shows_none() -> None:
    """title=None; assert '(none)' rendered."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "status": "pending",
            "title": None,
            "created_at": "2026-05-01T12:00:00+00:00",
            "updated_at": "2026-05-01T12:00:00+00:00",
            "actor": {"kind": "operator", "id": "1"},
            "last_event": None,
            "next_commands": [],
            "chat_id": None,
            "reply_to_message_id": None,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Title: (none)" in reply_text, f"Expected 'Title: (none)', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_no_last_event_shows_none() -> None:
    """last_event=None; assert '(none)' rendered."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "status": "pending",
            "title": "Test",
            "created_at": "2026-05-01T12:00:00+00:00",
            "updated_at": "2026-05-01T12:00:00+00:00",
            "actor": {"kind": "operator", "id": "1"},
            "last_event": None,
            "next_commands": ["stop"],
            "chat_id": None,
            "reply_to_message_id": None,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Last event: (none)" in reply_text, f"Expected 'Last event: (none)', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_empty_next_commands_shows_none() -> None:
    """next_commands=[]; assert '(none)' rendered."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "status": "completed",
            "title": "Done",
            "created_at": "2026-05-01T12:00:00+00:00",
            "updated_at": "2026-05-01T12:30:00+00:00",
            "actor": {"kind": "operator", "id": "1"},
            "last_event": {
                "id": "e-1",
                "type": "task.completed",
                "emitted_at": "2026-05-01T12:30:00+00:00",
            },
            "next_commands": [],
            "chat_id": None,
            "reply_to_message_id": None,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Available: (none)" in reply_text, f"Expected 'Available: (none)', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_multiple_commands_renders_comma_separated() -> None:
    """next_commands=['stop','reject']; assert '/stop, /reject' in reply."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "status": "plan_ready",
            "title": "Plan ready",
            "created_at": "2026-05-01T12:00:00+00:00",
            "updated_at": "2026-05-01T12:30:00+00:00",
            "actor": {"kind": "operator", "id": "1"},
            "last_event": None,
            "next_commands": ["approve", "reject", "stop"],
            "chat_id": None,
            "reply_to_message_id": None,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "/approve, /reject, /stop" in reply_text, (
        f"Expected '/approve, /reject, /stop', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_status_no_args_shows_usage() -> None:
    """Message text is '/status'; assert usage reply without example."""
    async with _make_registry_client() as client:
        msg = _make_message(text="/status")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "Usage: /status <task-id>", f"Unexpected reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_invalid_task_id_shows_usage() -> None:
    """Message text is '/status not-a-task-id'; assert usage reply with example."""
    async with _make_registry_client() as client:
        msg = _make_message(text="/status not-a-task-id")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /status <task-id>" in reply_text, f"Unexpected reply: {reply_text!r}"
    assert "example:" in reply_text, f"Expected 'example:' in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_task_not_found_404() -> None:
    """Mock raises HTTPStatusError with 404; assert 'Task not found' reply."""
    async with _make_registry_client() as client:
        exc = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/x"),
            response=httpx.Response(404, content=b'{"detail":"not found"}'),
        )
        client.get_task = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Task not found" in reply_text, f"Expected 'Task not found', got: {reply_text!r}"
    assert f"<code>{_TASK_ID}</code>" in reply_text, (
        f"task_id not in <code> for 404: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_status_network_error() -> None:
    """Mock raises httpx.ReadTimeout; assert 'Could not reach registry' reply."""
    async with _make_registry_client(raise_exc=httpx.ReadTimeout("timed out")) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry" in reply_text, (
        f"Expected 'Could not reach registry', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_status_too_many_redirects() -> None:
    """TooManyRedirects → 'Registry unreachable. Try again in a moment.' reply."""
    async with _make_registry_client(raise_exc=httpx.TooManyRedirects("loop")) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Registry unreachable" in reply_text, (
        f"Expected 'Registry unreachable', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_status_5xx_replies_retry_message() -> None:
    """Mock 500 → reply starts with '⚠️ Status query failed' or 'Registry unavailable'."""
    async with _make_registry_client(
        status_code=500,
        body="",
    ) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️"), f"Expected '⚠️' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_malformed_response() -> None:
    """RegistryResponseError → 'Received malformed response from registry.' reply."""
    async with _make_registry_client() as client:
        client.get_task = AsyncMock(  # type: ignore[method-assign]
            side_effect=RegistryResponseError("malformed body")
        )
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "malformed response" in reply_text, f"Expected 'malformed response', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_unexpected_exception() -> None:
    """RuntimeError backstop → 'Unexpected error' reply."""
    async with _make_registry_client() as client:
        client.get_task = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Unexpected error" in reply_text, f"Expected 'Unexpected error', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_status_html_chars_are_escaped() -> None:
    """All externally-sourced string fields are HTML-escaped in the reply."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "status": "<script>alert(1)</script>",
            "title": "A & B < C > D",
            "created_at": "2026-05-01T12:00:00+00:00",
            "updated_at": "2026-05-01T12:30:00+00:00",
            "actor": {"kind": "op<er&ator", "id": "12<script>345"},
            "last_event": {
                "id": "e-1",
                "type": "task.<started>",
                "emitted_at": "2026-05-01T12:30:00+00:00",
            },
            "next_commands": ["stop&go", "rej<ect"],
            "chat_id": None,
            "reply_to_message_id": None,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/status {_TASK_ID}")
        await handle_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # Verify raw HTML tags are absent
    assert "<script>" not in reply_text, f"Raw '<script>' found: {reply_text!r}"
    assert "<started>" not in reply_text, f"Raw '<started>' found: {reply_text!r}"
    # Verify escaped forms are present
    assert "&lt;script&gt;" in reply_text, f"Escaped script tag missing: {reply_text!r}"
    assert "&amp; B" in reply_text, f"Escaped ampersand missing: {reply_text!r}"
    assert "/stop&amp;go" in reply_text, f"Escaped next_command missing: {reply_text!r}"
    assert "/rej&lt;ect" in reply_text, f"Escaped next_command missing: {reply_text!r}"


# ---------------------------------------------------------------------------
# Router tests (1)
# ---------------------------------------------------------------------------


def test_make_status_router_returns_fresh_routers() -> None:
    """Each call returns a distinct Router instance (no shared state)."""
    r1 = make_status_router()
    r2 = make_status_router()
    assert r1 is not r2, "Router factory must return fresh instances"
