"""Tests for the async SQLite engine factory (Story 2.3 AC-8, AC-9).

AC-8: Pragmas verified at runtime — journal_mode, synchronous, foreign_keys,
      busy_timeout are all applied via the connect event listener.
AC-9: FK enforcement smoke test — inserting an orphan session row raises
      IntegrityError (proves PRAGMA foreign_keys=ON is live).

Additional tests:
- Read-only URL rewrite: create_engine(url, read_only=True) rewrites URL.
- Read-only engine rejects writes: OperationalError on INSERT.

Notes on in-memory vs file:
- journal_mode=WAL is a file-based journal mode — SQLite ignores the PRAGMA on
  :memory: DBs (returns 'memory'). The WAL pragma test uses a real temp file.
- All other pragma tests use in-memory StaticPool (faster, isolated).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator
from random import Random
from typing import Any

import pytest
import pytest_asyncio
from events.clock import FROZEN_EPOCH, FrozenClock
from events.ids import new_session_id
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from registry_state.adapters.sqlite_store import create_engine, get_session
from registry_state.schema import Base, Session

_INI_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Helper: query a PRAGMA value via raw async connection
# ---------------------------------------------------------------------------


async def _pragma(session: AsyncSession, name: str) -> str:
    """Return the string value of a PRAGMA via the session's connection."""
    result = await session.execute(text(f"PRAGMA {name}"))
    row = result.fetchone()
    assert row is not None
    return str(row[0])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pragma_listener(engine: Any) -> None:
    """Attach the 4-pragma listener to a sync engine (mirrors sqlite_store.py)."""

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


@pytest_asyncio.fixture
async def mem_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory StaticPool engine with tables + pragmas (except WAL, which
    is file-only; foreign_keys / synchronous / busy_timeout still apply).
    """
    test_engine = create_async_engine(
        _INI_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _make_pragma_listener(test_engine)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session(test_engine)
    async with session_factory() as sess:
        yield sess

    await test_engine.dispose()


@pytest_asyncio.fixture
async def file_session() -> AsyncGenerator[AsyncSession, None]:
    """File-based engine (temp file) with all pragmas including WAL.

    WAL mode is a file-based journal mode — it cannot be enabled on :memory:.
    This fixture provides a real SQLite file so PRAGMA journal_mode=WAL works.
    """
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name

    url = f"sqlite+aiosqlite:///{db_path}"
    test_engine = create_engine(url)  # uses sqlite_store's create_engine with all pragmas

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session(test_engine)
    async with session_factory() as sess:
        yield sess

    await test_engine.dispose()
    os.unlink(db_path)


# ---------------------------------------------------------------------------
# AC-8: Pragma tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wal_mode_applied(file_session: AsyncSession) -> None:
    """PRAGMA journal_mode == 'wal' after engine creation (file-based DB).

    WAL is a file-based journal mode — in-memory DBs return 'memory' regardless.
    """
    value = await _pragma(file_session, "journal_mode")
    assert value == "wal", f"Expected journal_mode=wal, got: {value!r}"


@pytest.mark.asyncio
async def test_synchronous_normal_applied(mem_session: AsyncSession) -> None:
    """PRAGMA synchronous == 1 (NORMAL) after engine creation."""
    value = await _pragma(mem_session, "synchronous")
    assert value == "1", f"Expected synchronous=1 (NORMAL), got: {value!r}"


@pytest.mark.asyncio
async def test_foreign_keys_on_applied(mem_session: AsyncSession) -> None:
    """PRAGMA foreign_keys == 1 (ON) after engine creation."""
    value = await _pragma(mem_session, "foreign_keys")
    assert value == "1", f"Expected foreign_keys=1 (ON), got: {value!r}"


@pytest.mark.asyncio
async def test_busy_timeout_applied(mem_session: AsyncSession) -> None:
    """PRAGMA busy_timeout == 5000 after engine creation."""
    value = await _pragma(mem_session, "busy_timeout")
    assert value == "5000", f"Expected busy_timeout=5000, got: {value!r}"


# ---------------------------------------------------------------------------
# AC-9: FK enforcement smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_key_violation_raises(mem_session: AsyncSession) -> None:
    """Inserting a session with a non-existent task_id raises IntegrityError.

    This proves PRAGMA foreign_keys=ON is actually enforced — SQLite silently
    ignores FK constraints when the pragma is off.
    """
    c = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    r = Random(42)
    orphan_session = Session(
        id=new_session_id(clock=c, rng=r),
        task_id="t-00000000-0000-7000-8000-000000000000",  # non-existent task
        worker_kind="claude-code",
        status="running",
        started_at=FROZEN_EPOCH,
    )
    mem_session.add(orphan_session)
    with pytest.raises(IntegrityError):
        await mem_session.flush()


# ---------------------------------------------------------------------------
# Additional engine factory tests
# ---------------------------------------------------------------------------


def test_read_only_url_rewrite_adds_mode_ro_and_file_prefix() -> None:
    """create_engine(url, read_only=True) rewrites to ``file:<path>?mode=ro``.

    The F2 fix converts ``sqlite+aiosqlite:///path/to/db`` into
    ``sqlite+aiosqlite:///file:/path/to/db?mode=ro`` — ``file:`` prefix and
    ``mode=ro`` are both required for SQLite URI mode to reject writes.
    """
    base_url = "sqlite+aiosqlite:///path/to/state.sqlite3"
    engine = create_engine(base_url, read_only=True)
    url_str = str(engine.url)
    assert "mode=ro" in url_str, f"mode=ro missing from URL: {url_str!r}"
    assert "file:" in url_str, f"file: prefix missing from URL: {url_str!r}"
    # NullPool — no persistent connections; no dispose needed.


def test_read_only_sets_uri_connect_arg() -> None:
    """The engine's connect_args must include ``uri=True`` — aiosqlite/sqlite3
    ignores ``uri=true`` as a URL query parameter; it must be a kwarg.
    """
    base_url = "sqlite+aiosqlite:///path/to/state.sqlite3"
    engine = create_engine(base_url, read_only=True)
    # create_connect_args returns ([posargs], {kwargs}) for the DB-API call.
    _args, kwargs = engine.dialect.create_connect_args(engine.url)
    assert kwargs.get("uri") is True, f"Expected uri=True in connect_args, got: {kwargs!r}"


def test_read_only_rejects_memory_url() -> None:
    """``read_only=True`` + ``:memory:`` is nonsensical — raises ValueError."""
    with pytest.raises(ValueError, match="in-memory"):
        create_engine("sqlite+aiosqlite:///:memory:", read_only=True)


def test_read_only_rejects_non_aiosqlite_url() -> None:
    """``read_only=True`` requires a ``sqlite+aiosqlite://`` URL."""
    with pytest.raises(ValueError, match="sqlite\\+aiosqlite"):
        create_engine("postgresql+asyncpg://x/y", read_only=True)


