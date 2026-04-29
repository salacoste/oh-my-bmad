"""Tests for /task command handler and RegistryAPIClient (Story 3.3 AC-9, AC-12).

Coverage:
- test_task_handler_replies_with_task_id (AC-12)
- test_task_handler_uses_message_id_for_idempotency_key (AC-12)
- test_task_handler_propagates_request_id (AC-12)
- test_task_handler_empty_description_replies_usage (AC-12)
- test_task_handler_whitespace_only_description_replies_usage (AC-12)
- test_task_handler_idempotency_replayed_appends_suffix (AC-12)
- test_task_handler_4xx_replies_rejected_message (AC-12)
- test_task_handler_5xx_replies_retry_message (AC-12)
- test_task_handler_timeout_replies_unreachable (AC-12)
- test_task_handler_latency_under_p95_budget (AC-9) — marked @pytest.mark.slow
- test_registry_client_reuses_http_session (AC-12)
- test_idempotency_key_from_message_format (AC-12)
- test_format_http_error_409 (AC-8)
- test_format_http_error_4xx_no_detail (AC-8)
- test_format_http_error_5xx (AC-8)
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
)
from telegram_gateway.handlers.task_command import (
    _format_http_error,
    _idempotency_key_from_message,
    handle_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TASK_ID = "t-00000000-0000-7000-8000-000000000001"
_FAKE_EVENT_ID = "e-00000000-0000-7000-8000-000000000002"
_FAKE_CREATED_AT = "2024-01-01T00:00:00Z"

_VALID_RESPONSE_JSON = json.dumps(
    {
        "task_id": _FAKE_TASK_ID,
        "event_id": _FAKE_EVENT_ID,
        "created_at": _FAKE_CREATED_AT,
    }
)


def _make_message(
    *,
    text: str = "/task hello world",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
) -> MagicMock:
    """Build a minimal aiogram Message mock."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_registry_client(
    *,
    status_code: int = 201,
    body: str = _VALID_RESPONSE_JSON,
    headers: dict[str, str] | None = None,
    raise_exc: Exception | None = None,
) -> RegistryAPIClient:
    """Build a RegistryAPIClient backed by a fake httpx transport."""

    if raise_exc is not None:

        async def _transport(request: httpx.Request) -> httpx.Response:
            raise raise_exc

    else:

        def _transport(request: httpx.Request) -> httpx.Response:  # type: ignore[misc]
            return httpx.Response(
                status_code=status_code,
                content=body.encode(),
                headers=headers or {},
                request=request,
            )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    return RegistryAPIClient(
        base_url="http://registry-api:8080",
        http_client=http_client,
    )


def _make_bot() -> MagicMock:
    bot = MagicMock()
    return bot


# ---------------------------------------------------------------------------
# Unit: _idempotency_key_from_message
# ---------------------------------------------------------------------------


def test_idempotency_key_from_message_format() -> None:
    """AC-12: key format is 'telegram-{chat_id}-{message_id}'."""
    msg = _make_message(chat_id=12345, message_id=99)
    key = _idempotency_key_from_message(msg)
    assert key == "telegram-12345-99"


# ---------------------------------------------------------------------------
# Unit: _format_http_error
# ---------------------------------------------------------------------------


