"""/task command handler for telegram-gateway (Story 3.3 / FR1 / FR28 / NFR-P2).

Bootstrap Minimum #1 — first concrete operator user-journey end-to-end.

The operator sends ``/task <description>`` from Telegram; this handler:

1. Derives a deterministic idempotency key from ``(chat_id, message_id)``
   so Telegram retries of the same physical message map to the same
   registry-api call (FR28 / AC-10).

   Idempotency key strategy (AC-7 / review-fix H1)
   ------------------------------------------------
   The key is a UUIDv5 derived from a fixed Telegram-service namespace UUID
   and the seed string ``"{chat_id}:{message_id}"``, then **reshaped** so the
   version nibble reads ``7`` and the variant nibble reads ``10xx`` (i.e., the
   standard RFC 4122 variant bits).  This satisfies registry-api's
   ``IdempotencyKeyMiddleware`` UUIDv7 regex::

       ^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$

   The reshape is deterministic: the same ``(chat_id, message_id)`` always
   produces the same key.  The UUIDv5 namespace UUID is::

       _TELEGRAM_NAMESPACE_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

   It encodes the ``"tg:"`` service discriminator so that a hypothetical
   Slack gateway using the same numeric ids would generate different keys.
   Negative ``chat_id`` values (Telegram supergroup groups start at -100…)
   are embedded in the seed string as-is; the UUID bytes hide the sign
   so there is no double-hyphen footgun in the final string representation.

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

HTML parse mode (AC-7 / Story 3.1 M5)
--------------------------------------
Reply messages use HTML markup (``<code>…</code>``).  ``DefaultBotProperties(
parse_mode=ParseMode.HTML)`` is set globally in lifespan.py so all
``message.reply(...)`` calls inherit HTML mode without explicit kwarg.
"""

from __future__ import annotations

import html
import logging

import httpx
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers import _keys
from telegram_gateway.handlers.registry_client import RegistryAPIClient

_log = logging.getLogger("telegram_gateway.handlers.task_command")

# Re-export for backward-compat with tests that import _UUIDV7_BARE_RE and
# _idempotency_key_from_message from task_command (AC-15 / Story 3.4 Task 1).
_UUIDV7_BARE_RE = _keys.UUIDV7_BARE_RE
_idempotency_key_from_message = _keys.idempotency_key_from_message


def _format_http_error(exc: httpx.HTTPStatusError) -> str:
    """Surface RFC 7807 error details as a human-readable Telegram reply.

    Differentiates:
    - 401/403: authorization error → fixed human-readable message (M2).
    - 409: idempotency collision from a concurrent bot instance.
    - 4xx other: validation / Pydantic error; parse RFC 7807 ``detail``.
      When ``detail`` is a list (FastAPI 422 shape), extracts the first
      ``"msg"`` entry.  All interpolated values are HTML-escaped (H5).
    - 5xx: registry unavailable.

    Falls back to ``"⚠️ Task rejected: HTTP {status}"`` when the body is
    not valid JSON or lacks ``detail``.
    """
    status = exc.response.status_code

    if status in (401, 403):
        return "⚠️ Not authorized. Contact your administrator."

    if status == 409:
        # Concurrent bot instance submitted the same idempotency key via
        # a different path (unusual but possible in multi-replica deploys).
        try:
            body = exc.response.json()
            task_id_raw = body.get("task_id", "")
        except Exception:  # noqa: BLE001 — best-effort body parse
            task_id_raw = ""
        if task_id_raw:
            task_id_safe = html.escape(str(task_id_raw))
            return (
                f"⚠️ Duplicate idempotency key — another instance already submitted "
                f"this message. Stored result: {task_id_safe}."
            )
        return "⚠️ Duplicate idempotency key — another instance already submitted this message."

    if 400 <= status < 500:
        # Parse RFC 7807 / FastAPI validation body for the ``detail`` field.
        try:
            body = exc.response.json()
            detail_raw = body.get("detail")
        except Exception:  # noqa: BLE001 — body may not be JSON (e.g., proxy 413)
            detail_raw = None

        if detail_raw is not None:
            # FastAPI 422 returns detail as a list of dicts: [{"loc": [...], "msg": "..."}].
            if isinstance(detail_raw, list):
                msgs = [d.get("msg", "") for d in detail_raw if isinstance(d, dict)]
                detail_str = "; ".join(m for m in msgs if m) or str(detail_raw)
            else:
                detail_str = str(detail_raw)
            return f"⚠️ Task rejected: {html.escape(detail_str)}"
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
    # Strip the command prefix; split on any whitespace (M10) handles both
    # "/task description" (space) and "/task\ndescription" (newline).
    # aiogram's Command("task") filter already strips "/task" and "@botname"
    # mentions before the handler is called, but we still split here to handle
    # the raw text fallback path in tests.
    raw_text = message.text or ""
    parts = raw_text.split(None, 1)  # split on any whitespace, max 1 split
    description = parts[1].strip() if len(parts) > 1 else ""

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
    except httpx.TooManyRedirects:
        # M3: TooManyRedirects is an httpx.HTTPError subclass but indicates
        # misconfiguration, not a transient network issue — give a distinct message.
        await message.reply("⚠️ Registry misconfigured: too many redirects.")
        return
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
    except Exception as exc:  # noqa: BLE001 — M3 backstop: never let exceptions kill the webhook
        _log.exception(
            "/task handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await message.reply("⚠️ Internal error. Logs captured.")
        return

    status_suffix = " (retry deduped)" if response.idempotency_status == "replayed" else ""
    await message.reply(
        f"Task <code>{response.task_id}</code> created. Planning. Events on thread.{status_suffix}"
    )


__all__ = ["handle_task", "make_task_router"]
