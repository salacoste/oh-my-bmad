"""/approvals command handler for telegram-gateway (Story 11.3 / FR63).

The operator sends ``/approvals`` from Telegram; this handler:

1. Checks via registry-api's GET ``/v1/approvals/inbox/{chat_id}`` whether
   the operator already has a pinned Forum-Topic inbox open in this chat.
2. If yes: reply with the existing thread link (no new topic created).
3. If no:
   a. Call aiogram's ``bot.create_forum_topic(...)`` to create a new
      Forum-Topic in the operator's chat.
   b. POST ``/v1/approvals/inbox`` to emit ``approval.inbox_opened`` via
      registry-api → JSONL → registry-state materializer. This is the
      ONLY state-mutation surface (FR26 single-writer rule — never
      writes SQLite directly).
   c. Reply with the new thread link.

aiogram permission edge-case (Story 11.3 out-of-scope risk):
  The bot needs ``can_manage_topics`` permission in the chat. Telegram
  raises ``TelegramBadRequest`` with reason text including ``"not enough
  rights to manage chat topics"`` / ``"can_manage_topics"``. We catch
  this and surface an actionable error message instead of crashing.

No audit-event emission from this handler (Story 11.1 P1-H5 lesson
parallel):
  The ``approval.inbox_opened`` event is emitted server-side by
  registry-api on a successful POST. Emitting a second envelope from
  the bot would duplicate the audit signal.

Allowlist + tier discipline (Story 11.3 AC6):
  AllowlistMiddleware (Story 3.2) silent-drops non-allowlisted callers
  BEFORE reaching this handler. Tier-2 categorization (Epic 6) is
  documented in the spec; explicit ROUTE_TIER_MAP entry deferred to
  Story 11.2.1 (DD5 follow-up — ``capability.denied`` emission).

HTML parse mode (Story 3.1 M5): replies use HTML markup; aiogram's
global ``DefaultBotProperties(parse_mode=ParseMode.HTML)`` from
lifespan.py applies to all ``message.reply(...)`` / ``safe_reply``
calls without explicit kwarg.
"""

from __future__ import annotations

import html

import httpx
import structlog
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_idempotency_key, new_request_id

from telegram_gateway.handlers._errors import format_http_error, log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = structlog.get_logger("telegram_gateway.handlers.approvals_command")

# Story 11.3 out-of-scope risk: substrings to detect "missing permission"
# inside ``TelegramBadRequest`` messages. Matching is case-insensitive and
# substring-based because Telegram occasionally reword these messages.
_MISSING_TOPIC_PERMISSION_HINTS = (
    "can_manage_topics",
    "not enough rights to manage chat topics",
    "not enough rights to create a topic",
)


def _is_missing_topic_permission_error(exc: TelegramBadRequest) -> bool:
    """Return True if *exc* indicates the bot lacks ``can_manage_topics``."""
    msg = str(exc).lower()
    return any(hint in msg for hint in _MISSING_TOPIC_PERMISSION_HINTS)


