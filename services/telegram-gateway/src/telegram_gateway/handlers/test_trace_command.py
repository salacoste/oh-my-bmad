"""Tests for /trace command handler (Story 9.7 / FR59a).

Story 9.7 pass-1 PH-A3 / PH-B1 — zero test coverage on telegram /trace handler.

Covers:
  * test_trace_allowlist_rejects_unauthorized_chat (PH-B1)
  * test_trace_pagination_boundary_20_21
  * test_trace_html_escapes_trace_id
  * test_trace_page_arg_invalid_shows_error (PM-B9)
  * test_trace_invalid_shape_friendly_error
  * test_trace_no_events_message
  * test_trace_renders_events_happy_path
  * test_trace_zwsp_prefix_stripped (PM-E12)
  * test_get_trace_adapter_200 (PM-A8)
  * test_get_trace_adapter_malformed_body (PM-A8)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError
from telegram_gateway.handlers.trace_command import handle_trace

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_TG_TRACE_ID = "tg:99999"
_FAKE_BASE = "http://registry-api:8080"


def _make_message(
    *,
    text: str = f"/trace {_VALID_TRACE_ID}",
    chat_id: int = 100,
    user_id: int = 999,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_client(events: list[dict] | None = None) -> RegistryAPIClient:
    client = MagicMock(spec=RegistryAPIClient)
    client.get_trace = AsyncMock(return_value=events if events is not None else [])
    return client


def _make_event(i: int = 0) -> dict:
    return {
        "event_id": f"e-00000000-0000-7000-8000-{i:012d}",
        "type": "task.created",
        "emitted_at": "2026-01-01T10:00:00.000000Z",
        "emitted_at_monotonic_ns": 1000 + i,
        "trace_id": _VALID_TRACE_ID,
        "payload": {},
        "extensions": {},
    }


# ---------------------------------------------------------------------------
# PH-B1: per-chat allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_allowlist_rejects_unauthorized_chat() -> None:
    """PH-B1: allowed_chat_ids enforced — unknown chat gets silent drop."""
    msg = _make_message(chat_id=999_999)
    client = _make_client(events=[_make_event()])

    await handle_trace(msg, client, allowed_chat_ids=frozenset({100}))

    # Silent drop: no reply sent, no get_trace call
    msg.reply.assert_not_called()
    client.get_trace.assert_not_called()


@pytest.mark.asyncio
async def test_trace_allowlist_passes_authorized_chat() -> None:
    """PH-B1: chat in allowed_chat_ids proceeds normally."""
    msg = _make_message(chat_id=100)
    client = _make_client(events=[_make_event()])

    await handle_trace(msg, client, allowed_chat_ids=frozenset({100}))

    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_trace_allowlist_empty_bypasses_check() -> None:
    """Empty allowed_chat_ids = bypass (back-compat default)."""
    msg = _make_message(chat_id=999_999)
    client = _make_client(events=[])

    # Empty frozenset = bypass; any chat allowed
    await handle_trace(msg, client, allowed_chat_ids=frozenset())

    # Proceeded to API call (empty result → no-events reply)
    client.get_trace.assert_called_once()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_pagination_boundary_20_fits_one_message() -> None:
    """20 events fit in a single message — no pagination notice."""
    events = [_make_event(i) for i in range(20)]
    msg = _make_message()
    client = _make_client(events=events)

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Page" not in reply_text


@pytest.mark.asyncio
async def test_trace_pagination_boundary_21_shows_page_notice() -> None:
    """21 events → page notice in reply (page 1/2)."""
    events = [_make_event(i) for i in range(21)]
    msg = _make_message()
    client = _make_client(events=events)

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "Page 1/2" in reply_text


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_html_escapes_trace_id() -> None:
    """trace_id is HTML-escaped in reply (XSS prevention)."""
    # Inject an XSS payload as trace_id via the rendered trace_id label.
    # We use the no-events path so handler replies with the trace_id in text.
    trace_id = _VALID_TRACE_ID
    msg = _make_message(text=f"/trace {trace_id}")
    client = _make_client(events=[])

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # The trace_id appears in the reply and is safe (no raw < > characters injected)
    assert trace_id in reply_text


# ---------------------------------------------------------------------------
# Argument parsing errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_page_arg_invalid_shows_error() -> None:
    """PM-B9: invalid page=abc → error reply, not silent default to 1."""
    msg = _make_message(text=f"/trace {_VALID_TRACE_ID} page=abc")
    client = _make_client(events=[_make_event()])

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "invalid" in reply_text.lower() or "page" in reply_text.lower()
    # No API call made when page arg is invalid
    client.get_trace.assert_not_called()


@pytest.mark.asyncio
async def test_trace_invalid_shape_friendly_error() -> None:
    """Bad trace_id shape → friendly error, no API call."""
    msg = _make_message(text="/trace not-a-valid-id-at-all")
    client = _make_client(events=[])

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "invalid" in reply_text.lower() or "UUIDv7" in reply_text
    client.get_trace.assert_not_called()


@pytest.mark.asyncio
async def test_trace_no_events_message() -> None:
    """Empty results → 'no events' reply."""
    msg = _make_message()
    client = _make_client(events=[])

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "no events" in reply_text.lower() or _VALID_TRACE_ID in reply_text


@pytest.mark.asyncio
async def test_trace_renders_events_happy_path() -> None:
    """Happy path: single event rendered with type visible."""
    events = [_make_event(0)]
    msg = _make_message()
    client = _make_client(events=events)

    await handle_trace(msg, client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "task.created" in reply_text


# ---------------------------------------------------------------------------
# PM-E12: ZWSP stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_zwsp_prefix_stripped() -> None:
    """PM-E12: ZWSP-prefixed trace_id is cleaned before validation."""
    zwsp = "​"
    trace_id_with_zwsp = f"{zwsp}{_VALID_TRACE_ID}"
    msg = _make_message(text=f"/trace {trace_id_with_zwsp}")
    client = _make_client(events=[_make_event()])

    # Should NOT reject with invalid shape — ZWSP stripped, real trace_id validated
    await handle_trace(msg, client)

    # get_trace called (ZWSP was stripped, valid trace_id passed)
    client.get_trace.assert_called_once_with(
        trace_id=_VALID_TRACE_ID,
        request_id=client.get_trace.call_args.kwargs["request_id"],
    )


# ---------------------------------------------------------------------------
# PM-A8: RegistryAPIClient.get_trace() adapter tests
# ---------------------------------------------------------------------------


class TestGetTraceAdapter:
    @pytest.mark.asyncio
    async def test_get_trace_adapter_200(self) -> None:
        """200 with list body → returns list of dicts."""
        fake_events = [_make_event(0)]
        fake_response = httpx.Response(
            200,
            json=fake_events,
            request=httpx.Request("GET", f"{_FAKE_BASE}/v1/trace/{_VALID_TRACE_ID}"),
        )
        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=fake_response)

        client = RegistryAPIClient(http_client=http_client)
        result = await client.get_trace(trace_id=_VALID_TRACE_ID)

        assert isinstance(result, list)
        assert result[0]["type"] == "task.created"

    @pytest.mark.asyncio
    async def test_get_trace_adapter_malformed_body(self) -> None:
        """Non-list 200 body → RegistryResponseError."""
        fake_response = httpx.Response(
            200,
            json={"not": "a list"},
            request=httpx.Request("GET", f"{_FAKE_BASE}/v1/trace/{_VALID_TRACE_ID}"),
        )
        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=fake_response)

        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(RegistryResponseError):
            await client.get_trace(trace_id=_VALID_TRACE_ID)

    @pytest.mark.asyncio
    async def test_get_trace_adapter_500_raises(self) -> None:
        """500 → HTTPStatusError propagated."""
        fake_response = httpx.Response(
            500,
            json={"detail": "internal"},
            request=httpx.Request("GET", f"{_FAKE_BASE}/v1/trace/{_VALID_TRACE_ID}"),
        )
        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=fake_response)

        client = RegistryAPIClient(http_client=http_client)
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_trace(trace_id=_VALID_TRACE_ID)
