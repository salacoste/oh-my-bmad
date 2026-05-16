"""/stop command handler for telegram-gateway (Story 3.16 / FR7).

The operator sends ``/stop <task-id>`` from Telegram; this handler:

1. Validates the task-id against TASK_ID_PATTERN (UUIDv7 with "t-" prefix).
2. Derives a deterministic idempotency key from ``(chat_id, message_id)``
   via ``_keys.idempotency_key_from_message`` (FR28).
3. POSTs ``{"action": "stop"}`` to registry-api via
   :meth:`RegistryAPIClient.submit_decision`.
4. Replies with ``"🛑 Stopped by @<handle> at <ts>. Task halted."`` on success,
   or a human-readable error on failure.

No audit-event emission
-----------------------
This handler does NOT emit ``task.stop_requested`` or any ``task.*`` event.
Registry-api's eventual ``POST /v1/tasks/{id}/decisions`` handler (Story 6.4)
emits ``task.stop_requested`` server-side; Story 6.5 owns the full audit envelope.
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
from telegram_gateway.handlers._errors import format_http_error, log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.stop_command")


async def handle_stop(
    message: Message,
    registry_client: RegistryAPIClient,
    trace_id: str | None = None,
) -> None:
    """Handle the /stop <task-id> command.

    Extracts and validates the task-id, derives an idempotency key,
    calls registry-api submit_decision with action="stop", and replies
    with the result.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py (shared with /task handler — Story 3.3 AC-5).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    # Story 9.3 pass-1 review H5: surface silent correlation loss.
    # Story 9.3 pass-2 review Q2: downgraded from WARNING to DEBUG via helper.
    if trace_id is None:
        log_missing_trace_id(_log, "/stop")
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
            "stop from_user is None (message_id=%s, chat_id=%s)",
            getattr(message, "message_id", "?"),
            getattr(message.chat, "id", "?") if message.chat else "?",
        )
        operator_actor_id = "unknown"
        operator_handle = "operator"

    raw_text = message.text or ""
    parts = raw_text.split(None, 1)

    task_id = _keys.extract_task_id_from_message(message)
    if task_id is None:
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /stop <task-id>")
        else:
            await _safe_reply(
                message,
                "Usage: /stop <task-id>; example: /stop t-0192a1b5-1234-7abc-89de-f0123456789a",
            )
        return

    idempotency_key = _keys.idempotency_key_from_message(message)
    request_id = new_request_id()

    try:
        response = await registry_client.submit_decision(
            task_id=task_id,
            action="stop",
            idempotency_key=idempotency_key,
            operator_actor_id=operator_actor_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except httpx.TooManyRedirects:
        await _safe_reply(message, "⚠️ Registry misconfigured: too many redirects.")
        return
    except httpx.HTTPStatusError as exc:
        reply = format_http_error(exc, command_label="Stop command")
        _log.warning(
            "registry-api HTTP error for /stop (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError as exc:
        _log.exception(
            "registry-api malformed response for /stop (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error for /stop (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception as exc:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/stop handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    decided_at_iso = html.escape(response.decided_at.isoformat())

    if response.idempotency_status == "replayed":
        reply_text = (
            f"🛑 Stopped by @{operator_handle} at {decided_at_iso} (retry deduped). Task halted."
        )
    else:
        reply_text = f"🛑 Stopped by @{operator_handle} at {decided_at_iso}. Task halted."

    await _safe_reply(message, reply_text)


def make_stop_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3), make_approve_router()
    (Story 3.4), make_ping_router() (Story 3.5), make_status_router() (3.14),
    make_logs_router() (3.15).
    """
    router = Router()
    router.message(Command("stop"))(handle_stop)
    return router


__all__ = ["handle_stop", "make_stop_router"]
