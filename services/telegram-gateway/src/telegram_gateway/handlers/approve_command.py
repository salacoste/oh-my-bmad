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
from telegram_gateway.handlers.registry_client import RegistryAPIClient
from telegram_gateway.handlers.task_command import _format_http_error

_log = logging.getLogger("telegram_gateway.handlers.approve_command")


def _extract_task_id(message: Message) -> str | None:
    """Parse "/approve <task-id>" and validate UUIDv7 shape.

    Returns the task-id string if valid, None otherwise.
    Rejects uppercase hex, t-less IDs, and non-UUIDv7 version nibbles.
    Stories 3.16/3.17/3.18 copy this function verbatim, importing
    TASK_ID_PATTERN from _keys.py.
    """
    parts = (message.text or "").split(None, 2)
    if len(parts) < 2:
        return None
    candidate = parts[1]
    return candidate if _keys.TASK_ID_PATTERN.match(candidate) else None


def make_approve_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as Story 3.3's make_task_router() factory pattern.
    """
    router = Router()

    @router.message(Command("approve"))
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
        raw_text = message.text or ""
        parts = raw_text.split(None, 1)

        # Determine the usage reply based on whether any arg was provided.
        task_id = _extract_task_id(message)
        if task_id is None:
            # Distinguish "no arg" from "invalid arg" for UX clarity.
            if len(parts) < 2:
                await message.reply("Usage: /approve <task-id>")
            else:
                await message.reply(
                    "Usage: /approve <task-id>; "
                    "example: /approve t-0192a1b5-1234-7abc-89de-f0123456789a"
                )
            return

        idempotency_key = _keys.idempotency_key_from_message(message)
        request_id = new_request_id()
        operator_actor_id = str(message.from_user.id) if message.from_user else "unknown"

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
            await message.reply("⚠️ Registry misconfigured: too many redirects.")
            return
        except httpx.HTTPStatusError as exc:
            reply = _format_http_error(exc)
            _log.warning(
                "registry-api HTTP error for /approve (status=%s request_id=%s): %s",
                exc.response.status_code,
                request_id,
                exc,
            )
            await message.reply(reply)
            return
        except httpx.HTTPError as exc:
            _log.warning(
                "registry-api network error for /approve (type=%s request_id=%s): %s",
                type(exc).__name__,
                request_id,
                exc,
            )
            await message.reply(f"⚠️ Could not reach registry: {type(exc).__name__}.")
            return
        except Exception as exc:  # noqa: BLE001 — H2 backstop: never propagate to webhook
            _log.exception(
                "/approve handler unexpected error (request_id=%s): %s",
                request_id,
                exc,
            )
            await message.reply("⚠️ Internal error. Logs captured.")
            return

        # Determine operator display handle (H5: html.escape all interpolated values).
        if message.from_user and message.from_user.username:
            operator_handle = html.escape(message.from_user.username)
        elif message.from_user and message.from_user.first_name:
            operator_handle = html.escape(message.from_user.first_name)
        else:
            operator_handle = "operator"

        decided_at_iso = html.escape(response.decided_at.isoformat())

        # AC-6: success reply with optional replay suffix.
        if response.idempotency_status == "replayed":
            reply_text = (
                f"✅ Approved by @{operator_handle} at {decided_at_iso} (retry deduped). Pushing."
            )
        else:
            reply_text = f"✅ Approved by @{operator_handle} at {decided_at_iso}. Pushing."

        await message.reply(reply_text)

    return router


__all__ = ["make_approve_router"]
