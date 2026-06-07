"""Alembic async migration environment for registry-state (Story 2.3, Story 30.3).

Adaptations from the default Alembic env.py template:
1. Uses ``async_engine_from_config`` + ``await connection.run_sync(...)`` because
   our engine is an ``AsyncEngine`` (aiosqlite / asyncpg dialect). The sync
   pattern silently fails for async engines — the connection yields a coroutine
   and ``context.configure`` chokes.
2. Reads DB URL from ``REGISTRY_DATABASE_URL`` (Phase 6 canonical) or
   ``REGISTRY_STATE_DB_URL`` (legacy fallback). Default for local dev:
   ``sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3`` (P6-I1).
   Tests override via programmatic URL to use an in-memory or tmpfile URL.
3. ``target_metadata = Base.metadata`` feeds all model tables + indexes to
   Alembic autogenerate.
4. Dual-backend aware: SQLite URLs use ``NullPool``; Postgres URLs use the
   default connection pool (``AsyncAdaptedQueuePool``).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from registry_state.schema import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False prevents alembic's fileConfig call from
    # silencing application loggers (e.g. telegram_gateway.lifespan) that are
    # not listed in alembic.ini. The default is True, which sets .disabled=True
    # on every logger not explicitly declared in alembic.ini — this was a P0
    # production silent-failure: the AC-6 empty-allowlist WARNING was swallowed
    # whenever migrations ran before the lifespan logger fired.
    # AC-12 scope deviation: this file is in services/registry-state/, outside
    # the story-3.2 scope declaration. Patched here as a verifier-pass repair
    # (same precedent as commit 87a5061's S-3 test fix) because the root cause
    # resides in alembic config, not telegram-gateway code.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# DB URL resolution priority:
#   1. Programmatic ``cfg.set_main_option("sqlalchemy.url", ...)`` — e.g.
#      from tests — is authoritative and always wins.
#   2. ``REGISTRY_DATABASE_URL`` env var — Phase 6 canonical (Story 30.3).
#   3. ``REGISTRY_STATE_DB_URL`` env var — legacy fallback (pre-Phase 6).
#   4. ``_DEFAULT_URL`` (production filesystem path) — final fallback (P6-I1).
#
# The alembic.ini placeholder (``:memory:``) signals "not yet set" and is
# replaced by option 2–4. A non-placeholder URL in the config means the
# caller (programmatic test code) already decided; we leave it alone.
# ---------------------------------------------------------------------------
_ALEMBIC_INI_PLACEHOLDER = "sqlite+aiosqlite:///:memory:"
_DEFAULT_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"
_POSTGRES_PREFIX = "postgresql+asyncpg://"


def _resolve_url() -> str:
    """Resolve the database URL from env vars or the default."""
    _current_url = config.get_main_option("sqlalchemy.url")
    if _current_url != _ALEMBIC_INI_PLACEHOLDER and _current_url is not None:
        return _current_url
    # Phase 6 canonical env var takes priority; legacy fallback next.
    return (
        os.environ.get("REGISTRY_DATABASE_URL")
        or os.environ.get("REGISTRY_STATE_DB_URL")
        or _DEFAULT_URL
    )


config.set_main_option("sqlalchemy.url", _resolve_url())


def do_run_migrations(connection: Connection) -> None:
    """Synchronous inner function passed to ``connection.run_sync``."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live (async) DB connection.

    SQLite uses ``NullPool`` (no connection pooling for local file DBs).
    Postgres uses the default ``AsyncAdaptedQueuePool`` for proper
    connection management.
    """
    url = config.get_main_option("sqlalchemy.url") or ""
    kwargs: dict[str, Any] = {}
    if not url.startswith(_POSTGRES_PREFIX):
        # SQLite path: NullPool avoids "database is locked" surprises.
        kwargs["poolclass"] = pool.NullPool
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        **kwargs,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    """Generate SQL scripts without a live connection (offline mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())


def get_target_metadata() -> Any:
    """Expose target_metadata for testing / introspection."""
    return target_metadata
