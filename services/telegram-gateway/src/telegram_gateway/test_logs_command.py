"""Tests for /logs command handler (Story 3.15 AC-9).

Coverage (>=17 tests):
RegistryAPIClient.get_logs_digest tests (4):
- test_get_logs_digest_success — mock transport returns 200 with valid digest JSON
- test_get_logs_digest_404_raises — mock transport returns 404; HTTPStatusError raised
- test_get_logs_digest_malformed_json_raises_registry_response_error — 200 with invalid body
- test_get_logs_digest_sends_request_id_header — verify X-Request-ID header present

Handler tests (12):
- test_handle_logs_success_renders_digest — digest text + task_id in <code>
- test_handle_logs_success_with_truncation_notice — truncated=True shows truncation notice
- test_handle_logs_truncated_true_with_large_digest_preserves_cli_command
  — truncated+overflow preserves CLI escape hatch
- test_handle_logs_local_truncation_without_server_flag_shows_escape_hatch
  — truncated=False+overflow still shows CLI escape hatch
- test_handle_logs_no_args_shows_usage — "/logs" → usage reply
- test_handle_logs_invalid_task_id_shows_usage — "/logs bad" → usage with example
- test_handle_logs_404_returns_placeholder — HTTPStatusError 404 → placeholder message
- test_handle_logs_404_placeholder_contains_task_id — task_id in <code> in placeholder
- test_handle_logs_network_error — ReadTimeout → "Could not reach registry"
- test_handle_logs_too_many_redirects — TooManyRedirects → "Registry unreachable"
- test_handle_logs_5xx_replies_retry_message — 500 via format_http_error
- test_handle_logs_malformed_response — RegistryResponseError → "malformed response"
- test_handle_logs_unexpected_exception — RuntimeError backstop → "Unexpected error"

HTML security test (1):
- test_handle_logs_html_chars_are_escaped — digest with HTML chars escaped

Router tests (1):
- test_make_logs_router_returns_fresh_routers — factory produces distinct instances
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.logs_command import handle_logs, make_logs_router
from telegram_gateway.handlers.registry_client import (
    LogsDigestResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"

_VALID_DIGEST_JSON = json.dumps(
    {
        "task_id": _TASK_ID,
        "digest": "Task started → planning phase\nPlan approved → executing\n"
        "Tests failing: assert x == y\nAgent attempting fix…",
        "truncated": False,
        "line_count": 4,
    }
)


def _make_message(
    *,
    text: str = "/logs",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /logs tests."""
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
    body: str = _VALID_DIGEST_JSON,
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
# RegistryAPIClient.get_logs_digest tests (4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_logs_digest_success() -> None:
    """Mock transport returns 200 with valid digest JSON; assert parsed fields match."""
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=_VALID_DIGEST_JSON.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        result = await client.get_logs_digest(task_id=_TASK_ID)

    assert isinstance(result, LogsDigestResponseLocal)
    assert result.task_id == _TASK_ID
    assert result.truncated is False
    assert result.line_count == 4
    assert "planning phase" in result.digest


@pytest.mark.asyncio
async def test_get_logs_digest_404_raises() -> None:
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
            await client.get_logs_digest(task_id=_TASK_ID)
        assert exc_info.value.response.status_code == 404


@pytest.mark.asyncio
async def test_get_logs_digest_malformed_json_raises_registry_response_error() -> None:
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
            await client.get_logs_digest(task_id=_TASK_ID)


@pytest.mark.asyncio
async def test_get_logs_digest_sends_request_id_header() -> None:
    """Verify X-Request-ID header present when provided."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=200,
            content=_VALID_DIGEST_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        await client.get_logs_digest(task_id=_TASK_ID, request_id="test-rid-logs-01")

    assert captured["rid"] == "test-rid-logs-01", (
        f"Expected X-Request-ID 'test-rid-logs-01', got {captured['rid']!r}"
    )


# ---------------------------------------------------------------------------
# Handler tests (10+)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_logs_success_renders_digest() -> None:
    """Mock client returns full LogsDigestResponseLocal; assert reply contains digest + task_id."""
    async with _make_registry_client() as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert f"<code>{_TASK_ID}</code>" in reply_text
    assert "planning phase" in reply_text
    assert "executing" in reply_text
    # No truncation notice when truncated=False
    assert "truncated" not in reply_text.lower()


@pytest.mark.asyncio
async def test_handle_logs_success_with_truncation_notice() -> None:
    """truncated=True; assert truncation notice in reply."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "digest": "Line 1\nLine 2\nLine 3",
            "truncated": True,
            "line_count": 3,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "truncated" in reply_text.lower()
    assert "oh-my-bmad-cli events" in reply_text


@pytest.mark.asyncio
async def test_handle_logs_truncated_true_with_large_digest_preserves_cli_command() -> None:
    """truncated=True + digest exceeding _MAX_REPLY_LEN preserves CLI escape hatch."""
    from telegram_gateway.handlers.logs_command import _MAX_REPLY_LEN

    large_digest = "A" * (_MAX_REPLY_LEN + 2000)
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "digest": large_digest,
            "truncated": True,
            "line_count": 5,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert len(reply_text) <= _MAX_REPLY_LEN, (
        f"Reply length {len(reply_text)} exceeds _MAX_REPLY_LEN {_MAX_REPLY_LEN}"
    )
    assert "oh-my-bmad-cli events" in reply_text, (
        "CLI escape hatch missing from truncated reply"
    )
    assert "truncated" in reply_text.lower()


