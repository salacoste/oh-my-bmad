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
- test_extract_task_id_accepts_valid_uuidv7 — direct extract_task_id_from_message call
- test_extract_task_id_rejects_uppercase — uppercase hex rejected
- test_extract_task_id_rejects_legacy_format — bare UUID without t- prefix
- test_approve_handler_latency_under_p95_budget — @pytest.mark.slow NFR-P2
- test_approve_handler_swallows_reply_failure — H2/H3 _safe_reply swallows exc
- test_handle_approve_warns_on_null_from_user — M2 log warning
- test_handle_approve_handles_null_from_user_with_valid_task_id — M8
- test_approve_handler_no_arg_no_example — M9 absence assertion
- test_idempotency_key_is_deterministic — L7 determinism
- test_submit_decision_rejects_invalid_task_id — M11
- test_submit_decision_omits_hint_when_none — M12
- test_submit_decision_includes_hint_when_string — M12
- test_submit_decision_idempotency_status_from_body — M3 body path
- test_submit_decision_idempotency_status_from_header — M3 header fallback
- test_approve_handler_404_replies_task_not_found — L4
- test_extract_task_id_rejects_trailing_garbage — M1
- test_handle_approve_from_user_none_no_double_at — Story 3.5.1 regression
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from telegram_gateway.handlers._keys import (
    UUIDV7_BARE_RE as _UUIDV7_RE,  # L3: import from canonical location
)
from telegram_gateway.handlers._keys import (
    extract_task_id_from_message,
    idempotency_key_from_message,
)
from telegram_gateway.handlers.approve_command import handle_approve
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

    M10 note: use the async fixture _registry_client_fixture in new tests
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
    """M10: async fixture with proper teardown to avoid ResourceWarning.

    Accepts indirect params dict: status_code, body, headers, raise_exc.
    """
    params = getattr(request, "param", {}) if hasattr(request, "param") else {}
    status_code: int = params.get("status_code", 200)
    body: str = params.get("body", _VALID_DECISION_RESPONSE_JSON)
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
# Unit: extract_task_id_from_message (L2: promoted to _keys)
# ---------------------------------------------------------------------------


def test_extract_task_id_accepts_valid_uuidv7() -> None:
    """AC-10: extract_task_id_from_message returns task-id string for valid t-<uuidv7>."""
    msg = _make_message(text=f"/approve {_FAKE_TASK_ID}")
    result = extract_task_id_from_message(msg)
    assert result == _FAKE_TASK_ID


def test_extract_task_id_rejects_uppercase() -> None:
    """AC-10: uppercase hex chars are rejected (only lowercase valid)."""
    msg = _make_message(text="/approve t-00000000-0000-7000-8000-00000000000A")
    assert extract_task_id_from_message(msg) is None


def test_extract_task_id_rejects_legacy_format() -> None:
    """AC-10: bare UUID without 't-' prefix is rejected."""
    msg = _make_message(text="/approve 00000000-0000-7000-8000-000000000001")
    assert extract_task_id_from_message(msg) is None


def test_extract_task_id_rejects_non_uuidv7_version() -> None:
    """extract_task_id_from_message: version nibble must be 7, not 4."""
    msg = _make_message(text="/approve t-00000000-0000-4000-8000-000000000001")
    assert extract_task_id_from_message(msg) is None


def test_extract_task_id_rejects_no_arg() -> None:
    """extract_task_id_from_message returns None when no arg is present."""
    msg = _make_message(text="/approve")
    assert extract_task_id_from_message(msg) is None


def test_extract_task_id_rejects_trailing_garbage() -> None:
    """M1: '/approve t-xxx trailing-garbage' — split(None,1) makes candidate
    't-xxx trailing-garbage' which fails TASK_ID_PATTERN, correctly rejecting."""
    msg = _make_message(text=f"/approve {_FAKE_TASK_ID} extra-garbage")
    # The candidate after split(None,1) is "{task_id} extra-garbage" — fails regex.
    assert extract_task_id_from_message(msg) is None


