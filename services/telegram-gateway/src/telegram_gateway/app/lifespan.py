"""Async lifespan for telegram-gateway (Story 3.1 AC-4 / AC-5).

``AsyncExitStack``-based lifespan that:

1. Constructs an :class:`events.event_log_writer.EventLogWriter`
   pointed at ``settings.event_log_dir`` so audit emission has a real
   sink before any ``.value`` read fires.
2. Calls :py:meth:`TelegramSettings.from_env` with the writer's
   ``append`` as the emit callback + a ``system`` actor identifying the
   service. This rewraps every ``audited_secret_field`` so subsequent
   ``.value`` reads schedule a ``secret.accessed`` envelope onto the
   running loop.
3. Builds an :class:`aiogram.Bot` from the audited bot token (1 audit
   read) and an empty :class:`aiogram.Dispatcher`. The bot's session
   close-callback is registered AFTER ``Bot()`` returns successfully so
   a partial-startup failure tears the session down only when there is
   actually a session to close (review-fix L9).
4. **Caches the webhook secret token bytes** (review-fix H4) — reading
   ``audited.webhook_secret_token.value`` per webhook request would emit
   one ``secret.accessed`` envelope per delivery, saturating the audit
   log under production volume. We read ``.value`` exactly once at boot
   and stash the encoded bytes on ``app.state.expected_webhook_secret_bytes``.
5. Calls :py:meth:`aiogram.Bot.set_webhook` with the cached secret-token
   bytes (decoded back to str for the aiogram API), ``drop_pending_updates=True``
   so a downtime backlog is discarded on restart. ``drop_pending_updates=True``
   is intentional: messages queued during downtime are discarded.
   Stories 3.16 / 3.17 add explicit ``/stop`` + ``/retry`` UX for
   recovery. The implicit drop on restart is acceptable for Phase 1.
6. Stashes ``bot`` / ``dp`` / ``settings`` / ``writer`` / cached secret
   bytes / dispatch-task set on ``app.state`` **AFTER** ``set_webhook``
   succeeds (review-fix M8) — if startup fails, the partial state never
   becomes visible.
7. On shutdown, drains the in-flight fire-and-forget dispatch tasks
   first (review-fix M3 follow-up), then calls
   :func:`secret_hygiene.flush_pending_emissions` (timeout=2.0s) so the
   in-flight audit-emission tasks complete BEFORE
   :py:meth:`aiogram.Bot.session.close` and
   :py:meth:`EventLogWriter.close` run. Without the flush the
   fire-and-forget audit tasks race the writer's underlying file handle
   and can drop events.

LIFO teardown order (review-fix M1)
-----------------------------------

``AsyncExitStack`` unwinds callbacks in last-in-first-out order. The
push order in this lifespan is:

1. ``writer.close``          → pops 5th (last)
2. ``bot.session.close``     → pops 4th
3. ``http_client.aclose``    → pops 3rd
4. ``_drain_dispatch_tasks`` → pops 2nd
5. ``flush_pending_emissions`` → pops 1st

So shutdown runs:
  flush_pending_emissions → drain dispatch tasks → http_client.aclose
  → bot.session.close → writer.close

``flush`` MUST run before ``writer.close`` or the in-flight
``secret.accessed`` tasks raise ``RuntimeError("EventLogWriter is
closed")``. ``http_client.aclose`` runs AFTER dispatch tasks are
drained: in-flight ``/task`` handlers that are still awaiting the
registry-api response use the http_client — closing it while they run
would raise ``"client has been closed" RuntimeError`` (review-fix H4).
``bot.session.close`` runs after the http client so the bot session
remains open while audit tasks emit (they only touch the writer).

Audit-count cold-start invariant (AC-9, post-H4-fix)
----------------------------------------------------

Boot + a single webhook delivery now yields exactly **2**
``secret.accessed`` envelopes — bot_token (``Bot()``) +
webhook_secret_token (single startup read for caching). The webhook
handler's per-request header-compare uses the cached bytes and emits
no audit events.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from events.clock import Clock
from events.envelope import Actor
from events.event_log_writer import EventLogWriter
from fastapi import FastAPI
from secret_hygiene import flush_pending_emissions

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.middleware import AllowlistMiddleware
from telegram_gateway.app.rate_limit import PerActorRateLimitMiddleware
from telegram_gateway.handlers import (
    make_agent_router,
    make_approvals_router,
    make_approve_router,
    make_key_status_router,
    make_logs_router,
    make_ping_router,
    make_reject_router,
    make_retry_router,
    make_status_router,
    make_stop_router,
    make_task_router,
)
from telegram_gateway.handlers.registry_client import RegistryAPIClient
from telegram_gateway.handlers.trace_command import make_trace_router

# Drain timeout for in-flight ``secret.accessed`` emission tasks on
# shutdown. Matches the registry-api precedent (Story 2.9 + 2.16 H6).
_FLUSH_TIMEOUT_SECONDS = 2.0

# Drain timeout for in-flight fire-and-forget dispatch tasks on shutdown.
# Each handler is bounded by aiogram's own timeouts; this gives a small
# extra grace period before the loop is torn down.
_DISPATCH_DRAIN_TIMEOUT_SECONDS = 5.0

# Stable actor identity for every audit envelope this service emits via
# ``AuditedSecret.value`` reads. ``kind="system"`` per Story 2.10 +
# Story 2.16 audited_secret module docstring. Imported by tests and
# middleware via ``from telegram_gateway.app.lifespan import
# TELEGRAM_GATEWAY_ACTOR`` (review-fix L13: promoted from private
# ``_TELEGRAM_GATEWAY_ACTOR`` to public). The private name is kept as an
# alias for backward-compat with any existing importers during the
# transition to the public name.
TELEGRAM_GATEWAY_ACTOR = Actor(kind="system", id="telegram-gateway")
# Backward-compat alias — prefer TELEGRAM_GATEWAY_ACTOR in new code.
_TELEGRAM_GATEWAY_ACTOR = TELEGRAM_GATEWAY_ACTOR

_log = logging.getLogger("telegram_gateway.lifespan")


async def _drain_dispatch_tasks(
    tasks: set[asyncio.Task[None]],
    timeout: float = _DISPATCH_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Await all in-flight fire-and-forget dispatch tasks (review-fix M3).

    Best-effort: a hung handler that doesn't honor cancellation should
    not block shutdown indefinitely. We wait *timeout* seconds and log
    the count of stragglers.
    """
    if not tasks:
        return
    done, pending = await asyncio.wait(list(tasks), timeout=timeout)
    if pending:
        _log.warning(
            "dispatch drain: %d task(s) did not complete within %ss",
            len(pending),
            timeout,
        )
        for task in pending:
            task.cancel()
        # Give cancelled tasks a chance to finalize.
        await asyncio.gather(*pending, return_exceptions=True)
    _ = done  # silence unused-var; the set is implicit-checked above


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
            # Push order is documented in the module docstring. Summary:
            # writer.close pushed FIRST so it pops LAST.
            stack.push_async_callback(writer.close)

            # Story 11.3.7 / AC2 — TELEGRAM_SKIP_WEBHOOK_SET hermetic defaults
            # are populated by :func:`telegram_gateway.app.config.apply_hermetic_defaults_to_env`
            # BEFORE this lifespan runs. The two production callers are
            # ``__main__.main()`` (bootstrap from_env at __main__.py:212) and
            # the colocated test fixtures that invoke the helper explicitly
            # before ``_seed_settings``. Calling the helper here would be
            # dead-on-arrival — settings_seed has already been constructed
            # at this point. See Story 11.3.7 Dev Agent Record (Edge-E1
            # discharge) for the full rationale.

            # First service-side use of AuditedBaseSettings.from_env
            # (Story 2.16). The placeholder wrappers on *settings_seed*
            # are replaced here with ones carrying the real emit + actor.
            audited = TelegramSettings.from_env(
                emit=writer.append,
                actor=TELEGRAM_GATEWAY_ACTOR,
                clock=clock,
            )

            # Story 3.2 AC-6: closed-by-default surface — an empty
            # allowlist rejects every inbound update including the
            # operator's own. Emit a WARNING on first boot so the
            # ``.env`` gap is loud rather than producing a silent
            # "/ping never replies" experience.
            if not audited.tg_allowlist_user_ids:
                _log.warning(
                    "TG_ALLOWLIST_USER_IDS is empty — rejecting all inbound "
                    "updates. Set the env-var to a non-empty list."
                )

            # Bot construction reads bot_token.value once → 1 audit envelope.
            # ``parse_mode="HTML"`` moved to ``DefaultBotProperties`` per
            # aiogram 3.7+ API (review-fix M5).
            # Use aiogram 3.x async context-manager protocol (M7): Bot.__aenter__
            # returns self; Bot.__aexit__ calls session.close(). This is cleaner
            # than push_async_callback(bot.session.close) and ensures the session
            # is fully closed even if __aexit__ does extra cleanup in future
            # aiogram versions.
            bot = await stack.enter_async_context(
                Bot(
                    token=audited.bot_token.value,
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                )
            )

            dp = Dispatcher()

            # Story 3.2 AC-4: register the allowlist middleware on the
            # ``update`` observer's OUTER chain so it fires BEFORE
            # handler routing. Outer placement matters: defense-in-depth
            # for unhandled update types (``my_chat_member``, ``poll``,
            # etc.) — non-allowlisted senders are rejected even when no
            # handler would have matched (NFR-S4).
            #
            # ORDERING CONTRACT (L17): AllowlistMiddleware MUST be the
            # FIRST registered outer_middleware on dp.update — Story 3.6
            # ack. When Story 3.6 adds the request-id / rate-limiter
            # outer middleware, AllowlistMiddleware must remain at index 0
            # so allowlist enforcement happens before any downstream
            # middleware that might touch handler state. Register ALL
            # other outer middleware AFTER this call.
            dp.update.outer_middleware.register(
                AllowlistMiddleware(
                    allowlist=audited.tg_allowlist_user_ids,
                    emit=writer.append,
                    actor=TELEGRAM_GATEWAY_ACTOR,
                    clock=clock,
                )
            )

            # Story 7.5.1 AC-1: per-actor rate limiter registered AFTER
            # AllowlistMiddleware so only allowlisted updates consume
            # per-actor tokens. Non-allowlisted updates are silently
            # dropped by the allowlist before reaching this middleware.
            # Thresholds are operator-tunable via TelegramSettings env-vars
            # (defaults: capacity=10, refill=5.0/s, max_entries=1024).
            dp.update.outer_middleware.register(
                PerActorRateLimitMiddleware(
                    capacity=audited.tg_per_actor_rate_limit_capacity,
                    refill_per_second=audited.tg_per_actor_rate_limit_refill_per_sec,
                    max_entries=audited.tg_per_actor_rate_limit_max_entries,
                    clock=clock,
                )
            )

            # Cache the webhook secret bytes ONCE at boot (review-fix H4).
            # Per-request reads would emit one ``secret.accessed``
            # envelope per webhook delivery, saturating the audit log
            # under production webhook volume. Encoding here is the only
            # ``.value`` read for the webhook secret over the service's
            # lifetime; subsequent header compares operate on the cached
            # bytes via ``hmac.compare_digest``.
            #
            # set_webhook ALSO needs the str form, so we decode the
            # cached bytes back rather than reading ``.value`` a second
            # time.
            expected_webhook_secret_bytes = audited.webhook_secret_token.value.encode("utf-8")

            # Fire-and-forget dispatch task set (review-fix M3 anchor).
            # Tracked on app.state so the webhook handler can register
            # tasks; drained on shutdown via _drain_dispatch_tasks.
            dispatch_tasks: set[asyncio.Task[None]] = set()

            # set_webhook uses the cached secret bytes (decoded back to
            # str for the aiogram API). No additional ``.value`` read.
            #
            # Story 11.3.7 / AC2: hermetic-test gate. When
            # ``TELEGRAM_SKIP_WEBHOOK_SET=1`` is set, skip the live
            # api.telegram.org call so the S-4 separability test (and
            # any CI env without outbound Telegram reachability) can
            # boot telegram-gateway to healthy. Production-default
            # (flag unset → False) preserves the normal set_webhook
            # path; no behavior change for operators with real .env.
            if not audited.telegram_skip_webhook_set:
                await bot.set_webhook(
                    url=str(audited.webhook_url),
                    secret_token=expected_webhook_secret_bytes.decode("utf-8"),
                    drop_pending_updates=True,
                )
            else:
                _log.info(
                    "set_webhook SKIPPED — TELEGRAM_SKIP_WEBHOOK_SET=1 "
                    "(hermetic test mode); webhook URL %r and secret will "
                    "NOT be registered with Telegram",
                    str(audited.webhook_url),
                )

            # Story 3.3 AC-4: construct the long-lived httpx.AsyncClient ONCE
            # (Story 3.1 H4 cache-once pattern — never construct per-request).
            http_client = httpx.AsyncClient(
                base_url=str(audited.registry_api_base_url),
                timeout=httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0),
            )
            registry_client = RegistryAPIClient(
                http_client=http_client,
            )

            # LIFO teardown order (review-fix H4): push callbacks so that
            # shutdown runs in the order documented in the module docstring:
            #   flush_pending_emissions → _drain_dispatch_tasks
            #   → http_client.aclose → bot.session.close → writer.close
            #
            # Push http_client.aclose FIRST among the three so it pops
            # THIRD (after drain + flush).  Dispatch tasks must complete
            # before the http client closes — in-flight /task handlers that
            # are awaiting registry-api use the http_client, so closing it
            # first would raise "client has been closed" (review-fix H4).
            stack.push_async_callback(http_client.aclose)

            # Push _drain_dispatch_tasks so it pops SECOND (before http_client).
            stack.push_async_callback(_drain_dispatch_tasks, dispatch_tasks)

            # Push flush_pending_emissions LAST so it pops FIRST on
            # teardown (Story 2.16 H6 / Epic-2-retro tech-debt #2). Use
            # functools.partial for keyword safety (review-fix L18).
            stack.push_async_callback(
                functools.partial(flush_pending_emissions, timeout=_FLUSH_TIMEOUT_SECONDS)
            )

            # Story 3.3 AC-5 / Story 3.4 L5: update workflow_data BEFORE
            # registering routers so handlers never see a missing registry_client.
            # If include_router raised after workflow_data was not yet set, any
            # handler that ran during the broken window would KeyError on
            # registry_client injection. Updating first eliminates that window.
            dp.workflow_data.update({"registry_client": registry_client})

            # make_task_router() / make_approve_router() create fresh Routers
            # per lifespan so aiogram's "already attached" guard never fires
            # across test lifespans that each build a new Dispatcher.
            dp.include_router(make_task_router())
            dp.include_router(make_approve_router())
            # Story 11.3 / FR63 — /approvals operator pinned-thread handler.
            dp.include_router(make_approvals_router())
            dp.include_router(make_ping_router())
            # Story 11.5.1 / FR65a — /key-status surfaces the singleton
            # KeyFingerprint row via registry-api GET /v1/key-status.
            dp.include_router(make_key_status_router())
            dp.include_router(make_status_router())
            dp.include_router(make_logs_router())
            dp.include_router(make_stop_router())
            dp.include_router(make_reject_router())
            dp.include_router(make_retry_router())
            dp.include_router(make_agent_router())
            # Story 9.7 / FR59a — /trace <trace_id> causal-chain query.
            # Story 9.7 pass-2 TH-B1: wire per-chat allowlist (defense-in-depth
            # on top of the per-user AllowlistMiddleware). Fall back to the
            # per-user allowlist when ``trace_allowed_chat_ids`` is empty so
            # the common 1:1 DM case (chat_id == user_id) inherits the same
            # closed-by-default surface without explicit operator config.
            #
            # Pass-3 UH-1 decision: the user-ids-as-chat-ids fallback is
            # KEPT as a deliberate dev-mode default. Rationale: most operators
            # interact with the bot via 1:1 DM where chat_id == user_id (a
            # positive integer matching the user's Telegram id), so reusing
            # ``tg_allowlist_user_ids`` produces a "just works" experience.
            # Operators wanting group chats (negative chat_ids) MUST set
            # ``TRACE_ALLOWED_CHAT_IDS`` explicitly. ``handle_trace`` itself
            # now REQUIRES the allowlist kwarg (pass-3 UH-1) so empty values
            # produced by a misconfigured fallback path still deny every
            # chat — closed-by-default surface, not a bypass.
            trace_chat_allowlist = (
                audited.trace_allowed_chat_ids
                if audited.trace_allowed_chat_ids
                else audited.tg_allowlist_user_ids
            )
            dp.include_router(make_trace_router(allowed_chat_ids=trace_chat_allowlist))

            # Assign app.state ONLY after set_webhook succeeds
            # (review-fix M8). If set_webhook raises, the stack unwinds
            # without leaving partial state visible to any handler.
            app.state.bot = bot
            app.state.dp = dp
            app.state.settings = audited
            app.state.writer = writer
            app.state.expected_webhook_secret_bytes = expected_webhook_secret_bytes
            app.state._dispatch_tasks = dispatch_tasks
            app.state.http_client = http_client
            app.state.registry_client = registry_client

            # AC-5 contract: log line is verbatim "Webhook set · ready" and
            # the only structured field is the path. NEVER include the URL
            # token portion or the secret_token value (Story 2.17 log-leakage
            # contract). The path is platform-internal — operator already
            # knows it.
            #
            # Story 11.3.7 / AC2 — emit the "ready" log only when set_webhook
            # actually ran. Operators / monitoring that parse for the
            # verbatim string MUST be able to distinguish "real ready" from
            # "skipped". The skip-mode INFO at line 313 is the dual signal
            # for hermetic boots.
            if not audited.telegram_skip_webhook_set:
                _log.info("Webhook set · ready", extra={"path": audited.webhook_path})
            else:
                _log.info(
                    "Webhook SKIPPED · lifespan ready (hermetic test mode)",
                    extra={"path": audited.webhook_path},
                )

            yield

    return lifespan


__all__ = [
    "make_lifespan",
    "make_trace_router",
    "TELEGRAM_GATEWAY_ACTOR",
    "_TELEGRAM_GATEWAY_ACTOR",
]
