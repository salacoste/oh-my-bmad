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
- ``read_only=True`` rewrites the URL to SQLite URI mode: the database part
  gets a ``file:`` prefix and the query string carries ``uri=true&mode=ro``.
  The pysqlite/aiosqlite dialect recognises ``uri=true`` in the URL query,
  promotes it to the sqlite3 ``uri=True`` kwarg, and passes the remaining
  ``mode=ro`` through as the SQLite-URI query — so the OS rejects writes at
  the connection level (belt-and-braces with the single-writer CI check).
  The pre-fix implementation only appended ``&mode=ro&uri=true`` to the URL
  without the ``file:`` prefix; the dialect then ran ``os.path.abspath`` on
  the database path and sqlite3 silently opened a phantom R/W file — a
  false-pass for the read-only test.
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
        read_only: When ``True``, rewrite the URL to SQLite URI mode
            (``file:<path>?uri=true&mode=ro``) so writes are rejected at the
            OS level. The pysqlite/aiosqlite dialect reads ``uri=true`` out
            of the query string and promotes it to the sqlite3 ``uri=True``
            kwarg. Rejects in-memory URLs because read-only + in-memory is
            nonsensical.

    Returns:
        Fully configured ``AsyncEngine`` with WAL + FK pragmas wired.

    Raises:
        ValueError: If ``read_only=True`` with a non-``sqlite+aiosqlite://``
            URL or with an in-memory path.
    """
    connect_args: dict[str, Any] = {"check_same_thread": False}

    if read_only:
        # SQLite URI mode: the database component must carry a `file:`
        # prefix (else the dialect runs os.path.abspath on it and sqlite3
        # opens it as a plain filename). The `uri=true` flag goes in the
        # URL query string — the dialect promotes it to a sqlite3 kwarg.
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            raise ValueError(f"read_only=True requires a sqlite+aiosqlite URL; got {url!r}")
        path_part = url[len(prefix) :]
        if path_part == ":memory:" or path_part.startswith(":memory:"):
            raise ValueError("read_only=True is incompatible with in-memory SQLite")
        # Strip any pre-existing query string; we own the full query here.
        path, _, _ = path_part.partition("?")
        url = f"{prefix}file:{path}?uri=true&mode=ro"

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args=connect_args,
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