# ---------------------------------------------------------------------------
# Integration: handle_approve (M6: imported directly as module-level function)
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
        await handle_approve(msg, registry_client=client)

    assert captured_url, "Transport was never called"
    assert f"/v1/tasks/{_FAKE_TASK_ID}/decisions" in captured_url[0]
    assert captured_body[0] == {"action": "approve"}
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_approve_handler_replies_with_username_and_timestamp() -> None:
    """AC-10: success reply contains HTML-escaped @-handle and ISO timestamp."""
    msg = _make_message(username="myoperator")
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

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
        await handle_approve(msg, registry_client=client)

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
        await handle_approve(msg, registry_client=client)

    rid = captured["rid"]
    assert rid, "X-Request-ID header was not sent"
    assert _UUIDV7_RE.match(rid), f"X-Request-ID {rid!r} does not match UUIDv7 pattern"


@pytest.mark.asyncio
async def test_approve_handler_no_arg_replies_usage() -> None:
    """AC-10 / M9: /approve (no arg) → reply contains 'Usage: /approve <task-id>'
    and does NOT contain 'example' (no-arg vs invalid-arg distinction)."""
    msg = _make_message(text="/approve")
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /approve <task-id>" in reply_text
    # M9: no-arg reply must NOT include the example (that's for invalid-arg only).
    assert "example" not in reply_text.lower()


@pytest.mark.asyncio
async def test_approve_handler_invalid_task_id_replies_usage() -> None:
    """AC-10 / M9: /approve foo → reply contains usage + example."""
    msg = _make_message(text="/approve not-a-valid-id")
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /approve <task-id>" in reply_text
    # M9: invalid-arg reply MUST include the example.
    assert "example" in reply_text.lower()


@pytest.mark.asyncio
async def test_approve_handler_409_renders_state_error() -> None:
    """AC-10: 4xx RFC 7807 detail → reply contains '⚠️ Task rejected: Task is in state'.

    Registry-api returns a 4xx (422 unprocessable) when the task is in a state
    that does not allow approval (e.g., 'planning'). The bot renders the RFC 7807
    'detail' field directly via format_http_error (architecture.md:228).
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
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Task is in state" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_5xx_replies_retry_message() -> None:
    """AC-10: mock 500 → reply starts with '⚠️ Registry unavailable: HTTP 500'."""
    client = _make_registry_client(status_code=500, body="")
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️ Registry unavailable: HTTP 500")


@pytest.mark.asyncio
async def test_approve_handler_timeout_replies_unreachable() -> None:
    """AC-10: ReadTimeout → reply contains 'Could not reach registry'."""
    client = _make_registry_client(raise_exc=httpx.ReadTimeout("timed out"))
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

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
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "(retry deduped)" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_unexpected_exception_replies_internal_error() -> None:
    """AC-10 / H2 backstop: bare RuntimeError → reply contains 'Internal error'."""
    client = _make_registry_client()
    client.submit_decision = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

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
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "&lt;script&gt;" in reply_text
    assert "<script>" not in reply_text


@pytest.mark.asyncio
async def test_approve_handler_handles_no_username_falls_back_to_first_name() -> None:
    """AC-10: username=None, first_name='Ivan' → reply contains 'Ivan'."""
    msg = _make_message(username=None, first_name="Ivan")
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Ivan" in reply_text


@pytest.mark.asyncio
async def test_approve_handler_handles_no_username_no_first_name() -> None:
    """AC-10: both username and first_name absent → reply contains literal 'operator'."""
    msg = _make_message(username=None, first_name=None)
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "operator" in reply_text
    assert "@@" not in reply_text, f"Double @ detected in reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_approve_handler_too_many_redirects() -> None:
    """M3: TooManyRedirects → 'Registry misconfigured: too many redirects.' reply."""
    client = _make_registry_client(raise_exc=httpx.TooManyRedirects("too many"))
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text.lower()


@pytest.mark.asyncio
async def test_approve_handler_html_escapes_username_in_success_reply() -> None:
    """H5: username containing HTML special chars is escaped in success reply."""
    msg = _make_message(username="<evil>user</evil>")
    client = _make_registry_client()
    await handle_approve(msg, registry_client=client)

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
    await handle_approve(msg, registry_client=client)

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
# New tests: H2/H3 _safe_reply swallows failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_swallows_reply_failure() -> None:
    """H2/H3: if message.reply raises, handle_approve returns normally (M3 contract)."""
    client = _make_registry_client()
    msg = _make_message()
    msg.reply = AsyncMock(side_effect=RuntimeError("telegram down"))

    # Must not raise — handler swallows the reply failure via _safe_reply.
    await handle_approve(msg, registry_client=client)
    # reply was attempted (and failed silently)
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_handle_approve_swallows_reply_failure_on_error_path() -> None:
    """H3: reply failure on error path (timeout) is also swallowed."""
    client = _make_registry_client(raise_exc=httpx.ReadTimeout("timeout"))
    msg = _make_message()
    msg.reply = AsyncMock(side_effect=RuntimeError("telegram down"))

    # Must not raise even when both the registry call and the reply fail.
    await handle_approve(msg, registry_client=client)
    msg.reply.assert_called_once()


# ---------------------------------------------------------------------------
# New tests: M2 warn log on null from_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_warns_on_null_from_user(caplog: pytest.LogCaptureFixture) -> None:
    """M2: from_user is None → a WARNING is logged with message_id and chat_id."""
    import logging

    client = _make_registry_client()
    msg = _make_message()
    msg.from_user = None  # simulate anonymous/system message

    with caplog.at_level(logging.WARNING, logger="telegram_gateway.handlers.approve_command"):
        await handle_approve(msg, registry_client=client)

    assert any("from_user is None" in r.message for r in caplog.records), (
        "Expected WARNING about null from_user, got: " + str([r.message for r in caplog.records])
    )


# ---------------------------------------------------------------------------
# New tests: M8 null from_user with valid task_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_handles_null_from_user_with_valid_task_id() -> None:
    """M8: from_user=None and valid task_id → X-Actor-Id: unknown sent; reply has '@operator'."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["actor"] = request.headers.get("x-actor-id", "")
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
        msg.from_user = None

        await handle_approve(msg, registry_client=client)

    assert captured["actor"] == "unknown", f"Expected 'unknown', got {captured['actor']!r}"
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text, f"Double @ detected: {reply_text!r}"


