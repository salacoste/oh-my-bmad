"""Alembic async migration environment for registry-state (Story 2.3).

Adaptations from the default Alembic env.py template:
1. Uses ``async_engine_from_config`` + ``await connection.run_sync(...)`` because
   our engine is an ``AsyncEngine`` (aiosqlite dialect). The sync pattern silently
   fails for async engines — the connection yields a coroutine and
   ``context.configure`` chokes.
2. Reads DB URL from ``REGISTRY_STATE_DB_URL`` env var. Default for local dev:
   ``sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3`` (Arch line 797).
   Tests override via env var to use an in-memory or tmpfile URL.
3. ``target_metadata = Base.metadata`` feeds all 5 model tables + 6 indexes to
   Alembic autogenerate.
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
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# DB URL resolution priority:
#   1. Programmatic ``cfg.set_main_option("sqlalchemy.url", ...)`` — e.g.
#      from tests — is authoritative and always wins.
#   2. ``REGISTRY_STATE_DB_URL`` env var — for production / operator override.
#   3. ``_DEFAULT_URL`` (production filesystem path) — final fallback.
#
# The alembic.ini placeholder (``:memory:``) signals "not yet set" and is
# replaced by option 2 or 3. A non-placeholder URL in the config means the
# caller (programmatic test code) already decided; we leave it alone.
# ---------------------------------------------------------------------------
_ALEMBIC_INI_PLACEHOLDER = "sqlite+aiosqlite:///:memory:"
_DEFAULT_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"

_current_url = config.get_main_option("sqlalchemy.url")
if _current_url == _ALEMBIC_INI_PLACEHOLDER or _current_url is None:
    _env_url = os.environ.get("REGISTRY_STATE_DB_URL")
    config.set_main_option("sqlalchemy.url", _env_url or _DEFAULT_URL)


def do_run_migrations(connection: Connection) -> None:
    """Synchronous inner function passed to ``connection.run_sync``."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live (async) DB connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
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