def test_read_only_strips_preexisting_query_string() -> None:
    """Pre-existing query params are stripped; mode=ro is authoritative.

    The F2 rewrite owns the query string entirely — a pre-existing
    ``?timeout=5`` in the URL is dropped so we don't end up with conflicting
    or surplus params. Final URL carries exactly one ``?`` separator.
    """
    base_url = "sqlite+aiosqlite:///path/to/db.sqlite3?timeout=5"
    engine = create_engine(base_url, read_only=True)
    url_str = str(engine.url)
    assert "timeout" not in url_str, f"timeout param not stripped: {url_str!r}"
    assert "mode=ro" in url_str
    assert url_str.count("?") == 1, f"Multiple '?' in URL: {url_str!r}"


@pytest.mark.asyncio
async def test_read_only_engine_rejects_writes() -> None:
    """A read-only engine raises OperationalError on INSERT against a REAL file.

    Flow:
      1. Create a real SQLite file via writable engine + ``Base.metadata.create_all``.
      2. Open the SAME file read-only.
      3. Attempt an INSERT — must raise OperationalError mentioning "readonly".

    Previously the test relied on the broken URL-rewrite (``uri=true`` in the
    URL), which aiosqlite silently ignored — so the engine fell back to
    read/write and no error was raised on writes to a non-existent path
    (sqlite auto-created a phantom file). This version actually exercises
    the read-only path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"

        # First: create the tables using a writable engine.
        write_engine = create_engine(url)
        async with write_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await write_engine.dispose()

        # Now open read-only and attempt an INSERT.
        ro_engine = create_engine(url, read_only=True)
        try:
            async with ro_engine.connect() as conn:
                with pytest.raises(OperationalError, match="readonly|read-only"):
                    await conn.execute(
                        text(
                            "INSERT INTO tasks (id, status, created_at, updated_at, "
                            "actor_kind, actor_id) VALUES "
                            "('t-test', 'pending', '2026-01-01', '2026-01-01', "
                            "'human', 'op')"
                        )
                    )
        finally:
            await ro_engine.dispose()
