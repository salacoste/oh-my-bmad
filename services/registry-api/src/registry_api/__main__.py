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

Logging is configured on stderr with structured ISO-8601 timestamps. Per F12
of the Story 2.9 code review, the DB URL is redacted before logging so a
``user:password@host`` URL never leaks the password segment to operator logs.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import uvicorn
from events.clock import SystemClock

from registry_api.app import build_app

_SERVICE = "registry-api"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
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

    clock = SystemClock()
    app = build_app(base_dir=log_dir, db_url=db_url, clock=clock)

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
