"""Unit tests for TelegramOutbound — Story 3.9 AC-6 / AC-9.

5 tests:
1. Happy-path send — 200 response → no exception, no emit.
2. 429 retry — first attempt 429, second attempt 200 → succeeds without raise.
3. 500 retry — first attempt 500, second attempt 200 → succeeds without raise.
4. Network error retry — first attempt TransportError, second attempt 200 → succeeds.
5. Terminal failure — all 3 attempts fail → emit callback invoked, no raise.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from clawhip_daemon.adapters.telegram_outbound import TelegramOutbound

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot_token(value: str = "test-token-123") -> MagicMock:
    """Build a minimal AuditedSecret mock that returns *value* from .value."""
    secret = MagicMock()
    secret.value = value
    return secret


@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Lifespan-managed AsyncClient — proper teardown per Story 3.4 M10."""
    async with httpx.AsyncClient() as client:
        yield client


# ---------------------------------------------------------------------------
# 1. Happy-path send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_thread_happy_path() -> None:
    """AC-6: 200 response → no exception raised; payload sent correctly."""
    captured: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(status_code=200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        outbound = TelegramOutbound(
            bot_token=_make_bot_token("mytoken"),
            http_client=client,
        )
        await outbound.send_to_thread(
            chat_id=-1001,
            reply_to_message_id=42,
            text="Task t-abc: task.completed",
        )

    assert "botmytoken/sendMessage" in captured["url"]  # type: ignore[operator]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["chat_id"] == -1001
    assert body["reply_to_message_id"] == 42
    assert body["text"] == "Task t-abc: task.completed"
    assert body["parse_mode"] == "HTML"


# ---------------------------------------------------------------------------
# 2. 429 retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_thread_retries_on_429() -> None:
    """AC-6: first attempt returns 429; second attempt returns 200 → no exception."""
    call_count = 0

    async def _transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(status_code=429, json={"ok": False}, request=request)
        return httpx.Response(status_code=200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        outbound = TelegramOutbound(
            bot_token=_make_bot_token(),
            http_client=client,
        )
        await outbound.send_to_thread(chat_id=100, reply_to_message_id=1, text="retry test")

    assert call_count == 2


# ---------------------------------------------------------------------------
# 3. 500 retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_thread_retries_on_500() -> None:
    """AC-6: first attempt returns 500; second attempt returns 200 → no exception."""
    call_count = 0

    async def _transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(status_code=500, json={"ok": False}, request=request)
        return httpx.Response(status_code=200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        outbound = TelegramOutbound(
            bot_token=_make_bot_token(),
            http_client=client,
        )
        await outbound.send_to_thread(chat_id=100, reply_to_message_id=1, text="500 retry")

    assert call_count == 2


# ---------------------------------------------------------------------------
# 4. Network error retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_thread_retries_on_network_error() -> None:
    """AC-6: first attempt raises TransportError; second attempt succeeds → no exception."""
    call_count = 0

    async def _transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status_code=200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        outbound = TelegramOutbound(
            bot_token=_make_bot_token(),
            http_client=client,
        )
        await outbound.send_to_thread(chat_id=100, reply_to_message_id=1, text="network retry")

    assert call_count == 2


# ---------------------------------------------------------------------------
# 5. Terminal failure → emit called, no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_thread_terminal_failure_emits_and_returns() -> None:
    """AC-6: all 3 attempts fail → emit callback invoked; nothing raised to caller."""
    call_count = 0

    async def _transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=500, json={"ok": False}, request=request)

    emit_mock = AsyncMock()

    async with httpx.AsyncClient(transport=httpx.MockTransport(_transport)) as client:
        outbound = TelegramOutbound(
            bot_token=_make_bot_token(),
            http_client=client,
            emit=emit_mock,
        )
        # Must NOT raise even though all retries exhausted.
        await outbound.send_to_thread(chat_id=100, reply_to_message_id=1, text="terminal failure")

    assert call_count == 3  # all 3 attempts exhausted
    emit_mock.assert_called_once()  # sink.delivery_failed emission triggered
    payload = emit_mock.call_args[0][0]
    # H6 review fix: emit now receives a typed SinkDeliveryFailedPayload model.
    assert payload.sink_name == "telegram"
    assert payload.consecutive_failures == 1
