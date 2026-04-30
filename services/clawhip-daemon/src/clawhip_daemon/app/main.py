"""clawhip-daemon application entrypoint — Story 3.9 AC-8.

Promotes ``__main__.py`` from Story 1.4's no-op hello-world stub to a real
Telegram outbound sink subscriber loop.

``build_app()`` constructs the dependency graph:
    TelegramOutbound → (bot_token, http_client)
    TelegramSink     → (base_dir, registry_api_url, http_client, outbound)

``run()`` calls ``await sink.run()`` indefinitely until SIGTERM / SIGINT.

``main()`` is the sync entry-point for ``python -m clawhip_daemon``:
  - reads env vars (see below)
  - installs SIGTERM / SIGINT → ``stop_event.set()`` (best-effort POSIX)
  - calls ``asyncio.run(run(...))``

Environment variables:
    CLAWHIP_DAEMON_LOG_DIR        path to JSONL event-log directory
                                  (default: /var/lib/oh-my-bmad/registry/events)
    CLAWHIP_DAEMON_REGISTRY_API_URL   registry-api base URL
                                  (default: http://registry-api:8080)
    TELEGRAM_BOT_TOKEN            required — Telegram Bot API token

Logging stack (Story 3.6 / 3.7 carry-forward):
    structlog wired identically to telegram-gateway.__main__._configure_logging().
    The idempotency sentinel ``_STRUCTLOG_CONFIGURED`` prevents double-wiring
    when ``main()`` is called multiple times in the same process (e.g. tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import NamedTuple

import httpx
import structlog
from events.clock import SystemClock
from events.envelope import Actor
from secret_hygiene.audited_secret import AuditedSecret
from secret_hygiene.sanitizer import redact_secrets

from clawhip_daemon.adapters.sinks.telegram_sink import TelegramSink
from clawhip_daemon.adapters.telegram_outbound import TelegramOutbound

_SERVICE = "clawhip-daemon"

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_REGISTRY_API_URL = "http://registry-api:8080"

# Story 3.6 carry-forward: idempotency sentinel — re-running main() must not
# double-wire the structlog processor chain or stack handlers on the root logger.
_STRUCTLOG_CONFIGURED: bool = False


def _configure_logging() -> None:
    """Wire structlog + bridge stdlib logging through the same processor chain.

    Idempotent: re-entry across main() calls is a no-op.  Mirrors
    telegram_gateway.__main__._configure_logging() exactly (Story 3.6 AC-4).
    """
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return

    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _STRUCTLOG_CONFIGURED = True


class _AppComponents(NamedTuple):
    """Bundled app components for clean teardown."""

    sink: TelegramSink
    http_client: httpx.AsyncClient
    stop_event: asyncio.Event


def build_app(
    *,
    base_dir: Path,
    registry_api_url: str,
    bot_token: AuditedSecret,
) -> tuple[TelegramSink, httpx.AsyncClient]:
    """Construct TelegramOutbound + TelegramSink.

    The caller owns the returned ``http_client`` lifecycle — must call
    ``await http_client.aclose()`` on shutdown.

    Args:
        base_dir:          Root directory containing ``YYYY-MM-DD.jsonl`` event logs.
        registry_api_url:  registry-api base URL (e.g. ``http://registry-api:8080``).
        bot_token:         ``AuditedSecret`` wrapping ``TELEGRAM_BOT_TOKEN``.

    Returns:
        ``(TelegramSink, httpx.AsyncClient)`` — wire the client into the sink
        and close it on shutdown.
    """
    http_client = httpx.AsyncClient(timeout=10.0)
    outbound = TelegramOutbound(
        bot_token=bot_token,
        http_client=http_client,
    )
    sink = TelegramSink(
        base_dir=base_dir,
        registry_api_url=registry_api_url,
        http_client=http_client,
        outbound=outbound,
        clock=SystemClock(),
    )
    return sink, http_client


async def run(
    *,
    base_dir: Path,
    registry_api_url: str,
    bot_token: AuditedSecret,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Build components and run the sink loop until *stop_event* fires.

    Args:
        base_dir:         Root directory containing JSONL event logs.
        registry_api_url: registry-api base URL.
        bot_token:        ``AuditedSecret`` wrapping ``TELEGRAM_BOT_TOKEN``.
        stop_event:       Optional ``asyncio.Event``; set it to stop the loop.
                          If ``None``, a local event is created (useful in tests).
    """
    stop = stop_event if stop_event is not None else asyncio.Event()
    sink, http_client = build_app(
        base_dir=base_dir,
        registry_api_url=registry_api_url,
        bot_token=bot_token,
    )
    try:
        await sink.run(stop_event=stop)
    finally:
        await http_client.aclose()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Best-effort SIGTERM/SIGINT → ``stop_event.set()`` on POSIX.

    On Windows ``loop.add_signal_handler`` raises ``NotImplementedError``
    for both signals — we fall back silently (``SIGINT`` still works via
    ``KeyboardInterrupt``).
    """
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue


def main() -> None:
    """Sync entrypoint for ``python -m clawhip_daemon``.

    Reads configuration from environment variables, installs signal handlers
    for clean shutdown, and runs the Telegram outbound sink loop.
    """
    _configure_logging()
    log = logging.getLogger(_SERVICE)

    log_dir = Path(os.environ.get("CLAWHIP_DAEMON_LOG_DIR", _DEFAULT_LOG_DIR))
    registry_api_url = os.environ.get("CLAWHIP_DAEMON_REGISTRY_API_URL", _DEFAULT_REGISTRY_API_URL)
    token_raw = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token_raw:
        log.error("TELEGRAM_BOT_TOKEN is required but not set; clawhip-daemon cannot start")
        sys.exit(2)

    # Wrap the token in an AuditedSecret with emit=None (Phase 1: no
    # EventLogWriter in clawhip-daemon — single-writer rule prevents direct
    # event log writes here; the audit trail for this read lands when
    # clawhip-bridge MCP is wired in a future story).
    bot_token = AuditedSecret(
        token_raw,
        secret_name="telegram_bot_token",
        emit=None,
        actor=Actor(kind="system", id=_SERVICE),
        clock=SystemClock(),
    )

    log.info(
        "%s starting — log_dir=%s registry_api=%s",
        _SERVICE,
        log_dir,
        registry_api_url,
    )

    stop_event = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
        await run(
            base_dir=log_dir,
            registry_api_url=registry_api_url,
            bot_token=bot_token,
            stop_event=stop_event,
        )

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


__all__ = ["build_app", "main", "run"]
