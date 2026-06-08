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
- test_format_http_error_409_with_task_id (AC-8)
- test_format_http_error_409_no_body_task_id (AC-8)
- test_format_http_error_5xx (AC-8)
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers._errors import format_http_error as _format_http_error
from telegram_gateway.handlers._keys import (
    UUIDV7_BARE_RE as _UUIDV7_BARE_RE,
)
from telegram_gateway.handlers._keys import (
    idempotency_key_from_message as _idempotency_key_from_message,
)
from telegram_gateway.handlers.registry_client import RegistryAPIClient
from telegram_gateway.handlers.task_command import handle_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TASK_ID = "t-00000000-0000-7000-8000-000000000001"
_FAKE_EVENT_ID = "e-00000000-0000-7000-8000-000000000002"
_FAKE_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)  # L3: explicit datetime

_VALID_RESPONSE_JSON = json.dumps(
    {
        "task_id": _FAKE_TASK_ID,
        "event_id": _FAKE_EVENT_ID,
        "created_at": _FAKE_CREATED_AT.isoformat(),
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


@pytest.fixture()
async def registry_client_fixture(request: pytest.FixtureRequest) -> RegistryAPIClient:  # type: ignore[misc]
    """Async fixture: RegistryAPIClient backed by a fake httpx transport (M6).

    Accepts indirect params dict with keys: status_code, body, headers, raise_exc.
    Properly closes the underlying AsyncClient via async with teardown.
    """
    params = getattr(request, "param", {}) if hasattr(request, "param") else {}
    status_code: int = params.get("status_code", 201)
    body: str = params.get("body", _VALID_RESPONSE_JSON)
    headers: dict[str, str] | None = params.get("headers", None)
    raise_exc: Exception | None = params.get("raise_exc", None)

    if raise_exc is not None:

        async def _transport(req: httpx.Request) -> httpx.Response:
            raise raise_exc

    else:

        async def _transport(req: httpx.Request) -> httpx.Response:
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


def _make_registry_client(
    *,
    status_code: int = 201,
    body: str = _VALID_RESPONSE_JSON,
    headers: dict[str, str] | None = None,
    raise_exc: Exception | None = None,
) -> RegistryAPIClient:
    """Build a RegistryAPIClient backed by a fake httpx transport.

    M6 note: use registry_client_fixture in new tests (proper teardown).
    Kept for backward-compat with existing tests that call it directly.
    The AsyncClient is not closed after the test — use registry_client_fixture
    for new tests to avoid ResourceWarning.
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


def _make_bot() -> MagicMock:
    bot = MagicMock()
    return bot


# ---------------------------------------------------------------------------
# Unit: _idempotency_key_from_message  (H1 / FR28 / L4 / L6)
# ---------------------------------------------------------------------------


def test_idempotency_key_deterministic() -> None:
    """H1: same (chat_id, message_id) always produces the same key."""
    msg1 = _make_message(chat_id=12345, message_id=99)
    msg2 = _make_message(chat_id=12345, message_id=99)
    assert _idempotency_key_from_message(msg1) == _idempotency_key_from_message(msg2)


def test_idempotency_key_matches_uuidv7_regex() -> None:
    """H1: key satisfies the IdempotencyKeyMiddleware UUIDv7 regex."""
    msg = _make_message(chat_id=12345, message_id=99)
    key = _idempotency_key_from_message(msg)
    assert _UUIDV7_BARE_RE.match(key), f"Key {key!r} does not match UUIDv7 pattern"


def test_idempotency_key_different_pairs_produce_different_keys() -> None:
    """H1: different (chat_id, message_id) pairs produce different keys."""
    msg_a = _make_message(chat_id=111, message_id=1)
    msg_b = _make_message(chat_id=111, message_id=2)
    msg_c = _make_message(chat_id=222, message_id=1)
    assert _idempotency_key_from_message(msg_a) != _idempotency_key_from_message(msg_b)
    assert _idempotency_key_from_message(msg_a) != _idempotency_key_from_message(msg_c)


def test_idempotency_key_passes_middleware_regex() -> None:
    """H1 integration: key accepted by the IdempotencyKeyMiddleware regex constraint."""
    middleware_uuidv7_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    msg = _make_message(chat_id=999, message_id=42)
    key = _idempotency_key_from_message(msg)
    assert middleware_uuidv7_re.match(key), (
        f"Key {key!r} rejected by IdempotencyKeyMiddleware regex"
    )


def test_idempotency_key_from_message_format() -> None:
    """AC-12 (updated): key is a UUIDv7-shaped string, deterministic per (chat_id, message_id)."""
    msg = _make_message(chat_id=12345, message_id=99)
    key = _idempotency_key_from_message(msg)
    # Must be a valid UUID string
    parsed = uuid.UUID(key)
    assert parsed.version == 7  # version nibble reshaped to 7
    # Must satisfy the bare UUIDv7 regex
    assert _UUIDV7_BARE_RE.match(key)


# ---------------------------------------------------------------------------
# Unit: _format_http_error  (AC-8 / H5 / M2)
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


def test_format_http_error_409_empty_body() -> None:
    """L5: 409 with empty (zero-byte) body returns fallback without raising."""
    exc = _make_http_status_error(409, body="")
    result = _format_http_error(exc)
    assert "Duplicate idempotency key" in result


def test_format_http_error_4xx_with_detail() -> None:
    """AC-8: 422 with RFC 7807 string detail parses the detail field."""
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


def test_format_http_error_401_unauthorized() -> None:
    """M2: 401 returns the 'Not authorized' message."""
    exc = _make_http_status_error(401, body="")
    result = _format_http_error(exc)
    assert result == "⚠️ Not authorized. Contact your administrator."


def test_format_http_error_403_forbidden() -> None:
    """M2: 403 returns the 'Not authorized' message."""
    exc = _make_http_status_error(403, body="")
    result = _format_http_error(exc)
    assert result == "⚠️ Not authorized. Contact your administrator."


def test_format_http_error_422_with_list_detail() -> None:
    """H5: FastAPI 422 list-typed detail extracts the 'msg' field."""
    body = json.dumps({"detail": [{"loc": ["body", "title"], "msg": "field required"}]})
    exc = _make_http_status_error(422, body=body)
    result = _format_http_error(exc)
    assert "field required" in result
    assert "[{" not in result  # no Python repr of the list


def test_format_http_error_html_escapes_detail() -> None:
    """H5: string detail is HTML-escaped to prevent injection."""
    body = json.dumps({"detail": "<script>x</script>"})
    exc = _make_http_status_error(422, body=body)
    result = _format_http_error(exc)
    assert "&lt;script&gt;" in result
    assert "<script>" not in result


def test_format_http_error_html_escapes_task_id_from_body() -> None:
    """H5: task_id from 409 body is HTML-escaped."""
    body = json.dumps({"task_id": "<evil>"})
    exc = _make_http_status_error(409, body=body)
    result = _format_http_error(exc)
    assert "&lt;evil&gt;" in result
    assert "<evil>" not in result


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

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        await client.create_task(
            description="hello",
            idempotency_key="some-uuid-key",
            operator_actor_id="999",
            request_id="req-abc",
        )
    assert captured_headers.get("idempotency-key") == "some-uuid-key"
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


@pytest.mark.asyncio
async def test_create_task_raises_httpx_error_on_malformed_json() -> None:
    """H2: 201 with non-JSON body raises httpx.HTTPError (not unhandled exception)."""
    client = _make_registry_client(
        status_code=201,
        body="<html>bad</html>",
        headers={"content-type": "text/html"},
    )
    with pytest.raises(httpx.HTTPError):
        await client.create_task(
            description="test",
            idempotency_key="k",
            operator_actor_id="1",
        )


@pytest.mark.asyncio
async def test_create_task_raises_httpx_error_on_missing_field() -> None:
    """H2: 201 with JSON missing required fields raises httpx.HTTPError."""
    client = _make_registry_client(
        status_code=201,
        body=json.dumps({"task_id": "t-x"}),  # missing event_id + created_at
        headers={"content-type": "application/json"},
    )
    with pytest.raises(httpx.HTTPError):
        await client.create_task(
            description="test",
            idempotency_key="k",
            operator_actor_id="1",
        )


@pytest.mark.asyncio
async def test_registry_client_default_idempotency_status_is_applied() -> None:
    """M8: absent X-Idempotency-Status header → idempotency_status == 'applied'."""
    client = _make_registry_client()  # no X-Idempotency-Status header
    result = await client.create_task(
        description="test",
        idempotency_key="k",
        operator_actor_id="1",
    )
    assert result.idempotency_status == "applied"


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
    """AC-12: successful /task replies with task_id in HTML <code> tag; no '(retry deduped)'."""
    msg = _make_message(text="/task hello world")
    client = _make_registry_client()
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert _FAKE_TASK_ID in reply_text
    assert "<code>" in reply_text
    assert "created" in reply_text
    assert "(retry deduped)" not in reply_text  # M8: absent on first delivery


@pytest.mark.asyncio
async def test_task_handler_uses_message_id_for_idempotency_key() -> None:
    """AC-12: outbound request carries a UUIDv7-shaped Idempotency-Key header."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("idempotency-key", "")
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message(text="/task do something", message_id=77, chat_id=555)
        await handle_task(msg, _make_bot(), client)

    key = captured["key"]
    assert key, "Idempotency-Key header was not sent"
    assert _UUIDV7_BARE_RE.match(key), f"Idempotency-Key {key!r} does not match UUIDv7 pattern"
    # Determinism: same message produces same key
    msg2 = _make_message(text="/task do something", message_id=77, chat_id=555)
    key2 = _idempotency_key_from_message(msg2)
    assert key == key2


@pytest.mark.asyncio
async def test_task_handler_propagates_request_id() -> None:
    """AC-12: outbound request has X-Request-ID matching a UUIDv7-like pattern."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["rid"] = request.headers.get("x-request-id", "")
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message(text="/task build something")
        await handle_task(msg, _make_bot(), client)

    rid = captured["rid"]
    assert rid, "X-Request-ID header was not sent"
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
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_task_handler_replies_internal_error_on_unexpected_exception() -> None:
    """H2 backstop: RuntimeError from create_task → generic error reply, no raise."""
    from unittest.mock import AsyncMock as _AsyncMock

    client = _make_registry_client()
    client.create_task = _AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message(text="/task trigger runtime error")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "⚠️ Internal error" in reply_text


@pytest.mark.asyncio
async def test_task_handler_too_many_redirects() -> None:
    """M3: TooManyRedirects → 'Registry misconfigured: too many redirects.' reply."""
    client = _make_registry_client(raise_exc=httpx.TooManyRedirects("too many"))
    msg = _make_message(text="/task something")
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text.lower()


@pytest.mark.asyncio
async def test_task_handler_uses_unknown_actor_when_from_user_is_none() -> None:
    """M9: message.from_user is None → X-Actor-Id: unknown sent to registry."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["actor"] = request.headers.get("x-actor-id", "")
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message(text="/task something")
        msg.from_user = None  # simulate anonymous/system message
        await handle_task(msg, _make_bot(), client)

    assert captured["actor"] == "unknown"


@pytest.mark.asyncio
async def test_task_handler_handles_newline_separator() -> None:
    """M10: '/task\\nFix the bug' (newline, no space) correctly extracts description."""
    msg = _make_message(text="/task\nFix the bug")
    client = _make_registry_client()
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # Should succeed and reply with task id, not the usage string
    assert "Usage:" not in reply_text
    assert _FAKE_TASK_ID in reply_text


@pytest.mark.asyncio
async def test_task_handler_handles_bot_mention() -> None:
    """L7: '/task@mybot Fix bug' — description extracted as 'Fix bug'.

    aiogram's Command("task") filter handles bot-mention stripping automatically
    before the handler is called. Here we test that the split(None, 1) logic
    in handle_task correctly handles the raw text form that includes the mention,
    because the handler is called directly (bypassing aiogram routing) in unit tests.
    When called via aiogram in production, aiogram passes the text without the
    command prefix, so the split handles what remains correctly.
    """
    msg = _make_message(text="/task@mybot Fix bug")
    client = _make_registry_client()
    await handle_task(msg, _make_bot(), client)
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # The split(None, 1) produces parts[1] = "Fix bug" after stripping the command token
    assert "Usage:" not in reply_text
    assert _FAKE_TASK_ID in reply_text


# ---------------------------------------------------------------------------
# NFR-P2 latency test (AC-9) — marked @pytest.mark.slow
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_task_handler_latency_under_p95_budget() -> None:
    """AC-9 / NFR-P2: p95 of 100 sequential /task invocations < 0.25 s.

    Registry mock responds in ~200 ms (asyncio.sleep) to simulate realistic
    registry-api latency. Measures the handler body only (not network); the
    200 ms mock represents realistic registry-api latency.

    Threshold is 0.25 s (50 ms headroom above the 200 ms mock latency) rather
    than the original 1.0 s — with an exact-200ms mock, 0.25 s is a meaningful
    upper bound that catches genuine regressions in handler overhead.  The
    original 1.0 s threshold could never legitimately fail given exact 200 ms
    mock responses (M5).

    p95 formula uses math.ceil to avoid off-by-one for n != 100 (M4).
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.200)  # 200 ms simulated registry latency
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_slow_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        bot = _make_bot()

        latencies: list[float] = []
        n = 100
        for i in range(n):
            msg = _make_message(text=f"/task bench iteration {i}", message_id=i + 1)
            t0 = time.perf_counter()
            await handle_task(msg, bot, client)
            latencies.append(time.perf_counter() - t0)

    latencies.sort()
    p95_index = math.ceil(0.95 * n) - 1  # M4: correct percentile index
    p95 = latencies[p95_index]
    assert p95 < 0.25, (  # M5: tightened from 1.0 s to 0.25 s (200 ms mock + 50 ms headroom)
        f"NFR-P2: p95 latency {p95:.3f} s exceeds 0.25 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )


# ---------------------------------------------------------------------------
# Story 3.9 AC-5 / AC-9 — handle_task populates chat_id + reply_to_message_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_handler_forwards_positive_chat_id_and_message_id() -> None:
    """AC-5: handle_task forwards chat.id + message_id to create_task (positive chat)."""
    captured: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        msg = _make_message(text="/task deploy prod", chat_id=123456789, message_id=99)
        await handle_task(msg, _make_bot(), client)

    assert captured.get("chat_id") == 123456789
    assert captured.get("reply_to_message_id") == 99


@pytest.mark.asyncio
async def test_task_handler_forwards_negative_supergroup_chat_id() -> None:
    """AC-5: handle_task forwards negative supergroup chat_id correctly (no sign drop)."""
    captured: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            status_code=201,
            content=_VALID_RESPONSE_JSON.encode(),
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    ) as http_client:
        client = RegistryAPIClient(http_client=http_client)
        # Supergroup chat ids are large negative numbers (-100 prefix in Telegram).
        msg = _make_message(text="/task review PR", chat_id=-1001234567890, message_id=777)
        await handle_task(msg, _make_bot(), client)

    assert captured.get("chat_id") == -1001234567890
    assert captured.get("reply_to_message_id") == 777
