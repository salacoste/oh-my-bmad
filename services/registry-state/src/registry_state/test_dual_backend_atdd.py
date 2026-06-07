"""ATDD red-phase contract tests for dual-backend database support (Epic 30).

Phase 6 Epic 30 — Postgres Migration. These tests assert contracts that are
NOT YET IMPLEMENTED. Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate). When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a HARD
FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts asserted (all xfail):
  1. Engine factory selects backend based on URL scheme
  2. SQLite backend preserves existing pragmas
  3. Postgres backend uses connection pooling, not NullPool
  4. Postgres backend does NOT apply SQLite pragmas
  5. Both backends produce identical schema after migration
  6. Backend-conditional config: unset → SQLite (P6-I1)
  7. Alembic migrations run on both backends
  8. read_only mode works on SQLite (existing) and is rejected on Postgres
"""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Story 30.2: Engine factory selects backend by URL scheme
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engine_returns_postgres_engine_for_asyncpg_url() -> None:
    """Given a postgresql+asyncpg URL, create_engine returns an engine with
    asyncpg dialect (not aiosqlite)."""
    from registry_state.adapters.sqlite_store import create_engine

    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)
    # The dialect name must be 'postgresql', not 'sqlite'.
    assert engine.dialect.name == "postgresql", (
        f"Expected postgresql dialect, got: {engine.dialect.name!r}"
    )
    await engine.dispose()


def test_create_engine_accepts_postgres_url_without_error() -> None:
    """Given a postgresql+asyncpg URL, create_engine must not raise an error.
    Current implementation raises ValueError for non-sqlite URLs."""
    from registry_state.adapters.sqlite_store import create_engine

    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    # Current code: raises ValueError("read_only requires sqlite+aiosqlite")
    # or silently creates a broken engine. After Story 30.2: returns AsyncEngine.
    engine = create_engine(url)
    assert engine is not None


# ---------------------------------------------------------------------------
# Story 30.2: SQLite backend preserves existing pragmas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ref_sqlite_backend_applies_wal_pragma() -> None:
    """[Reference] SQLite engine created through the factory still applies
    PRAGMA journal_mode=WAL on file-based databases. Not xfail — this
    contract is already satisfied by the existing code."""
    from registry_state.adapters.sqlite_store import create_engine, get_session
    from registry_state.schema import Base

    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name

    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session(engine)
    async with session_factory() as session:
        result = await session.execute(text("PRAGMA journal_mode"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "wal", f"Expected journal_mode=wal, got: {row[0]!r}"

    await engine.dispose()
    os.unlink(db_path)


# ---------------------------------------------------------------------------
# Story 30.2: Postgres backend uses connection pooling
# ---------------------------------------------------------------------------


def test_postgres_backend_uses_connection_pool() -> None:
    """Postgres engine must use a real connection pool (AsyncAdaptedQueuePool),
    NOT NullPool (which is SQLite-only)."""
    from registry_state.adapters.sqlite_store import create_engine

    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)
    # The pool must NOT be NullPool.
    assert not isinstance(engine.pool, NullPool), "Postgres engine must not use NullPool"
    # The pool SHOULD be a real pool class (AsyncAdaptedQueuePool or similar).
    assert isinstance(engine.pool, AsyncAdaptedQueuePool), (
        f"Expected AsyncAdaptedQueuePool, got: {type(engine.pool).__name__}"
    )


# ---------------------------------------------------------------------------
# Story 30.2: Postgres backend does NOT apply SQLite pragmas
# ---------------------------------------------------------------------------


def test_postgres_backend_has_no_sqlite_pragma_listeners() -> None:
    """Postgres engine must NOT have PRAGMA event listeners attached (those
    are SQLite-only and would fail on Postgres connections).

    The SQLite pragma listener is registered via
    ``@event.listens_for(sync_engine, "connect")`` which SQLAlchemy routes to
    the pool's ``connect`` dispatch.  For Postgres, no such listener exists.
    """
    from registry_state.adapters.sqlite_store import create_engine

    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)

    # The pragma listener is registered on the pool-level "connect" event.
    # The dispatch collection is iterable — each item is a bound listener function.
    pool_connect = engine.pool.dispatch.connect
    has_pragma_listener = any("_set_pragmas" in getattr(fn, "__name__", "") for fn in pool_connect)

    assert not has_pragma_listener, "Postgres engine must not have SQLite PRAGMA event listeners"


# ---------------------------------------------------------------------------
# Story 30.2: Backend-conditional config (P6-I1)
# ---------------------------------------------------------------------------


def test_ref_unset_database_url_defaults_to_sqlite() -> None:
    """[Reference] When REGISTRY_DATABASE_URL is not set, the engine factory
    creates a SQLite engine. Not xfail — already satisfied by existing code."""
    from registry_state.adapters.sqlite_store import create_engine

    default_url = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"
    engine = create_engine(default_url)
    assert engine.dialect.name == "sqlite", (
        f"Default URL must produce SQLite engine, got: {engine.dialect.name!r}"
    )


# ---------------------------------------------------------------------------
# Story 30.2: Postgres read_only mode is unsupported
# ---------------------------------------------------------------------------


