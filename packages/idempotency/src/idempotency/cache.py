"""IdempotencyCacheStore — in-process TTLCache + SQLite durability (Story 2.7).

Public surface:
  - ``CacheHit``              — frozen dataclass returned by read operations.
  - ``IdempotencyCacheStore`` — get / store / get_or_run / sweep_expired.

Architecture
------------
The store combines two layers:
  1. ``cachetools.TTLCache`` (in-process) — sub-microsecond hot-path reads.
  2. SQLite ``idempotency_cache`` table (durable) — survives restarts.

Write-through pattern (durability guarantee):
  SQLite write FIRST → in-process populate second.
  If the SQLite write fails for any reason, in-process is NOT touched.

Read-through pattern:
  in-process hit → return immediately.
  in-process miss → query SQLite → if found and not expired, populate
  in-process cache and return; if expired, DELETE from both + return None.

Schema duplication
------------------
``_IDEMPOTENCY_TABLE`` is a SQLAlchemy Core ``Table`` that mirrors the ORM
model ``registry_state.schema.IdempotencyCache`` (Story 2.3) column-by-column.
This duplication is **intentional** — the architecture invariant forbids
``packages/`` from importing ``services/``. Drift risk is mitigated by
``TestColumnConsistency`` in ``test_cache.py``, which reflects both definitions
and asserts column names + nullability match. If they drift, the test fails
loudly.

Authoritative source: ``services/registry-state/src/registry_state/schema.py``
  → class ``IdempotencyCache`` + ``ix_idempotency_cache_expires_at`` index.

Concurrency model
-----------------
``_global_lock: asyncio.Lock`` — guards ALL reads/writes to the cachetools
  TTLCache internal state. cachetools is NOT thread-safe; the global lock
  serializes low-level in-process mutations.

``_key_locks: dict[str, asyncio.Lock]`` + ``_key_refcounts: dict[str, int]`` —
  per-key asyncio locks that serialize high-level ``get_or_run`` operations.
  A single lock is created per key on first access; an explicit refcount
  tracks how many callers hold a reference. The lock is removed from the
  dict only after the last caller releases it. We do NOT use a
  ``WeakValueDictionary`` here: between bursts of activity Python may GC the
  Lock, and a subsequent caller would receive a fresh Lock instance —
  breaking the serialization invariant that proves NFR-R4.

``_key_locks_dict_lock: asyncio.Lock`` — guards the per-key lock dict itself
  so that lock-creation/teardown is atomic: two concurrent callers for the
  same key cannot each create separate locks (which would break the
  serialization guarantee).

NFR-R4 proof: under a 100× concurrent retry storm for the same key, all 100
coroutines acquire the same per-key lock, serialize, and the factory runs
EXACTLY ONCE.

Expiry boundary
---------------
Expiry is ``expires_at <= now()`` — exactly-at-expiry IS expired. Both the
``get()`` lazy-eviction path AND ``sweep_expired()`` use this same boundary
so the in-process and SQLite layers agree.

Sweep vs. store race
--------------------
The ``store()`` method performs INSERT and SELECT in the SAME transaction so
that a concurrent ``sweep_expired()`` cannot delete the row between the two
phases. Combined with the FR26 single-writer invariant this makes
``IdempotencyConflict`` essentially unreachable at runtime; it is retained as
a defensive postcondition that documents the (sub-)invariant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import cachetools
from events.clock import Clock
from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from idempotency.errors import IdempotencyConflict

# ---------------------------------------------------------------------------
# Schema definition — mirrors registry_state.schema.IdempotencyCache (Story 2.3)
# ---------------------------------------------------------------------------
# DUPLICATION WARNING: keep in sync with
#   services/registry-state/src/registry_state/schema.py  → IdempotencyCache
# The column-consistency test (TestColumnConsistency in test_cache.py)
# asserts that names, types and nullability match.  Do NOT change this
# definition without updating the ORM model and running that test.

_meta = MetaData()

_IDEMPOTENCY_TABLE = Table(
    "idempotency_cache",
    _meta,
    Column("idempotency_key", String(36), primary_key=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("result_event_id", String(38), nullable=False),
    Column("request_id_on_first_hit", String(36), nullable=False),
    # Mirrors the ORM ``ix_idempotency_cache_expires_at`` index
    # (registry-state/schema.py:335) — backs the ``sweep_expired`` range
    # delete on ``expires_at``. Included here (Story 11.3.12) so a DB
    # bootstrapped from this Core MetaData via :func:`create_idempotency_schema`
    # gets the same index the migrator-created table had.
    Index("ix_idempotency_cache_expires_at", "expires_at"),
)


async def create_idempotency_schema(engine: AsyncEngine) -> None:
    """Create the ``idempotency_cache`` table (+ index) on ``engine``'s DB.

    Story 11.3.12 — registry-api now runs its writable idempotency-cache
    engine against its OWN ``idempotency.sqlite3`` file (split out of
    ``state.sqlite3`` so registry-state is the sole writer of the audit
    store, closing the cross-uid WAL crash-loop). That separate file needs
    the table created; the idempotency package owns the canonical Core
    ``Table`` definition, so bootstrapping it lives here rather than in the
    registry-state migrator.

    Idempotent: ``create_all`` issues ``CREATE TABLE IF NOT EXISTS`` so a
    re-run against an existing file is a no-op. Caller decides when to run
    it (registry-api gates on an auto-create flag, mirroring
    ``REGISTRY_STATE_AUTO_CREATE_SCHEMA``).
    """
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheHit:
    """Immutable result of a successful cache lookup.

    Attributes:
        result_event_id:         The event ID produced by the first successful
                                 execution for this idempotency key.
        request_id_on_first_hit: The request_id of the original (winning)
                                 request that populated the cache entry.
        created_at:              UTC timestamp when the entry was created.
        expires_at:              UTC timestamp after which the entry is expired
                                 (created_at + 7 days by default).
    """

    result_event_id: str
    request_id_on_first_hit: str
    created_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _hit_from_row(row: Row[tuple[str, datetime, datetime, str, str]]) -> CacheHit:
    """Build a ``CacheHit`` from a SQLAlchemy Core row.

    Uses ``_mapping`` (a ``RowMapping``) to access values by column name and
    avoids returning ``Any``.  Attaches UTC tzinfo when SQLite returns naive
    datetimes (which it does for ``DateTime(timezone=True)`` on aiosqlite).
    """
    m = row._mapping  # noqa: SLF001 — documented SA public API for named access
    result_event_id: str = str(m["result_event_id"])
    request_id_on_first_hit: str = str(m["request_id_on_first_hit"])

    raw_created: datetime = m["created_at"]
    raw_expires: datetime = m["expires_at"]

    created_at = raw_created.replace(tzinfo=UTC) if raw_created.tzinfo is None else raw_created
    expires_at = raw_expires.replace(tzinfo=UTC) if raw_expires.tzinfo is None else raw_expires

    return CacheHit(
        result_event_id=result_event_id,
        request_id_on_first_hit=request_id_on_first_hit,
        created_at=created_at,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IdempotencyCacheStore:
    """Idempotency cache combining cachetools.TTLCache + SQLite durability.

    Parameters
    ----------
    session_maker:
        Async sessionmaker bound to the SQLite engine that owns the
        ``idempotency_cache`` table.
    clock:
        Injectable clock (production: SystemClock; tests: FrozenClock /
        TickingClock).  Used for ``created_at`` / ``expires_at`` and for
        the ``sweep_expired`` expiry boundary.
    ttl_seconds:
        Entry lifetime in seconds.  Default 604800 = 7 days (FR28 / PRD
        line 85).  Configurable for tests.
    max_in_process:
        Maximum entries in the cachetools.TTLCache before LRU eviction
        kicks in.  Default 100 000.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        clock: Clock,
        ttl_seconds: int = 604800,
        max_in_process: int = 100_000,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0; got {ttl_seconds}")
        if max_in_process <= 0:
            raise ValueError(f"max_in_process must be > 0; got {max_in_process}")

        self._session_maker = session_maker
        self._clock = clock
        self._ttl_seconds = ttl_seconds

        # In-process TTLCache — lazy eviction on access.  NOT thread-safe;
        # every access is guarded by _global_lock.  The ``timer`` callable
        # syncs the in-process TTL boundary to the same Clock used by the
        # SQLite path (otherwise the in-process layer would honor wall-clock
        # time while the SQLite layer honors the injected clock).
        self._in_process: cachetools.TTLCache[str, CacheHit] = cachetools.TTLCache(
            maxsize=max_in_process,
            ttl=ttl_seconds,
            timer=lambda: clock.now().timestamp(),
        )

        # asyncio.Lock() created sync in __init__ — safe on Python 3.12 (loop
        # bind on first await). Story 2.5/2.6 precedent.
        # Guards all reads/writes to _in_process (low-level cachetools safety).
        self._global_lock: asyncio.Lock = asyncio.Lock()

        # Per-key locks for get_or_run serialization. Refcounted (NOT a
        # WeakValueDictionary) so locks survive GC between bursts of activity
        # for the same key — see module docstring "Concurrency model".
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_refcounts: dict[str, int] = {}
        # Guards mutation of _key_locks / _key_refcounts (atomicity of
        # lock-creation and lock-teardown).
        self._key_locks_dict_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _acquire_key_lock(self, key: str) -> asyncio.Lock:
        """Return (or create) the asyncio.Lock for *key*; bumps refcount.

        Creation is atomic: the dict lock is held only for the check-and-set,
        not for the duration of the per-key operation. Pair every call with
        ``_release_key_lock`` (use try/finally) so the lock is freed when the
        last caller for *key* returns.
        """
        async with self._key_locks_dict_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
                self._key_refcounts[key] = 1
            else:
                self._key_refcounts[key] += 1
            return lock

    async def _release_key_lock(self, key: str) -> None:
        """Decrement the refcount for *key*; remove the lock when it hits 0."""
        async with self._key_locks_dict_lock:
            self._key_refcounts[key] -= 1
            if self._key_refcounts[key] == 0:
                del self._key_locks[key]
                del self._key_refcounts[key]

    def _expires_at(self, created_at: datetime) -> datetime:
        return created_at + timedelta(seconds=self._ttl_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> CacheHit | None:
        """Return the cached hit for *key*, or ``None`` on miss/expiry.

        Read path:
          1. Check in-process TTLCache (fast).
          2. On miss, query SQLite.
          3. If SQLite row exists and not expired, populate in-process + return.
          4. If SQLite row is expired, DELETE it from SQLite + return None.
        """
        # --- in-process fast path ---
        async with self._global_lock:
            hit = self._in_process.get(key)
        if hit is not None:
            return hit

        # --- SQLite fallback ---
        now = self._clock.now()
        async with self._session_maker() as session:
            result = await session.execute(
                select(_IDEMPOTENCY_TABLE).where(_IDEMPOTENCY_TABLE.c.idempotency_key == key)
            )
            row = result.fetchone()

        if row is None:
            return None

        hit = _hit_from_row(row)

        if hit.expires_at <= now:
            # Expired — delete from SQLite (lazy eviction).
            async with self._session_maker() as session:
                await session.execute(
                    delete(_IDEMPOTENCY_TABLE).where(_IDEMPOTENCY_TABLE.c.idempotency_key == key)
                )
                await session.commit()
            return None

        # Populate in-process and return.
        async with self._global_lock:
            self._in_process[key] = hit
        return hit

    async def store(
        self,
        key: str,
        *,
        result_event_id: str,
        request_id: str,
    ) -> CacheHit:
        """Write a new cache entry and return the WINNER's ``CacheHit``.

        Uses SQLite ``INSERT OR IGNORE`` (on_conflict_do_nothing) semantics.
        If a concurrent writer beat us to the PK:
          - Our INSERT is silently discarded.
          - The same-transaction SELECT returns the existing winner's row.
          - ``IdempotencyConflict`` is raised only if the SELECT still
            returns nothing — a defensive postcondition that, given FR26
            (single writer) and INSERT+SELECT in one transaction, should be
            unreachable in production.

        Write-through: SQLite is written FIRST; in-process is populated only
        after a successful SQLite commit. INSERT and SELECT execute inside
        the SAME transaction so a concurrent ``sweep_expired()`` cannot
        delete the row between phases.
        """
        now = self._clock.now()
        created_at = now
        expires_at = self._expires_at(created_at)

        stmt = (
            sqlite_insert(_IDEMPOTENCY_TABLE)
            .values(
                idempotency_key=key,
                created_at=created_at,
                expires_at=expires_at,
                result_event_id=result_event_id,
                request_id_on_first_hit=request_id,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )

        async with self._session_maker() as session, session.begin():
            await session.execute(stmt)
            # SELECT inside the SAME transaction — sees the just-INSERTed
            # row OR the pre-existing winner. A concurrent sweep cannot
            # delete the row between phases at this isolation level.
            result = await session.execute(
                select(_IDEMPOTENCY_TABLE).where(_IDEMPOTENCY_TABLE.c.idempotency_key == key)
            )
            row = result.fetchone()
            # Transaction committed at session.begin() exit.

        if row is None:
            # Defensive: under FR26 + same-transaction SELECT this branch is
            # unreachable. Retained as a postcondition that documents the
            # invariant.
            raise IdempotencyConflict(key)

        hit = _hit_from_row(row)

        # Populate in-process ONLY after confirmed SQLite write.
        async with self._global_lock:
            self._in_process[key] = hit

        return hit

    async def get_or_run(
        self,
        key: str,
        *,
        request_id: str,
        factory: Callable[[], Awaitable[str]],
    ) -> tuple[CacheHit, bool]:
        """High-level idempotent-execution API.

        Returns ``(CacheHit, was_run)`` where ``was_run=True`` means the
        factory ran in this call.

        Serializes concurrent same-key calls via a per-key asyncio.Lock
        so the factory runs AT MOST ONCE per (key, TTL-window).  This is
        the mechanism that proves NFR-R4 (zero duplicate executions under
        a 100× retry storm).

        Algorithm:
          1. Acquire per-key lock (refcounted).
          2. Check cache — if hit, return (hit, False).
          3. Run factory to get result_event_id.
          4. store(key, result_event_id, request_id).
          5. Return (stored_hit, True).
          6. Release per-key lock (in finally — even on factory exception).

        Factory contract:
            - Factory runs at most once per SUCCESSFUL invocation per
              (key, ttl-window).
            - On factory exception: the lock releases without storing; the
              exception propagates to the caller. Subsequent calls with the
              same key (within the ttl-window) will re-run the factory until
              one succeeds — i.e., this is at-least-once retry semantics on
              failure, exactly-once on success.
            - If your factory has side effects that must NOT be retried, wrap
              it in a circuit breaker or store a sentinel CacheHit on first
              attempt.

        Re-entrancy:
            - The per-key lock is NOT reentrant. A factory MUST NOT
              recursively call ``get_or_run`` for its OWN key — that would
              deadlock on the inner call. Calling ``get_or_run`` for a
              DIFFERENT key from inside a factory is safe.
        """
        lock = await self._acquire_key_lock(key)
        try:
            async with lock:
                cached = await self.get(key)
                if cached is not None:
                    return cached, False

                result_event_id = await factory()
                stored = await self.store(
                    key,
                    result_event_id=result_event_id,
                    request_id=request_id,
                )
                return stored, True
        finally:
            await self._release_key_lock(key)

    async def sweep_expired(self) -> int:
        """Delete all expired rows from SQLite; return the number deleted.

        Also evicts matching keys from in-process cache so tests with very
        short TTLs see immediate eviction rather than waiting for cachetools'
        lazy eviction.

        Called at startup (and optionally on a periodic schedule in Phase 2).
        Safe to call multiple times (idempotent).

        Expiry boundary
        ---------------
        Uses ``expires_at <= now()`` — exactly-at-expiry is expired. This
        matches the lazy-eviction boundary in ``get()`` so the two layers
        agree on whether a borderline entry is alive.

        Race condition note
        -------------------
        Between SQLite commit and in-process eviction, a concurrent ``get()``
        can return SQLite-deleted rows from the in-process cache. Self-healing
        on next access (lazy eviction by cachetools' timer). In production
        this window is negligible; in tests use barriers if exactness is
        required.
        """
        now = self._clock.now()

        async with self._session_maker() as session:
            result = await session.execute(
                delete(_IDEMPOTENCY_TABLE)
                .where(_IDEMPOTENCY_TABLE.c.expires_at <= now)
                .returning(_IDEMPOTENCY_TABLE.c.idempotency_key)
            )
            deleted_keys: list[str] = [str(r[0]) for r in result.fetchall()]
            await session.commit()

        rowcount = len(deleted_keys)

        if deleted_keys:
            async with self._global_lock:
                for k in deleted_keys:
                    self._in_process.pop(k, None)

        return rowcount


__all__ = [
    "CacheHit",
    "IdempotencyCacheStore",
    "_IDEMPOTENCY_TABLE",
]