@pytest.mark.asyncio
async def test_handle_logs_local_truncation_without_server_flag_shows_escape_hatch() -> None:
    """truncated=False but digest exceeds _MAX_REPLY_LEN → CLI escape hatch still appears."""
    from telegram_gateway.handlers.logs_command import _MAX_REPLY_LEN

    large_digest = "B" * (_MAX_REPLY_LEN + 1000)
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "digest": large_digest,
            "truncated": False,
            "line_count": 3,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert len(reply_text) <= _MAX_REPLY_LEN, (
        f"Reply length {len(reply_text)} exceeds _MAX_REPLY_LEN {_MAX_REPLY_LEN}"
    )
    assert "oh-my-bmad-cli events" in reply_text, (
        "CLI escape hatch missing when local truncation occurs without server flag"
    )
    assert "truncated" in reply_text.lower()


@pytest.mark.asyncio
async def test_handle_logs_no_args_shows_usage() -> None:
    """Message text is '/logs'; assert usage reply."""
    async with _make_registry_client() as client:
        msg = _make_message(text="/logs")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "Usage: /logs <task-id>", f"Unexpected reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_logs_invalid_task_id_shows_usage() -> None:
    """Message text is '/logs not-a-task-id'; assert usage reply with example."""
    async with _make_registry_client() as client:
        msg = _make_message(text="/logs not-a-task-id")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /logs <task-id>" in reply_text, f"Unexpected reply: {reply_text!r}"
    assert "example:" in reply_text, f"Expected 'example:' in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_logs_404_returns_placeholder() -> None:
    """HTTPStatusError 404 → placeholder message with 'not yet available'."""
    async with _make_registry_client() as client:
        exc = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/x/logs/digest"),
            response=httpx.Response(404, content=b'{"detail":"not found"}'),
        )
        client.get_logs_digest = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "not yet available" in reply_text, f"Expected 'not yet available', got: {reply_text!r}"
    assert "oh-my-bmad-cli events" in reply_text


@pytest.mark.asyncio
async def test_handle_logs_404_placeholder_contains_task_id() -> None:
    """404 placeholder has task_id in <code> tags."""
    async with _make_registry_client() as client:
        exc = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/x/logs/digest"),
            response=httpx.Response(404, content=b'{"detail":"not found"}'),
        )
        client.get_logs_digest = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert f"<code>{_TASK_ID}</code>" in reply_text, (
        f"task_id not in <code> for placeholder: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_logs_network_error() -> None:
    """ReadTimeout → 'Could not reach registry' reply."""
    async with _make_registry_client(raise_exc=httpx.ReadTimeout("timed out")) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry" in reply_text, (
        f"Expected 'Could not reach registry', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_logs_too_many_redirects() -> None:
    """TooManyRedirects → 'Registry unreachable' reply."""
    async with _make_registry_client(raise_exc=httpx.TooManyRedirects("loop")) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Registry unreachable" in reply_text, (
        f"Expected 'Registry unreachable', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_logs_5xx_replies_retry_message() -> None:
    """500 → reply starts with '⚠️'."""
    async with _make_registry_client(
        status_code=500,
        body="",
    ) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️"), f"Expected '⚠️' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_logs_malformed_response() -> None:
    """RegistryResponseError → 'malformed response' reply."""
    async with _make_registry_client() as client:
        client.get_logs_digest = AsyncMock(  # type: ignore[method-assign]
            side_effect=RegistryResponseError("malformed body")
        )
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "malformed response" in reply_text, f"Expected 'malformed response', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_logs_unexpected_exception() -> None:
    """RuntimeError backstop → 'Unexpected error' reply."""
    async with _make_registry_client() as client:
        client.get_logs_digest = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Unexpected error" in reply_text, f"Expected 'Unexpected error', got: {reply_text!r}"


# ---------------------------------------------------------------------------
# HTML security test (1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_logs_html_chars_are_escaped() -> None:
    """Digest text containing HTML chars is escaped in the reply."""
    body = json.dumps(
        {
            "task_id": _TASK_ID,
            "digest": "Agent ran: <script>alert(1)</script>\nFound A & B < C",
            "truncated": False,
            "line_count": 2,
        }
    )
    async with _make_registry_client(body=body) as client:
        msg = _make_message(text=f"/logs {_TASK_ID}")
        await handle_logs(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<script>" not in reply_text, f"Raw '<script>' found: {reply_text!r}"
    assert "&lt;script&gt;" in reply_text, f"Escaped script tag missing: {reply_text!r}"
    assert "&amp; B" in reply_text, f"Escaped ampersand missing: {reply_text!r}"


# ---------------------------------------------------------------------------
# Router tests (1)
# ---------------------------------------------------------------------------


def test_make_logs_router_returns_fresh_routers() -> None:
    """Each call returns a distinct Router instance (no shared state)."""
    r1 = make_logs_router()
    r2 = make_logs_router()
    assert r1 is not r2, "Router factory must return fresh instances"
