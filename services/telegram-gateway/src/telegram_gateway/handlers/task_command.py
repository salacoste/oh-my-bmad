"""/task command handler for telegram-gateway (Story 3.3 / FR1 / FR28 / NFR-P2).

Bootstrap Minimum #1 — first concrete operator user-journey end-to-end.

The operator sends ``/task <description>`` from Telegram; this handler:

1. Derives a deterministic idempotency key from ``(chat_id, message_id)``
   so Telegram retries of the same physical message map to the same
   registry-api call (FR28 / AC-10).
2. POSTs ``{"title": description}`` to registry-api via
   :class:`~telegram_gateway.handlers.registry_client.RegistryAPIClient`.
3. Replies with ``"Task <code>{task_id}</code> created. Planning. Events
   on thread."`` on success, or a human-readable error on failure.

Important: This handler does NOT emit a ``task.created`` audit event.
Registry-api emits it internally when ``POST /v1/tasks`` succeeds
(Story 2.9 / FR26 / AC-11). Emitting a second envelope from the bot
would violate the single-writer rule (FR26) and create a duplicate
audit signal. Any temptation to add bot-side emission here should be
redirected to Story 2.9.

Idempotency ownership (AC-10)
-----------------------------
The bot does NOT maintain its own memory of "already replied for this
message_id". Both first-delivery and Telegram-retry submissions are
forwarded to registry-api. The first delivery returns
``X-Idempotency-Status: applied``; the retry returns
``X-Idempotency-Status: replayed``. The bot appends ``" (retry deduped)"``
on replay. No duplicate task is ever created — that invariant is owned
by registry-api (FR28 / NFR-R4).

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises). Telegram receives a 200 ACK from
the webhook endpoint regardless of what happens inside.
"""

from __future__ import annotations

import logging

import httpx
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers.registry_client import RegistryAPIClient

_log = logging.getLogger("telegram_gateway.handlers.task_command")


def _idempotency_key_from_message(message: Message) -> str:
    """Derive a deterministic idempotency key from Telegram (chat_id, message_id).

    Format: "telegram-{chat_id}-{message_id}".
    Telegram retries deliver the same message_id for the same physical message,
    so registry-api (FR28) will deduplicate duplicate deliveries and return the
    same task_id. The key is opaque to registry-api but deterministic for the bot.
    Future commands (/approve 3.4, /retry 3.18) follow the same pattern.
    """
    return f"telegram-{message.chat.id}-{message.message_id}"


def _format_http_error(exc: httpx.HTTPStatusError) -> str:
    """Surface RFC 7807 error details as a human-readable Telegram reply.

    Differentiates:
    - 409: idempotency collision from a concurrent bot instance.
    - 4xx other: validation / Pydantic error; parse RFC 7807 ``detail``.
    - 5xx: registry unavailable.

    Falls back to ``"⚠️ Task rejected: HTTP {status}"`` when the body is
    not valid JSON or lacks ``detail``.
    """
    status = exc.response.status_code
    if status == 409:
        # Concurrent bot instance submitted the same idempotency key via
        # a different path (unusual but possible in multi-replica deploys).
        try:
            body = exc.response.json()
            task_id_from_body = body.get("task_id", "")
        except Exception:  # noqa: BLE001 — best-effort body parse
            task_id_from_body = ""
        if task_id_from_body:
            return (
                f"⚠️ Duplicate idempotency key — another instance already submitted "
                f"this message. Stored result: {task_id_from_body}."
            )
        return "⚠️ Duplicate idempotency key — another instance already submitted this message."
    if 400 <= status < 500:
        # Parse RFC 7807 / FastAPI validation body for the ``detail`` field.
        try:
            body = exc.response.json()
            detail = body.get("detail")
        except Exception:  # noqa: BLE001 — body may not be JSON (e.g., proxy 413)
            detail = None
        if detail:
            return f"⚠️ Task rejected: {detail}"
        return f"⚠️ Task rejected: HTTP {status}"
    # 5xx — transient registry error.
    return f"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."


def make_task_router() -> Router:
    """Create a fresh Router with the /task handler registered.

    Called once per lifespan (i.e. once per Dispatcher). A new Router
    instance is required each time because aiogram marks a Router as
    "attached" after the first ``dp.include_router`` call and rejects
    subsequent includes with ``RuntimeError('Router is already attached')``.
    """
    r = Router()
    r.message.register(handle_task, Command("task"))
    return r


async def handle_task(
    message: Message,
    bot: Bot,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /task <description> command.

    Extracts the description from the message text, validates it is non-empty,
    derives an idempotency key, calls registry-api, and replies with the result.

    ``bot`` is injected automatically by aiogram v3.
    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py.

    This handler ALWAYS returns normally — exceptions are surfaced as a Telegram
    reply so Telegram never retries the webhook delivery (Story 3.1 M3 contract).
    """
    # Strip the command prefix; handle both "/task" and "/task@botname" forms.
    raw_text = message.text or ""
    # Remove command prefix (/task or /task@botname)
    description = raw_text.split(" ", 1)[1].strip() if " " in raw_text else ""

    if not description:
        await message.reply("Usage: /task <description>")
        return

    idempotency_key = _idempotency_key_from_message(message)
    request_id = new_request_id()

    try:
        response = await registry_client.create_task(
            description=description,
            idempotency_key=idempotency_key,
            operator_actor_id=str(message.from_user.id) if message.from_user else "unknown",
            request_id=request_id,
        )
    except httpx.HTTPStatusError as exc:
        reply = _format_http_error(exc)
        _log.warning(
            "registry-api HTTP error for /task (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await message.reply(reply)
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error for /task (type=%s request_id=%s): %s",
            type(exc).__name__,
            request_id,
            exc,
        )
        await message.reply(f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return

    status_suffix = " (retry deduped)" if response.idempotency_status == "replayed" else ""
    await message.reply(
        f"Task <code>{response.task_id}</code> created. Planning. Events on thread.{status_suffix}"
    )


__all__ = ["handle_task", "make_task_router", "_idempotency_key_from_message", "_format_http_error"]
