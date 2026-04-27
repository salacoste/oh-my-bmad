"""Async lifespan for telegram-gateway (Story 3.1 AC-4 / AC-5).

``AsyncExitStack``-based lifespan that:

1. Constructs an :class:`registry_state.adapters.event_log.EventLogWriter`
   pointed at ``settings.event_log_dir`` so audit emission has a real
   sink before any ``.value`` read fires.
2. Calls :py:meth:`TelegramSettings.from_env` with the writer's
   ``append`` as the emit callback + a ``system`` actor identifying the
   service. This rewraps every ``audited_secret_field`` so subsequent
   ``.value`` reads schedule a ``secret.accessed`` envelope onto the
   running loop.
3. Builds an :class:`aiogram.Bot` from the audited bot token (1 audit
   read) and an empty :class:`aiogram.Dispatcher`. The bot's session
   close-callback is registered AFTER construction so a partial-startup
   failure (e.g., ``set_webhook`` raising) still tears the session down.
4. Calls :py:meth:`aiogram.Bot.set_webhook` with the audited webhook
   secret token (1 audit read), ``drop_pending_updates=True`` so a
   downtime backlog is discarded on restart.
5. Stashes ``bot`` / ``dp`` / ``settings`` on ``app.state`` so the
   webhook route can dispatch into the same dispatcher instance.
6. On shutdown, calls :func:`secret_hygiene.flush_pending_emissions`
   FIRST (timeout=2.0s) so the in-flight audit-emission tasks complete
   BEFORE :py:meth:`EventLogWriter.close` runs — without the flush, the
   fire-and-forget audit tasks race the writer's underlying file handle
   and can drop events.

Audit-count cold-start invariant (AC-9): boot + a single webhook
delivery yields exactly 3 ``secret.accessed`` envelopes — bot_token
(``Bot()``), webhook_secret_token (``set_webhook(...)``),
webhook_secret_token (header-compare in :mod:`telegram_gateway.app.webhook`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from aiogram import Bot, Dispatcher
from events.clock import Clock
from events.envelope import Actor
from fastapi import FastAPI
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per story 3.1 (mirrors registry_api/app.py:42)
    EventLogWriter,
)
from secret_hygiene import flush_pending_emissions

from telegram_gateway.app.config import TelegramSettings

# Drain timeout for in-flight ``secret.accessed`` emission tasks on
# shutdown. Matches the registry-api precedent (Story 2.9 + 2.16 H6).
_FLUSH_TIMEOUT_SECONDS = 2.0

# Stable actor identity for every audit envelope this service emits via
# ``AuditedSecret.value`` reads. ``kind="system"`` per Story 2.10 +
# Story 2.16 audited_secret module docstring.
_TELEGRAM_GATEWAY_ACTOR = Actor(kind="system", id="telegram-gateway")

_log = logging.getLogger("telegram_gateway.lifespan")


def make_lifespan(
    settings_seed: TelegramSettings,
    clock: Clock,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Return a FastAPI ``lifespan`` callable bound to *settings_seed* + *clock*.

    *settings_seed* is the placeholder-wrapped instance produced by
    ``TelegramSettings.from_env(emit=None, ...)`` at app-build time —
    it provides the non-secret configuration (``event_log_dir``,
    ``webhook_url``, ``webhook_path``) needed BEFORE the writer exists.
    The lifespan re-runs ``from_env`` with the real ``emit`` callback
    once the writer is up so subsequent secret reads emit audit events.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            writer = EventLogWriter(base_dir=settings_seed.event_log_dir, clock=clock)
            # ``AsyncExitStack`` unwinds callbacks in LIFO order. We want
            # ``flush_pending_emissions`` to run FIRST on teardown so the
            # in-flight ``secret.accessed`` audit tasks have a chance to
            # complete BEFORE :py:meth:`EventLogWriter.close` closes the
            # underlying file descriptor (Story 2.16 H6 / Epic-2-retro
            # tech-debt #2). Push the writer close FIRST so it sits at the
            # bottom of the stack and pops LAST; push flush LAST so it
            # pops FIRST.
            stack.push_async_callback(writer.close)
            stack.push_async_callback(flush_pending_emissions, _FLUSH_TIMEOUT_SECONDS)

            # First service-side use of AuditedBaseSettings.from_env
            # (Story 2.16). The placeholder wrappers on *settings_seed*
            # are replaced here with ones carrying the real emit + actor.
            audited = TelegramSettings.from_env(
                emit=writer.append,
                actor=_TELEGRAM_GATEWAY_ACTOR,
                clock=clock,
            )

            # Bot construction reads bot_token.value once → 1 audit envelope.
            bot = Bot(token=audited.bot_token.value)
            stack.push_async_callback(bot.session.close)

            dp = Dispatcher()
            app.state.bot = bot
            app.state.dp = dp
            app.state.settings = audited
            app.state.writer = writer

            # set_webhook reads webhook_secret_token.value once → 1 audit envelope.
            await bot.set_webhook(
                url=str(audited.webhook_url),
                secret_token=audited.webhook_secret_token.value,
                drop_pending_updates=True,
            )
            # AC-5 contract: log line is verbatim "Webhook set · ready" and
            # the only structured field is the path. NEVER include the URL
            # token portion or the secret_token value (Story 2.17 log-leakage
            # contract). The path is platform-internal — operator already
            # knows it.
            _log.info("Webhook set · ready", extra={"path": audited.webhook_path})

            yield

    return lifespan


__all__ = ["make_lifespan"]
