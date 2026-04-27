"""Webhook + health routes for telegram-gateway (Story 3.1 AC-6 / AC-8).

The webhook handler verifies the ``X-Telegram-Bot-Api-Secret-Token``
header against ``settings.webhook_secret_token.value`` using
:func:`hmac.compare_digest` (constant-time — a naive ``==`` leaks header
length via timing). On match, the JSON body is validated through
:class:`aiogram.types.Update` and dispatched into
:py:meth:`aiogram.Dispatcher.feed_webhook_update`. On mismatch / missing
header, the handler returns a bare ``Response(status_code=403)`` (NOT
401 — Telegram does not expect an auth-challenge response).

This story does NOT add per-route rate-limiting (Story 3.6 owns the
middleware stack). Telegram itself rate-limits per-bot, and the tunnel
is operator-only ingress.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from aiogram.types import Update
from fastapi import APIRouter, Header, Request, Response

from telegram_gateway import __version__

router = APIRouter()


@router.post("/v1/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> Response:
    """Verify the secret-token header, then feed the update into the dispatcher.

    AC-6: ``hmac.compare_digest`` is mandatory — a naive ``==`` would
    short-circuit on the first mismatching byte and let an attacker
    derive the secret one byte at a time via timing.

    AC-7 latency budget: this path is ``await``-only (no blocking I/O).
    JSON parsing happens in-process via Starlette's ``await
    request.json()``; ``Update.model_validate`` is a pure-Python
    validator; ``feed_webhook_update`` returns immediately when no
    handler matches.
    """
    expected = request.app.state.settings.webhook_secret_token.value
    presented = x_telegram_bot_api_secret_token or ""
    if not hmac.compare_digest(presented, expected):
        return Response(status_code=403)
    update = Update.model_validate(await request.json())
    await request.app.state.dp.feed_webhook_update(request.app.state.bot, update)
    return Response(status_code=200)


@router.get("/v1/health")
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


__all__ = ["router"]
