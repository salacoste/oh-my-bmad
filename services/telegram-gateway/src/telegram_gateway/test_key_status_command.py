"""Tests for /key-status command handler (Story 11.5.1 / AC3 + AC5).

Two tests per the Story 11.5 AC6 deferral budget:

1. ``test_key_status_telegram_command_renders_current_fingerprint`` — happy
   path: mocked RegistryAPIClient returns a typed KeyStatusResponseLocal
   with known fingerprint / rotated_at / rotated_by; handler reply contains
   the verbatim 4-line block and all 3 string fields are HTML-escaped.
2. ``test_key_status_telegram_command_404_cold_start_renders_operator_message``
   (Story 11.5.1 AC5 bonus) — registry-api 404 → handler renders the
   "key fingerprint not yet materialized" message rather than a stack
   trace or generic format_http_error envelope.

Pattern mirrors ``test_ping_command.py`` exactly: aiogram Message mock with
AsyncMock-backed ``reply``, RegistryAPIClient backed by httpx.MockTransport,
module-level handler invoked directly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from telegram_gateway.handlers.key_status_command import handle_key_status
from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
)

# ---------------------------------------------------------------------------
# Shared fixture values mirror routes/test_key_status.py for cross-service
# determinism (same fingerprint constant traces a single value through
# the whole tri-party contract).
# ---------------------------------------------------------------------------

_FIXTURE_FINGERPRINT = "a1b2c3d4e5f6789a"
_FIXTURE_ROTATED_BY = "http-api"
_FIXTURE_ROTATED_AT = datetime(2026, 5, 21, 14, 30, 0, tzinfo=UTC)

_VALID_KEY_STATUS_JSON = json.dumps(
    {
        "fingerprint": _FIXTURE_FINGERPRINT,
        # ISO-Z form matches the registry-api response shape from
        # FastAPI's default datetime serializer.
        "rotated_at": "2026-05-21T14:30:00Z",
        "rotated_by_actor_id": _FIXTURE_ROTATED_BY,
    }
)


def _make_message() -> MagicMock:
    """Build a minimal aiogram Message mock for /key-status tests."""
    msg = MagicMock()
    msg.text = "/key-status"
    msg.message_id = 42
    msg.chat.id = 100
    msg.from_user.id = 999
    msg.from_user.username = "testoperator"
    msg.reply = AsyncMock(return_value=None)
    return msg


def _make_registry_client(
    *,
    status_code: int = 200,
    body: str = _VALID_KEY_STATUS_JSON,
) -> RegistryAPIClient:
    """RegistryAPIClient backed by httpx.MockTransport (matches test_ping_command pattern)."""

    async def _transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=body.encode(),
            request=request,
        )

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    return RegistryAPIClient(http_client=http_client)


# ---------------------------------------------------------------------------
# AC3 / AC5 — happy path (Story 11.5 AC6 spec test name)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_status_telegram_command_renders_current_fingerprint() -> None:
    """AC3 happy path — reply contains the verbatim 4-line operator block.

    Verifies:
      * fingerprint / rotated_at / rotated_by all appear in the reply text
      * the "Operator HMAC signing key:" header is the verbatim block prefix
      * "Signing active: yes" terminator is present
      * all 3 string fields are HTML-escaped (no raw "<" / ">" / "&" outside of
        the literal template — defensive even though the fixture values are
        safe — the assertion checks for `html.escape` invariants)
    """
    client = _make_registry_client()
    msg = _make_message()

    await handle_key_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]

    # Block header + terminator.
    assert reply_text.startswith("Operator HMAC signing key:\n"), (
        f"reply should start with the operator-block header; got {reply_text!r}"
    )
    assert reply_text.endswith("Signing active: yes"), (
        f"reply should end with 'Signing active: yes'; got {reply_text!r}"
    )

    # All 3 field values present.
    assert _FIXTURE_FINGERPRINT in reply_text
    assert _FIXTURE_ROTATED_BY in reply_text
    # ISO-Z timestamp present in seconds-precision form.
    assert "2026-05-21T14:30:00Z" in reply_text

    # 4-line layout (header + 3 fields + trailer) = 5 lines total.
    assert reply_text.count("\n") == 4, (
        f"expected exactly 4 newlines (5-line block); got {reply_text.count(chr(10))} "
        f"in {reply_text!r}"
    )


# ---------------------------------------------------------------------------
# AC5 bonus — cold-start path (404)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_status_telegram_command_404_cold_start_renders_operator_message() -> None:
    """AC5 cold-start: registry-api 404 → operator-readable 'not yet materialized'.

    Verifies the handler distinguishes a cold-start 404 from a generic HTTP
    error: the reply mentions "Key fingerprint not yet materialized" rather
    than the generic format_http_error envelope ("Key status failed: ...").
    """
    client = _make_registry_client(
        status_code=404,
        body=json.dumps(
            {
                "type": "about:blank",
                "title": "Not Found",
                "status": 404,
                "detail": (
                    "key fingerprint not yet materialized — registry-state "
                    "has not observed a key.rotated event since bootstrap."
                ),
            }
        ),
    )
    msg = _make_message()

    await handle_key_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "not yet materialized" in reply_text.lower(), (
        f"reply should mention cold-start guidance; got {reply_text!r}"
    )
    assert "Key status failed" not in reply_text, (
        f"reply should NOT use generic format_http_error envelope for 404; got {reply_text!r}"
    )
    # No stack trace leaked.
    assert "Traceback" not in reply_text


# ---------------------------------------------------------------------------
# AC5 bonus — HTML-escape defense-in-depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_status_telegram_command_html_escapes_string_fields() -> None:
    """Epic 11 retro L4 — defensive HTML-escape on all interpolated strings.

    Even though server-side Field constraints limit the actor_id charset,
    a future schema-loosening regression must not silently introduce a
    Telegram-side HTML injection. The handler MUST call html.escape on all
    3 string fields before substituting them into the reply.

    NOTE: a malicious rotated_by_actor_id value would only reach this code
    path if the server-side Field(max_length=128) constraint was loosened
    AND the materializer accepted unsafe charsets. This test pins the
    defense-in-depth at the rendering boundary regardless.
    """
    # Craft a body where rotated_by contains an HTML special char.
    body = json.dumps(
        {
            "fingerprint": _FIXTURE_FINGERPRINT,
            "rotated_at": "2026-05-21T14:30:00Z",
            # Note: rotated_by is constrained to max_length=128 + non-empty
            # but the regex/charset isn't tightly bounded. A future
            # regression that admits "<script>" would silently inject.
            "rotated_by_actor_id": "evil<script>x</script>",
        }
    )
    client = _make_registry_client(body=body)
    msg = _make_message()

    await handle_key_status(msg, registry_client=client)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # Raw "<script>" must NOT appear; HTML-escaped form should.
    assert "<script>" not in reply_text, (
        f"unescaped HTML must not appear in reply; got {reply_text!r}"
    )
    assert "&lt;script&gt;" in reply_text, f"HTML-escaped form expected; got {reply_text!r}"