async def handle_approvals(
    message: Message,
    registry_client: RegistryAPIClient,
    trace_id: str | None = None,
) -> None:
    """Handle the ``/approvals`` command.

    Always returns normally — exceptions are surfaced as Telegram replies
    so the webhook never retries (Story 3.1 M3 contract).
    """
    if trace_id is None:
        log_missing_trace_id(_log, "/approvals")

    # Derive operator identity from message.from_user (Story 3.4 pattern).
    if message.from_user:
        operator_actor_id = str(message.from_user.id)
    else:
        _log.warning(
            "approvals from_user is None",
            message_id=getattr(message, "message_id", None),
            chat_id=getattr(message.chat, "id", None) if message.chat else None,
        )
        operator_actor_id = "unknown"

    if message.chat is None:
        await _safe_reply(message, "⚠️ Cannot determine chat — /approvals requires a chat context.")
        return

    chat_id = message.chat.id

    # Step 1: check existing inbox state via registry-api.
    request_id = new_request_id()
    try:
        existing = await registry_client.get_pinned_inbox(
            operator_chat_id=chat_id, request_id=request_id
        )
    except httpx.HTTPStatusError as exc:
        reply = format_http_error(exc)
        _log.warning(
            "registry-api HTTP error on /approvals get_pinned_inbox",
            status_code=exc.response.status_code,
            request_id=request_id,
        )
        await _safe_reply(message, reply)
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error on /approvals get_pinned_inbox",
            exc_type=type(exc).__name__,
            request_id=request_id,
        )
        await _safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception("/approvals unexpected error on inbox lookup", request_id=request_id)
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    if existing is not None:
        # Inbox already open — reply with existing link.
        await _safe_reply(
            message,
            (
                f"ℹ️ Approval inbox already open in this chat "
                f"(thread {existing.inbox_thread_id}). Opened by "
                f"@{html.escape(existing.opened_by_actor_id)}."
            ),
        )
        return

    # Step 2: create the Forum-Topic via aiogram.
    if message.bot is None:
        _log.error("message.bot is None — cannot create forum topic")
        await _safe_reply(message, "⚠️ Internal error: bot unavailable. Logs captured.")
        return

    try:
        forum_topic = await message.bot.create_forum_topic(
            chat_id=chat_id,
            name="Approvals Inbox",
        )
    except TelegramBadRequest as exc:
        if _is_missing_topic_permission_error(exc):
            _log.warning(
                "/approvals bot lacks can_manage_topics",
                chat_id=chat_id,
                operator_actor_id=operator_actor_id,
            )
            await _safe_reply(
                message,
                (
                    "⚠️ Bot lacks <code>can_manage_topics</code> permission in this chat. "
                    "Grant it via Telegram chat settings → Administrators → bot → "
                    "Enable 'Manage Topics', then retry /approvals."
                ),
            )
            return
        _log.warning(
            "/approvals create_forum_topic failed",
            chat_id=chat_id,
            exc_type=type(exc).__name__,
            exc=str(exc),
        )
        await _safe_reply(message, "⚠️ Could not create Forum-Topic. Logs captured.")
        return
    except Exception as exc:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception(
            "/approvals create_forum_topic unexpected error",
            chat_id=chat_id,
            exc_type=type(exc).__name__,
        )
        await _safe_reply(message, "⚠️ Internal error creating Forum-Topic. Logs captured.")
        return

    new_thread_id = forum_topic.message_thread_id

    # Step 3: POST /v1/approvals/inbox to emit approval.inbox_opened.
    idempotency_key = new_idempotency_key()
    try:
        response = await registry_client.open_inbox(
            operator_chat_id=chat_id,
            inbox_thread_id=new_thread_id,
            idempotency_key=idempotency_key,
            operator_actor_id=operator_actor_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except httpx.HTTPStatusError as exc:
        _log.warning(
            "/approvals registry-api error on open_inbox",
            status_code=exc.response.status_code,
            request_id=request_id,
        )
        # Forum-Topic was created but event emission failed; the operator
        # has a stray topic in their chat. Surface a structured error
        # rather than pretending it succeeded — the next /approvals call
        # will see no inbox state and try again (idempotently from the
        # event-emission side via Idempotency-Key).
        await _safe_reply(message, format_http_error(exc))
        return
    except RegistryResponseError:
        _log.exception("/approvals malformed registry-api response", request_id=request_id)
        await _safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "/approvals registry-api network error on open_inbox",
            exc_type=type(exc).__name__,
            request_id=request_id,
        )
        await _safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception("/approvals unexpected error on open_inbox", request_id=request_id)
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    # Step 4: reply with the new thread link.
    _log.info(
        "/approvals inbox opened",
        chat_id=chat_id,
        inbox_thread_id=response.inbox_thread_id,
        event_id=response.event_id,
        operator_actor_id=operator_actor_id,
    )
    await _safe_reply(
        message,
        (
            f"✅ Approval inbox opened (thread {response.inbox_thread_id}). "
            "Future approval requests will land here."
        ),
    )


def make_approvals_router() -> Router:
    """Factory — fresh Router per dispatcher instance (Story 3.4 pattern)."""
    router = Router()
    router.message(Command("approvals"))(handle_approvals)
    return router


__all__ = ["handle_approvals", "make_approvals_router"]
