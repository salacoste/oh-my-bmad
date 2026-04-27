"""FastAPI application factory for telegram-gateway (Story 3.1 AC-3).

``build_app(*, settings, clock) -> FastAPI`` mirrors
:func:`registry_api.app.build_app` — async lifespan via
:func:`telegram_gateway.app.lifespan.make_lifespan`, route mounting via
:func:`fastapi.FastAPI.include_router`, no middleware (Story 3.6 adds
the request-id / idempotency-key / log-sanitizer / rate-limiter stack).

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
from telegram_gateway.app.webhook import router as webhook_router


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
    app.include_router(webhook_router)
    return app


__all__ = ["build_app"]
