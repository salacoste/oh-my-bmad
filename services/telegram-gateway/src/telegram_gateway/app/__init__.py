"""telegram-gateway FastAPI + aiogram bootstrap (Story 3.1).

Public surface:
  - :func:`build_app`: factory returning a wired-up :class:`fastapi.FastAPI`.
  - :class:`TelegramSettings`: ``AuditedBaseSettings`` subclass declaring the
    bot token + webhook secret token + webhook URL/path/event-log dir.

The :func:`build_app` factory mirrors :mod:`registry_api.app` — async
``AsyncExitStack`` lifespan, app-state attachment, and a thin route module.
"""

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.main import build_app

__all__ = ["TelegramSettings", "build_app"]
