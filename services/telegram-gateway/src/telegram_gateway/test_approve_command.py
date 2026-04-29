"""Tests for /approve command handler (Story 3.4 AC-10, AC-11).

Coverage (≥16 tests per AC-10):
- test_approve_handler_calls_registry — happy path 200; POST shape assert
- test_approve_handler_replies_with_username_and_timestamp — reply text check
- test_approve_handler_uses_uuidv7_idempotency_key — header UUIDv7 shape
- test_approve_handler_propagates_request_id — X-Request-ID header
- test_approve_handler_no_arg_replies_usage — /approve (no arg)
- test_approve_handler_invalid_task_id_replies_usage — /approve foo
- test_approve_handler_409_renders_state_error — 4xx RFC 7807 detail
- test_approve_handler_5xx_replies_retry_message — 500
- test_approve_handler_timeout_replies_unreachable — ReadTimeout
- test_approve_handler_replays_when_idempotency_status_replayed — replay suffix
- test_approve_handler_unexpected_exception_replies_internal_error — RuntimeError
- test_approve_handler_html_escapes_state_error_detail — XSS prevention
- test_approve_handler_handles_no_username_falls_back_to_first_name — fallback
- test_approve_handler_handles_no_username_no_first_name — literal "operator"
- test_extract_task_id_accepts_valid_uuidv7 — direct _extract_task_id call
- test_extract_task_id_rejects_uppercase — uppercase hex rejected
- test_extract_task_id_rejects_legacy_format — bare UUID without t- prefix
- test_approve_handler_latency_under_p95_budget — @pytest.mark.slow NFR-P2
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.approve_command import _extract_task_id
from telegram_gateway.handlers.registry_client import (
    DecisionResponseLocal,
    RegistryAPIClient,
)

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_FAKE_TASK_ID = "t-00000000-0000-7000-8000-000000000001"
_FAKE_DECISION_ID = "d-00000000-0000-7000-8000-000000000002"
_FAKE_DECIDED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_VALID_DECISION_RESPONSE_JSON = json.dumps(
    {
        "task_id": _FAKE_TASK_ID,
        "decision_id": _FAKE_DECISION_ID,
        "action": "approve",
        "decided_at": _FAKE_DECIDED_AT.isoformat(),
    }
)

_UUIDV7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _make_message(
    *,
    text: str = f"/approve {_FAKE_TASK_ID}",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
    first_name: str | None = "Test",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /approve tests."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = first_name
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_registry_client(
    *,
    status_code: int = 200,
    body: str = _VALID_DECISION_RESPONSE_JSON,
    headers: dict[str, str] | None = None,
    raise_exc: Exception | None = None,
) -> RegistryAPIClient:
    """Build a RegistryAPIClient backed by a fake httpx transport.

    Follows the M6 pattern from test_task_command.py. Use the async fixture
    (_make_registry_client_fixture) in new tests that need teardown hygiene.
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


async def _invoke_approve(
    message: MagicMock,
    registry_client: RegistryAPIClient,
) -> None:
    """Call handle_approve via the router factory (extracts the inner handler)."""
    # Import here to get the actual function after module load.
    from telegram_gateway.handlers.approve_command import make_approve_router

    router = make_approve_router()
    # The handler is registered as a message handler; extract and call directly.
    # This mirrors the test_task_command.py pattern of calling handle_task directly.
    # We extract handle_approve from the router's registered observers.
    observers = router.message.handlers
    assert observers, "No message handlers registered on approve router"
    handler_obj = observers[0]
    await handler_obj.callback(message, registry_client=registry_client)


# ---------------------------------------------------------------------------
# Unit: _extract_task_id
# ---------------------------------------------------------------------------


def test_extract_task_id_accepts_valid_uuidv7() -> None:
    """AC-10: _extract_task_id returns task-id string for valid t-<uuidv7>."""
    msg = _make_message(text=f"/approve {_FAKE_TASK_ID}")
    result = _extract_task_id(msg)
    assert result == _FAKE_TASK_ID


def test_extract_task_id_rejects_uppercase() -> None:
    """AC-10: uppercase hex chars are rejected (only lowercase valid)."""
    msg = _make_message(text="/approve t-00000000-0000-7000-8000-00000000000A")
    assert _extract_task_id(msg) is None


def test_extract_task_id_rejects_legacy_format() -> None:
    """AC-10: bare UUID without 't-' prefix is rejected."""
    msg = _make_message(text="/approve 00000000-0000-7000-8000-000000000001")
    assert _extract_task_id(msg) is None


