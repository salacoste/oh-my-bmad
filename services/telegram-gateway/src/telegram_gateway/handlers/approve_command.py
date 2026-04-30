"""/approve command handler for telegram-gateway (Story 3.4 / FR7 / NFR-P2).

Bootstrap Minimum #2 — operator approval flow from Telegram.

The operator sends ``/approve <task-id>`` from Telegram; this handler:

1. Validates the task-id against TASK_ID_PATTERN (UUIDv7 with "t-" prefix).
2. Derives a deterministic idempotency key from ``(chat_id, message_id)``
   via ``_keys.idempotency_key_from_message`` (FR28).
3. POSTs ``{"action": "approve"}`` to registry-api via
   :meth:`RegistryAPIClient.submit_decision`.
4. Replies with ``"✅ Approved by @<handle> at <ts>. Pushing."`` on success,
   or a human-readable error on failure.

No audit-event emission
-----------------------
This handler does NOT emit ``approval.granted`` or any ``task.*`` event.
Registry-api's eventual ``POST /v1/tasks/{id}/decisions`` handler (Story 6.4)
emits ``approval.granted`` server-side; Story 6.5 owns the full audit envelope.
Emitting a second envelope from the bot would violate the single-writer rule
(FR26) and create a duplicate audit signal.

State naming discrepancy
------------------------
The registry-api ``_NEXT_COMMANDS`` map (tasks.py line ~87) uses ``plan_ready``
as the state where ``approve`` is valid. The epic spec and PRD use
``awaiting_approval``. This discrepancy is pre-existing and owned by Story 6.4
to resolve. Tests use ``plan_ready``-shaped mock responses.

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises). Telegram receives a 200 ACK from
the webhook endpoint regardless of what happens inside.

HTML parse mode (AC-6 / Story 3.1 M5)
--------------------------------------
Reply messages use HTML markup. ``DefaultBotProperties(parse_mode=ParseMode.HTML)``
is set globally in lifespan.py so all ``message.reply(...)`` calls inherit HTML
mode without explicit kwarg.
"""

from __future__ import annotations

import html
import logging

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers import _keys
from telegram_gateway.handlers._errors import format_http_error
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.approve_command")

# L2: _extract_task_id promoted to _keys.extract_task_id_from_message.
# Private alias kept for any callers that imported the old name directly.
_extract_task_id = _keys.extract_task_id_from_message


async def handle_approve(
    message: Message,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /approve <task-id> command.

    Extracts and validates the task-id, derives an idempotency key,
    calls registry-api submit_decision, and replies with the result.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py (shared with /task handler — Story 3.3 AC-5).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    # L8: derive both actor fields in a single from_user guard block at the top.
    if message.from_user:
        operator_actor_id = str(message.from_user.id)
        if message.from_user.username:
            operator_handle = html.escape(message.from_user.username)
        elif message.from_user.first_name:
            operator_handle = html.escape(message.from_user.first_name)
        else:
            operator_handle = "operator"
    else:
        # M2: log a warning when from_user is None so the silent "unknown" actor
        # is visible in monitoring (e.g., a system/bot message routed here).
        _log.warning(
            "approve from_user is None (message_id=%s, chat_id=%s)",
            getattr(message, "message_id", "?"),
            getattr(message.chat, "id", "?") if message.chat else "?",
        )
        operator_actor_id = "unknown"
        operator_handle = "@operator"

    raw_text = message.text or ""
    parts = raw_text.split(None, 1)

    # L2: use extract_task_id_from_message from _keys (M1: uses split(None, 1)
    # so trailing garbage correctly causes regex failure).
    task_id = _keys.extract_task_id_from_message(message)
    if task_id is None:
        # Distinguish "no arg" from "invalid arg" for UX clarity (AC-4).
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /approve <task-id>")
        else:
            await _safe_reply(
                message,
                "Usage: /approve <task-id>; "
                "example: /approve t-0192a1b5-1234-7abc-89de-f0123456789a",
            )
        return

    idempotency_key = _keys.idempotency_key_from_message(message)
    request_id = new_request_id()

    try:
        response = await registry_client.submit_decision(
            task_id=task_id,
            action="approve",
            idempotency_key=idempotency_key,
            operator_actor_id=operator_actor_id,
            request_id=request_id,
        )
    except httpx.TooManyRedirects:
        # M3: TooManyRedirects is an httpx.HTTPError subclass but indicates
        # misconfiguration, not a transient network issue.
        await _safe_reply(message, "⚠️ Registry misconfigured: too many redirects.")
        return
    except httpx.HTTPStatusError as exc:
        reply = format_http_error(exc)
        _log.warning(
            "registry-api HTTP error for /approve (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError as exc:
        # H1: malformed-200 body — distinct from network errors.
        _log.exception(
            "registry-api malformed response for /approve (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error for /approve (type=%s request_id=%s): %s",
            type(exc).__name__,
            request_id,
            exc,
        )
        await _safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception as exc:  # noqa: BLE001 — H2 backstop: never propagate to webhook
        _log.exception(
            "/approve handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    decided_at_iso = html.escape(response.decided_at.isoformat())

    # AC-6: success reply with optional replay suffix.
    # L10: "(retry deduped)" is placed before the period, after the timestamp
    # clause — current form: "…at {ts} (retry deduped). Pushing."
    # The spec's "before the period" wording is satisfied by this placement.
    # The test asserts substring "(retry deduped)" only, not exact position.
    if response.idempotency_status == "replayed":
        reply_text = (
            f"✅ Approved by @{operator_handle} at {decided_at_iso} (retry deduped). Pushing."
        )
    else:
        reply_text = f"✅ Approved by @{operator_handle} at {decided_at_iso}. Pushing."

    await _safe_reply(message, reply_text)


def make_approve_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as Story 3.3's make_task_router() factory pattern.

    M6: handle_approve is a standalone module-level coroutine so tests can
    import it directly without brittle router-introspection.
    """
    router = Router()
    router.message(Command("approve"))(handle_approve)
    return router


__all__ = ["handle_approve", "make_approve_router"]
