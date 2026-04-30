"""Shared `safe_reply` helper used by all command handlers (M3 contract).

Wraps ``message.reply(...)`` so any exception (notably
``aiogram.exceptions.TelegramAPIError``) is logged and swallowed, never
propagating to the webhook dispatcher.

Telegram 4096-char limit
------------------------
Telegram messages are limited to 4096 chars; replies exceeding this raise
``TelegramBadRequest`` and are swallowed by ``safe_reply``.  Callers SHOULD
truncate proactively if their reply may exceed 4000 chars.
"""

from __future__ import annotations

import logging

from aiogram.types import Message

_log = logging.getLogger(__name__)

__all__ = ["safe_reply"]


async def safe_reply(message: Message, text: str) -> None:
    """Reply to a Telegram message, swallowing any delivery failure.

    All reply paths in command handlers use this helper so that a
    Telegram API error never propagates to the dispatcher and never violates
    the Story 3.1 M3 fire-and-forget contract.

    Args:
        message: The incoming Telegram message to reply to.
        text:    The reply text (HTML markup allowed; values MUST be
                 pre-escaped with ``html.escape``).
    """
    try:
        await message.reply(text)
    except Exception:  # noqa: BLE001
        _log.exception(
            "Failed to reply to message %s",
            getattr(message, "message_id", "?"),
        )