# ---------------------------------------------------------------------------
# Regression: double-@ backport (Story 3.5.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_approve_from_user_none_no_double_at() -> None:
    """Story 3.5.1: from_user=None renders '@operator' not '@@operator'."""
    captured: dict[str, str] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured["actor"] = request.headers.get("x-actor-id", "")
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
        msg.from_user = None

        await handle_approve(msg, registry_client=client)

    assert captured["actor"] == "unknown"
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@" not in reply_text, f"Double @ detected in reply: {reply_text!r}"


# ---------------------------------------------------------------------------
# New tests: L7 idempotency key determinism
# ---------------------------------------------------------------------------


def test_idempotency_key_is_deterministic() -> None:
    """L7: idempotency_key_from_message called twice with same mock → same key."""
    msg1 = _make_message(message_id=42, chat_id=100)
    msg2 = _make_message(message_id=42, chat_id=100)
    key1 = idempotency_key_from_message(msg1)
    key2 = idempotency_key_from_message(msg2)
    assert key1 == key2, f"Keys differ: {key1!r} vs {key2!r}"


# ---------------------------------------------------------------------------
# New tests: M11 submit_decision task_id validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_decision_rejects_invalid_task_id() -> None:
    """M11: submit_decision raises ValueError for task_id that fails TASK_ID_PATTERN."""
    client = _make_registry_client()
    with pytest.raises(ValueError, match="Invalid task_id"):
        await client.submit_decision(
            task_id="not-a-valid-task-id",
            action="approve",
            idempotency_key="00000000-0000-7000-8000-000000000001",
            operator_actor_id="999",
        )


# ---------------------------------------------------------------------------
# New tests: M12 hint body shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_decision_omits_hint_when_none() -> None:
    """M12: hint=None → 'hint' key absent from POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        await client.submit_decision(
            task_id=_FAKE_TASK_ID,
            action="approve",
            idempotency_key="00000000-0000-7000-8000-000000000001",
            operator_actor_id="999",
            hint=None,
        )

    assert "hint" not in captured_body, f"'hint' key should be absent, got body: {captured_body}"


@pytest.mark.asyncio
async def test_submit_decision_includes_hint_when_string() -> None:
    """M12: hint='replan' → body['hint'] == 'replan'."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        await client.submit_decision(
            task_id=_FAKE_TASK_ID,
            action="approve",
            idempotency_key="00000000-0000-7000-8000-000000000001",
            operator_actor_id="999",
            hint="replan",
        )

    assert captured_body.get("hint") == "replan", f"Expected hint='replan', got: {captured_body}"


