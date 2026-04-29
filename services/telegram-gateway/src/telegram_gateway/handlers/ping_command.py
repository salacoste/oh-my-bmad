"""/ping command handler for telegram-gateway (Story 3.5 / FR17 / NFR-O4).

Bootstrap Minimum #3 — closes the Bootstrap Milestone. After this story ships,
the operator can submit tasks (/task), approve them (/approve), and check
platform health (/ping) entirely from Telegram.

This handler calls GET /v1/health on registry-api via RegistryAPIClient and
replies with a one-line platform health summary.

No audit-event emission
-----------------------
This handler does NOT emit any audit event. If the server-side health endpoint
emits an event, that is registry-api's concern. Emitting from the bot would
violate the single-writer rule (FR26).

No idempotency key
------------------
GET is idempotent by HTTP semantics (RFC 7231 §4.2.2). A /ping invocation
fetches a read-only snapshot; repeating it does not create a second resource.
The Idempotency-Key header is meaningful only for state-mutating operations.
This is the first handler in the bot that omits the header; the omission is
deliberate, not an oversight. See also: get_platform_health() docstring.

Registry-API endpoint gap
--------------------------
GET /v1/health on registry-api is NOT yet implemented server-side. No story
owner has been assigned (gap in current epic plan). Until the endpoint lands,
a live call returns 404. Tests mock the transport layer and are runnable today.
See Dev Notes in 3-5-ping-command.md for candidate owner stories.

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises). Telegram receives a 200 ACK from
the webhook endpoint regardless of what happens inside.

HTML parse mode (AC-6 / Story 3.1 M5)
--------------------------------------
Reply messages use HTML markup. ``DefaultBotProperties(parse_mode=ParseMode.HTML)``
is set globally in lifespan.py so all ``message.reply(...)`` calls inherit HTML
mode without explicit kwarg. All externally sourced values are HTML-escaped.
"""

from __future__ import annotations

import html
import logging

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers._errors import format_http_error
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.ping_command")


async def _safe_reply(message: Message, text: str) -> None:
    """Reply to a Telegram message, swallowing any delivery failure.

    H2/H3: ALL reply paths in handle_ping use this helper so that a
    Telegram API error never propagates to the dispatcher and never violates
    the Story 3.1 M3 fire-and-forget contract.
    """
    try:
        await message.reply(text)
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "Failed to reply to message %s: %s",
            getattr(message, "message_id", "?"),
            exc,
        )


async def handle_ping(
    message: Message,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /ping command.

    Calls GET /v1/health on registry-api and replies with a one-line platform
    health summary: registry status, worker status, clawhip queue depth, and
    platform version.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py (shared with /task and /approve handlers).

    Module-level (not a closure inside make_ping_router) — matches Story 3.4 M6
    pattern. Tests import it directly without router introspection.

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    request_id = new_request_id()

    try:
        health = await registry_client.get_platform_health(request_id=request_id)
    except httpx.TooManyRedirects:
        # M3: TooManyRedirects is an httpx.HTTPError subclass but indicates
        # misconfiguration, not a transient network issue. Treat as network error
        # since it's not an HTTP status error.
        _log.warning(
            "registry-api too many redirects for /ping (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except RegistryResponseError as exc:
        # H1: malformed-200 body — distinct from network errors (Story 3.4 H1).
        # Caught BEFORE the generic httpx.HTTPError branch.
        _log.exception(
            "registry-api malformed response for /ping (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry returned an unexpected response. Logs captured.")
        return
    except httpx.HTTPStatusError as exc:
        # AC-7: non-2xx status → format_http_error from _errors.py (Story 3.4 M4).
        reply = format_http_error(exc)
        _log.warning(
            "registry-api HTTP error for /ping (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except httpx.HTTPError as exc:
        # AC-6: network / timeout errors → "Registry unreachable" reply.
        _log.warning(
            "registry-api network error for /ping (type=%s request_id=%s): %s",
            type(exc).__name__,
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except Exception as exc:  # noqa: BLE001 — AC-8 backstop: never propagate to webhook
        _log.exception(
            "/ping handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    # AC-4: success reply — all interpolated values wrapped in html.escape().
    # The version string MUST be escaped defensively even though "vX.Y.Z" is the
    # normal contract — operator env-var injection could produce strings with "<".
    registry_status_safe = html.escape(health.registry_status)
    worker_status_safe = html.escape(health.worker_status)
    version_safe = html.escape(health.version)
    queue_depth = health.clawhip_queue_depth  # int — no HTML chars possible

    summary = (
        f"pong · registry: {registry_status_safe}"
        f" · worker: {worker_status_safe}"
        f" · clawhip: {queue_depth} events queued"
        f" · version: {version_safe}"
    )

    # AC-4: prefix "⚠️ " only when registry_status == "unhealthy".
    # "degraded" does not get the prefix.
    reply_text = f"⚠️ {summary}" if health.registry_status == "unhealthy" else summary

    await _safe_reply(message, reply_text)


def make_ping_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3) and make_approve_router()
    (Story 3.4): each test lifespan builds a new Dispatcher and a new Router.

    M6: handle_ping is a standalone module-level coroutine so tests can import
    it directly without brittle router-introspection.
    """
    router = Router()
    router.message(Command("ping"))(handle_ping)
    return router


__all__ = ["handle_ping", "make_ping_router"]
