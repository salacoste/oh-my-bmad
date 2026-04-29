"""Tests for /ping command handler (Story 3.5 AC-10, AC-11).

Coverage (≥13 tests per AC-10):
- test_ping_handler_replies_with_health_summary — happy path exact template
- test_ping_handler_health_unhealthy_prefixes_warning_emoji — "unhealthy" prefix
- test_ping_handler_health_degraded_no_warning_emoji — "degraded" no prefix
- test_ping_handler_propagates_request_id — X-Request-ID header UUIDv7
- test_ping_handler_does_not_send_idempotency_key — Idempotency-Key absent
- test_ping_handler_5xx_replies_retry_message — 500 via format_http_error
- test_ping_handler_4xx_replies_error_message — 404 via format_http_error
- test_ping_handler_timeout_replies_unreachable — ReadTimeout
- test_ping_handler_replies_with_html_escaped_version — XSS prevention
- test_ping_handler_unexpected_exception_replies_internal_error — RuntimeError backstop
- test_ping_handler_swallows_reply_failure — TelegramError swallowed
- test_ping_handler_latency_under_p95_budget — @pytest.mark.slow NFR-O4
- test_get_platform_health_parses_minimal_response — direct client call
- test_get_platform_health_raises_registry_response_error_on_malformed_body — H1
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers._keys import UUIDV7_BARE_RE as _UUIDV7_RE
from telegram_gateway.handlers.ping_command import handle_ping
from telegram_gateway.handlers.registry_client import (
    HealthResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_VALID_HEALTH_JSON = json.dumps(
    {
        "registry_status": "healthy",
        "worker_status": "idle",
        "clawhip_queue_depth": 3,
        "version": "v1.2.3",
    }
)


def _make_message(
    *,
    text: str = "/ping",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /ping tests."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_registry_client(
    *,
    status_code: int = 200,
    body: str = _VALID_HEALTH_JSON,
    headers: dict[str, str] | None = None,
    raise_exc: Exception | None = None,
) -> RegistryAPIClient:
    """Build a RegistryAPIClient backed by a fake httpx transport."""
    if raise_exc is not None:

        async def _transport_raise(request: httpx.Request) -> httpx.Response:
            raise raise_exc

        transport_fn = _transport_raise
    else:

        async def _transport_ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=status_code,
                content=body.encode(),
                headers=headers or {},
                request=request,
            )

        transport_fn = _transport_ok

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(transport_fn),
    )
    return RegistryAPIClient(http_client=http_client)


# ---------------------------------------------------------------------------
# AC-10 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_handler_replies_with_health_summary() -> None:
    """AC-10: happy path — reply equals exact template from AC-4."""
    client = _make_registry_client()
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    expected = (
        "pong · registry: healthy · worker: idle · clawhip: 3 events queued · version: v1.2.3"
    )
    assert reply_text == expected, f"Expected {expected!r}, got {reply_text!r}"


@pytest.mark.asyncio
async def test_ping_handler_health_unhealthy_prefixes_warning_emoji() -> None:
    """AC-10: registry_status='unhealthy' → reply starts with '⚠️ pong'."""
    body = json.dumps(
        {
            "registry_status": "unhealthy",
            "worker_status": "idle",
            "clawhip_queue_depth": 0,
            "version": "v1.0.0",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ pong"), f"Expected '⚠️ pong' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_ping_handler_health_degraded_no_warning_emoji() -> None:
    """AC-10: registry_status='degraded' → reply starts with 'pong' (no emoji prefix)."""
    body = json.dumps(
        {
            "registry_status": "degraded",
            "worker_status": "busy",
            "clawhip_queue_depth": 5,
            "version": "v1.1.0",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("pong"), f"Expected 'pong' prefix (no emoji), got: {reply_text!r}"
    assert not reply_text.startswith("⚠️"), f"Unexpected emoji prefix for 'degraded': {reply_text!r}"


@pytest.mark.asyncio
async def test_ping_handler_propagates_request_id() -> None:
    """AC-10: X-Request-ID header in outbound GET request is a bare UUIDv7."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=200,
            content=_VALID_HEALTH_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message()
        await handle_ping(msg, registry_client=client)

    rid = captured.get("rid", "")
    assert rid, "X-Request-ID header was not sent"
    assert _UUIDV7_RE.match(rid), f"X-Request-ID {rid!r} does not match UUIDv7 pattern"


