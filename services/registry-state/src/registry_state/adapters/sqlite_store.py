"""Async SQLite engine factory for the registry-state service (Story 2.3).

Story 2.3 delivers schema + engine factory only. Session management,
materializer, writer, snapshotter arrive in Stories 2.4–2.7.

Design decisions:
- ``NullPool``: SQLite doesn't benefit from connection pooling for local
  file-based DBs; eliminates "database is locked" surprises.
- ``check_same_thread=False``: async bridge to sqlite3 may swap threads;
  safe because single-writer CI gate + AsyncSession discipline prevent races.
- Pragmas applied via ``event.listens_for(engine.sync_engine, "connect")``:
  ``PRAGMA foreign_keys=ON`` is per-connection and OFF by default in SQLite —
  it MUST be applied on every connection open, not at engine creation time.
- ``read_only=True`` rewrites the URL to SQLite URI mode (``?mode=ro&uri=true``)
  so the OS rejects writes at the connection level — belt-and-braces with the
  single-writer CI check.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_engine(url: str, *, read_only: bool = False) -> AsyncEngine:
    """Return an ``AsyncEngine`` configured for the registry-state SQLite store.

    Args:
        url: SQLAlchemy async URL, e.g. ``sqlite+aiosqlite:///path/to/state.sqlite3``
             or ``sqlite+aiosqlite:///:memory:``.
        read_only: When ``True``, rewrite the URL to SQLite URI mode with
            ``mode=ro`` so writes are rejected at the OS level.

    Returns:
        Fully configured ``AsyncEngine`` with WAL + FK pragmas wired.
    """
    if read_only:
        # SQLite URI-mode open. The aiosqlite dialect passes connect_args
        # through to the underlying sqlite3 module.
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}mode=ro&uri=true"

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def get_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an ``async_sessionmaker`` bound to *engine*.

    Usage::

        Session = get_session(engine)
        async with Session() as session:
            session.add(...)
            await session.commit()

    ``expire_on_commit=False`` avoids lazy-load failures after commit when
    accessing model attributes without a live session.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


__all__ = ["create_engine", "get_session"]
