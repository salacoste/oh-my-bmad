"""NFR tests for Postgres engine configuration and credential security (Epic 30).

Story 30-7: Performance validation (NFR-O14) and connection security (NFR-S16).

All tests are synchronous and require no running database. They inspect engine
configuration by examining the engine object returned by the factory.

Contracts asserted:
  1. Postgres pool size = 5 + 2 * worker_count (default 1 → 7)
  2. Postgres pool has pool_pre_ping=True
  3. Postgres URL string representation redacts the password
  4. SQLite still uses NullPool
  5. Postgres engine has no check_same_thread in connect_args
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from registry_state.adapters.sqlite_store import create_engine, is_postgres_url


def test_postgres_pool_config_size() -> None:
    """NFR-O14: Postgres pool_size = 5 + 2 * worker_count (default 1 → 7)."""
    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)
    assert engine.pool.status().startswith("Pool"), f"Expected pool, got: {engine.pool}"
    # SQLAlchemy stores pool_size on the AsyncAdaptedQueuePool instance.
    assert isinstance(engine.pool, AsyncAdaptedQueuePool)
    # pool_size is stored as _pool_size on the underlying pool; check via status
    # or by inspecting the creator kwargs. The factory computes 5 + 2*1 = 7.
    assert engine.pool._pool.maxsize == 7, (
        f"Expected pool maxsize=7 (5 + 2*1), got: {engine.pool._pool.maxsize}"
    )


def test_postgres_pool_has_pre_ping() -> None:
    """NFR-O14: Postgres engine must have pool_pre_ping=True for stale-connection detection."""
    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)
    # pre_ping is stored as _pre_ping on the sync engine's pool.
    assert engine.sync_engine.pool._pre_ping is True, "pool_pre_ping must be True for Postgres"


def test_postgres_url_password_not_in_str() -> None:
    """NFR-S16: Converting the engine URL to string must redact the password."""
    url = "postgresql+asyncpg://myuser:secret_password@db.example.com:5432/mydb"
    engine = create_engine(url)
    url_str = str(engine.url)
    assert "secret_password" not in url_str, (
        f"Password leaked in URL string representation: {url_str!r}"
    )
    # The URL should still contain the host and database name.
    assert "db.example.com" in url_str, f"Host missing from URL: {url_str!r}"
    assert "mydb" in url_str, f"Database name missing from URL: {url_str!r}"


def test_sqlite_pool_is_null_pool() -> None:
    """SQLite engine must use NullPool (no connection pooling for file-based DBs)."""
    url = "sqlite+aiosqlite:///:memory:"
    engine = create_engine(url)
    assert isinstance(engine.pool, NullPool), (
        f"SQLite engine must use NullPool, got: {type(engine.pool).__name__}"
    )


def test_postgres_engine_no_check_same_thread() -> None:
    """Postgres engine must NOT have check_same_thread in connect_args (SQLite-only)."""
    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url)
    # SQLAlchemy bakes connect_args into the creator function's closure under
    # the ``cparams`` freevar. Inspect the closure to verify check_same_thread
    # is absent (it is a sqlite3-specific parameter).
    creator = engine.pool._creator_arg
    freevars = creator.__code__.co_freevars  # type: ignore[union-attr]
    cparams = dict(
        zip(freevars, (cell.cell_contents for cell in creator.__closure__), strict=True)  # type: ignore[union-attr]
    ).get("cparams", {})
    assert "check_same_thread" not in cparams, (
        f"Postgres engine must not have check_same_thread in connect_args, got: {cparams}"
    )


def test_postgres_pool_has_bounded_defaults_for_worker_count() -> None:
    """Story 132.2: Postgres pool has explicit bounded production defaults."""
    url = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    engine = create_engine(url, worker_count=3)

    assert isinstance(engine.pool, AsyncAdaptedQueuePool)
    assert engine.pool._pool.maxsize == 11  # 5 + 2 * 3
    assert engine.sync_engine.pool._max_overflow == 5
    assert engine.sync_engine.pool._timeout == 30
    assert engine.sync_engine.pool._recycle == 1800
    assert engine.sync_engine.pool._pre_ping is True


@pytest.mark.parametrize("bad_worker_count", [True, False, 0, -1, 129, 1.5, "2", None])
def test_postgres_rejects_unsafe_worker_count_without_secret_leak(
    bad_worker_count: object,
) -> None:
    """Invalid worker counts fail closed without echoing DSNs or credentials."""
    url = "postgresql+asyncpg://user:super-secret-password@db.example.com:5432/registry"

    with pytest.raises(ValueError) as excinfo:
        create_engine(url, worker_count=bad_worker_count)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "worker_count" in message
    for forbidden in (url, "super-secret-password", "db.example.com", "registry"):
        assert forbidden not in message


def test_is_postgres_url_recognizes_only_supported_asyncpg_scheme() -> None:
    assert is_postgres_url("postgresql+asyncpg://user:pass@host/db") is True
    assert is_postgres_url("POSTGRESQL+ASYNCPG://user:pass@host/db") is True
    assert is_postgres_url("postgresql://user:pass@host/db") is False
    assert is_postgres_url("sqlite+aiosqlite:////tmp/state.sqlite3") is False
