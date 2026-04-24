"""Alembic migration tests for registry-state (Story 2.3 AC-6, AC-7).

These tests run ``alembic upgrade head`` programmatically against an in-memory
SQLite DB and verify:
  - AC-6: All 5 tables + 6 indexes + alembic_version table are created.
  - AC-7: Running ``upgrade head`` twice is idempotent — no re-application of DDL,
          alembic_version unchanged.

Invocation pattern: ``command.upgrade(cfg, "head")`` where cfg has the URL
set programmatically. env.py detects that the URL is already set (not overriding
it with the env var or default) so the programmatic URL is used.

Note: ``asyncio.run()`` inside env.py (called from alembic's ``script.run_env()``)
creates a fresh event loop, which means this test does NOT need to be async —
Alembic drives the async machinery internally via ``asyncio.run()``.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

# Expected tables and indexes defined by the initial migration (revision 0001).
_EXPECTED_TABLES = frozenset(
    ["tasks", "sessions", "events", "idempotency_cache", "snapshots", "alembic_version"]
)
_EXPECTED_INDEXES = frozenset(
    [
        "ix_events_task_id_emitted_at",
        "ix_events_session_id_emitted_at",
        "ix_events_type_emitted_at",
        "ix_sessions_task_id",
        "ix_idempotency_cache_expires_at",
        "ix_tasks_status_updated_at",
    ]
)
_REVISION = "0001"
_INI_PATH = str(Path(__file__).parent.parent.parent / "alembic.ini")


def _make_cfg(url: str) -> Config:
    """Return an Alembic config with the given SQLite URL."""
    cfg = Config(_INI_PATH)
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _run_upgrade(url: str) -> None:
    """Run ``alembic upgrade head`` against *url*."""
    command.upgrade(_make_cfg(url), "head")


def _inspect_db(path: str) -> tuple[frozenset[str], frozenset[str], list[str]]:
    """Return (tables, indexes, alembic_version_rows) from the SQLite file at *path*."""
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        # Tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = frozenset(row[0] for row in cur.fetchall())
        # Indexes
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = frozenset(
            row[0] for row in cur.fetchall() if not row[0].startswith("sqlite_autoindex_")
        )
        # alembic_version rows
        try:
            cur.execute("SELECT version_num FROM alembic_version")
            versions = [row[0] for row in cur.fetchall()]
        except sqlite3.OperationalError:
            versions = []
    finally:
        conn.close()
    return tables, indexes, versions


def test_upgrade_head_on_empty_db_creates_all_tables_and_indexes() -> None:
    """AC-6: upgrade head on empty DB creates 5 app tables + alembic_version + 6 indexes."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name

    url = f"sqlite+aiosqlite:///{db_path}"
    _run_upgrade(url)

    tables, indexes, versions = _inspect_db(db_path)

    # All 5 application tables + alembic_version must exist.
    missing_tables = _EXPECTED_TABLES - tables
    assert not missing_tables, f"Missing tables after upgrade head: {missing_tables}"

    # All 6 indexes must exist.
    missing_indexes = _EXPECTED_INDEXES - indexes
    assert not missing_indexes, f"Missing indexes after upgrade head: {missing_indexes}"

    # alembic_version must contain exactly one row with the initial revision id.
    assert len(versions) == 1, f"Expected 1 alembic_version row, got: {versions}"
    assert versions[0] == _REVISION, f"Expected revision {_REVISION!r}, got: {versions[0]!r}"


def test_upgrade_head_twice_is_noop() -> None:
    """AC-7: running upgrade head twice is idempotent — no DDL re-applied."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name

    url = f"sqlite+aiosqlite:///{db_path}"

    # First upgrade.
    _run_upgrade(url)
    tables_1, indexes_1, versions_1 = _inspect_db(db_path)

    # Second upgrade — must be a no-op.
    _run_upgrade(url)
    tables_2, indexes_2, versions_2 = _inspect_db(db_path)

    assert tables_1 == tables_2, "Tables changed between two upgrade head calls"
    assert indexes_1 == indexes_2, "Indexes changed between two upgrade head calls"
    assert versions_1 == versions_2, "alembic_version changed between two upgrade head calls"
    assert versions_2 == [_REVISION], f"Unexpected revision after second upgrade: {versions_2}"
