"""/reject command handler for telegram-gateway (Story 3.17 / FR7).

The operator sends ``/reject <task-id> [reason]`` from Telegram; this handler:

1. Validates the task-id against TASK_ID_PATTERN (UUIDv7 with "t-" prefix).
2. Extracts the optional reason from the remaining message text.
3. Derives a deterministic idempotency key from ``(chat_id, message_id)``
   via ``_keys.idempotency_key_from_message`` (FR28).
4. POSTs ``{"action": "reject", "hint": "<reason>"}`` to registry-api via
   :meth:`RegistryAPIClient.submit_decision`. The reason is passed as the
   ``hint`` parameter (FR7: "optional free-text hint injected into the
   orchestrator's next planning pass"). When no reason is provided,
   ``hint=None`` and the POST body omits the ``hint`` key.
5. Replies with ``"🚫 Rejected by @<handle> at <ts>. Task stopped."`` on
   success, or a human-readable error on failure.

No audit-event emission
-----------------------
This handler does NOT emit ``approval.rejected`` or any ``task.*`` event.
Registry-api's eventual ``POST /v1/tasks/{id}/decisions`` handler (Story 6.4)
emits ``approval.rejected`` server-side; Story 6.5 owns the full audit envelope.
Emitting a second envelope from the bot would violate the single-writer rule
(FR26) and create a duplicate audit signal.

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises). Telegram receives a 200 ACK from
the webhook endpoint regardless of what happens inside.

HTML parse mode (Story 3.1 M5)
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

_log = logging.getLogger("telegram_gateway.handlers.reject_command")


async def handle_reject(
    message: Message,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /reject <task-id> [reason] command.

    Extracts and validates the task-id, extracts the optional reason,
    derives an idempotency key, calls registry-api submit_decision with
    action="reject" and hint=reason, and replies with the result.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py (shared with /task handler — Story 3.3 AC-5).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    if message.from_user:
        operator_actor_id = str(message.from_user.id)
        if message.from_user.username:
            operator_handle = html.escape(message.from_user.username)
        elif message.from_user.first_name:
            operator_handle = html.escape(message.from_user.first_name)
        else:
            operator_handle = "operator"
    else:
        _log.warning(
            "reject from_user is None (message_id=%s, chat_id=%s)",
            getattr(message, "message_id", "?"),
            getattr(message.chat, "id", "?") if message.chat else "?",
        )
        operator_actor_id = "unknown"
        operator_handle = "operator"

    raw_text = message.text or ""
    parts = raw_text.split(None, 2)

    # /reject accepts an optional reason after the task-id, so we split into
    # max 3 parts: ["/reject", "<task-id>", "<reason>"]. We validate the
    # second token directly against TASK_ID_PATTERN instead of using
    # extract_task_id_from_message (which splits with maxsplit=1, causing
    # the reason text to be appended to the candidate and fail the regex).
    task_id = parts[1] if len(parts) >= 2 and _keys.TASK_ID_PATTERN.match(parts[1]) else None
    if task_id is None:
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /reject <task-id> [reason]")
        else:
            await _safe_reply(
                message,
                "Usage: /reject <task-id> [reason]; "
                "example: /reject t-0192a1b5-1234-7abc-89de-f0123456789a push before review",
            )
        return

    reason = parts[2].strip() if len(parts) >= 3 else None

    idempotency_key = _keys.idempotency_key_from_message(message)
    request_id = new_request_id()

    try:
        response = await registry_client.submit_decision(
            task_id=task_id,
            action="reject",
            idempotency_key=idempotency_key,
            operator_actor_id=operator_actor_id,
            request_id=request_id,
            hint=reason,
        )
    except httpx.TooManyRedirects:
        await _safe_reply(message, "⚠️ Registry misconfigured: too many redirects.")
        return
    except httpx.HTTPStatusError as exc:
        reply = format_http_error(exc, command_label="Reject command")
        _log.warning(
            "registry-api HTTP error for /reject (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError as exc:
        _log.exception(
            "registry-api malformed response for /reject (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error for /reject (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception as exc:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/reject handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    decided_at_iso = html.escape(response.decided_at.isoformat())

    if response.idempotency_status == "replayed":
        reply_text = (
            f"🚫 Rejected by @{operator_handle} at {decided_at_iso} (retry deduped). Task stopped."
        )
    else:
        reply_text = f"🚫 Rejected by @{operator_handle} at {decided_at_iso}. Task stopped."

    await _safe_reply(message, reply_text)


def make_reject_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3), make_approve_router()
    (Story 3.4), make_ping_router() (Story 3.5), make_status_router() (3.14),
    make_logs_router() (3.15), make_stop_router() (3.16).
    """
    router = Router()
    router.message(Command("reject"))(handle_reject)
    return router


__all__ = ["handle_reject", "make_reject_router"]