def test_extract_task_id_rejects_non_uuidv7_version() -> None:
    """_extract_task_id: version nibble must be 7, not 4."""
    msg = _make_message(text="/approve t-00000000-0000-4000-8000-000000000001")
    assert _extract_task_id(msg) is None


def test_extract_task_id_rejects_no_arg() -> None:
    """_extract_task_id returns None when no arg is present."""
    msg = _make_message(text="/approve")
    assert _extract_task_id(msg) is None


# ---------------------------------------------------------------------------
# Integration: handle_approve (via router extraction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_handler_calls_registry() -> None:
    """AC-10: happy path — POST /v1/tasks/{task_id}/decisions called with action=approve."""
    captured_url: list[str] = []
    captured_body: list[dict[str, str]] = []

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_url.append(str(request.url))
        captured_body.append(cast(dict[str, str], json.loads(request.content)))
        return httpx.Response(
            status_code=200,
            content=_VALID_DECISION_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message()
        await _invoke_approve(msg, client)

    assert captured_url, "Transport was never called"
    assert f"/v1/tasks/{_FAKE_TASK_ID}/decisions" in captured_url[0]
    assert captured_body[0] == {"action": "approve"}
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_approve_handler_replies_with_username_and_timestamp() -> None:
    """AC-10: success reply contains HTML-escaped @-handle and ISO timestamp."""
    msg = _make_message(username="myoperator")
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "myoperator" in reply_text
    assert _FAKE_DECIDED_AT.isoformat() in reply_text
    assert "Pushing" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_uses_uuidv7_idempotency_key() -> None:
    """AC-10: Idempotency-Key header matches UUIDv7 regex."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("idempotency-key", "")
        return httpx.Response(
            status_code=200,
            content=_VALID_DECISION_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message(message_id=77, chat_id=555)
        await _invoke_approve(msg, client)

    key = captured["key"]
    assert key, "Idempotency-Key header was not sent"
    assert _UUIDV7_RE.match(key), f"Idempotency-Key {key!r} does not match UUIDv7 pattern"


@pytest.mark.asyncio
async def test_approve_handler_propagates_request_id() -> None:
    """AC-10: X-Request-ID header is a bare UUIDv7."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=200,
            content=_VALID_DECISION_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message()
        await _invoke_approve(msg, client)

    rid = captured["rid"]
    assert rid, "X-Request-ID header was not sent"
    assert _UUIDV7_RE.match(rid), f"X-Request-ID {rid!r} does not match UUIDv7 pattern"


@pytest.mark.asyncio
async def test_approve_handler_no_arg_replies_usage() -> None:
    """AC-10: /approve (no arg) → reply contains 'Usage: /approve <task-id>'."""
    msg = _make_message(text="/approve")
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /approve <task-id>" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_invalid_task_id_replies_usage() -> None:
    """AC-10: /approve foo → reply contains usage + example."""
    msg = _make_message(text="/approve not-a-valid-id")
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /approve <task-id>" in reply_text
    assert "example" in reply_text.lower()


@pytest.mark.asyncio
async def test_approve_handler_409_renders_state_error() -> None:
    """AC-10: 4xx RFC 7807 detail → reply contains '⚠️ Task rejected: Task is in state'.

    Registry-api returns a 4xx (422 unprocessable) when the task is in a state
    that does not allow approval (e.g., 'planning'). The bot renders the RFC 7807
    'detail' field directly via _format_http_error (architecture.md:228).
    Note: 409 is reserved for idempotency-key collision; state-machine errors
    use 422 per registry-api's validation layer (Story 6.4).
    """
    detail_msg = "Task is in state 'planning'; cannot approve"
    client = _make_registry_client(
        status_code=422,
        body=json.dumps({"detail": detail_msg}),
        headers={"content-type": "application/json"},
    )
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Task is in state" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_5xx_replies_retry_message() -> None:
    """AC-10: mock 500 → reply starts with '⚠️ Registry unavailable: HTTP 500'."""
    client = _make_registry_client(status_code=500, body="")
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ Registry unavailable: HTTP 500")


@pytest.mark.asyncio
async def test_approve_handler_timeout_replies_unreachable() -> None:
    """AC-10: ReadTimeout → reply contains 'Could not reach registry'."""
    client = _make_registry_client(raise_exc=httpx.ReadTimeout("timed out"))
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_replays_when_idempotency_status_replayed() -> None:
    """AC-10: idempotency_status='replayed' → reply contains '(retry deduped)'."""
    client = _make_registry_client(
        headers={"X-Idempotency-Status": "replayed", "content-type": "application/json"},
    )
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "(retry deduped)" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_unexpected_exception_replies_internal_error() -> None:
    """AC-10 / H2 backstop: bare RuntimeError → reply contains 'Internal error'."""
    client = _make_registry_client()
    client.submit_decision = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Internal error" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_html_escapes_state_error_detail() -> None:
    """AC-10 / H5: detail containing '<script>' → reply has &lt;script&gt;, not raw tag."""
    detail_xss = "<script>alert(1)</script>"
    client = _make_registry_client(
        status_code=422,
        body=json.dumps({"detail": detail_xss}),
        headers={"content-type": "application/json"},
    )
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "&lt;script&gt;" in reply_text
    assert "<script>" not in reply_text


@pytest.mark.asyncio
async def test_approve_handler_handles_no_username_falls_back_to_first_name() -> None:
    """AC-10: username=None, first_name='Ivan' → reply contains 'Ivan'."""
    msg = _make_message(username=None, first_name="Ivan")
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Ivan" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_handles_no_username_no_first_name() -> None:
    """AC-10: both username and first_name absent → reply contains literal 'operator'."""
    msg = _make_message(username=None, first_name=None)
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "operator" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_too_many_redirects() -> None:
    """M3: TooManyRedirects → 'Registry misconfigured: too many redirects.' reply."""
    client = _make_registry_client(raise_exc=httpx.TooManyRedirects("too many"))
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text.lower()


@pytest.mark.asyncio
async def test_approve_handler_html_escapes_username_in_success_reply() -> None:
    """H5: username containing HTML special chars is escaped in success reply."""
    msg = _make_message(username="<evil>user</evil>")
    client = _make_registry_client()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "&lt;evil&gt;" in reply_text
    assert "<evil>" not in reply_text


@pytest.mark.asyncio
async def test_approve_handler_401_replies_not_authorized() -> None:
    """M2: 401 from registry → '⚠️ Not authorized. Contact your administrator.'"""
    client = _make_registry_client(
        status_code=401,
        body="",
        headers={"content-type": "application/json"},
    )
    msg = _make_message()
    await _invoke_approve(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "⚠️ Not authorized. Contact your administrator."


# ---------------------------------------------------------------------------
# Unit: DecisionResponseLocal model
# ---------------------------------------------------------------------------


def test_decision_response_local_fields() -> None:
    """AC-1: DecisionResponseLocal has the required fields with correct types."""
    resp = DecisionResponseLocal(
        task_id=_FAKE_TASK_ID,
        decision_id=_FAKE_DECISION_ID,
        action="approve",
        decided_at=_FAKE_DECIDED_AT,
    )
    assert resp.task_id == _FAKE_TASK_ID
    assert resp.decision_id == _FAKE_DECISION_ID
    assert resp.action == "approve"
    assert resp.decided_at == _FAKE_DECIDED_AT
    assert resp.idempotency_status == "applied"  # default


def test_decision_response_local_replayed_status() -> None:
    """AC-1: idempotency_status='replayed' is accepted."""
    resp = DecisionResponseLocal(
        task_id=_FAKE_TASK_ID,
        decision_id=_FAKE_DECISION_ID,
        action="approve",
        decided_at=_FAKE_DECIDED_AT,
        idempotency_status="replayed",
    )
    assert resp.idempotency_status == "replayed"


# ---------------------------------------------------------------------------
# NFR-P2 latency test (AC-10) — marked @pytest.mark.slow
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_approve_handler_latency_under_p95_budget() -> None:
    """AC-10 / NFR-P2: p95 of 100 sequential /approve invocations < 0.25 s.

    Registry mock responds in ~200 ms (asyncio.sleep) to simulate realistic
    registry-api latency. Mirrors Story 3.3 M5 threshold and M4 percentile
    formula exactly.
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.200)  # 200 ms simulated registry latency
        return httpx.Response(
            status_code=200,
            content=_VALID_DECISION_RESPONSE_JSON.encode(),
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
            msg = _make_message(
                text=f"/approve {_FAKE_TASK_ID}",
                message_id=i + 1,
            )
            t0 = time.perf_counter()
            await _invoke_approve(msg, client)
            latencies.append(time.perf_counter() - t0)

    latencies.sort()
    p95_index = math.ceil(0.95 * n) - 1  # M4: correct percentile index
    p95 = latencies[p95_index]
    assert p95 < 0.25, (  # M5: 200 ms mock + 50 ms headroom
        f"NFR-P2: p95 latency {p95:.3f} s exceeds 0.25 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )
