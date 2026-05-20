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

import hashlib
import html

import httpx
import structlog
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers._errors import format_http_error, log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply
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
    """Return True if *exc* indicates the bot lacks ``can_manage_topics``.

    Story 11.3 review P29: structured check first — Telegram raises
    ``TelegramBadRequest`` from the ``create_forum_topic`` Bot-API method
    with a message that mentions ``can_manage_topics``. The substring
    fallback covers localized variants ("not enough rights to manage chat
    topics" / "not enough rights to create a topic") which Telegram
    occasionally rewords.
    """
    method_name = getattr(getattr(exc, "method", None), "__class__", type(None)).__name__
    exc_message = getattr(exc, "message", None) or str(exc)
    if method_name == "CreateForumTopic" and "can_manage_topics" in exc_message:
        return True
    msg = exc_message.lower()
    return any(hint in msg for hint in _MISSING_TOPIC_PERMISSION_HINTS)


def _deterministic_inbox_idempotency_key(chat_id: int, operator_actor_id: str) -> str:
    """Story 11.3 review P17: stable key collapses concurrent /approvals into one POST.

    Two ``/approvals`` calls from the same operator chat MUST collide on
    the registry-api idempotency cache so only one ``approval.inbox_opened``
    event is emitted. Using ``new_idempotency_key()`` (a random UUID) lost
    that property and allowed both invocations to create stray Forum-Topics.

    The key is a SHA-256 hash truncated to 32 hex chars so it is opaque
    and bounded; ``chat_id`` + ``operator_actor_id`` are the natural
    collision keys (one operator per chat).
    """
    seed = f"approvals-inbox|{chat_id}|{operator_actor_id}"
    return "ai-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


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

    # Story 11.3 review P2: ``message.from_user is None`` is treated as a
    # protocol violation (channel posts / anonymous group admins) — we
    # cannot identify the operator, so we reject rather than proceed
    # with a placeholder identity that would later let any allowlisted
    # caller hijack an inbox via UPSERT (P1).
    if message.from_user is None:
        _log.warning(
            "approval inbox rejected — message has no from_user",
            chat_id=getattr(message.chat, "id", None) if message.chat else None,
        )
        await safe_reply(
            message,
            "⚠ Unable to determine operator identity. Please send /approvals "
            "from a regular user account.",
        )
        return
    operator_actor_id = str(message.from_user.id)

    if message.chat is None:
        await safe_reply(message, "⚠️ Cannot determine chat — /approvals requires a chat context.")
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
        await safe_reply(message, reply)
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error on /approvals get_pinned_inbox",
            exc_type=type(exc).__name__,
            request_id=request_id,
        )
        await safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception("/approvals unexpected error on inbox lookup", request_id=request_id)
        await safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    if existing is not None:
        # Story 11.3 review P1: operator-identity check. Even though the
        # AllowlistMiddleware lets through every approved operator, two
        # operators sharing a chat must NOT hijack each other's inbox via
        # UPSERT. The original owner is recorded in ``opened_by_actor_id``;
        # only that operator can re-trigger ``/approvals`` for this chat.
        if existing.opened_by_actor_id != operator_actor_id:
            _log.warning(
                "/approvals rejected — inbox owned by another operator",
                chat_id=chat_id,
                requested_by=operator_actor_id,
                opened_by=existing.opened_by_actor_id,
            )
            await safe_reply(
                message,
                (
                    f"⚠ Approval inbox is already pinned by another operator "
                    f"({html.escape(existing.opened_by_actor_id)}). "
                    f"Contact them to coordinate."
                ),
            )
            return
        # Inbox already open — reply with existing link.
        # Story 11.3 review P18: drop the misleading ``@`` prefix —
        # ``opened_by_actor_id`` is a numeric Telegram user_id (or
        # ``http-api`` for the registry-api default), not a username.
        await safe_reply(
            message,
            (
                f"ℹ️ Approval inbox already open in this chat "
                f"(thread {existing.inbox_thread_id}). Opened by "
                f"{html.escape(existing.opened_by_actor_id)}."
            ),
        )
        return

    # Step 2: create the Forum-Topic via aiogram.
    if message.bot is None:
        _log.error("message.bot is None — cannot create forum topic")
        await safe_reply(message, "⚠️ Internal error: bot unavailable. Logs captured.")
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
            await safe_reply(
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
        await safe_reply(message, "⚠️ Could not create Forum-Topic. Logs captured.")
        return
    except Exception as exc:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception(
            "/approvals create_forum_topic unexpected error",
            chat_id=chat_id,
            exc_type=type(exc).__name__,
        )
        await safe_reply(message, "⚠️ Internal error creating Forum-Topic. Logs captured.")
        return

    new_thread_id = forum_topic.message_thread_id

    # Step 3: POST /v1/approvals/inbox to emit approval.inbox_opened.
    # Story 11.3 review P17: deterministic Idempotency-Key so concurrent
    # ``/approvals`` invocations collide on the registry-api cache and
    # only one event is emitted.
    idempotency_key = _deterministic_inbox_idempotency_key(chat_id, operator_actor_id)
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
        # Story 11.3 review P3: orphan Forum-Topic cleanup — the event
        # emission failed but the topic exists in the operator's chat.
        # Best-effort delete so the operator's chat is not littered with
        # stray topics across retries.
        await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        # Story 11.3 review P27: explicit failure-mode text so the operator
        # knows the topic was created but not linked, and that /approvals
        # is safe to retry (Idempotency-Key replay).
        await safe_reply(
            message,
            "⚠️ Inbox event emission failed — the Forum-Topic was created but "
            "is not yet linked. Retry /approvals.",
        )
        return
    except RegistryResponseError:
        _log.exception("/approvals malformed registry-api response", request_id=request_id)
        await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        await safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "/approvals registry-api network error on open_inbox",
            exc_type=type(exc).__name__,
            request_id=request_id,
        )
        await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        await safe_reply(message, f"⚠️ Could not reach registry: {type(exc).__name__}.")
        return
    except Exception:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception("/approvals unexpected error on open_inbox", request_id=request_id)
        await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        await safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    # Step 4: reply with the new thread link.
    _log.info(
        "/approvals inbox opened",
        chat_id=chat_id,
        inbox_thread_id=response.inbox_thread_id,
        event_id=response.event_id,
        operator_actor_id=operator_actor_id,
    )
    await safe_reply(
        message,
        (
            f"✅ Approval inbox opened (thread {response.inbox_thread_id}). "
            "Future approval requests will land here."
        ),
    )


async def _cleanup_orphan_topic(message: Message, chat_id: int, message_thread_id: int) -> None:
    """Best-effort delete of a Forum-Topic created when POST failed (P3).

    Telegram exposes ``delete_forum_topic`` which removes the topic that
    was created in the operator's chat right before the event emission
    failed. Failures here are logged but never propagated — the goal is
    to leave the operator's chat clean, not to mask the original error.
    """
    if message.bot is None:
        return
    try:
        await message.bot.delete_forum_topic(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )
    except Exception as cleanup_exc:  # noqa: BLE001 — best-effort
        _log.error(
            "orphan_topic_cleanup_failed",
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            exc_info=cleanup_exc,
        )


def make_approvals_router() -> Router:
    """Factory — fresh Router per dispatcher instance (Story 3.4 pattern)."""
    router = Router()
    router.message(Command("approvals"))(handle_approvals)
    return router


__all__ = ["handle_approvals", "make_approvals_router"]