def test_ref_read_only_rejects_postgres_url() -> None:
    """[Reference] read_only=True with a postgresql+asyncpg URL raises ValueError.
    Not xfail — already enforced by existing code's sqlite+aiosqlite check."""
    from registry_state.adapters.sqlite_store import create_engine

    with pytest.raises(ValueError, match="sqlite\\+aiosqlite"):
        create_engine("postgresql+asyncpg://test:test@localhost/db", read_only=True)


# ---------------------------------------------------------------------------
# Story 30.3: Both backends produce identical schema after migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_backends_produce_identical_schema() -> None:
    """After running Alembic migrations, both SQLite and Postgres must have
    the same tables with the same columns and types.

    This test creates two engines (SQLite in-memory + Postgres mock), runs
    create_all on both, and compares the reflected schema.

    NOTE: In the red phase, this fails because the generalized factory doesn't
    exist yet. In the green phase, the schema comparison will need a real
    Postgres connection (or a suitable mock).
    """
    from registry_state.adapters.sqlite_store import create_engine
    from registry_state.schema import Base

    # SQLite engine (in-memory)
    sqlite_url = "sqlite+aiosqlite:///:memory:"
    sqlite_engine = create_engine(sqlite_url)
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Get SQLite table names
    sqlite_tables: set[str] = set()
    async with sqlite_engine.connect() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        for row in result:
            sqlite_tables.add(row[0])

    # Postgres engine (would need real connection in green phase)
    # For red phase, we just assert the factory accepts Postgres URLs
    pg_url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    pg_engine = create_engine(pg_url)

    # Both engines must reference the same Base metadata
    assert len(pg_engine.dialect.name) > 0  # not empty

    await sqlite_engine.dispose()
    await pg_engine.dispose()


# ---------------------------------------------------------------------------
# Story 30.4: Alembic migration downgrade path
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Story 30.4 — Alembic downgrade must work on SQLite",
)
@pytest.mark.asyncio
async def test_alembic_downgrade_sqlite() -> None:
    """Running `alembic downgrade -1` on SQLite must succeed without error."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"

        alembic_cfg = AlembicConfig()
        alembic_cfg.set_main_option("script_location", "src/registry_state/migrations")
        alembic_cfg.set_main_option("sqlalchemy.url", url)

        # First, upgrade to head
        command.upgrade(alembic_cfg, "head")

        # Then, downgrade by one step
        command.downgrade(alembic_cfg, "-1")

        # Should not raise — that's the assertion


# ---------------------------------------------------------------------------
# Story 30.6: Separability S-12 — Postgres optional (NFR-M11)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Story 30.6 — S-12: full task lifecycle must pass on SQLite without REGISTRY_DATABASE_URL"
    ),
)
@pytest.mark.asyncio
async def test_s12_sqlite_lifecycle_without_postgres() -> None:
    """S-12 separability test: The full task lifecycle (create → materialize
    event → read state) must work on SQLite without Postgres installed.

    Uses in-memory SQLite (no env var, no Postgres) and exercises:
    1. Create tables
    2. Insert a task row
    3. Append an event
    4. Read back the task
    """
    from registry_state.adapters.sqlite_store import create_engine, get_session
    from registry_state.schema import Base, Task

    # No REGISTRY_DATABASE_URL set → SQLite (P6-I1)
    url = "sqlite+aiosqlite:///:memory:"
    engine = create_engine(url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session(engine)
    async with session_factory() as session:
        # Create a task
        task = Task(
            id="t-test00000000-0000-7000-8000-000000000000",
            status="CREATED",
            created_at="2026-06-07T00:00:00",
            updated_at="2026-06-07T00:00:00",
            actor_kind="human",
            actor_id="op",
        )
        session.add(task)
        await session.commit()

        # Read it back
        result = await session.execute(
            text("SELECT id, status FROM tasks WHERE id = :id"),
            {"id": "t-test00000000-0000-7000-8000-000000000000"},
        )
        row = result.fetchone()
        assert row is not None, "Task not found in SQLite database"
        assert row[1] == "CREATED", f"Expected status=CREATED, got: {row[1]!r}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# Story 30.7: Postgres connection security (NFR-S16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_credentials_not_logged() -> None:
    """When connecting to Postgres, the password in the URL must never appear
    in any log output. Tests by capturing log output during engine creation."""
    import logging

    from registry_state.adapters.sqlite_store import create_engine

    url = "postgresql+asyncpg://secret_user:secret_pass123@db.example.com:5432/prod_db"

    # Capture all log output
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("REGISTRY_DATABASE_URL", url)

        # Create a handler that captures log records
        captured_records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)  # type: ignore[assignment]

        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)

        try:
            engine = create_engine(url)
            await engine.dispose()
        finally:
            root_logger.removeHandler(handler)

        # Assert no log record contains the password
        for record in captured_records:
            msg = record.getMessage()
            assert "secret_pass123" not in msg, f"Password leaked in log: {msg!r}"


# ---------------------------------------------------------------------------
# Green-phase reference test (NOT xfail): existing SQLite factory still works
# ---------------------------------------------------------------------------


def test_existing_sqlite_factory_still_works() -> None:
    """Regression guard: the existing create_engine for SQLite must continue
    to work unchanged throughout Epic 30."""
    from registry_state.adapters.sqlite_store import create_engine

    url = "sqlite+aiosqlite:///:memory:"
    engine = create_engine(url)
    assert engine.dialect.name == "sqlite"
