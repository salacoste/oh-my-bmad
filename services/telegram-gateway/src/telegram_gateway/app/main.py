"""FastAPI application factory for telegram-gateway (Story 3.1 AC-3).

``build_app(*, settings, clock) -> FastAPI`` mirrors
:func:`registry_api.app.build_app` — async lifespan via
:func:`telegram_gateway.app.lifespan.make_lifespan`, route mounting via
:py:meth:`fastapi.FastAPI.add_api_route` (so the operator-supplied
``settings.webhook_path`` actually takes effect — review-fix H1/M18),
no middleware (Story 3.6 adds the request-id / idempotency-key /
log-sanitizer / rate-limiter stack).

The factory takes a ``TelegramSettings`` instance (typically the
placeholder-wrapped one from ``from_env(emit=None, ...)``) so callers
can override ``event_log_dir`` / ``webhook_path`` for tests without
poking env-vars. The lifespan rewraps the secrets with the real
``emit`` callback once the audit writer is up.
"""

from __future__ import annotations

from events.clock import Clock
from fastapi import FastAPI

from telegram_gateway import __version__
from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import make_lifespan
from telegram_gateway.app.rate_limit import WebhookRateLimitMiddleware
from telegram_gateway.app.webhook import health, telegram_webhook


def build_app(*, settings: TelegramSettings, clock: Clock) -> FastAPI:
    """Build and return the wired-up FastAPI application.

    Args:
        settings: A ``TelegramSettings`` instance. The lifespan will
                  call ``TelegramSettings.from_env(emit=writer.append,
                  actor=..., clock=clock)`` and replace ``app.state.settings``
                  with the rewrapped instance — this argument supplies
                  the non-secret configuration that's needed BEFORE the
                  writer exists (``event_log_dir``, ``webhook_path``).
        clock:    Injectable :class:`events.clock.Clock`.

    Returns:
        Fully configured :class:`fastapi.FastAPI` instance ready for
        ``uvicorn.run``.
    """
    app = FastAPI(
        title="oh-my-bmad telegram-gateway",
        version=__version__,
        lifespan=make_lifespan(settings, clock),
    )
    # Mount the webhook route DYNAMICALLY from settings.webhook_path so
    # operator overrides via TELEGRAM_WEBHOOK_PATH actually take effect
    # (review-fix H1/M18 — the previous decorator-based mount silently
    # ignored the field).
    app.add_api_route(
        settings.webhook_path,
        telegram_webhook,
        methods=["POST"],
    )
    app.add_api_route("/v1/health", health, methods=["GET"])
    # Story 3.6 AC-5/6/7: token-bucket rate limiter scoped to the webhook
    # path only. Thresholds are operator-tunable via TelegramSettings env-vars
    # (defaults match architecture.md:215 locked values).
    app.add_middleware(
        WebhookRateLimitMiddleware,
        webhook_path=settings.webhook_path,
        capacity=settings.tg_webhook_rate_limit_capacity,
        refill_per_second=settings.tg_webhook_rate_limit_refill_per_sec,
        clock=clock,
    )
    return app


__all__ = ["build_app"]
