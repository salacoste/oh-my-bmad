"""registry-api production entry point (Story 2.9 AC-6).

Reads env vars with sensible defaults, constructs ``SystemClock``, calls
``build_app``, and runs via ``uvicorn.run`` (programmatic, not CLI subprocess).

Environment variables:
    REGISTRY_API_DB_URL:   SQLAlchemy async URL for the SQLite store.
                           Default: sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3
    REGISTRY_API_LOG_DIR:  Root directory for JSONL event log files.
                           Default: /var/lib/oh-my-bmad/registry/events
    REGISTRY_API_HOST:     Bind host for uvicorn. Default: 0.0.0.0
    REGISTRY_API_PORT:     Bind port for uvicorn. Default: 8080

Logging stack (Story 3.6 AC-4)
------------------------------

Structlog is configured here (in ``__main__.py``) — an intentional deviation
from architecture.md:826 which says ``app/main.py``. ``app/main.py`` is the
FastAPI factory imported by tests, where wiring structlog would either pollute
pytest's ``caplog`` fixture or require a test-aware guard. ``__main__.py`` is
the production entry-point only; tests do not import it. Story 3.6 Dev Notes
documents this deviation.

Processor chain (architecture.md:413-417 — ``redact_secrets`` MUST run before
``JSONRenderer`` or it redacts nothing):

    merge_contextvars → add_log_level → add_logger_name
        → TimeStamper(iso, utc) → redact_secrets → JSONRenderer()

Stdlib ``logging.getLogger(...)`` callers (e.g. F12's DB URL redaction
message) are bridged through ``structlog.stdlib.ProcessorFormatter`` so they
travel the same processor chain.

Per F12 of the Story 2.9 code review, the DB URL is redacted before logging
so a ``user:password@host`` URL never leaks the password segment to operator
logs.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import structlog
import uvicorn
from events.clock import SystemClock
from secret_hygiene.sanitizer import redact_secrets

from registry_api.app import build_app

_SERVICE = "registry-api"

# Story 3.6 AC-4: idempotent structlog wiring. Tests that import the
# ``main`` symbol (TestEntryPoint) call it multiple times; without the
# guard, the root logger accumulates handlers and the structlog processor
# chain is double-wrapped on each call.
_STRUCTLOG_CONFIGURED: bool = False


def _configure_logging() -> None:
    """Wire structlog + bridge stdlib logging through the same processor chain.

    Idempotent: re-running ``main()`` (e.g. across the two ``TestEntryPoint``
    cases) does not double-wire processors or stack handlers on the root
    logger. The ``_STRUCTLOG_CONFIGURED`` sentinel + ``handlers.clear()``
    together keep the configuration deterministic.
    """
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return

    # Shared pre-chain — applied to BOTH stdlib-bridged records (via
    # ``ProcessorFormatter.foreign_pre_chain``) and structlog-native ones
    # (via the main ``processors`` list). Order is load-bearing:
    # ``redact_secrets`` MUST run before any rendering or the JSON output
    # contains the unredacted secret bytes (architecture.md:417).
    #
    # Story 3.6 H1/H3: ``ExtraAdder`` promotes ``extra={...}`` kwargs from
    # stdlib ``logging`` calls into the structlog event_dict so the
    # downstream ``redact_secrets`` processor can value-pattern-redact
    # secrets that arrive via ``_log.warning(..., extra={"received": ...})``
    # (the ``IdempotencyKeyMiddleware`` warning path). Without this
    # processor the stdlib LogRecord's extra attributes are NEVER copied
    # onto the event_dict and a leaked secret would render unredacted.
    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]

    # Canonical structlog 24.x bridge pattern (Story 3.6 review H3): the
    # ``ProcessorFormatter`` runs ``foreign_pre_chain`` ONLY for foreign
    # (stdlib) records and the ``processors=[remove_processors_meta,
    # JSONRenderer()]`` list for both flavours. Native structlog records
    # already had ``pre_chain`` applied via ``structlog.configure(processors=
    # [*pre_chain, wrap_for_formatter])`` and arrive here flagged with
    # ``_from_structlog=True`` so the foreign pre-chain is correctly skipped
    # — each field appears EXACTLY ONCE in the JSON output regardless of
    # whether the caller used native ``structlog.get_logger(...)`` or stdlib
    # ``logging.getLogger(...)``.
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


log = logging.getLogger(_SERVICE)

_DEFAULT_DB_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"
_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = "8080"

# Match `scheme://user:password@host/db` and redact the password segment.
# SQLite file URLs (``sqlite+aiosqlite:////path``) have no auth segment so
# the regex simply doesn't match — the URL passes through unchanged.
_URL_AUTH_RE = re.compile(r"(://[^:/@]+:)([^@]+)(@)")


def _redact_url(url: str) -> str:
    """Redact the password segment in a SQLAlchemy URL for safe logging.

    Examples:
        >>> _redact_url("postgresql://user:secret@host/db")
        'postgresql://user:****@host/db'
        >>> _redact_url("sqlite+aiosqlite:////var/lib/state.sqlite3")
        'sqlite+aiosqlite:////var/lib/state.sqlite3'
    """
    return _URL_AUTH_RE.sub(r"\1****\3", url)


def main() -> None:
    """Read configuration from env, build the app, and start uvicorn."""
    _configure_logging()
    db_url = os.environ.get("REGISTRY_API_DB_URL", _DEFAULT_DB_URL)
    log_dir = Path(os.environ.get("REGISTRY_API_LOG_DIR", _DEFAULT_LOG_DIR))
    host = os.environ.get("REGISTRY_API_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("REGISTRY_API_PORT", _DEFAULT_PORT))

    log.info(
        "%s starting — db_url=%r log_dir=%r host=%s port=%d",
        _SERVICE,
        _redact_url(db_url),
        str(log_dir),
        host,
        port,
    )

    # Story 11.3.12: the writable idempotency cache uses its OWN SQLite file
    # (default ``idempotency.sqlite3`` beside the state DB) so registry-state
    # is the sole writer of ``state.sqlite3`` — closes the cross-uid WAL
    # crash-loop. The separate file is created on start via the same
    # auto-create gate registry-state uses (off by default in production
    # where a migrator/operator owns schema; on for the self-contained
    # ROOT/separability composes).
    idempotency_db_url = os.environ.get("REGISTRY_API_IDEMPOTENCY_DB_URL")
    create_idempotency_schema_on_start = (
        os.environ.get("REGISTRY_API_AUTO_CREATE_IDEMPOTENCY_SCHEMA") == "1"
    )

    clock = SystemClock()
    app = build_app(
        base_dir=log_dir,
        db_url=db_url,
        clock=clock,
        idempotency_db_url=idempotency_db_url,
        create_idempotency_schema_on_start=create_idempotency_schema_on_start,
    )

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