def _make_http_status_error(
    status: int,
    body: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://registry-api:8080/v1/tasks")
    response = httpx.Response(
        status_code=status,
        content=body.encode(),
        headers=headers or {"content-type": "application/json"},
        request=request,
    )
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def test_format_http_error_409_with_task_id() -> None:
    """AC-8: 409 with task_id in body surfaces the stored result."""
    exc = _make_http_status_error(409, body=json.dumps({"task_id": "t-abc"}))
    result = _format_http_error(exc)
    assert "Duplicate idempotency key" in result
    assert "t-abc" in result


def test_format_http_error_409_no_body_task_id() -> None:
    """AC-8: 409 without task_id in body still surfaces collision message."""
    exc = _make_http_status_error(409, body=json.dumps({}))
    result = _format_http_error(exc)
    assert "Duplicate idempotency key" in result


def test_format_http_error_4xx_with_detail() -> None:
    """AC-8: 422 with RFC 7807 detail parses the detail field."""
    exc = _make_http_status_error(422, body=json.dumps({"detail": "title too long"}))
    result = _format_http_error(exc)
    assert result.startswith("⚠️ Task rejected:")
    assert "title too long" in result


def test_format_http_error_4xx_no_detail() -> None:
    """AC-8: 400 without JSON body falls back to HTTP status."""
    exc = _make_http_status_error(400, body="bad request")
    result = _format_http_error(exc)
    assert result == "⚠️ Task rejected: HTTP 400"


def test_format_http_error_5xx() -> None:
    """AC-8: 500 surfaces registry-unavailable message."""
    exc = _make_http_status_error(500, body="")
    result = _format_http_error(exc)
    assert "⚠️ Registry unavailable: HTTP 500" in result


# ---------------------------------------------------------------------------
# Unit: RegistryAPIClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_client_reuses_http_session() -> None:
    """AC-12: same http_client identity across two create_task calls."""
    client = _make_registry_client()
    original_http_client = client.http_client

    await client.create_task(
        description="first",
        idempotency_key="key-1",
        operator_actor_id="999",
        request_id="req-1",
    )
    assert client.http_client is original_http_client, (
        "http_client was re-instantiated between calls"
    )


@pytest.mark.asyncio
async def test_registry_client_sets_idempotency_key_header() -> None:
    """RegistryAPIClient forwards the idempotency_key as Idempotency-Key header."""
    captured_headers: dict[str, str] = {}

    def _transport(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    client = RegistryAPIClient(base_url="http://registry-api:8080", http_client=http_client)
    await client.create_task(
        description="hello",
        idempotency_key="telegram-100-42",
        operator_actor_id="999",
        request_id="req-abc",
    )
    assert captured_headers.get("idempotency-key") == "telegram-100-42"
    assert captured_headers.get("x-request-id") == "req-abc"


@pytest.mark.asyncio
async def test_registry_client_parses_replayed_status() -> None:
    """RegistryAPIClient sets idempotency_status='replayed' from header."""
    client = _make_registry_client(
        headers={"X-Idempotency-Status": "replayed", "content-type": "application/json"},
    )
    result = await client.create_task(
        description="dup",
        idempotency_key="key",
        operator_actor_id="1",
    )
    assert result.idempotency_status == "replayed"


@pytest.mark.asyncio
async def test_registry_client_raises_on_non_2xx() -> None:
    """RegistryAPIClient raises HTTPStatusError on 4xx/5xx."""
    client = _make_registry_client(
        status_code=422,
        body=json.dumps({"detail": "title too short"}),
        headers={"content-type": "application/json"},
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.create_task(
            description="x",
            idempotency_key="k",
            operator_actor_id="1",
        )
    assert exc_info.value.response.status_code == 422


# ---------------------------------------------------------------------------
# Unit: handle_task handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_handler_empty_description_replies_usage() -> None:
    """AC-12: /task (no args) replies with usage string."""
    msg = _make_message(text="/task")
    await handle_task(msg, _make_bot(), _make_registry_client())
    msg.reply.assert_called_once_with("Usage: /task <description>")


@pytest.mark.asyncio
async def test_task_handler_whitespace_only_description_replies_usage() -> None:
    """AC-12: '/task   ' (spaces only) replies with usage string."""
    msg = _make_message(text="/task   ")
    await handle_task(msg, _make_bot(), _make_registry_client())
    msg.reply.assert_called_once_with("Usage: /task <description>")


@pytest.mark.asyncio
async def test_task_handler_replies_with_task_id() -> None:
    """AC-12: successful /task replies with task_id in HTML <code> tag."""
    msg = _make_message(text="/task hello world")
    client = _make_registry_client()
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert _FAKE_TASK_ID in reply_text
    assert "<code>" in reply_text
    assert "created" in reply_text


@pytest.mark.asyncio
async def test_task_handler_uses_message_id_for_idempotency_key() -> None:
    """AC-12: outbound request carries Idempotency-Key: telegram-{chat}-{msg}."""
    captured: dict[str, str] = {}

    def _transport(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("idempotency-key", "")
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    client = RegistryAPIClient(base_url="http://registry-api:8080", http_client=http_client)
    msg = _make_message(text="/task do something", message_id=77, chat_id=555)
    await handle_task(msg, _make_bot(), client)
    assert captured["key"] == "telegram-555-77"


@pytest.mark.asyncio
async def test_task_handler_propagates_request_id() -> None:
    """AC-12: outbound request has X-Request-ID matching a UUIDv7-like pattern."""
    import re

    captured: dict[str, str] = {}

    def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    client = RegistryAPIClient(base_url="http://registry-api:8080", http_client=http_client)
    msg = _make_message(text="/task build something")
    await handle_task(msg, _make_bot(), client)
    rid = captured["rid"]
    assert rid, "X-Request-ID header was not sent"
    # UUIDv7 pattern: xxxxxxxx-xxxx-7xxx-yxxx-xxxxxxxxxxxx
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert uuid_pattern.match(rid), f"X-Request-ID {rid!r} does not match UUIDv7 pattern"


@pytest.mark.asyncio
async def test_task_handler_idempotency_replayed_appends_suffix() -> None:
    """AC-12: replayed response appends '(retry deduped)' to reply."""
    client = _make_registry_client(
        headers={"X-Idempotency-Status": "replayed", "content-type": "application/json"},
    )
    msg = _make_message(text="/task retry me")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "(retry deduped)" in reply_text


@pytest.mark.asyncio
async def test_task_handler_4xx_replies_rejected_message() -> None:
    """AC-12: 422 with RFC 7807 body replies with '⚠️ Task rejected:' prefix."""
    client = _make_registry_client(
        status_code=422,
        body=json.dumps({"detail": "title required"}),
        headers={"content-type": "application/json"},
    )
    msg = _make_message(text="/task something")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ Task rejected:")


@pytest.mark.asyncio
async def test_task_handler_5xx_replies_retry_message() -> None:
    """AC-12: 500 reply matches '⚠️ Registry unavailable: HTTP 500'."""
    client = _make_registry_client(status_code=500, body="")
    msg = _make_message(text="/task something")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "⚠️ Registry unavailable: HTTP 500" in reply_text


@pytest.mark.asyncio
async def test_task_handler_timeout_replies_unreachable() -> None:
    """AC-12: ReadTimeout → reply matches '⚠️ Could not reach registry: ReadTimeout'."""
    client = _make_registry_client(raise_exc=httpx.ReadTimeout("timed out"))
    msg = _make_message(text="/task something")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "⚠️ Could not reach registry: ReadTimeout" in reply_text


@pytest.mark.asyncio
async def test_task_handler_always_returns_normally_on_error() -> None:
    """Story 3.1 M3: handler NEVER raises; caller always gets None back."""
    client = _make_registry_client(status_code=500, body="")
    msg = _make_message(text="/task trigger 500")
    # handle_task is declared -> None; the key guarantee is it did not raise.
    # We assert the reply was called (error surfaced to user, not propagated).
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()


# ---------------------------------------------------------------------------
# NFR-P2 latency test (AC-9) — marked @pytest.mark.slow
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_task_handler_latency_under_p95_budget() -> None:
    """AC-9 / NFR-P2: p95 of 100 sequential /task invocations < 1.0 s.

    Registry mock responds in ~200 ms (asyncio.sleep) to simulate realistic
    registry-api latency. Measures the handler body only (not network); the
    200 ms mock represents realistic registry-api latency.

    Asserts p95 < 1.0 s (1.5 s headroom before the 2.5 s NFR-P2 threshold).
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.200)  # 200 ms simulated registry latency
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_slow_transport),
    )
    client = RegistryAPIClient(base_url="http://registry-api:8080", http_client=http_client)
    bot = _make_bot()

    latencies: list[float] = []
    n = 100
    for i in range(n):
        msg = _make_message(text=f"/task bench iteration {i}", message_id=i + 1)
        t0 = time.perf_counter()
        await handle_task(msg, bot, client)
        latencies.append(time.perf_counter() - t0)

    latencies.sort()
    p95_index = int(0.95 * n) - 1  # 0-based index for 95th percentile
    p95 = latencies[p95_index]
    assert p95 < 1.0, (
        f"NFR-P2: p95 latency {p95:.3f} s exceeds 1.0 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )
