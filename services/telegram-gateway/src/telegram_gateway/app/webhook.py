"""Webhook + health routes for telegram-gateway (Story 3.1 AC-6 / AC-8).

The webhook handler verifies the ``X-Telegram-Bot-Api-Secret-Token``
header against the **cached bytes** of ``settings.webhook_secret_token``
using :func:`hmac.compare_digest` (constant-time — a naive ``==`` leaks
header length via timing). On match, the JSON body is validated through
:class:`aiogram.types.Update` and dispatched into
:py:meth:`aiogram.Dispatcher.feed_webhook_update` **as a fire-and-forget
asyncio task** (review-fix M3/M22 — Telegram retries on non-2xx, so we
return 200 immediately and dispatch in the background to keep webhook
latency under NFR-R3's 500ms ceiling regardless of handler complexity).
On mismatch / missing header, the handler returns a bare
``Response(status_code=403)`` (NOT 401 — Telegram does not expect an
auth-challenge response).

This story does NOT add per-route rate-limiting (Story 3.6 owns the
middleware stack). Telegram itself rate-limits per-bot, and the tunnel
is operator-only ingress.

Audit-saturation note (review-fix H4)
-------------------------------------

The handler does NOT call ``settings.webhook_secret_token.value`` per
request. The lifespan caches the encoded bytes onto
``app.state.expected_webhook_secret_bytes`` exactly once at startup —
reading ``.value`` per webhook would emit one ``secret.accessed``
envelope per request, saturating the audit log under production webhook
volume (Telegram delivers 100s/sec for a busy bot). Caching at boot
preserves the single-emission-per-secret-read semantics for high-volume
read paths; future services integrating :class:`AuditedSecret` for
per-request use MUST follow the same pattern (carry-forward to Stories
5.4 / 5.7).

Body-size + handler-exception isolation (review-fixes H3, H5, M11)
-------------------------------------------------------------------

* ``request.body()`` is bounded to 1 MiB before parsing — defense-in-
  depth against authenticated-DoS via ``request.json()`` saturating
  process memory.
* ``json.loads`` / ``Update.model_validate`` exceptions return ``400``
  (not 422 with field paths echoing untrusted input).
* ``feed_webhook_update`` exceptions return ``200`` after logging — a
  handler bug must NOT trigger Telegram's 24h retry storm. The audit
  trail still records the secret access; observability comes from the
  exception log line.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Annotated, Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import Header, Request, Response
from pydantic import ValidationError

from telegram_gateway import __version__

# Defense-in-depth body-size cap (review-fix H5). Telegram's documented
# webhook payload max is far below this; 1 MiB is generous headroom that
# bounds memory pressure under malicious / buggy authenticated traffic.
_MAX_UPDATE_BYTES = 1 * 1024 * 1024

_log = logging.getLogger("telegram_gateway.webhook")


async def _safe_dispatch(dp: Dispatcher, bot: Bot, update: Update) -> None:
    """Run the dispatcher inside an exception-trap (review-fix H3/M3/M22).

    Any handler-side exception is logged + swallowed. Returning an error
    to Telegram triggers retry storms (up to 24h re-delivery) and the
    webhook latency budget (NFR-R3) shouldn't depend on handler logic.
    """
    try:
        await dp.feed_webhook_update(bot, update)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 — handler errors must not fail the webhook
        _log.exception(
            "telegram update dispatch failed",
            extra={"update_id": getattr(update, "update_id", None)},
        )


async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> Response:
    """Verify the secret-token header, then dispatch the update.

    AC-6: ``hmac.compare_digest`` is mandatory — a naive ``==`` would
    short-circuit on the first mismatching byte and let an attacker
    derive the secret one byte at a time via timing.

    AC-7 latency budget: this path is ``await``-only (no blocking I/O)
    and dispatches via ``asyncio.create_task`` so handler latency is
    decoupled from webhook latency.
    """
    expected_bytes: bytes = request.app.state.expected_webhook_secret_bytes

    # Coerce to bytes defensively (review-fix M2). FastAPI delivers a
    # plain ``str`` for the Header annotation, but a malformed ASGI scope
    # could in theory carry bytes; ``compare_digest`` raises TypeError
    # when its arguments mix ``str`` and ``bytes``.
    try:
        presented_bytes = str(x_telegram_bot_api_secret_token or "").encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return Response(status_code=403)

    if not x_telegram_bot_api_secret_token:
        # Distinct WARN for missing-vs-mismatched (review-fix L14). Never
        # log header content — only the diagnostic identity.
        _log.warning("webhook rejected: missing secret-token header")
        return Response(status_code=403)
    if not hmac.compare_digest(presented_bytes, expected_bytes):
        _log.warning("webhook rejected: secret-token mismatch")
        return Response(status_code=403)

    # Body size guard (review-fix H5). ``request.body()`` reads the full
    # buffered payload; bounding before ``json.loads`` keeps malicious /
    # buggy authenticated traffic from blowing process memory.
    raw = await request.body()
    if len(raw) > _MAX_UPDATE_BYTES:
        _log.warning(
            "webhook rejected: body exceeds max size",
            extra={"size": len(raw), "max": _MAX_UPDATE_BYTES},
        )
        return Response(status_code=413)

    # Body parse + Update validation guards (review-fix H3/M11). Both
    # return 400 — never echo field paths to Telegram (or any other
    # untrusted ingress).
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return Response(status_code=400)
    try:
        update = Update.model_validate(data)
    except ValidationError:
        return Response(status_code=400)

    # Fire-and-forget dispatch (review-fix M3/M22). The webhook returns
    # 200 immediately; ``app.state._dispatch_tasks`` anchors the task so
    # GC doesn't collect it mid-flight. Lifespan shutdown drains the
    # set before tearing the writer down.
    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp
    tasks: set[asyncio.Task[None]] = request.app.state._dispatch_tasks
    task = asyncio.create_task(_safe_dispatch(dp, bot, update))
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    return Response(status_code=200)


async def health() -> dict[str, str]:
    """Container-orchestration / Cloudflare-Tunnel health probe.

    Distinct from the operator-facing ``/ping`` Telegram command
    (Story 3.5). No auth required — readiness check only.
    """
    return {
        "status": "ok",
        "service": "telegram-gateway",
        "version": __version__,
    }


__all__ = ["telegram_webhook", "health"]
