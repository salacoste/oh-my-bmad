"""Tests for /agent command handler (Story 3.19 AC-6).

Coverage (>=16 tests):
Handler tests (10):
- test_handle_agent_success_renders_runtime_info — success reply with runtime + @handle
- test_handle_agent_http_status_error — HTTPStatusError → format_http_error reply
- test_handle_agent_network_error — ReadTimeout → "Could not reach registry: ReadTimeout"
- test_handle_agent_too_many_redirects — TooManyRedirects → "too many redirects"
- test_handle_agent_malformed_response — RegistryResponseError → "unexpected response"
- test_handle_agent_unexpected_exception — RuntimeError backstop → "Internal error"
- test_handle_agent_from_user_none_uses_operator — from_user None → @operator (no double-@)
- test_handle_agent_no_args_shows_usage — "/agent" → usage reply
- test_handle_agent_invalid_task_id_shows_usage — "/agent bad" → usage with example
- test_handle_agent_usage_reply_no_registry_call — usage path never calls get_task

HTML security tests (2):
- test_handle_agent_html_chars_in_username_are_escaped — HTML chars in username escaped
- test_handle_agent_html_chars_in_first_name_are_escaped — first_name HTML escaping path

Router test (1):
- test_make_agent_router_returns_fresh_routers — factory produces distinct instances

Actor resolution tests (2):
- test_handle_agent_username_none_uses_first_name — username=None falls back to first_name
- test_handle_agent_no_username_no_first_name_uses_operator — both None → @operator

Code-review fix tests (1):
- test_handle_agent_from_user_none_with_valid_chat — from_user=None + valid chat_id still replies
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.agent_command import handle_agent, make_agent_router
from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
    RegistryResponseError,
)

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"


def _make_message(
    *,
    text: str = "/agent",
    message_id: int = 42,
    chat_id: int = 100,
    user_id: int = 999,
    username: str | None = "testoperator",
    first_name: str | None = "Test",
) -> MagicMock:
    """Build a minimal aiogram Message mock for /agent tests."""
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
    """Build a mock RegistryAPIClient that either raises or returns a task."""
    client = MagicMock(spec=RegistryAPIClient)
    if side_effect is not None:
        client.get_task = AsyncMock(side_effect=side_effect)
    else:
        task_response = MagicMock()
        task_response.task_id = _TASK_ID
        client.get_task = AsyncMock(return_value=task_response)
    return client


# ---------------------------------------------------------------------------
# Handler tests (10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_success_renders_runtime_info() -> None:
    """Success reply contains runtime=claude-code and @handle."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "🤖" in reply_text
    assert "runtime=claude-code" in reply_text
    assert _TASK_ID in reply_text
    assert "@testoperator" in reply_text


@pytest.mark.asyncio
async def test_handle_agent_http_status_error() -> None:
    """HTTPStatusError → format_http_error reply with ⚠️ prefix."""
    client = _make_registry_client_with_mock()
    exc = httpx.HTTPStatusError(
        "Internal Server Error",
        request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/x"),
        response=httpx.Response(500, content=b'{"detail":"error"}'),
    )
    client.get_task = AsyncMock(side_effect=exc)  # type: ignore[method-assign]
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text.startswith("⚠️"), f"Expected '⚠️' prefix, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_network_error() -> None:
    """ReadTimeout → 'Could not reach registry: ReadTimeout' reply."""
    client = _make_registry_client_with_mock()
    client.get_task = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.ReadTimeout("timed out")
    )
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Could not reach registry: ReadTimeout" in reply_text, (
        f"Expected 'Could not reach registry: ReadTimeout', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_agent_too_many_redirects() -> None:
    """TooManyRedirects → 'too many redirects' reply."""
    client = _make_registry_client_with_mock()
    client.get_task = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.TooManyRedirects("loop")
    )
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "too many redirects" in reply_text, f"Expected 'too many redirects', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_malformed_response() -> None:
    """RegistryResponseError → 'unexpected response' reply."""
    client = _make_registry_client_with_mock()
    client.get_task = AsyncMock(  # type: ignore[method-assign]
        side_effect=RegistryResponseError("malformed body")
    )
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "unexpected response" in reply_text, (
        f"Expected 'unexpected response', got: {reply_text!r}"
    )


@pytest.mark.asyncio
async def test_handle_agent_unexpected_exception() -> None:
    """RuntimeError backstop → 'Internal error' reply."""
    client = _make_registry_client_with_mock()
    client.get_task = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    msg = _make_message(text=f"/agent {_TASK_ID}")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Internal error" in reply_text, f"Expected 'Internal error', got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_from_user_none_uses_operator() -> None:
    """from_user is None → @operator handle (no double-@)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}")
    msg.from_user = None

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text, f"Double @ detected: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_no_args_shows_usage() -> None:
    """Message text is '/agent'; assert usage reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/agent")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert reply_text == "Usage: /agent <task-id>", f"Unexpected reply: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_invalid_task_id_shows_usage() -> None:
    """Message text is '/agent not-a-task-id'; assert usage reply with example."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/agent not-a-task-id")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Usage: /agent <task-id>" in reply_text, f"Unexpected reply: {reply_text!r}"
    assert "example:" in reply_text, f"Expected 'example:' in reply, got: {reply_text!r}"


@pytest.mark.asyncio
async def test_handle_agent_usage_reply_no_registry_call() -> None:
    """Usage path never calls get_task."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text="/agent")

    await handle_agent(msg, registry_client=client)

    client.get_task.assert_not_called()


# ---------------------------------------------------------------------------
# HTML security tests (2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_html_chars_in_username_are_escaped() -> None:
    """Username containing HTML chars is escaped in the reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}", username="<script>alert(1)</script>")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<script>" not in reply_text, f"Raw '<script>' found: {reply_text!r}"
    assert "&lt;script&gt;" in reply_text, f"Escaped script tag missing: {reply_text!r}"


# ---------------------------------------------------------------------------
# Router test (1)
# ---------------------------------------------------------------------------


def test_make_agent_router_returns_fresh_routers() -> None:
    """Each call returns a distinct Router instance (no shared state)."""
    r1 = make_agent_router()
    r2 = make_agent_router()
    assert r1 is not r2, "Router factory must return fresh instances"


# ---------------------------------------------------------------------------
# Actor resolution tests (2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_username_none_uses_first_name() -> None:
    """username=None falls back to html.escape(first_name) in reply."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}", username=None, first_name="Alice")

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@Alice" in reply_text


@pytest.mark.asyncio
async def test_handle_agent_no_username_no_first_name_uses_operator() -> None:
    """Both username and first_name are None → @operator (no double-@)."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}", username=None, first_name=None)

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text


# ---------------------------------------------------------------------------
# HTML security tests — first_name path (code-review addition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_html_chars_in_first_name_are_escaped() -> None:
    """first_name with HTML chars is escaped when username is None."""
    client = _make_registry_client_with_mock()
    msg = _make_message(
        text=f"/agent {_TASK_ID}",
        username=None,
        first_name="<b>admin</b>",
    )

    await handle_agent(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "<b>" not in reply_text
    assert "&lt;b&gt;admin&lt;/b&gt;" in reply_text


# ---------------------------------------------------------------------------
# Code-review fix tests (1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_from_user_none_with_valid_chat() -> None:
    """from_user=None with valid chat still replies correctly."""
    client = _make_registry_client_with_mock()
    msg = _make_message(text=f"/agent {_TASK_ID}", chat_id=777)
    msg.from_user = None

    await handle_agent(msg, registry_client=client)

    client.get_task.assert_called_once()
    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "@operator" in reply_text
    assert "@@operator" not in reply_text
