"""orchestrator-adapter entry point — structlog wiring + MCP + OMC lifecycle (Story 5.10)."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog
from secret_hygiene.sanitizer import redact_secrets

from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.app.main import run_adapter

_SERVICE = "orchestrator-adapter"

_STRUCTLOG_CONFIGURED: bool = False


def _configure_structlog() -> None:
    """Wire structlog + bridge stdlib logging (idempotent)."""
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
        cache_logger_on_first_use=False,
    )
    _STRUCTLOG_CONFIGURED = True


async def _run() -> None:
    log = structlog.get_logger(__name__)
    settings = OrchestratorSettings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_stop(signum: int) -> None:
        log.info("stopping", signal=signum)
        stop_event.set()

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_stop, sig)
    except (NotImplementedError, RuntimeError):
        pass

    await run_adapter(settings, stop_event)


def main() -> None:
    _configure_structlog()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
