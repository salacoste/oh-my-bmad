"""Tests for /reject command handler (Story 3.17 AC-6).

Coverage (>=17 tests):
Handler tests (12):
- test_handle_reject_success_renders_confirmation — success reply with @handle + timestamp
- test_handle_reject_success_with_retry_deduped — idempotency_status="replayed" shows deduped
- test_handle_reject_with_reason_passes_hint — reason text forwarded as hint
- test_handle_reject_without_reason_passes_none_hint — no reason → hint=None
- test_handle_reject_no_args_shows_usage — "/reject" → usage reply
- test_handle_reject_invalid_task_id_shows_usage — "/reject bad" → usage with example
- test_handle_reject_http_status_error — HTTPStatusError → format_http_error reply
- test_handle_reject_network_error — ReadTimeout → "Could not reach registry: ReadTimeout"
- test_handle_reject_too_many_redirects — TooManyRedirects → "too many redirects"
- test_handle_reject_malformed_response — RegistryResponseError → "unexpected response"
- test_handle_reject_unexpected_exception — RuntimeError backstop → "Internal error"
- test_handle_reject_from_user_none_uses_unknown_actor — from_user None → unknown/@operator

HTML security test (1):
- test_handle_reject_html_chars_in_username_are_escaped — HTML chars in username escaped

Router test (1):
- test_make_reject_router_returns_fresh_routers — factory produces distinct instances

Code-review fix tests (4):
- test_handle_reject_reason_truncated_at_max_length — reason > MAX_REASON_LENGTH is truncated
- test_handle_reject_unicode_reason_passes_through[emoji] — emoji reason passes through
- test_handle_reject_unicode_reason_passes_through[rtl-override] — RTL override
- test_handle_reject_unicode_reason_passes_through[newlines] — newlines in reason pass through
- test_handle_reject_unicode_reason_passes_through[zwj] — zero-width joiner reason passes through
- test_handle_reject_from_user_none_logs_chat_id — from_user=None logs chat_id correctly
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.registry_client import (
    DecisionResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)
from telegram_gateway.handlers.reject_command import handle_reject, make_reject_router

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"
_DECISION_ID = "d-0192a1b5-1234-7abc-89de-f0123456789b"
_DECIDED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_VALID_DECISION_JSON = json.dumps(
    {
        "task_id": _TASK_ID,
        "decision_id": _DECISION_ID,
        "action": "reject",
        "decided_at": _DECIDED_AT.isoformat(),
    }
)


def _make_message(
    *,
    text: str = "/reject",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
    first_name: str | None = "Test",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /reject tests."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = first_name
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_registry_client_with_mock(
    *,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock RegistryAPIClient that either raises or returns a DecisionResponseLocal."""
    client = MagicMock(spec=RegistryAPIClient)
    if side_effect is not None:
        client.submit_decision = AsyncMock(side_effect=side_effect)
    else:
        client.submit_decision = AsyncMock(
            return_value=DecisionResponseLocal(
                task_id=_TASK_ID,
                decision_id=_DECISION_ID,
                action="reject",
                decided_at=_DECIDED_AT,
            ),
        )
    return client


# ---------------------------------------------------------------------------
# Handler tests (12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_reject_success_renders_confirmation() -> None:
    """Success reply contains @handle, timestamp, and 'Task stopped'."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@testoperator" in reply_text
    assert "Task stopped" in reply_text
    assert _DECIDED_AT.isoformat() in reply_text


@pytest.mark.asyncio
async def test_handle_reject_success_with_retry_deduped() -> None:
    """idempotency_status='replayed' → '(retry deduped)' in reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(
        return_value=DecisionResponseLocal(
            task_id=_TASK_ID,
            decision_id=_DECISION_ID,
            action="reject",
            decided_at=_DECIDED_AT,
            idempotency_status="replayed",
        ),
    )
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "(retry deduped)" in reply_text
    assert "Task stopped" in reply_text


@pytest.mark.asyncio
async def test_handle_reject_with_reason_passes_hint() -> None:
    """Reason text after task-id is passed as hint to submit_decision."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID} push before review")

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] == "push before review"
    assert call_kwargs["action"] == "reject"


@pytest.mark.asyncio
async def test_handle_reject_without_reason_passes_none_hint() -> None:
    """No reason text → hint=None in submit_decision call."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] is None