# ---------------------------------------------------------------------------
# New tests: M3 idempotency_status from body vs header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_decision_idempotency_status_from_body() -> None:
    """M3: idempotency_status in body takes precedence over header."""
    body_with_status = json.dumps(
        {
            "task_id": _FAKE_TASK_ID,
            "decision_id": _FAKE_DECISION_ID,
            "action": "approve",
            "decided_at": _FAKE_DECIDED_AT.isoformat(),
            "idempotency_status": "replayed",
        }
    )
    client = _make_registry_client(
        body=body_with_status,
        # Header says "applied" — body should win.
        headers={"X-Idempotency-Status": "applied", "content-type": "application/json"},
    )
    result = await client.submit_decision(
        task_id=_FAKE_TASK_ID,
        action="approve",
        idempotency_key="00000000-0000-7000-8000-000000000001",
        operator_actor_id="999",
    )
    assert result.idempotency_status == "replayed"


@pytest.mark.asyncio
async def test_submit_decision_idempotency_status_from_header() -> None:
    """M3: when body has no idempotency_status, falls back to X-Idempotency-Status header."""
    client = _make_registry_client(
        # Body has no idempotency_status field.
        headers={"X-Idempotency-Status": "replayed", "content-type": "application/json"},
    )
    result = await client.submit_decision(
        task_id=_FAKE_TASK_ID,
        action="approve",
        idempotency_key="00000000-0000-7000-8000-000000000001",
        operator_actor_id="999",
    )
    assert result.idempotency_status == "replayed"


# ---------------------------------------------------------------------------
# New tests: L4 HTTP 404 task not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_handler_404_replies_task_not_found() -> None:
    """L4: 404 with RFC 7807 body → reply contains '⚠️ Task rejected:'."""
    client = _make_registry_client(
        status_code=404,
        body=json.dumps({"title": "Task not found", "status": 404, "detail": "no such task"}),
        headers={"content-type": "application/problem+json"},
    )
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # 404 is a 4xx so format_http_error renders it as "⚠️ Task rejected: ..."
    assert "⚠️ Task rejected:" in reply_text


# ---------------------------------------------------------------------------
# NFR-P2 latency test (AC-10) — marked @pytest.mark.slow
# M7: reduced mock sleep to 100ms, threshold to 200ms (2× headroom).
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_approve_handler_latency_under_p95_budget() -> None:
    """AC-10 / NFR-P2: p95 of 100 sequential /approve invocations < 0.200 s.

    M7: Registry mock responds in ~100 ms (asyncio.sleep) to simulate realistic
    registry-api latency. Threshold is 0.200 s (2× headroom above 100 ms mock).
    This gives a much more stable test on loaded CI than the previous 200ms/250ms
    pairing (only 50ms headroom). Mirrors Story 3.3 M5 formula exactly.
    """

    async def _slow_transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.100)  # 100 ms simulated registry latency (M7)
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
            await handle_approve(msg, registry_client=client)
            latencies.append(time.perf_counter() - t0)

    latencies.sort()
    p95_index = math.ceil(0.95 * n) - 1  # M4: correct percentile index
    p95 = latencies[p95_index]
    assert p95 < 0.200, (  # M7: 100 ms mock + 100 ms headroom (2× ratio)
        f"NFR-P2: p95 latency {p95:.3f} s exceeds 0.200 s budget "
        f"(max={latencies[-1]:.3f} s, min={latencies[0]:.3f} s)"
    )


# ---------------------------------------------------------------------------
# Story 6.10: --override license parsing and 409 license-block handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_license_parsed_and_sent() -> None:
    """--override license in message → override='license' in POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override license")
        await handle_approve(msg, registry_client=client)

    assert captured_body.get("override") == "license"


@pytest.mark.asyncio
async def test_no_override_when_flag_absent() -> None:
    """No --override in message → 'override' key absent from POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID}")
        await handle_approve(msg, registry_client=client)

    assert "override" not in captured_body


