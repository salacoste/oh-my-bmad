"""/key-status command handler for telegram-gateway (Story 11.5.1 / FR65a).

Calls GET /v1/key-status on registry-api via :class:`RegistryAPIClient` and
replies with a 4-line operator-readable summary of the currently-active HMAC
signing-key fingerprint plus its rotation provenance:

```
Operator HMAC signing key:
  Fingerprint:    a1b2c3d4e5f6789a
  Last rotated:   2026-05-21T14:30:00Z
  Rotated by:     http-api
  Signing active: yes
```

Pattern mirrors :mod:`telegram_gateway.handlers.ping_command` exactly: same
exception envelope, same module-level handler (NOT a closure), same HTML-escape
discipline for all interpolated string fields, same FR26-safe no-emission
contract (read-only GET, no audit event).

Cold-start path
---------------

When registry-state has not yet observed any ``key.rotated`` event the
``KeyFingerprint`` singleton row is absent and registry-api returns 404. The
handler renders an operator-readable "key fingerprint not yet materialized"
message rather than a stack trace.

Security
--------

The 16-hex ``fingerprint`` is a one-way SHA-256[:8] truncation per ADR-0006
§Key-fingerprint — operator-readable; does NOT reveal ``OPERATOR_HMAC_KEY``.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers._errors import format_http_error, log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.key_status_command")


async def handle_key_status(
    message: Message,
    registry_client: RegistryAPIClient,
    trace_id: str | None = None,
) -> None:
    """Handle the /key-status command.

    Calls GET /v1/key-status on registry-api and replies with the 4-line
    operator-readable HMAC-key fingerprint block.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in :mod:`telegram_gateway.app.lifespan` (shared with /ping, /task,
    /approve handlers).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    if trace_id is None:
        log_missing_trace_id(_log, "/key-status")
    request_id = new_request_id()

    try:
        key_status = await registry_client.get_key_status(request_id=request_id, trace_id=trace_id)
    except httpx.TooManyRedirects:
        _log.warning(
            "registry-api too many redirects for /key-status (request_id=%s)",
            request_id,
        )
        await safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except httpx.HTTPStatusError as exc:
        # 404 = cold-start (no KeyFingerprint row yet). Render a clearer
        # operator-readable message than the generic format_http_error envelope.
        if exc.response.status_code == 404:
            _log.info(
                "registry-api /v1/key-status returned 404 (cold-start; request_id=%s)",
                request_id,
            )
            await safe_reply(
                message,
                "ℹ️ Key fingerprint not yet materialized — registry-state has "
                "not observed a key.rotated event since bootstrap. Restart the "
                "stack to trigger detection.",
            )
            return
        # Other HTTP errors: use the standard format_http_error envelope.
        reply = format_http_error(exc, command_label="Key status")
        _log.warning(
            "registry-api HTTP error for /key-status (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await safe_reply(message, reply)
        return
    except RegistryResponseError:
        _log.exception(
            "registry-api malformed response for /key-status (request_id=%s)",
            request_id,
        )
        await safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError:
        _log.warning(
            "registry-api network error for /key-status (request_id=%s)",
            request_id,
        )
        await safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except Exception:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/key-status handler unexpected error (request_id=%s)",
            request_id,
        )
        await safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    # Success — render the 4-line operator-readable block. Epic 11 retro L4:
    # html.escape ALL interpolated string fields even though they're
    # constrained (hex / ISO / actor-id) — defense-in-depth against a future
    # schema-loosening regression introducing a Telegram-side HTML injection.
    fingerprint_safe = html.escape(key_status.fingerprint)
    rotated_at_safe = html.escape(_iso_z(key_status.rotated_at))
    rotated_by_safe = html.escape(key_status.rotated_by_actor_id)

    reply_text = (
        "Operator HMAC signing key:\n"
        f"  Fingerprint:    {fingerprint_safe}\n"
        f"  Last rotated:   {rotated_at_safe}\n"
        f"  Rotated by:     {rotated_by_safe}\n"
        "  Signing active: yes"
    )

    await safe_reply(message, reply_text)


def _iso_z(dt: object) -> str:
    """Render a datetime as ISO-Z with second precision.

    ``dt`` is a ``datetime`` from pydantic deserialization; we accept ``object``
    to keep the helper resilient to ``model_validate`` returning the raw string
    on some pydantic codepaths (the type hint on
    :class:`KeyStatusResponseLocal.rotated_at` IS ``datetime``, so the str
    fallback is dead in the happy path — kept as defensive cast for
    serialization edge cases).

    Story 11.5.1 /code-review-fix M1: a NAIVE datetime (``tzinfo=None``) from
    a JSON payload without explicit offset would round-trip via
    ``isoformat()`` without ``+00:00`` — the ``replace("+00:00", "Z")`` would
    no-op and the operator would see ``"2026-05-21T14:30:00"`` (no Z), wrongly
    implying local-time semantics. Normalize naive timestamps to UTC explicitly
    before formatting so the trailing ``Z`` is always present.
    """
    if isinstance(dt, datetime):
        normalized = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        # strip subsecond + ensure trailing Z for operator readability
        return normalized.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(dt)


def make_key_status_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Mirrors :func:`make_ping_router` exactly. Avoids the "Router already
    attached" RuntimeError across test lifespans.
    """
    router = Router()
    router.message(Command("key-status", "key_status"))(handle_key_status)
    return router


__all__ = ["handle_key_status", "make_key_status_router"]
