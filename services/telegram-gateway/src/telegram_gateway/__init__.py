"""telegram-gateway — Telegram bot ingress: aiogram v3 dispatcher + webhook + allowlist middleware + command surface.

Story 1.2 shipped the scaffold (`__version__` stub).
Story 3.1 ships the aiogram v3 dispatcher behind a FastAPI webhook endpoint
plus :class:`TelegramSettings` (the first ``AuditedBaseSettings`` consumer
on the service side, exercising Story 2.16's audit infrastructure).
Stories 3.2 (allowlist), 3.3–3.5 (Bootstrap Minimum commands), and 3.6
(middleware stack) build atop this ingress.
"""

__version__ = "0.2.0"

# NOTE: do NOT eagerly import :func:`build_app` here — :mod:`telegram_gateway.app.main`
# itself imports ``__version__`` from this module to populate
# :class:`fastapi.FastAPI`'s ``version`` field, and an eager re-export
# would create a circular import on first ``import telegram_gateway``.
# Callers must import ``build_app`` from :mod:`telegram_gateway.app`.

__all__ = ["__version__"]
