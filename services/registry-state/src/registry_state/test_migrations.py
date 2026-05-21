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

# Expected tables and indexes defined by the initial migration (revision 0001)
# plus subsequent additive migrations. ``approval_inbox`` added by migration
# 0007 (Story 11.3 / FR63); ``key_fingerprint`` added by migration 0008
# (Story 11.5 / FR65a — singleton row tracking the current HMAC signing-key
# fingerprint for rotation detection).
_EXPECTED_TABLES = frozenset(
    [
        "tasks",
        "sessions",
        "events",
        "idempotency_cache",
        "snapshots",
        "approval_inbox",
        "key_fingerprint",
        "alembic_version",
    ]
)
_EXPECTED_INDEXES = frozenset(
    [
        "ix_events_task_id_emitted_at",
        "ix_events_session_id_emitted_at",
        "ix_events_type_emitted_at",
        "ix_events_trace_id",  # Story 9.7 / AC4
        "ix_sessions_task_id_status",
        "ix_idempotency_cache_expires_at",
        "ix_tasks_status_updated_at",
    ]
)
# Tracks the latest alembic head revision. Bump when a new migration is added.
# History: 0001 initial → 0002 task thread binding → 0003 session compound index
# → 0004 add task state columns (Story 8.x) → 0005 add event trace_id (Story 9.7)
# → 0006 add event trace_id_synthetic_source + extensions (Story 9.8 D6+D7)
# → 0007 add approval_inbox table (Story 11.3 AC2 / FR63)
# → 0008 add key_fingerprint table (Story 11.5 AC2 / FR65a).
_REVISION = "0008"
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


def _sqlite_master_snapshot(path: str) -> list[tuple[str, str, str]]:
    """Return sorted ``(type, name, sql)`` tuples from ``sqlite_master``.

    Used by AC-7 to prove a second ``upgrade head`` is byte-identical — not
    just same-names, but same DDL (CREATE TABLE/INDEX SQL text).
    """
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
        )
        return sorted((t, n, s) for t, n, s in cur.fetchall())
    finally:
        conn.close()


def test_upgrade_head_on_empty_db_creates_all_tables_and_indexes() -> None:
    """AC-6: upgrade head on empty DB creates 6 app tables + alembic_version + 6 indexes.

    Story 11.3 (FR63) added ``approval_inbox`` via migration 0007 so the
    expected table count moved from 5 → 6 application tables.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
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
    """AC-7: running upgrade head twice is idempotent — sqlite_master byte-identical."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"

        # First upgrade.
        _run_upgrade(url)
        tables_1, indexes_1, versions_1 = _inspect_db(db_path)
        snapshot_1 = _sqlite_master_snapshot(db_path)

        # Second upgrade — must be a no-op.
        _run_upgrade(url)
        tables_2, indexes_2, versions_2 = _inspect_db(db_path)
        snapshot_2 = _sqlite_master_snapshot(db_path)

        assert tables_1 == tables_2, "Tables changed between two upgrade head calls"
        assert indexes_1 == indexes_2, "Indexes changed between two upgrade head calls"
        assert versions_1 == versions_2, "alembic_version changed between two upgrade head calls"
        assert versions_2 == [_REVISION], f"Unexpected revision after second upgrade: {versions_2}"
        # Byte-identical DDL: compares (type, name, sql) tuples.
        assert snapshot_1 == snapshot_2, "sqlite_master changed between two upgrade head calls"


def test_migration_0006_adds_synthetic_source_and_extensions_columns() -> None:
    """Story 9.8 D6+D7: migration 0006 adds two nullable columns to ``events``.

    Smoke test: upgrade head on an empty DB and assert both new columns
    exist with the expected (nullable, no-default) shape. Existing rows
    (none here) would carry NULL for both per the additive-migration
    contract.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"
        _run_upgrade(url)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(events)")
            cols = {row[1]: row for row in cur.fetchall()}
        finally:
            conn.close()

        # Both columns must exist.
        assert "trace_id_synthetic_source" in cols, (
            "migration 0006 must add events.trace_id_synthetic_source"
        )
        assert "extensions" in cols, "migration 0006 must add events.extensions"
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk).
        # Both columns must be nullable (notnull == 0).
        assert cols["trace_id_synthetic_source"][3] == 0, (
            "events.trace_id_synthetic_source must be nullable"
        )
        assert cols["extensions"][3] == 0, "events.extensions must be nullable"


def test_migration_0006_preserves_existing_rows_with_null() -> None:
    """Story 9.8 D6+D7: pre-9.8 rows survive migration 0006 with NULL on new cols.

    Drives the additive-migration contract: ADD COLUMN ... NULL on SQLite
    is metadata-only; existing rows keep their data and receive NULL on
    the new columns. We seed a row through the materializer-equivalent
    raw INSERT to exercise the column-default path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"
        _run_upgrade(url)

        # Seed a row directly via sqlite3 (post-0006 schema; new columns
        # absent from the INSERT — they must default to NULL).
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO tasks (id, status, created_at, updated_at, "
                "actor_kind, actor_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "t-00000000-0000-7000-8000-000000000001",
                    "blocked",
                    "2026-05-19T00:00:00.000",
                    "2026-05-19T00:00:00.000",
                    "operator",
                    "test-op",
                ),
            )
            conn.execute(
                "INSERT INTO events (id, type, schema_version, emitted_at, "
                "emitted_at_monotonic_ns, actor_kind, actor_id, task_id, "
                "request_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "e-00000000-0000-7000-8000-000000000001",
                    "task.created",
                    "1.1.0",
                    "2026-05-19T00:00:00.000",
                    1,
                    "operator",
                    "test-op",
                    "t-00000000-0000-7000-8000-000000000001",
                    "00000000-0000-7000-8000-000000000001",
                    '{"k":"v"}',
                ),
            )
            conn.commit()

            cur = conn.execute("SELECT trace_id_synthetic_source, extensions FROM events")
            row = cur.fetchone()
        finally:
            conn.close()

        assert row == (None, None), "pre-9.8 row must have NULL on both new columns; got " + repr(
            row
        )


def test_migration_0007_adds_approval_inbox_table() -> None:
    """Story 11.3 / FR63: migration 0007 adds the ``approval_inbox`` table.

    Smoke test: upgrade head on an empty DB and assert the new table exists
    with the expected 4-column shape (operator_chat_id PK, inbox_thread_id,
    opened_at, opened_by_actor_id) all NOT NULL.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"
        _run_upgrade(url)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(approval_inbox)")
            cols = {row[1]: row for row in cur.fetchall()}
        finally:
            conn.close()

        # All 4 columns must exist.
        assert set(cols) == {
            "operator_chat_id",
            "inbox_thread_id",
            "opened_at",
            "opened_by_actor_id",
        }, f"unexpected approval_inbox columns: {set(cols)}"
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk).
        # All four columns must be NOT NULL (notnull == 1).
        for name, row in cols.items():
            assert row[3] == 1, f"approval_inbox.{name} must be NOT NULL"
        # operator_chat_id is the PRIMARY KEY (pk == 1).
        assert cols["operator_chat_id"][5] == 1, "operator_chat_id must be PRIMARY KEY"
        # No other column is a PK.
        for name in ("inbox_thread_id", "opened_at", "opened_by_actor_id"):
            assert cols[name][5] == 0, f"{name} must not be part of PRIMARY KEY"


def test_migration_0008_adds_key_fingerprint_table() -> None:
    """Story 11.5 / FR65a: migration 0008 adds the ``key_fingerprint`` table.

    Smoke test: upgrade head on an empty DB and assert the new table exists
    with the expected 4-column shape (id PK, fingerprint, rotated_at,
    rotated_by_actor_id) all NOT NULL.

    Story 11.5 PP6: the table is a singleton — the only valid ``id`` is
    the literal string ``"current"``. This is now enforced by a
    ``ck_key_fingerprint_singleton`` CHECK constraint at the schema
    layer (was convention-only pre-PP6). The constraint is verified by
    inspecting the SQLite CREATE TABLE DDL.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"
        _run_upgrade(url)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(key_fingerprint)")
            cols = {row[1]: row for row in cur.fetchall()}
            # PP6: assert the CHECK constraint exists.
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='key_fingerprint'"
            )
            ddl_row = cur.fetchone()
            ddl = ddl_row[0] if ddl_row else ""
        finally:
            conn.close()

        # All 4 columns must exist.
        assert set(cols) == {
            "id",
            "fingerprint",
            "rotated_at",
            "rotated_by_actor_id",
        }, f"unexpected key_fingerprint columns: {set(cols)}"
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk).
        # All four columns must be NOT NULL (notnull == 1).
        for name, row in cols.items():
            assert row[3] == 1, f"key_fingerprint.{name} must be NOT NULL"
        # id is the PRIMARY KEY (pk == 1).
        assert cols["id"][5] == 1, "key_fingerprint.id must be PRIMARY KEY"
        # No other column is a PK.
        for name in ("fingerprint", "rotated_at", "rotated_by_actor_id"):
            assert cols[name][5] == 0, f"key_fingerprint.{name} must not be part of PRIMARY KEY"
        # PP6: CHECK constraint must be present in the table DDL.
        assert "ck_key_fingerprint_singleton" in ddl, (
            "migration 0008 must declare ck_key_fingerprint_singleton CHECK constraint; "
            f"DDL: {ddl!r}"
        )
        assert "id = 'current'" in ddl, (
            f"ck_key_fingerprint_singleton must enforce id = 'current'; DDL: {ddl!r}"
        )


def test_migration_0008_round_trip_downgrade_drops_table() -> None:
    """Story 11.5 AC2: round-trip ``upgrade head`` → ``downgrade -1`` drops the table.

    Belt-and-braces verification that the migration's ``downgrade()`` is
    symmetric with ``upgrade()`` — a property all our migrations preserve
    so operators can roll back cleanly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "state.sqlite3")
        url = f"sqlite+aiosqlite:///{db_path}"
        _run_upgrade(url)

        # Confirm table exists post-upgrade.
        tables_pre, _, versions_pre = _inspect_db(db_path)
        assert "key_fingerprint" in tables_pre
        assert versions_pre == ["0008"]

        # Downgrade one revision.
        command.downgrade(_make_cfg(url), "-1")

        tables_post, _, versions_post = _inspect_db(db_path)
        assert "key_fingerprint" not in tables_post, "downgrade -1 must drop key_fingerprint table"
        assert versions_post == ["0007"], (
            f"alembic_version must roll back to 0007; got {versions_post}"
        )
