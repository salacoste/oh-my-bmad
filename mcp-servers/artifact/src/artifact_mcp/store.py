"""Content-addressed persisted artifact store for artifact-mcp (Epic 19 / ADR-0011).

An :class:`ArtifactStore` owns a DEDICATED local-filesystem subtree (NEVER the
registry DB; ADR-0011 store-isolation invariant P3-I2) rooted at the path injected
by the ``__main__`` boundary (``ARTIFACT_MCP_STORE_PATH``). It is a persisted
build/run-output store: ``put`` writes opaque ``bytes`` under their SHA-256
content hash and records a logical-name + metadata row in a sqlite index, ``get``
re-hashes the object on read to detect partial-write/corruption, ``list``
enumerates the index, ``sweep`` enforces retention (TTL + total-size cap), and
``delete`` removes a single object (used by the Tier-3 ``artifact.delete`` tool in
Story 19.3).

Layout (under ``store_root``)::

    <root>/
      objects/<hash[:2]>/<hash>     # one immutable blob per distinct content
      tmp/<uuid>                    # in-flight writes, atomically renamed into place
      index.db                      # sqlite logical-name + metadata index (+ -wal/-shm)

Why stdlib ``hashlib`` + ``sqlite3`` + raw SQL (NOT SQLAlchemy) — ADR-0011:
  1. Content-addressing is a SHA-256 over the bytes — ``hashlib`` is stdlib, zero
     deps. The object layer is a plain filesystem (``os.replace`` atomic rename).
  2. The index needs only insert/upsert + ``ORDER BY created_at`` retention
     queries — easiest to drive via raw SQL, and it adds ZERO new dependencies.
  3. It deliberately avoids ``scripts/check_single_writer.py``, which AST-flags
     SQLAlchemy ``session.add`` / ``execute(insert(...))`` patterns OUTSIDE
     registry-state. A raw ``conn.execute("INSERT ...")`` is not flagged. (Same
     rationale as memory-mcp's store; ADR-0012 §6.)

Crash-safety (ADR-0011): every object is written to ``<root>/tmp/<uuid>``,
``fsync``'d, then ``os.replace``'d to its final content-addressed path — a crash
mid-write leaves an orphan temp file (swept on startup), NEVER a corrupt object.
Content-addressing makes any partial write detectable on read: ``get`` re-hashes
the bytes and returns ``None`` on mismatch. The index runs in WAL mode so a crash
mid-index-write leaves a recoverable log.

File-mode discipline (ADR-0011 §7 / ADR-0012 §7) — DO NOT SKIP:
  The store subtree dirs (root, ``objects/``, ``tmp/``) are created group-writable
  + setgid (``0o2775``) and the index DB + its ``-wal`` / ``-shm`` siblings + every
  object blob are chmod'd group-writable (``0o660``, never world-readable). This
  defeats the default umask (022 → strips group-write) so a cross-uid recovery
  path (a DIFFERENT omb-group service uid that opens the same store on a shared
  volume) does not crash-loop on ``PermissionError`` — the systemic umask-022 bug
  that bit registry-state's event log + SQLite WAL repeatedly. The ``-wal`` /
  ``-shm`` files do not exist until the first write, so they are chmod'd
  defensively (``OSError`` / ``FileNotFoundError`` is ignored).
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import os
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# 0o2775 = setgid + rwxrwxr-x. Setgid (leading 2) propagates group ownership to
# new files; group-write (second 7) lets cross-uid omb-group services write into
# the dir. Mirrors ``events._filesystem.ensure_shared_dir`` (ADR-0012 §7) and the
# memory-mcp store-dir mode.
_STORE_DIR_MODE: int = 0o2775

# 0o660 = rw-rw---- (owner+group read/write, NEVER world-readable). Artifact blobs
# + the index DB may hold build/run output, so the others-triad stays 0.
# Group-WRITE defeats the umask so a cross-uid recoverer is not locked out.
# Mirrors registry-state's event-log file mode (Story 11.3.11) + memory-mcp's DB
# file mode (ADR-0012 §7).
_FILE_MODE: int = 0o660


class ArtifactStore:
    """Content-addressed artifact store over a dedicated local-FS subtree.

    One instance owns one long-lived ``sqlite3.Connection`` to the index DB under
    *store_root*. Construction is idempotent: ``objects/`` / ``tmp/`` and the
    ``artifacts`` index table are created with ``mkdir(exist_ok=True)`` /
    ``IF NOT EXISTS`` semantics, so an existing store is reopened cleanly, and a
    startup :meth:`sweep` removes orphan temp files + enforces retention.

    Index schema (one row per logical name, keyed by ``name``)::

        artifacts(
            name            TEXT PRIMARY KEY,   -- logical artifact name
            hash            TEXT NOT NULL,      -- sha256 of the content
            size            INTEGER NOT NULL,   -- byte length of the content
            task_id         TEXT,               -- originating task (nullable)
            created_at      REAL NOT NULL,      -- epoch seconds (retention ORDER BY)
            retention_class TEXT                -- reserved retention bucket (nullable)
        )

    The object layer is content-addressed and deduplicated: two puts of identical
    bytes map to the SAME ``objects/<hash[:2]>/<hash>`` blob. The index maps a
    logical *name* → that hash + metadata; an anonymous put (no name) records its
    row under the hash itself so retention can still account for it.
    """

    def __init__(
        self,
        store_root: Path,
        *,
        max_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Open (or create) the store under *store_root* and initialise the layout.

        Ensures ``objects/`` / ``tmp/`` exist (mode ``0o2775``), opens a WAL-mode
        sqlite connection to ``index.db``, creates the ``artifacts`` table
        (idempotent), and chmods the DB + ``-wal`` / ``-shm`` siblings to
        ``0o660``. Construction does NOT run a sweep — the factory (``build_server``)
        runs the startup sweep explicitly so the deletion list can be surfaced to
        the 19.4 event emitter.

        Args:
            store_root: Absolute path to this store's DEDICATED subtree. NEVER the
                registry DB (ADR-0011 P3-I2). The path is used as-is; the
                ``__main__`` boundary is responsible for any normalisation.
            max_bytes: Optional total-size cap (bytes). When set, :meth:`sweep`
                deletes oldest objects until the total indexed size is at or below
                this cap. ``None`` → unbounded.
            ttl_seconds: Optional time-to-live (seconds). When set, :meth:`sweep`
                deletes objects whose ``created_at`` is older than ``now -
                ttl_seconds``. ``None`` → no TTL expiry.
        """
        self._store_root: Path = store_root
        self._max_bytes: int | None = max_bytes
        self._ttl_seconds: int | None = ttl_seconds

        self._objects_dir: Path = store_root / "objects"
        self._tmp_dir: Path = store_root / "tmp"
        self._index_path: Path = store_root / "index.db"

        # Store subtree dirs: group-writable + setgid so a cross-uid omb-group
        # service can write into the store on a shared volume (ADR-0011 §7). chmod
        # is best-effort — a dir we don't own must not fail-loud (the mkdir already
        # succeeded).
        for directory in (store_root, self._objects_dir, self._tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(directory, _STORE_DIR_MODE)

        # ``isolation_level=None`` is NOT set — we use the default deferred
        # transaction + explicit ``commit()``. ``check_same_thread`` is left
        # default: the store is single-writer by construction (one process, one
        # connection); the FastMCP request path serializes over it.
        self._conn: sqlite3.Connection = sqlite3.connect(str(self._index_path))
        self._conn.row_factory = sqlite3.Row

        # WAL mode for crash-safety (ADR-0011). Set before any schema DDL so the
        # schema writes land in WAL too.
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._init_schema()

        # File-mode discipline (ADR-0011 §7): defeat the umask on the index DB and
        # its WAL/SHM siblings. The -wal / -shm files only exist after the first
        # write opens them, so chmod defensively and ignore FileNotFoundError.
        self._chmod_index_files()

    def _init_schema(self) -> None:
        """Create the ``artifacts`` index table (idempotent)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                name            TEXT PRIMARY KEY,
                hash            TEXT NOT NULL,
                size            INTEGER NOT NULL,
                task_id         TEXT,
                created_at      REAL NOT NULL,
                retention_class TEXT
            )
            """
        )
        self._conn.commit()

    def _chmod_index_files(self) -> None:
        """chmod the index DB + ``-wal`` / ``-shm`` siblings to ``0o660`` (best-effort).

        Defeats the default umask (ADR-0011 §7). The ``-wal`` / ``-shm`` files may
        not exist until the first write opens them, so each chmod is wrapped in
        :func:`contextlib.suppress` (``OSError`` covers ``FileNotFoundError`` and
        the "we don't own it" cross-uid case).
        """
        base = str(self._index_path)
        for path in (base, f"{base}-wal", f"{base}-shm"):
            with contextlib.suppress(OSError):
                os.chmod(path, _FILE_MODE)

    def _object_path(self, content_hash: str) -> Path:
        """Return the content-addressed blob path for *content_hash* (sharded by prefix)."""
        return self._objects_dir / content_hash[:2] / content_hash

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        """Return the lowercase hex SHA-256 of *content*."""
        return hashlib.sha256(content).hexdigest()

    def put(
        self,
        content: bytes,
        *,
        name: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Store *content* under its SHA-256 hash and record an index row.

        The object is written via write-temp-then-atomic-rename: the bytes go to
        ``tmp/<uuid>`` (``fsync``'d), then ``os.replace`` moves the temp file onto
        the final ``objects/<hash[:2]>/<hash>`` path. Identical content
        DEDUPLICATES — a second put of the same bytes maps to the existing blob and
        reports ``deduped=True`` (the object is not rewritten). The index row is
        keyed by the logical *name* (or the hash itself when *name* is None) and
        upserted so re-putting under the same name refreshes its hash/metadata.

        Retention is NOT enforced here — the caller (``build_server`` / the 19.3
        ``artifact.put`` tool) calls :meth:`sweep` after the put so the deleted
        hashes can be surfaced to the 19.4 event emitter.

        Args:
            content: The opaque artifact bytes to persist.
            name: Optional logical name to index the content under. When None, the
                content hash is used as the index key (anonymous content-addressed
                put).
            task_id: Optional originating task id recorded in the index row.

        Returns:
            A dict ``{"hash", "size", "name", "deduped"}``: the content hash, the
            byte length, the resolved index key (logical name or hash), and whether
            the object blob already existed (dedup hit).
        """
        content_hash = self._hash_bytes(content)
        final_path = self._object_path(content_hash)

        deduped = final_path.exists()
        if not deduped:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(final_path.parent, _STORE_DIR_MODE)
            self._atomic_write(final_path, content)

        index_key = name if name is not None else content_hash
        created_at = self._now()
        self._conn.execute(
            """
            INSERT INTO artifacts (name, hash, size, task_id, created_at, retention_class)
            VALUES (:name, :hash, :size, :task_id, :created_at, NULL)
            ON CONFLICT(name) DO UPDATE SET
                hash       = excluded.hash,
                size       = excluded.size,
                task_id    = excluded.task_id,
                created_at = excluded.created_at
            """,
            {
                "name": index_key,
                "hash": content_hash,
                "size": len(content),
                "task_id": task_id,
                "created_at": created_at,
            },
        )
        self._conn.commit()
        # First write materialises -wal/-shm; re-assert their mode now.
        self._chmod_index_files()

        return {
            "hash": content_hash,
            "size": len(content),
            "name": index_key,
            "deduped": deduped,
        }

    def _atomic_write(self, final_path: Path, content: bytes) -> None:
        """Write *content* to a temp file, fsync, then atomically rename into place.

        A crash before the ``os.replace`` leaves an orphan ``tmp/<uuid>`` (swept on
        startup), NEVER a partial object at *final_path*. The temp file is chmod'd
        ``0o660`` before the rename so the final blob inherits the group-writable
        mode (defeating the umask, ADR-0011 §7).
        """
        tmp_path = self._tmp_dir / uuid.uuid4().hex
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            # chmod explicitly — O_CREAT mode is masked by the umask, so re-assert
            # the group-writable mode before the rename (ADR-0011 §7).
            with contextlib.suppress(OSError):
                os.chmod(tmp_path, _FILE_MODE)
            os.replace(tmp_path, final_path)
        finally:
            # If the rename did not consume the temp file (write/replace raised),
            # remove the orphan so a failed put leaves no debris.
            with contextlib.suppress(OSError):
                if tmp_path.exists():
                    tmp_path.unlink()

    def get(self, content_hash: str) -> bytes | None:
        """Return the bytes stored under *content_hash*, re-hashing to verify integrity.

        Reads the content-addressed blob and recomputes its SHA-256; a mismatch
        (partial write, on-disk corruption, or a tampered object file) yields
        ``None`` rather than handing back bad bytes. A missing object also yields
        ``None``.

        Args:
            content_hash: The lowercase hex SHA-256 of the desired content.

        Returns:
            The verified object bytes, or ``None`` when the object is absent or its
            recomputed hash does not match *content_hash*.
        """
        object_path = self._object_path(content_hash)
        if not object_path.exists():
            return None
        try:
            data = object_path.read_bytes()
        except OSError:
            return None
        if self._hash_bytes(data) != content_hash:
            # Tamper / partial-write / corruption — content-addressing detects it.
            return None
        return data

    def list(self) -> builtins.list[dict[str, Any]]:
        """Return every index row, newest-first.

        Returns:
            A list of dicts with ``name`` / ``hash`` / ``size`` / ``task_id`` /
            ``created_at`` / ``retention_class`` columns, ordered by descending
            ``created_at`` (newest first).
        """
        rows = self._conn.execute(
            """
            SELECT name, hash, size, task_id, created_at, retention_class
            FROM artifacts
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def sweep(self) -> builtins.list[str]:
        """Enforce retention + clear orphan temp files; return the deleted hashes.

        Two retention rules (both optional, read from the store's ``ttl_seconds`` /
        ``max_bytes`` at construction):

          1. TTL — every index row older than ``now - ttl_seconds`` is deleted.
          2. Size cap — after TTL expiry, while the total indexed size exceeds
             ``max_bytes``, the OLDEST remaining rows are deleted until the total
             is at or below the cap.

        Deleting an index row also removes its content-addressed blob IF no other
        index row references the same hash (dedup-safe). Orphan ``tmp/*`` files
        (debris from a crashed put) are always cleared.

        Retention is the ONLY deletion path that runs WITHOUT a Tier-3 approval —
        it is system-initiated (ADR-0011 §5). Story 19.4 will emit an
        ``artifact.deleted`` event per returned hash; for 19.2 this just returns the
        list of deleted content hashes.

        Returns:
            The list of content hashes whose blobs were deleted (in deletion
            order). Empty when nothing was evicted.
        """
        self._clear_orphan_temp_files()

        now = self._now()
        deleted_hashes: builtins.list[str] = []

        rows = self._conn.execute(
            "SELECT name, hash, size, created_at FROM artifacts ORDER BY created_at ASC"
        ).fetchall()
        live = [dict(r) for r in rows]

        # Rule 1: TTL expiry.
        if self._ttl_seconds is not None:
            cutoff = now - self._ttl_seconds
            expired = [r for r in live if r["created_at"] < cutoff]
            for row in expired:
                self._delete_index_row(row["name"])
            deleted_hashes.extend(self._evict_unreferenced(r["hash"] for r in expired))
            live = [r for r in live if r["created_at"] >= cutoff]

        # Rule 2: total-size cap — evict oldest-first until at/below the cap.
        if self._max_bytes is not None:
            total = sum(int(r["size"]) for r in live)
            # ``live`` is already ASC by created_at (oldest first).
            for row in live:
                if total <= self._max_bytes:
                    break
                self._delete_index_row(row["name"])
                deleted_hashes.extend(self._evict_unreferenced((row["hash"],)))
                total -= int(row["size"])

        if deleted_hashes:
            self._conn.commit()
            self._chmod_index_files()
        return deleted_hashes

    def delete(self, content_hash: str) -> bool:
        """Delete every index row pointing at *content_hash* and its blob.

        Ships now for the Tier-3 ``artifact.delete`` tool that lands in Story 19.3
        (operator-approved single-object deletion). Removes all index rows whose
        ``hash`` matches, then unlinks the content-addressed blob.

        Args:
            content_hash: The lowercase hex SHA-256 of the object to delete.

        Returns:
            ``True`` if an index row OR the blob existed and was removed; ``False``
            when nothing matched (already absent).
        """
        cur = self._conn.execute("SELECT name FROM artifacts WHERE hash = ?", (content_hash,))
        names = [row["name"] for row in cur.fetchall()]
        for name in names:
            self._delete_index_row(name)

        blob_removed = False
        object_path = self._object_path(content_hash)
        with contextlib.suppress(OSError):
            if object_path.exists():
                object_path.unlink()
                blob_removed = True

        if names or blob_removed:
            self._conn.commit()
            self._chmod_index_files()
            return True
        return False

    def _delete_index_row(self, name: str) -> None:
        """Delete the index row keyed by logical *name* (no commit)."""
        self._conn.execute("DELETE FROM artifacts WHERE name = ?", (name,))

    def _evict_unreferenced(self, hashes: Iterable[str]) -> builtins.list[str]:
        """Unlink each blob in *hashes* that no surviving index row references.

        Dedup-safe: a hash still referenced by another (non-deleted) index row is
        kept. Returns the hashes whose blobs were actually unlinked.
        """
        evicted: builtins.list[str] = []
        for content_hash in hashes:
            if content_hash in evicted:
                continue
            still_referenced = (
                self._conn.execute(
                    "SELECT 1 FROM artifacts WHERE hash = ? LIMIT 1", (content_hash,)
                ).fetchone()
                is not None
            )
            if still_referenced:
                continue
            object_path = self._object_path(content_hash)
            with contextlib.suppress(OSError):
                if object_path.exists():
                    object_path.unlink()
            evicted.append(content_hash)
        return evicted

    def _clear_orphan_temp_files(self) -> None:
        """Unlink any debris left in ``tmp/`` by a crashed put (best-effort)."""
        with contextlib.suppress(OSError):
            for entry in self._tmp_dir.iterdir():
                with contextlib.suppress(OSError):
                    if entry.is_file():
                        entry.unlink()

    @staticmethod
    def _now() -> float:
        """Return the current wall-clock time in epoch seconds.

        A module-local indirection so the retention tests can monkeypatch the
        clock without threading an ``events.Clock`` through the FS store (the store
        is deliberately config-injected and free of ``os.environ`` / global state).
        """
        import time

        return time.time()

    def close(self) -> None:
        """Close the underlying sqlite index connection."""
        self._conn.close()


__all__ = ["ArtifactStore"]
