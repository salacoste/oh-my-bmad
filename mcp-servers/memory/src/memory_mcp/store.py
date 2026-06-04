"""SQLite FTS5-backed persistent knowledge store for memory-mcp (Epic 18 / ADR-0012 §6-§7).

A :class:`MemoryStore` owns a DEDICATED SQLite database (NEVER the registry DB;
ADR-0012 store-isolation invariant P3-I2) at the path injected by the
``__main__`` boundary (``MEMORY_MCP_STORE_PATH``). It is a persistent cross-task
knowledge store: ``upsert`` writes a document keyed by a stable string id,
``read`` fetches one by key, and ``search`` runs a ranked FTS5 full-text query
over the document bodies.

Why stdlib ``sqlite3`` + raw SQL (NOT SQLAlchemy) — ADR-0012 §6:
  1. FTS5 is a SQLite-native virtual-table feature, easiest to drive via raw SQL
     (``CREATE VIRTUAL TABLE ... USING fts5(...)`` + ``MATCH`` + ``bm25()``).
  2. It adds ZERO new dependencies — FTS5 is compiled into the stdlib ``sqlite3``
     module's bundled SQLite.
  3. It deliberately avoids ``scripts/check_single_writer.py``, which AST-flags
     SQLAlchemy ``session.add`` / ``execute(insert(...))`` patterns OUTSIDE
     registry-state. A raw ``conn.execute("INSERT ...")`` is not flagged.

Single-writer by construction: one process, one long-lived connection. The
caller (the memory tools in 18.3/18.4) serializes over the FastMCP request path.

Crash-safety (ADR-0012 §6): the connection runs in WAL mode
(``PRAGMA journal_mode=WAL``) so a crash mid-write leaves a recoverable log.

File-mode discipline (ADR-0012 §7) — DO NOT SKIP:
  The store dir is created group-writable + setgid (``0o2775``) and the DB file
  plus its ``-wal`` / ``-shm`` siblings are chmod'd group-writable (``0o660``).
  This defeats the default umask (022 → strips group-write) so a cross-uid
  recovery path (a DIFFERENT omb-group service uid that opens the same store on a
  shared volume) does not crash-loop on ``PermissionError`` — the systemic
  umask-022 bug that bit registry-state's event log + SQLite WAL repeatedly. The
  ``-wal`` / ``-shm`` files do not exist until the first write, so they are
  chmod'd defensively (``FileNotFoundError`` is ignored).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

# 0o2775 = setgid + rwxrwxr-x. Setgid (leading 2) propagates group ownership to
# new files; group-write (second 7) lets cross-uid omb-group services write into
# the dir. Mirrors ``events._filesystem.ensure_shared_dir`` (ADR-0012 §7).
_STORE_DIR_MODE: int = 0o2775

# 0o660 = rw-rw---- (owner+group read/write, NEVER world-readable). The DB may
# hold task contents, so others-triad stays 0. Group-WRITE defeats the umask so a
# cross-uid recoverer is not locked out. Mirrors registry-state's event-log file
# mode (Story 11.3.11) and the ADR-0012 §7 file-mode discipline.
_DB_FILE_MODE: int = 0o660


class MemoryStore:
    """Persistent FTS5-indexed document store over a dedicated SQLite database.

    One instance owns one long-lived ``sqlite3.Connection`` to ``store_path``.
    Construction is idempotent: the parent dir, the ``documents`` table, and the
    FTS5 virtual table are all created with ``IF NOT EXISTS`` semantics, so an
    existing store is reopened cleanly.

    Schema (ADR-0012 §6):
        ``documents``  — ``key`` (TEXT PRIMARY KEY), ``title`` (TEXT),
        ``body`` (TEXT), ``created_at`` / ``updated_at`` (TEXT, caller-supplied
        metadata; nullable). The canonical source of truth.

        ``documents_fts`` — an external-content FTS5 virtual table indexing
        ``key`` / ``title`` / ``body`` and mirrored to ``documents`` via
        triggers, so a ``MATCH`` query ranks documents by full-text relevance
        (``bm25()`` → ``ORDER BY rank``).
    """

    def __init__(self, store_path: Path) -> None:
        """Open (or create) the store at *store_path* and initialise the schema.

        Ensures the parent dir exists with mode ``0o2775`` (setgid +
        group-write), opens a WAL-mode connection, creates the ``documents``
        table + ``documents_fts`` virtual table + sync triggers (idempotent),
        and chmods the DB + ``-wal`` / ``-shm`` siblings to ``0o660``.

        Args:
            store_path: Absolute path to this store's DEDICATED SQLite DB file.
                NEVER the registry DB (ADR-0012 P3-I2). The path is used as-is;
                the ``__main__`` boundary is responsible for any normalisation.
        """
        self._store_path: Path = store_path

        # Parent dir: group-writable + setgid so a cross-uid omb-group service
        # can write into the store dir on a shared volume (ADR-0012 §7). chmod
        # is best-effort — a dir we don't own must not fail-loud (the mkdir
        # already succeeded).
        parent = store_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(parent, _STORE_DIR_MODE)

        # ``isolation_level=None`` → autocommit; we issue explicit transactions
        # via raw SQL. ``check_same_thread=False`` is NOT set: the store is
        # single-writer by construction (one process, one connection).
        self._conn: sqlite3.Connection = sqlite3.connect(str(store_path))
        self._conn.row_factory = sqlite3.Row

        # WAL mode for crash-safety (ADR-0012 §6). Set before any schema DDL so
        # the schema writes land in WAL too.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._init_schema()

        # File-mode discipline (ADR-0012 §7): defeat the umask on the DB file and
        # its WAL/SHM siblings. The -wal / -shm files only exist after the first
        # write opens them, so chmod defensively and ignore FileNotFoundError.
        self._chmod_db_files()

    def _init_schema(self) -> None:
        """Create the ``documents`` table + FTS5 index + sync triggers (idempotent)."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                key        TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                body       TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                key,
                title,
                body,
                content='documents',
                content_rowid='rowid'
            );

            CREATE TRIGGER IF NOT EXISTS documents_ai
            AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, key, title, body)
                VALUES (new.rowid, new.key, new.title, new.body);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_ad
            AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, key, title, body)
                VALUES ('delete', old.rowid, old.key, old.title, old.body);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_au
            AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, key, title, body)
                VALUES ('delete', old.rowid, old.key, old.title, old.body);
                INSERT INTO documents_fts(rowid, key, title, body)
                VALUES (new.rowid, new.key, new.title, new.body);
            END;
            """
        )

    def _chmod_db_files(self) -> None:
        """chmod the DB file + ``-wal`` / ``-shm`` siblings to ``0o660`` (best-effort).

        Defeats the default umask (ADR-0012 §7). The ``-wal`` / ``-shm`` files
        may not exist until the first write opens them, so each chmod is wrapped
        in :func:`contextlib.suppress` (``OSError`` covers ``FileNotFoundError``
        and the "we don't own it" cross-uid case).
        """
        base = str(self._store_path)
        for path in (base, f"{base}-wal", f"{base}-shm"):
            with contextlib.suppress(OSError):
                os.chmod(path, _DB_FILE_MODE)

    def upsert(
        self,
        key: str,
        body: str,
        *,
        title: str = "",
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        """Insert or replace the document keyed by *key* and refresh its FTS entry.

        Raw SQL ``INSERT ... ON CONFLICT(key) DO UPDATE`` (an upsert). The FTS5
        index is kept in sync by the ``documents_ai`` / ``documents_au`` triggers
        installed in :meth:`_init_schema`, so callers never touch
        ``documents_fts`` directly. After the write, the DB/-wal/-shm modes are
        re-asserted (the -wal/-shm files come into existence on first write).

        Args:
            key: Stable string id (the primary key). An existing key is updated.
            body: Full-text-indexed document body.
            title: Optional short title (also full-text indexed).
            created_at: Optional caller-supplied creation timestamp metadata.
            updated_at: Optional caller-supplied update timestamp metadata.
        """
        self._conn.execute(
            """
            INSERT INTO documents (key, title, body, created_at, updated_at)
            VALUES (:key, :title, :body, :created_at, :updated_at)
            ON CONFLICT(key) DO UPDATE SET
                title      = excluded.title,
                body       = excluded.body,
                updated_at = excluded.updated_at
            """,
            {
                "key": key,
                "title": title,
                "body": body,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        )
        self._conn.commit()
        # First write materialises -wal/-shm; re-assert their mode now.
        self._chmod_db_files()

    def read(self, key: str) -> dict[str, Any] | None:
        """Return the document keyed by *key* as a dict, or ``None`` if absent.

        Args:
            key: The document's primary key.

        Returns:
            A dict with ``key`` / ``title`` / ``body`` / ``created_at`` /
            ``updated_at`` columns, or ``None`` when no row matches.
        """
        row = self._conn.execute(
            "SELECT key, title, body, created_at, updated_at FROM documents WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return documents matching the FTS5 *query*, ranked best-first.

        Runs an FTS5 ``MATCH`` over ``documents_fts`` and joins back to the
        canonical ``documents`` rows, ordering by ``bm25()`` relevance (lower =
        better; surfaced as ``ORDER BY rank``). An irrelevant document never
        appears in the result set (FTS5 returns only matching rows).

        Args:
            query: An FTS5 query string (e.g. a bare term or ``term1 term2``).
            limit: Maximum number of results to return (best-ranked first).

        Returns:
            A list of document dicts (same shape as :meth:`read`), ordered by
            descending relevance. Empty when nothing matches.
        """
        rows = self._conn.execute(
            """
            SELECT d.key, d.title, d.body, d.created_at, d.updated_at
            FROM documents_fts f
            JOIN documents d ON d.rowid = f.rowid
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()


__all__ = ["MemoryStore"]