@pytest.mark.asyncio
async def test_ping_handler_does_not_send_idempotency_key() -> None:
    """AC-10 / AC-5: Idempotency-Key header is ABSENT from the outbound GET request.

    GET is idempotent by HTTP semantics (RFC 7231 §4.2.2). This is the first
    handler in the bot that deliberately omits the idempotency key.
    """
    captured_headers: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        # Store all header names in lowercase for case-insensitive assertion.
        captured_headers.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(
            status_code=200,
            content=_VALID_HEALTH_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message()
        await handle_ping(msg, registry_client=client)

    assert "idempotency-key" not in captured_headers, (
        f"Idempotency-Key header must NOT be sent for GET /v1/health, "
        f"got headers: {list(captured_headers.keys())}"
    )


@pytest.mark.asyncio
async def test_ping_handler_5xx_replies_retry_message() -> None:
    """AC-10: mock 500 → reply starts with '⚠️ Registry unavailable: HTTP 500'."""
    client = _make_registry_client(status_code=500, body="")
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ Registry unavailable: HTTP 500"), (
        f"Expected '⚠️ Registry unavailable: HTTP 500', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_ping_handler_4xx_replies_error_message() -> None:
    """AC-10: mock 404 → reply rendered by format_http_error (4xx branch)."""
    client = _make_registry_client(
        status_code=404,
        body=json.dumps({"detail": "health endpoint not found"}),
        headers={"content-type": "application/json"},
    )
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # format_http_error 4xx branch → "⚠️ Task rejected: ..."
    assert "⚠️" in reply_text, f"Expected warning emoji in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_ping_handler_timeout_replies_unreachable() -> None:
    """AC-10: ReadTimeout → reply equals '⚠️ Registry unreachable. Try again in a moment.'"""
    client = _make_registry_client(raise_exc=httpx.ReadTimeout("timed out"))
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "⚠️ Registry unreachable. Try again in a moment.", (
        f"Unexpected reply: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_ping_handler_replies_with_html_escaped_version() -> None:
    """AC-10 / H5: version='v1.0.0-<branch>' → reply contains 'v1.0.0-&lt;branch&gt;'."""
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "idle",
            "clawhip_queue_depth": 0,
            "version": "v1.0.0-<branch>",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "v1.0.0-&lt;branch&gt;" in reply_text, (
        f"Expected HTML-escaped version in reply, got: {reply_text!r}"
    )
    assert "<branch>" not in reply_text, (
        f"Raw '<branch>' must not appear in reply, got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_ping_handler_unexpected_exception_replies_internal_error() -> None:
    """AC-10 / AC-8 backstop: RuntimeError → reply contains 'Internal error'."""
    client = _make_registry_client()
    client.get_platform_health = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Internal error" in reply_text, f"Expected 'Internal error', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_ping_handler_swallows_reply_failure() -> None:
    """AC-10 / M3 contract: message.reply raises → handler returns normally without raising."""
    client = _make_registry_client()
    msg = _make_message()
    msg.reply = AsyncMock(side_effect=RuntimeError("telegram down"))

    # Must not raise — _safe_reply swallows the failure.
    await handle_ping(msg, registry_client=client)
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_get_platform_health_parses_minimal_response() -> None:
    """AC-10: direct call to get_platform_health(); assert HealthResponseLocal fields match."""
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=_VALID_HEALTH_JSON.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        result = await client.get_platform_health()

    assert isinstance(result, HealthResponseLocal)
    assert result.registry_status == "healthy"
    assert result.worker_status == "idle"
    assert result.clawhip_queue_depth == 3
    assert result.version == "v1.2.3"


@pytest.mark.asyncio
async def test_get_platform_health_raises_registry_response_error_on_malformed_body() -> None:
    """AC-10 / H1: mock returns '{}' (missing required fields) → RegistryResponseError raised."""
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
            await client.get_platform_health()


# ---------------------------------------------------------------------------
# NFR-O4 latency test (AC-10) — marked @pytest.mark.slow
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_ping_handler_latency_under_p95_budget() -> None:
    """AC-10 / NFR-O4: p95 of 100 sequential /ping invocations < 0.200 s.

    Registry mock responds in ~100 ms (asyncio.sleep) to simulate realistic
    registry-api latency. Threshold is 0.200 s (2× headroom above 100 ms mock).
    Uses math.ceil(0.95 * n) - 1 percentile index formula (Story 3.4 M4 carry-forward).
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.100)  # 100 ms simulated registry latency
        return httpx.Response(
            status_code=200,
            content=_VALID_HEALTH_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_slow_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)

        latencies: list[float] = []
        n = 100
        for i in range(n):
            msg = _make_message(message_id=i + 1)
            t0 = time.perf_counter()
            await handle_ping(msg, registry_client=client)
            latencies.append(time.perf_counter() - t0)

    latencies.sort()
    p95_index = math.ceil(0.95 * n) - 1  # M4: correct percentile index formula
    p95 = latencies[p95_index]
    assert p95 < 0.200, (
        f"NFR-O4: p95 latency {p95:.3f} s exceeds 0.200 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )
