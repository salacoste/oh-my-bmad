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

Allowlist + tier discipline (Story 11.3 AC6, closed by Story 11.2.1):
  AllowlistMiddleware (Story 3.2) silent-drops non-allowlisted callers
  BEFORE reaching this handler — the Telegram-side gate is allowlist-only
  by design. Tier-2 enforcement is SERVER-SIDE via registry-api's
  ``ROUTE_TIER_MAP["POST /v1/approvals/inbox"] = Tier.TWO``
  (``services/registry-api/src/registry_api/adapters/middleware.py:432``).
  On a tier mismatch, ``TierEnforcementMiddleware`` returns RFC 7807 403
  AND emits a ``capability.denied`` v1.1.0 audit event (Story 11.2.1).

HTML parse mode (Story 3.1 M5): replies use HTML markup; aiogram's
global ``DefaultBotProperties(parse_mode=ParseMode.HTML)`` from
lifespan.py applies to all ``message.reply(...)`` / ``safe_reply``
calls without explicit kwarg.
"""

from __future__ import annotations

import hashlib
import html
from typing import Literal

import httpx
import structlog
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_idempotency_key, new_request_id

from telegram_gateway.handlers._errors import log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply
from telegram_gateway.handlers.registry_client import (
    OpenInboxResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)

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

    Story 11.3 PP4: ``/approvals reopen`` sub-command forces a new
    Forum-Topic + fresh ``approval.inbox_opened`` event (materializer
    UPSERT transitions state). Use when the operator manually deleted
    the pinned topic but the registry-state row is still present.
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

    # Story 11.3 PP4: parse ``/approvals reopen`` sub-command. If the
    # operator passes ``reopen`` as the first arg, skip the "already
    # open" reply and force a fresh Forum-Topic + UPSERT. Argument
    # parsing is intentionally tolerant of leading whitespace and
    # additional args (ignored).
    raw_text = (message.text or "").strip()
    is_reopen = False
    if raw_text.startswith("/approvals"):
        # Strip the command token (preserve original casing for the rest).
        tail = raw_text[len("/approvals") :].strip()
        if tail.split()[:1] == ["reopen"]:
            is_reopen = True

    # Step 1: check existing inbox state via registry-api.
    request_id = new_request_id()
    try:
        existing = await registry_client.get_pinned_inbox(
            operator_chat_id=chat_id, request_id=request_id
        )
    except httpx.HTTPStatusError as exc:
        # Story 11.3 PP7: mirror the POST-path sanitization — never echo
        # raw httpx body text into the operator's chat (could leak stack
        # traces or DSN fragments on a misconfigured 5xx).
        _log.warning(
            "registry-api HTTP error on /approvals get_pinned_inbox",
            status_code=exc.response.status_code,
            request_id=request_id,
        )
        await safe_reply(
            message,
            (
                f"⚠ Registry API returned {exc.response.status_code} on inbox "
                f"state check. Try again or contact ops."
            ),
        )
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

    if existing is not None and not is_reopen:
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

    # Story 11.3 PP4: when ``reopen`` is in effect and an existing inbox
    # row is owned by a different operator, refuse — same as the
    # default path. Owner-only re-open prevents hijack via reopen.
    if existing is not None and is_reopen and existing.opened_by_actor_id != operator_actor_id:
        _log.warning(
            "/approvals reopen rejected — inbox owned by another operator",
            chat_id=chat_id,
            requested_by=operator_actor_id,
            opened_by=existing.opened_by_actor_id,
        )
        await safe_reply(
            message,
            (
                f"⚠ Approval inbox is owned by another operator "
                f"({html.escape(existing.opened_by_actor_id)}); "
                f"cannot reopen."
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
    response, post_error = await _post_inbox_with_410_retry(
        registry_client=registry_client,
        chat_id=chat_id,
        new_thread_id=new_thread_id,
        idempotency_key=idempotency_key,
        operator_actor_id=operator_actor_id,
        request_id=request_id,
        trace_id=trace_id,
    )
    if post_error is not None:
        await _handle_post_error(message, post_error, chat_id, new_thread_id, request_id)
        return
    assert response is not None  # paired with post_error being None

    # Step 4: reply with the new thread link.
    _log.info(
        "/approvals inbox opened",
        chat_id=chat_id,
        inbox_thread_id=response.inbox_thread_id,
        event_id=response.event_id,
        operator_actor_id=operator_actor_id,
        reopen=is_reopen,
    )
    if is_reopen:
        await safe_reply(
            message,
            (
                f"✅ Approval inbox reopened (thread {response.inbox_thread_id}). "
                "Future approval requests will land here."
            ),
        )
    else:
        await safe_reply(
            message,
            (
                f"✅ Approval inbox opened (thread {response.inbox_thread_id}). "
                "Future approval requests will land here."
            ),
        )
    return


# ---------------------------------------------------------------------------
# POST helpers: PP1 (410 retry), PP5 (status-class branching), PP15 (cleanup verify)
# ---------------------------------------------------------------------------


class _PostError:
    """Tagged failure outcome from :func:`_post_inbox_with_410_retry`.

    The handler maps each ``kind`` to a distinct cleanup + reply policy
    per Story 11.3 PP5 (don't delete orphan topics on indeterminate
    states like 5xx / timeouts).
    """

    __slots__ = ("kind", "status_code", "exc_type_name")

    def __init__(
        self,
        kind: Literal[
            "client_error",  # 4xx (except 410-after-retry, which is "client_error")
            "server_error",  # 5xx
            "network_error",  # connect/timeout
            "malformed_response",  # RegistryResponseError
            "unexpected",  # backstop
        ],
        *,
        status_code: int | None = None,
        exc_type_name: str | None = None,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.exc_type_name = exc_type_name


async def _post_inbox_with_410_retry(
    *,
    registry_client: RegistryAPIClient,
    chat_id: int,
    new_thread_id: int,
    idempotency_key: str,
    operator_actor_id: str,
    request_id: str,
    trace_id: str | None,
) -> tuple[OpenInboxResponseLocal | None, _PostError | None]:
    """Issue POST /v1/approvals/inbox with Story 11.3 PP1 410-retry.

    Pass-1 P17 + P4 left an unrecoverable window: deterministic
    Idempotency-Key + post-restart cache eviction = the gateway could
    never get past 410 Gone. PP1 closes the window by falling back to
    :func:`new_idempotency_key` exactly once on 410, ensuring forward
    progress without leaking a corrupt slot.
    """

    async def _attempt(key: str) -> OpenInboxResponseLocal:
        return await registry_client.open_inbox(
            operator_chat_id=chat_id,
            inbox_thread_id=new_thread_id,
            idempotency_key=key,
            operator_actor_id=operator_actor_id,
            request_id=request_id,
            trace_id=trace_id,
        )

    try:
        response = await _attempt(idempotency_key)
        return response, None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 410:
            # PP1: 410 Gone — registry-api lost the in-memory slot after a
            # restart while the SQLite idempotency row remained. Retry
            # ONCE with a fresh random key so the operator is unblocked.
            _log.warning(
                "/approvals POST 410 — retrying with fresh idempotency key",
                chat_id=chat_id,
                request_id=request_id,
            )
            try:
                response = await _attempt(new_idempotency_key())
                return response, None
            except httpx.HTTPStatusError as retry_exc:
                status = retry_exc.response.status_code
                _log.warning(
                    "/approvals registry-api HTTP error on 410-retry",
                    status_code=status,
                    request_id=request_id,
                )
                if 400 <= status < 500:
                    return None, _PostError("client_error", status_code=status)
                return None, _PostError("server_error", status_code=status)
            except RegistryResponseError:
                _log.exception(
                    "/approvals malformed response on 410 retry",
                    request_id=request_id,
                )
                return None, _PostError("malformed_response")
            except httpx.HTTPError as retry_net_exc:
                return None, _PostError("network_error", exc_type_name=type(retry_net_exc).__name__)
        status = exc.response.status_code
        _log.warning(
            "/approvals registry-api HTTP error on open_inbox",
            status_code=status,
            request_id=request_id,
        )
        if 400 <= status < 500:
            return None, _PostError("client_error", status_code=status)
        return None, _PostError("server_error", status_code=status)
    except RegistryResponseError:
        _log.exception("/approvals malformed registry-api response", request_id=request_id)
        return None, _PostError("malformed_response")
    except httpx.HTTPError as exc:
        _log.warning(
            "/approvals registry-api network error on open_inbox",
            exc_type=type(exc).__name__,
            request_id=request_id,
        )
        return None, _PostError("network_error", exc_type_name=type(exc).__name__)
    except Exception:  # noqa: BLE001 — Story 3.1 M3 backstop
        _log.exception(
            "/approvals unexpected error on open_inbox",
            request_id=request_id,
        )
        return None, _PostError("unexpected")


async def _handle_post_error(
    message: Message,
    err: _PostError,
    chat_id: int,
    new_thread_id: int,
    request_id: str,
) -> None:
    """Map a :class:`_PostError` to a cleanup + reply policy (PP5 + PP15).

    PP5: cleanup is ONLY safe on 4xx (server explicitly rejected) — on
    5xx / timeouts the server may have persisted the event before
    failing, so deleting the topic would orphan the event.

    PP15: when cleanup runs, verify ``delete_forum_topic`` succeeded
    before telling the operator to retry — a failed cleanup means the
    chat is dirty AND the next /approvals will create yet another
    orphan.
    """
    if err.kind == "client_error":
        cleanup_succeeded = await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        if cleanup_succeeded:
            await safe_reply(
                message,
                "⚠️ Inbox event emission failed — Forum-Topic was deleted. Retry /approvals.",
            )
        else:
            await safe_reply(
                message,
                "⚠️ Inbox event emission failed AND the temporary Forum-Topic "
                "could not be deleted — contact ops to clean up.",
            )
        return
    if err.kind == "server_error":
        # PP5: 5xx — server-side error, state indeterminate. Don't cleanup.
        await safe_reply(
            message,
            "⚠️ Registry API returned a server error; inbox state is indeterminate — contact ops.",
        )
        return
    if err.kind == "network_error":
        # PP5: timeout / connect failure — state indeterminate. Don't cleanup.
        exc_name = err.exc_type_name or "NetworkError"
        await safe_reply(
            message,
            f"⚠️ Registry API unreachable ({exc_name}); inbox state is indeterminate — contact ops.",
        )
        return
    if err.kind == "malformed_response":
        cleanup_succeeded = await _cleanup_orphan_topic(message, chat_id, new_thread_id)
        if cleanup_succeeded:
            await safe_reply(
                message,
                "⚠️ Registry returned an unexpected response; Forum-Topic was "
                "deleted. Logs captured.",
            )
        else:
            await safe_reply(
                message,
                "⚠️ Registry returned an unexpected response AND Forum-Topic "
                "cleanup failed — contact ops.",
            )
        return
    # err.kind == "unexpected"
    cleanup_succeeded = await _cleanup_orphan_topic(message, chat_id, new_thread_id)
    if cleanup_succeeded:
        await safe_reply(message, "⚠️ Internal error. Logs captured.")
    else:
        await safe_reply(
            message,
            "⚠️ Internal error AND Forum-Topic cleanup failed — contact ops.",
        )


async def _cleanup_orphan_topic(message: Message, chat_id: int, message_thread_id: int) -> bool:
    """Best-effort delete of a Forum-Topic created when POST failed (P3).

    Telegram exposes ``delete_forum_topic`` which removes the topic that
    was created in the operator's chat right before the event emission
    failed. Failures here are logged but never propagated — the goal is
    to leave the operator's chat clean, not to mask the original error.

    Story 11.3 PP15: returns ``True`` when the delete actually succeeded
    so the caller can choose a "retry /approvals" reply (safe) versus a
    "contact ops" reply (cleanup failed → next /approvals would orphan
    another topic).
    """
    if message.bot is None:
        return False
    try:
        await message.bot.delete_forum_topic(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )
        return True
    except Exception as cleanup_exc:  # noqa: BLE001 — best-effort
        _log.error(
            "orphan_topic_cleanup_failed",
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            exc_info=cleanup_exc,
        )
        return False


def make_approvals_router() -> Router:
    """Factory — fresh Router per dispatcher instance (Story 3.4 pattern)."""
    router = Router()
    router.message(Command("approvals"))(handle_approvals)
    return router


__all__ = ["handle_approvals", "make_approvals_router"]