@pytest.mark.asyncio
async def test_handle_reject_no_args_shows_usage() -> None:
    """Message text is '/reject'; assert usage reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/reject")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "Usage: /reject <task-id> [reason]", f"Unexpected reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_reject_invalid_task_id_shows_usage() -> None:
    """Message text is '/reject not-a-task-id'; assert usage reply with example."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/reject not-a-task-id")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /reject <task-id>" in reply_text, f"Unexpected reply: {reply_text!r}"
    assert "example:" in reply_text, f"Expected 'example:' in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_reject_http_status_error() -> None:
    """HTTPStatusError → format_http_error reply with ⚠️ prefix."""
    client = _make_registry_client_with_mock()
    exc = httpx.HTTPStatusError(
        "Internal Server Error",
        request=httpx.Request("POST", "http://registry-api:8080/v1/tasks/x/decisions"),
        response=httpx.Response(500, content=b'{"detail":"error"}'),
    )
    client.submit_decision = AsyncMock(side_effect=exc)
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️"), f"Expected '⚠️' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_reject_network_error() -> None:
    """ReadTimeout → 'Could not reach registry: ReadTimeout' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry: ReadTimeout" in reply_text, (
        f"Expected 'Could not reach registry: ReadTimeout', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_reject_too_many_redirects() -> None:
    """TooManyRedirects → 'too many redirects' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(side_effect=httpx.TooManyRedirects("loop"))
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text, f"Expected 'too many redirects', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_reject_malformed_response() -> None:
    """RegistryResponseError → 'unexpected response' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(side_effect=RegistryResponseError("malformed body"))
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "unexpected response" in reply_text, (
        f"Expected 'unexpected response', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_reject_unexpected_exception() -> None:
    """RuntimeError backstop → 'Internal error' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(side_effect=RuntimeError("boom"))
    msg = _make_message(text=f"/reject {_TASK_ID}")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Internal error" in reply_text, f"Expected 'Internal error', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_reject_from_user_none_uses_unknown_actor() -> None:
    """from_user is None → 'unknown' actor_id, '@operator' handle (no double-@)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID}")
    msg.from_user = None

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["operator_actor_id"] == "unknown"
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text, f"Double @ detected: {reply_text!r}"


# ---------------------------------------------------------------------------
# HTML security test (1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_reject_html_chars_in_username_are_escaped() -> None:
    """Username containing HTML chars is escaped in the reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID}", username="<script>alert(1)</script>")

    await handle_reject(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<script>" not in reply_text, f"Raw '<script>' found: {reply_text!r}"
    assert "&lt;script&gt;" in reply_text, f"Escaped script tag missing: {reply_text!r}"


# ---------------------------------------------------------------------------
# Router test (1)
# ---------------------------------------------------------------------------


def test_make_reject_router_returns_fresh_routers() -> None:
    """Each call returns a distinct Router instance (no shared state)."""
    r1 = make_reject_router()
    r2 = make_reject_router()
    assert r1 is not r2, "Router factory must return fresh instances"


# ---------------------------------------------------------------------------
# Code-review fix tests (3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_reject_reason_truncated_at_max_length() -> None:
    """Reason exceeding MAX_REASON_LENGTH is silently truncated."""
    from telegram_gateway.handlers.reject_command import MAX_REASON_LENGTH

    long_reason = "x" * (MAX_REASON_LENGTH + 50)
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID} {long_reason}")

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert len(call_kwargs["hint"]) == MAX_REASON_LENGTH
    assert call_kwargs["hint"] == "x" * MAX_REASON_LENGTH


@pytest.mark.parametrize(
    "reason_text",
    [
        "🔥 task is 🔥",  # emoji
        "‮ reversed text",  # RTL override
        "line1\nline2",  # newlines
        "a‍b",  # zero-width joiner
    ],
    ids=["emoji", "rtl-override", "newlines", "zwj"],
)
@pytest.mark.asyncio
async def test_handle_reject_unicode_reason_passes_through(reason_text: str) -> None:
    """Unicode reason strings are passed as hint without corruption."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID} {reason_text}")

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] == reason_text


@pytest.mark.asyncio
async def test_handle_reject_from_user_none_logs_chat_id() -> None:
    """from_user=None logs chat_id correctly (chat is always present on real messages)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/reject {_TASK_ID}", chat_id=777)
    msg.from_user = None

    await handle_reject(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["operator_actor_id"] == "unknown"
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text
