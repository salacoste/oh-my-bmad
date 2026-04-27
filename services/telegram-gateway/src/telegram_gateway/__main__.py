"""telegram-gateway production entry point (Story 3.1 AC-3).

Reads env vars, constructs :class:`SystemClock`, calls
:py:meth:`TelegramSettings.from_env` with ``emit=None`` to obtain a
placeholder-wrapped settings instance for the app factory (the lifespan
rewraps the secrets with the real ``emit`` callback once the audit
writer is up — see :mod:`telegram_gateway.app.lifespan`), then runs via
``uvicorn.run`` (programmatic, not CLI subprocess).

Environment variables consumed by :class:`TelegramSettings`:

* ``TELEGRAM_BOT_TOKEN`` (required, audited)
* ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` (required, audited)
* ``TELEGRAM_WEBHOOK_URL`` (required, must be ``https``)
* ``EVENT_LOG_DIR`` (optional; default ``/var/lib/oh-my-bmad/events``)

Plus host/port for uvicorn:

* ``TELEGRAM_GATEWAY_HOST`` (default ``0.0.0.0``)
* ``TELEGRAM_GATEWAY_PORT`` (default ``8080``)
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn
from events.clock import SystemClock
from events.envelope import Actor

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.main import build_app

_SERVICE = "telegram-gateway"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(_SERVICE)

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = "8080"

# Bootstrap actor for the placeholder ``from_env(emit=None, ...)`` call —
# never produces audit events (emit is None) but satisfies the API
# contract; the lifespan rebuilds with a real actor before any value
# read.
_BOOTSTRAP_ACTOR = Actor(kind="system", id="telegram-gateway-bootstrap")


def main() -> None:
    """Read configuration from env, build the app, and start uvicorn."""
    host = os.environ.get("TELEGRAM_GATEWAY_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("TELEGRAM_GATEWAY_PORT", _DEFAULT_PORT))

    clock = SystemClock()

    # emit=None placeholder — the lifespan rebuilds the settings with the
    # real EventLogWriter.append once the writer exists. We need a
    # populated instance HERE so build_app's lifespan factory can read
    # ``event_log_dir`` / ``webhook_path`` before any audit fires.
    settings = TelegramSettings.from_env(
        emit=None,
        actor=_BOOTSTRAP_ACTOR,
        clock=clock,
    )

    log.info(
        "%s starting — host=%s port=%d webhook_path=%s",
        _SERVICE,
        host,
        port,
        settings.webhook_path,
    )

    app = build_app(settings=settings, clock=clock)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
