"""Tests for /retry command handler (Story 3.18 AC-6).

Coverage (>=24 tests):
Handler tests (12):
- test_handle_retry_success_renders_confirmation — success reply with @handle + timestamp
- test_handle_retry_success_with_retry_deduped — idempotency_status="replayed" shows deduped
- test_handle_retry_with_hint_passes_hint — hint text forwarded as hint
- test_handle_retry_without_hint_passes_none_hint — no hint → hint=None
- test_handle_retry_no_args_shows_usage — "/retry" → usage reply
- test_handle_retry_invalid_task_id_shows_usage — "/retry bad" → usage with example
- test_handle_retry_http_status_error — HTTPStatusError → format_http_error reply
- test_handle_retry_network_error — ReadTimeout → "Could not reach registry: ReadTimeout"
- test_handle_retry_too_many_redirects — TooManyRedirects → "too many redirects"
- test_handle_retry_malformed_response — RegistryResponseError → "unexpected response"
- test_handle_retry_unexpected_exception — RuntimeError backstop → "Internal error"
- test_handle_retry_from_user_none_uses_unknown_actor — from_user None → unknown/@operator

HTML security tests (2):
- test_handle_retry_html_chars_in_username_are_escaped — HTML chars in username escaped
- test_handle_retry_html_chars_in_first_name_are_escaped — HTML chars in first_name escaped

Actor resolution tests (2):
- test_handle_retry_username_none_uses_first_name — username=None falls back to first_name
- test_handle_retry_no_username_no_first_name_uses_operator — both None → @operator

Router test (1):
- test_make_retry_router_returns_fresh_routers — factory produces distinct instances

Code-review fix tests (7):
- test_handle_retry_hint_truncated_at_max_length — hint > MAX_HINT_LENGTH is truncated
- test_handle_retry_hint_exactly_at_max_length_passes — hint == MAX_HINT_LENGTH passes through
- test_handle_retry_unicode_hint_passes_through[emoji] — emoji hint passes through
- test_handle_retry_unicode_hint_passes_through[rtl-override] — RTL override
- test_handle_retry_unicode_hint_passes_through[newlines] — newlines in hint pass through
- test_handle_retry_unicode_hint_passes_through[zwj] — zero-width joiner hint passes through
- test_handle_retry_from_user_none_logs_chat_id — from_user=None with valid chat still replies
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.registry_client import (
    DecisionResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)
from telegram_gateway.handlers.retry_command import handle_retry, make_retry_router

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"
_DECISION_ID = "d-0192a1b5-1234-7abc-89de-f0123456789b"
_DECIDED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_message(
    *,
    text: str = "/retry",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
    first_name: str | None = "Test",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /retry tests."""
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
                action="retry",
                decided_at=_DECIDED_AT,
            ),
        )
    return client


# ---------------------------------------------------------------------------
# Handler tests (12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_retry_success_renders_confirmation() -> None:
    """Success reply contains @handle, timestamp, and 'Task resumed'."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@testoperator" in reply_text
    assert "Task resumed" in reply_text
    assert _DECIDED_AT.isoformat() in reply_text


@pytest.mark.asyncio
async def test_handle_retry_success_with_retry_deduped() -> None:
    """idempotency_status='replayed' → '(retry deduped)' in reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(  # type: ignore[method-assign]
        return_value=DecisionResponseLocal(
            task_id=_TASK_ID,
            decision_id=_DECISION_ID,
            action="retry",
            decided_at=_DECIDED_AT,
            idempotency_status="replayed",
        ),
    )
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "(retry deduped)" in reply_text
    assert "Task resumed" in reply_text


@pytest.mark.asyncio
async def test_handle_retry_with_hint_passes_hint() -> None:
    """Hint text after task-id is passed as hint to submit_decision."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID} rate limit per-user")

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] == "rate limit per-user"
    assert call_kwargs["action"] == "retry"


@pytest.mark.asyncio
async def test_handle_retry_without_hint_passes_none_hint() -> None:
    """No hint text → hint=None in submit_decision call."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] is None