@pytest.mark.asyncio
async def test_invalid_override_ignored() -> None:
    """--override unknown shows error reply and does not submit to registry (no POST made)."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override unknown")
        await handle_approve(msg, registry_client=client)

    assert "override" not in captured_body


@pytest.mark.asyncio
async def test_409_license_block_shows_override_hint() -> None:
    """409 with approval_blocked_by/license_flag → override hint reply."""
    client = _make_registry_client(
        status_code=409,
        body=json.dumps(
            {
                "type": "approval_blocked_by",
                "title": "Approval Blocked",
                "status": 409,
                "detail": "License flag active.",
                "extensions": {"reason": "license_flag"},
            }
        ),
        headers={"content-type": "application/problem+json"},
    )
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "--override license" in reply_text
    assert "License flag" in reply_text


@pytest.mark.asyncio
async def test_409_generic_not_intercepted() -> None:
    """409 without approval_blocked_by type → falls through to format_http_error."""
    client = _make_registry_client(
        status_code=409,
        body=json.dumps(
            {
                "type": "about:blank",
                "title": "Conflict",
                "status": 409,
                "detail": "some other conflict",
            }
        ),
        headers={"content-type": "application/problem+json"},
    )
    msg = _make_message()
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # Should NOT contain the license override hint
    assert "--override license" not in reply_text


@pytest.mark.asyncio
async def test_submit_decision_includes_override_when_license() -> None:
    """submit_decision with override='license' includes it in POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        await client.submit_decision(
            task_id=_FAKE_TASK_ID,
            action="approve",
            idempotency_key="00000000-0000-7000-8000-000000000001",
            operator_actor_id="999",
            override="license",
        )

    assert captured_body.get("override") == "license"


@pytest.mark.asyncio
async def test_submit_decision_omits_override_when_none() -> None:
    """submit_decision with override=None omits 'override' from POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        await client.submit_decision(
            task_id=_FAKE_TASK_ID,
            action="approve",
            idempotency_key="00000000-0000-7000-8000-000000000001",
            operator_actor_id="999",
            override=None,
        )

    assert "override" not in captured_body


@pytest.mark.asyncio
async def test_override_equals_syntax() -> None:
    """--override=license (equals syntax) is accepted."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override=license")
        await handle_approve(msg, registry_client=client)

    assert captured_body.get("override") == "license"


@pytest.mark.asyncio
async def test_override_prefix_no_false_match() -> None:
    """--override licensefoo does NOT match (word-boundary enforced)."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override licensefoo")
        await handle_approve(msg, registry_client=client)

    assert "override" not in captured_body


@pytest.mark.asyncio
async def test_override_no_space_no_false_match() -> None:
    """--overridelicense (no separator) does NOT match."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --overridelicense")
        await handle_approve(msg, registry_client=client)

    assert "override" not in captured_body


# ---------------------------------------------------------------------------
# Story 6.11: --override budget parsing and 409 budget-block handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_budget_parsed_and_sent() -> None:
    """--override budget in message → override='budget' in POST body."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override budget")
        await handle_approve(msg, registry_client=client)

    assert captured_body.get("override") == "budget"


@pytest.mark.asyncio
async def test_override_budget_with_equals_syntax() -> None:
    """--override=budget (equals syntax) is parsed correctly."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override=budget")
        await handle_approve(msg, registry_client=client)

    assert captured_body.get("override") == "budget"


@pytest.mark.asyncio
async def test_budget_exceeded_409_shows_hint() -> None:
    """409 with reason=budget_exceeded shows override hint."""
    client = _make_registry_client(
        status_code=409,
        body=json.dumps(
            {
                "type": "approval_blocked_by",
                "title": "Approval Blocked",
                "status": 409,
                "detail": "Budget exceeded.",
                "extensions": {"reason": "budget_exceeded"},
            }
        ),
        headers={"content-type": "application/problem+json"},
    )
    msg = _make_message(text=f"/approve {_FAKE_TASK_ID}")
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "--override budget" in reply_text
    assert "Budget exceeded" in reply_text


@pytest.mark.asyncio
async def test_override_budget_uppercase_is_lowercased_and_matched() -> None:
    """--override BUDGET (uppercase) is lowercased and matched — value check is case-insensitive."""
    captured_body: dict[str, object] = {}

    async def _transport(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
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
        msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override BUDGET")
        await handle_approve(msg, registry_client=client)

    # Case-insensitive: BUDGET is lowercased to "budget" and matched.
    assert captured_body.get("override") == "budget"


@pytest.mark.asyncio
async def test_unknown_override_value_shows_hint() -> None:
    """--override unknown shows error reply, does not submit."""
    client = _make_registry_client()
    msg = _make_message(text=f"/approve {_FAKE_TASK_ID} --override unknown")
    await handle_approve(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Unknown override" in reply_text
    assert "license" in reply_text
    assert "budget" in reply_text
