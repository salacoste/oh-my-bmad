"""console-cli entry point — Typer app invocation + structlog wiring (Story 4.1)."""

from __future__ import annotations

import logging
import sys

import structlog
from secret_hygiene.sanitizer import redact_secrets

from console_cli.app.main import app

_STRUCTLOG_CONFIGURED: bool = False


def _configure_structlog() -> None:
    """Wire structlog + bridge stdlib logging (idempotent).

    Same canonical pattern as telegram-gateway: pre-chain runs
    merge_contextvars → add_log_level → add_logger_name → ExtraAdder
    → TimeStamper → redact_secrets, then ProcessorFormatter bridges
    stdlib records. ``redact_secrets`` MUST run before rendering.
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
        cache_logger_on_first_use=False,
    )
    _STRUCTLOG_CONFIGURED = True


def main() -> None:
    """Configure logging and invoke the Typer app."""
    _configure_structlog()
    app()


if __name__ == "__main__":
    main()