@pytest.mark.asyncio
async def test_handle_retry_no_args_shows_usage() -> None:
    """Message text is '/retry'; assert usage reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/retry")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "Usage: /retry <task-id> [hint]", f"Unexpected reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_retry_invalid_task_id_shows_usage() -> None:
    """Message text is '/retry not-a-task-id'; assert usage reply with example."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/retry not-a-task-id")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /retry <task-id>" in reply_text, f"Unexpected reply: {reply_text!r}"
    assert "example:" in reply_text, f"Expected 'example:' in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_retry_http_status_error() -> None:
    """HTTPStatusError → format_http_error reply with ⚠️ prefix."""
    client = _make_registry_client_with_mock()
    exc = httpx.HTTPStatusError(
        "Internal Server Error",
        request=httpx.Request("POST", "http://registry-api:8080/v1/tasks/x/decisions"),
        response=httpx.Response(500, content=b'{"detail":"error"}'),
    )
    client.submit_decision = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️"), f"Expected '⚠️' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_retry_network_error() -> None:
    """ReadTimeout → 'Could not reach registry: ReadTimeout' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.ReadTimeout("timed out")
    )
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry: ReadTimeout" in reply_text, (
        f"Expected 'Could not reach registry: ReadTimeout', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_retry_too_many_redirects() -> None:
    """TooManyRedirects → 'too many redirects' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.TooManyRedirects("loop")
    )
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text, f"Expected 'too many redirects', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_retry_malformed_response() -> None:
    """RegistryResponseError → 'unexpected response' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(  # type: ignore[method-assign]
        side_effect=RegistryResponseError("malformed body")
    )
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "unexpected response" in reply_text, (
        f"Expected 'unexpected response', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_retry_unexpected_exception() -> None:
    """RuntimeError backstop → 'Internal error' reply."""
    client = _make_registry_client_with_mock()
    client.submit_decision = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message(text=f"/retry {_TASK_ID}")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Internal error" in reply_text, f"Expected 'Internal error', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_retry_from_user_none_uses_unknown_actor() -> None:
    """from_user is None → 'unknown' actor_id, '@operator' handle (no double-@)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}")
    msg.from_user = None

    await handle_retry(msg, registry_client=client)

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
async def test_handle_retry_html_chars_in_username_are_escaped() -> None:
    """Username containing HTML chars is escaped in the reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}", username="<script>alert(1)</script>")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<script>" not in reply_text, f"Raw '<script>' found: {reply_text!r}"
    assert "&lt;script&gt;" in reply_text, f"Escaped script tag missing: {reply_text!r}"


# ---------------------------------------------------------------------------
# Router test (1)
# ---------------------------------------------------------------------------


def test_make_retry_router_returns_fresh_routers() -> None:
    """Each call returns a distinct Router instance (no shared state)."""
    r1 = make_retry_router()
    r2 = make_retry_router()
    assert r1 is not r2, "Router factory must return fresh instances"


# ---------------------------------------------------------------------------
# Code-review fix tests (4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_retry_hint_truncated_at_max_length() -> None:
    """Hint exceeding MAX_HINT_LENGTH is silently truncated."""
    from telegram_gateway.handlers.retry_command import MAX_HINT_LENGTH

    long_hint = "x" * (MAX_HINT_LENGTH + 50)
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID} {long_hint}")

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert len(call_kwargs["hint"]) == MAX_HINT_LENGTH
    assert call_kwargs["hint"] == "x" * MAX_HINT_LENGTH


@pytest.mark.parametrize(
    "hint_text",
    [
        "🔥 retry with 🔥",  # emoji
        "‮ reversed text",  # RTL override
        "line1\nline2",  # newlines
        "a‍b",  # zero-width joiner
    ],
    ids=["emoji", "rtl-override", "newlines", "zwj"],
)
@pytest.mark.asyncio
async def test_handle_retry_unicode_hint_passes_through(hint_text: str) -> None:
    """Unicode hint strings are passed as hint without corruption."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID} {hint_text}")

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["hint"] == hint_text


@pytest.mark.asyncio
async def test_handle_retry_from_user_none_logs_chat_id() -> None:
    """from_user=None logs chat_id correctly (chat is always present on real messages)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}", chat_id=777)
    msg.from_user = None

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert call_kwargs["operator_actor_id"] == "unknown"
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text


# ---------------------------------------------------------------------------
# HTML security tests — first_name path (code-review addition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_retry_html_chars_in_first_name_are_escaped() -> None:
    """first_name with HTML chars is escaped when username is None."""
    client = _make_registry_client_with_mock()
    msg = _make_message(
        text=f"/retry {_TASK_ID}",
        username=None,
        first_name="<b>admin</b>",
    )

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<b>" not in reply_text
    assert "&lt;b&gt;admin&lt;/b&gt;" in reply_text


# ---------------------------------------------------------------------------
# Actor resolution tests (code-review addition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_retry_username_none_uses_first_name() -> None:
    """username=None falls back to html.escape(first_name) in reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}", username=None, first_name="Alice")

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@Alice" in reply_text


@pytest.mark.asyncio
async def test_handle_retry_no_username_no_first_name_uses_operator() -> None:
    """Both username and first_name are None → @operator (no double-@)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID}", username=None, first_name=None)

    await handle_retry(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text


# ---------------------------------------------------------------------------
# Boundary test (code-review addition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_retry_hint_exactly_at_max_length_passes() -> None:
    """Hint exactly at MAX_HINT_LENGTH is not truncated."""
    from telegram_gateway.handlers.retry_command import MAX_HINT_LENGTH

    hint = "a" * MAX_HINT_LENGTH
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/retry {_TASK_ID} {hint}")

    await handle_retry(msg, registry_client=client)

    client.submit_decision.assert_called_once()
    call_kwargs = client.submit_decision.call_args[1]
    assert len(call_kwargs["hint"]) == MAX_HINT_LENGTH
