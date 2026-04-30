"""Tests for /ping command handler (Story 3.5 AC-10, AC-11).

Coverage (≥13 tests per AC-10 + review-fix additions):
- test_ping_handler_replies_with_health_summary — happy path exact template
- test_ping_handler_health_unhealthy_prefixes_warning_emoji — "unhealthy" prefix
- test_ping_handler_health_degraded_no_warning_emoji — "degraded" no prefix
- test_ping_handler_propagates_request_id — X-Request-ID header UUIDv7
- test_ping_handler_does_not_send_idempotency_key — Idempotency-Key absent
- test_ping_handler_5xx_replies_retry_message — 500 via format_http_error
- test_ping_handler_4xx_replies_error_message — 404 via format_http_error (H2 tighten)
- test_ping_handler_timeout_replies_unreachable — ReadTimeout
- test_ping_handler_replies_with_html_escaped_version — XSS prevention
- test_ping_handler_unexpected_exception_replies_internal_error — RuntimeError backstop
- test_ping_handler_swallows_reply_failure — TelegramAPIError swallowed (M5)
- test_ping_handler_latency_under_p95_budget — @pytest.mark.slow NFR-O4 (M6)
- test_get_platform_health_parses_minimal_response — direct client call
- test_get_platform_health_raises_registry_response_error_on_malformed_body — H1
- test_ping_handler_malformed_200_replies_unexpected_response — M8
- test_ping_handler_renders_unknown_status_strings — M9 (H1 forward-compat)
- test_health_response_rejects_negative_clawhip_queue_depth — M10
- test_health_response_rejects_overlong_version — M11
- test_ping_handler_unhealthy_worker_no_emoji_prefix — M12
- test_get_platform_health_ignores_extra_fields_for_forward_compat — M13
- test_ping_handler_html_escapes_all_string_fields — L2
- test_health_response_rejects_clawhip_queue_depth_over_limit — L4
- test_ping_handler_stopped_status_no_emoji_prefix — H1 forward-compat (stopped state)
- test_ping_handler_success_via_fixture — M7 async fixture smoke test
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from unittest.mock import AsyncMock, MagicMock

import aiogram.exceptions
import httpx
import pytest
import pytest_asyncio

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
    """Build a RegistryAPIClient backed by a fake httpx transport.

    M7 note: use the async fixture _registry_client_fixture in new tests
    that need proper teardown hygiene (no ResourceWarning).
    Kept for backward-compat with tests that construct clients inline.
    """
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


@pytest_asyncio.fixture()
async def _registry_client_fixture(
    request: pytest.FixtureRequest,
) -> RegistryAPIClient:  # type: ignore[misc]
    """M7: async fixture with proper teardown to avoid ResourceWarning.

    Accepts indirect params dict: status_code, body, headers, raise_exc.
    """
    params = getattr(request, "param", {}) if hasattr(request, "param") else {}
    status_code: int = params.get("status_code", 200)
    body: str = params.get("body", _VALID_HEALTH_JSON)
    headers: dict[str, str] | None = params.get("headers", None)
    raise_exc: Exception | None = params.get("raise_exc", None)

    if raise_exc is not None:

        async def _transport(req: httpx.Request) -> httpx.Response:
            raise raise_exc  # type: ignore[misc]

    else:

        async def _transport(req: httpx.Request) -> httpx.Response:  # type: ignore[misc]
            return httpx.Response(
                status_code=status_code,
                content=body.encode(),
                headers=headers or {},
                request=req,
            )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        yield RegistryAPIClient(http_client=http_client)


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
        # L5: assertions inside the async with block.
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
        # L5: assertions inside the async with block.
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
    """AC-10 / M4: mock 404 → reply starts with '⚠️ Health check failed:' prefix (H2).

    H2: /ping 4xx must say 'Health check failed' not 'Task rejected'.
    M4: assert exact /ping-specific 4xx prefix rather than just '⚠️' presence.
    """
    client = _make_registry_client(
        status_code=404,
        body=json.dumps({"detail": "health endpoint not found"}),
        headers={"content-type": "application/json"},
    )
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ Health check failed:"), (
        f"Expected '⚠️ Health check failed:' prefix for /ping 4xx, got: {reply_text!r}"
    )


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
    """AC-10 / M3 / M5 contract: message.reply raises TelegramAPIError → handler returns normally.

    M5: use aiogram.exceptions.TelegramAPIError (actual production exception class)
    rather than RuntimeError so that future tightening of safe_reply to
    'except TelegramAPIError' would not break this test.
    """
    client = _make_registry_client()
    msg = _make_message()
    mock_method = MagicMock()
    msg.reply = AsyncMock(
        side_effect=aiogram.exceptions.TelegramAPIError(method=mock_method, message="reply failed")
    )

    # Must not raise — safe_reply swallows the failure.
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
# Review-fix additions (M8–M13, L2, L4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_handler_malformed_200_replies_unexpected_response() -> None:
    """M8: RegistryResponseError (malformed 200) → reply equals unexpected-response text."""
    client = _make_registry_client()
    client.get_platform_health = AsyncMock(  # type: ignore[method-assign]
        side_effect=RegistryResponseError("malformed body")
    )
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "⚠️ Registry returned an unexpected response. Logs captured.", (
        f"Unexpected reply: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_ping_handler_renders_unknown_status_strings() -> None:
    """M9 / H1: unknown status strings (e.g., 'warning', 'maintenance') render without error.

    After H1, HealthResponseLocal uses str typing, so unknown states that the
    server-side endpoint might add in the future are forwarded verbatim rather
    than causing RegistryResponseError.
    """
    body = json.dumps(
        {
            "registry_status": "warning",
            "worker_status": "maintenance",
            "clawhip_queue_depth": 2,
            "version": "v2.0.0",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "warning" in reply_text, f"Expected 'warning' in reply, got: {reply_text!r}"
    assert "maintenance" in reply_text, f"Expected 'maintenance' in reply, got: {reply_text!r}"
    # Neither "warning" nor "maintenance" equals "unhealthy" → no emoji prefix.
    assert not reply_text.startswith("⚠️"), (
        f"Unknown status strings must not trigger emoji prefix, got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_health_response_rejects_negative_clawhip_queue_depth() -> None:
    """M10: clawhip_queue_depth=-1 → RegistryResponseError raised by get_platform_health."""
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "idle",
            "clawhip_queue_depth": -1,
            "version": "v1.0.0",
        }
    )
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=body.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(RegistryResponseError):
            await client.get_platform_health()


@pytest.mark.asyncio
async def test_health_response_rejects_overlong_version() -> None:
    """M11: version string exceeding max_length=200 → RegistryResponseError raised."""
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "idle",
            "clawhip_queue_depth": 0,
            "version": "v" + "x" * 200,  # 201 chars total
        }
    )
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=body.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(RegistryResponseError):
            await client.get_platform_health()


@pytest.mark.asyncio
async def test_ping_handler_unhealthy_worker_no_emoji_prefix() -> None:
    """M12: worker_status='unhealthy' + registry_status='healthy' → reply starts with 'pong'.

    Per spec AC-4: only registry_status='unhealthy' triggers the '⚠️ ' prefix.
    An unhealthy worker without an unhealthy registry does NOT get the prefix.
    """
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "unhealthy",
            "clawhip_queue_depth": 0,
            "version": "v1.0.0",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("pong"), (
        f"Expected 'pong' prefix when only worker is unhealthy, got: {reply_text!r}"
    )
    assert not reply_text.startswith("⚠️"), (
        f"Must not prefix '⚠️' when registry_status='healthy', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_get_platform_health_ignores_extra_fields_for_forward_compat() -> None:
    """M13: extra fields from future server-side additions are dropped cleanly (extra='ignore').

    Documents the forward-compatibility intent of model_config extra='ignore'.
    """
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "idle",
            "clawhip_queue_depth": 0,
            "version": "v1.0.0",
            "future_field": "some_value",
            "another_new_field": 42,
        }
    )
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=body.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        result = await client.get_platform_health()

    assert isinstance(result, HealthResponseLocal)
    assert result.registry_status == "healthy"
    assert not hasattr(result, "future_field"), "Extra fields must not appear on the model"
    assert not hasattr(result, "another_new_field"), "Extra fields must not appear on the model"


@pytest.mark.asyncio
async def test_ping_handler_html_escapes_all_string_fields() -> None:
    """L2: all three string fields (version, registry_status, worker_status) are HTML-escaped."""
    body = json.dumps(
        {
            "registry_status": "<script>alert(1)</script>",
            "worker_status": "<img src=x>",
            "clawhip_queue_depth": 0,
            "version": "<b>bad</b>",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()
    await handle_ping(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<script>" not in reply_text, f"Raw '<script>' must be escaped, got: {reply_text!r}"
    assert "&lt;script&gt;" in reply_text, f"Expected escaped script tag, got: {reply_text!r}"
    assert "<img" not in reply_text, f"Raw '<img' must be escaped, got: {reply_text!r}"
    assert "<b>" not in reply_text, f"Raw '<b>' must be escaped, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_health_response_rejects_clawhip_queue_depth_over_limit() -> None:
    """L4: clawhip_queue_depth exceeding le=1_000_000 → RegistryResponseError raised."""
    body = json.dumps(
        {
            "registry_status": "healthy",
            "worker_status": "idle",
            "clawhip_queue_depth": 1_000_001,
            "version": "v1.0.0",
        }
    )
    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status_code=200,
                content=body.encode(),
                request=req,
            )
        ),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(RegistryResponseError):
            await client.get_platform_health()


@pytest.mark.asyncio
async def test_ping_handler_stopped_status_no_emoji_prefix() -> None:
    """H1 forward-compat: registry_status='stopped' (hypothetical new state) → no emoji prefix.

    Documents that only the exact string 'unhealthy' (case-insensitive) triggers
    the warning emoji prefix. Other novel status strings must render verbatim.
    """
    body = json.dumps(
        {
            "registry_status": "stopped",
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
    assert "stopped" in reply_text, f"Expected 'stopped' in reply, got: {reply_text!r}"
    assert not reply_text.startswith("⚠️"), (
        f"'stopped' must not trigger emoji prefix, got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_ping_handler_success_via_fixture(
    _registry_client_fixture: RegistryAPIClient,
) -> None:
    """M7: smoke test using async fixture with proper AsyncClient teardown.

    Validates the _registry_client_fixture wires up a working client and
    the handler returns the expected health summary.
    """
    msg = _make_message()
    await handle_ping(msg, registry_client=_registry_client_fixture)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("pong"), f"Expected 'pong' prefix, got: {reply_text!r}"


# ---------------------------------------------------------------------------
# NFR-O4 latency test (AC-10 / M6) — marked @pytest.mark.slow
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_ping_handler_latency_under_p95_budget() -> None:
    """AC-10 / NFR-O4 / M6: p95 of 100 sequential /ping invocations < 0.050 s.

    Registry mock responds in ~10 ms (asyncio.sleep) to simulate realistic
    in-process latency. Threshold is 0.050 s (5× headroom above 10 ms mock).
    Uses math.ceil(0.95 * n) - 1 percentile index formula (Story 3.4 M4
    carry-forward). M6: reduced from 100ms sleep / 0.200s threshold to
    10ms / 0.050s for faster CI with meaningful headroom.
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.010)  # 10 ms simulated registry latency
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
    assert p95 < 0.050, (
        f"NFR-O4: p95 latency {p95:.3f} s exceeds 0.050 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )
