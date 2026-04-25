# Story 2.7: Idempotency cache

Status: review

## Story

As **`registry-state`**,
I want **`packages/idempotency/src/idempotency/cache.py` exporting an `IdempotencyCacheStore` that combines `cachetools.TTLCache` (in-process fast path) with a SQLite `idempotency_cache` table (durability across restarts), exposing a `get_or_run(key, factory)` API that serializes concurrent same-key requests behind a per-key `asyncio.Lock` so the factory runs at most once per (key, 7-day window)**,
so that **FR28 holds (idempotent command submission) and NFR-R4 holds (zero duplicate executions under a 100× retry storm) — Telegram retries + network partitions never produce duplicate task execution**.

## Acceptance Criteria

1. **AC-1: `packages/idempotency/src/idempotency/cache.py`** — public surface. Exports:

   - `@dataclass(frozen=True) class CacheHit` with fields `result_event_id: str`, `request_id_on_first_hit: str`, `created_at: datetime`, `expires_at: datetime`. Used as the return type for cache reads.

   - `class IdempotencyCacheStore` with:
     - `__init__(self, *, session_maker: async_sessionmaker[AsyncSession], clock: Clock, ttl_seconds: int = 604800, max_in_process: int = 100_000) -> None` — `ttl_seconds=604800` is exactly 7 days (PRD line 85). `max_in_process=100_000` caps the cachetools.TTLCache; LRU eviction takes over once full. Validates `ttl_seconds > 0` and `max_in_process > 0`.
     - `async def get(self, key: str) -> CacheHit | None` — read-only cache check. Hits in-process first; falls back to SQLite. Expired entries return None and are deleted on access.
     - `async def store(self, key: str, *, result_event_id: str, request_id: str) -> CacheHit` — writes (key, result_event_id, request_id, created_at, expires_at) atomically to SQLite + populates in-process cache. Raises `IdempotencyConflict` on PK collision (signals a race; caller should re-`get`).
     - `async def get_or_run(self, key: str, *, request_id: str, factory: Callable[[], Awaitable[str]]) -> tuple[CacheHit, bool]` — the **high-level** API. Returns `(hit, was_run)` where `was_run=True` means factory ran this call. Serializes concurrent same-key calls via per-key asyncio.Lock so factory runs at most once per (key, ttl-window). This is the path NFR-R4 depends on.
     - `async def sweep_expired(self) -> int` — DELETE FROM idempotency_cache WHERE expires_at < clock.now(). Returns rows deleted. Called at startup AND on a periodic schedule (Phase 2).

2. **AC-2: Per-key locking for `get_or_run`.** Implementation: `_key_locks: WeakValueDictionary[str, asyncio.Lock]`. `get_or_run` acquires/creates a lock per `key`, holds it for the entire (cache-check + factory + store) sequence. Concurrent calls with the same key serialize → factory invokes ONCE → all 100 callers return the SAME `CacheHit`. The `WeakValueDictionary` ensures locks are GC'd when no longer referenced (memory bound).

3. **AC-3: TTL = 7 days exact.** `expires_at = created_at + timedelta(days=7)`. `ttl_seconds=604800` default. Both the in-process cachetools.TTLCache AND the SQLite `expires_at` column use the same timeout. Configurable via constructor for tests (e.g., `ttl_seconds=2` for 2-second TTL tests).

4. **AC-4: SQLAlchemy Core (not ORM) for table access.** `packages/idempotency/cache.py` defines the `idempotency_cache` table as a SQLAlchemy Core `Table` matching Story 2.3's schema column-by-column. This is intentional duplication to keep dependency direction clean (`packages/` MUST NOT depend on `services/`). Document the duplication risk in the module docstring with a pointer to Story 2.3's `schema.py` as the authoritative source. Add a unit test that asserts column-name + nullability consistency between the two definitions (uses metadata reflection — fails loudly if they drift).

5. **AC-5: SQLite operations use UPSERT semantics for `store`.** `sqlite_insert(_IDEMPOTENCY_TABLE).values(...).on_conflict_do_nothing(index_elements=["idempotency_key"])` — race-safe. After execute: re-fetch the row via `select(...).where(idempotency_key == key)` to return the WINNER's `CacheHit`. If two concurrent calls race past `get_or_run`'s lock (only possible across multiple processes, which FR26 forbids), the SQLite PK constraint enforces single-writer; the loser re-fetches the winner's row.

6. **AC-6: In-process cache is an OPTIMIZATION; SQLite is source of truth.** Read path: in-process hit → return; in-process miss → SQLite query → if found and not expired, populate in-process + return; if expired, DELETE from both + return None. Write path: SQLite insert FIRST (durability); if successful, populate in-process. If SQLite write fails (any reason), in-process is NOT touched — keeps the two layers consistent.

7. **AC-7: `IdempotencyConflict` exception class** in `packages/idempotency/src/idempotency/errors.py` (or sibling module). Typed exception with `key: str` field. Raised by `store()` on PK collision. Documented as "in single-writer deployment (FR26), this should never fire — represents a hard invariant violation".

8. **AC-8: Cachetools thread-safety wrapping.** `cachetools.TTLCache` is NOT thread-safe in stdlib sense. Inside `IdempotencyCacheStore`, every read/write to `self._in_process` is guarded by `self._global_lock: asyncio.Lock` (separate from per-key locks; protects the cachetools internal state). Per-key locks serialize HIGH-LEVEL operations; the global lock serializes LOW-LEVEL TTLCache mutations.

9. **AC-9: `packages/idempotency/pyproject.toml`** adds dependencies: `cachetools>=5.3`, `sqlalchemy[asyncio]>=2.0.30` (already locked from Story 2.3, so transitive). `events>=0.3.0` for the `Clock` Protocol. Version bumped `0.1.0 → 0.2.0` (first feature increment).

10. **AC-10: `packages/idempotency/src/idempotency/__init__.py`** re-exports the public surface:
    ```python
    from idempotency.cache import (
        CacheHit,
        IdempotencyCacheStore,
    )
    from idempotency.errors import IdempotencyConflict
    __version__ = "0.2.0"
    ```
    `__all__` alphabetical.

11. **AC-11: Co-located tests in `packages/idempotency/src/idempotency/test_cache.py`** — 18-22 tests:

    **TestBasicGetSet** (~5):
    - Get on miss returns None.
    - Store + get returns CacheHit with correct fields.
    - Store + restart (new instance, same DB) → get still hits via SQLite.
    - Expired entry → get returns None + row is deleted (lazy eviction).
    - `result_event_id` round-trips losslessly.

    **TestGetOrRun concurrency** (~4):
    - Single call: factory runs, returns (hit, True).
    - Second call same key: factory does NOT run, returns (cached_hit, False).
    - **100× concurrent calls same key:** `asyncio.gather(*[get_or_run(...)] * 100)` → factory called EXACTLY ONCE; all 100 return same `CacheHit`; only one `(was_run, True)` in the results.
    - Different keys → factories run in parallel.

    **TestTTL** (~4):
    - Configurable `ttl_seconds=2` → wait 3 sec → key is expired.
    - `sweep_expired` deletes only expired rows; valid rows untouched.
    - Sweep called at startup is idempotent.
    - In-process and SQLite both honor the same TTL boundary.

    **TestUPSERT** (~2):
    - `store(key, ...)` twice with same key returns the FIRST winner (race-safe).
    - `store` after `sweep_expired` removed the prior key works (TTL expired allows re-use).

    **TestValidationsAndErrors** (~3):
    - `ttl_seconds <= 0` → ValueError.
    - `max_in_process <= 0` → ValueError.
    - `IdempotencyConflict` raised on PK collision (mocked SQLite to force).

    **TestColumnConsistency** (~1):
    - Reflect the live ORM schema (`IdempotencyCache.__table__.columns`) and the local `_IDEMPOTENCY_TABLE.columns`; assert column names + types + nullability match. Catches drift.

12. **AC-12: Subscriber loop integration is OUT OF SCOPE.** Story 2.7 ships only the cache library + tests. The HTTP middleware integration (where `get_or_run` is actually called per request) lands in **Story 3.6** (FastAPI middleware stack). Story 2.7's tests prove the property at the cache-API level; Story 2.13's 100× replay test proves it end-to-end at the HTTP level.

13. **AC-13: mypy --strict clean.** No `Any`, no `cast()`, no `# type: ignore`. `dict[str, object]` per Story 2.5 pattern. `Callable[[], Awaitable[str]]` for the factory type.

14. **AC-14: `check_single_writer.py` green.** `packages/idempotency/cache.py` lives in `packages/`, not `services/**`. The CI gate excludes `services/registry-state/` ONLY — but `packages/` is also excluded from the scan (per Story 1.6). Verify by reading `scripts/check_single_writer.py` exclusion list. If `packages/` is included in the scan, the cache's INSERT into `idempotency_cache` would trip the gate. **If that happens, the right fix is to update the scanner exclusion list to include `packages/idempotency/` (the cache IS a writer to the cache table, and that's the intended design).**

15. **AC-15: scan-secrets clean.** No new secret patterns introduced.

16. **AC-16: Regression green.**
    - `just test` count bumps from **315 passed, 6 skipped** (post-Story-2.6-fixes) by ≥18 (target: 333+ passed for 18 new tests).
    - `just lint` — all 7 green; mypy --strict on ≥53 source files (was 51; +cache.py +errors.py +test_cache.py).
    - `just bootstrap-verify` — `idempotency 0.2.0`.
    - `just check-gates-self-test` — 3/3.

17. **AC-17: Atomic commit titled** `feat(idempotency): story 2.7 — cache library (TTLCache + SQLite durability) · FR28 NFR-R4`.

## Tasks / Subtasks

- [x] **Task 1: `packages/idempotency/src/idempotency/errors.py`** (AC: #7)
  - [ ] `class IdempotencyConflict(Exception)` with `key: str` field.

- [x] **Task 2: `packages/idempotency/src/idempotency/cache.py`** (AC: #1, #2, #3, #4, #5, #6, #8, #13)
  - [ ] `_IDEMPOTENCY_TABLE: Table` SQLAlchemy Core definition mirroring Story 2.3.
  - [ ] `@dataclass(frozen=True) CacheHit` with 4 fields.
  - [ ] `IdempotencyCacheStore` class with `get`, `store`, `get_or_run`, `sweep_expired`.
  - [ ] `_key_locks: WeakValueDictionary[str, asyncio.Lock]` for per-key serialization.
  - [ ] `_global_lock: asyncio.Lock` for cachetools internal-state guard.
  - [ ] In-process cache (`cachetools.TTLCache`) sized by `max_in_process`.
  - [ ] SQLite UPSERT via `sqlite_insert(_IDEMPOTENCY_TABLE).on_conflict_do_nothing`.
  - [ ] `sweep_expired` issues `delete(_IDEMPOTENCY_TABLE).where(expires_at < clock.now())`.
  - [ ] Module docstring documents the schema-duplication risk + AC-4 reflection-test mitigation.

- [x] **Task 3: `packages/idempotency/src/idempotency/__init__.py` + `pyproject.toml`** (AC: #9, #10)
  - [ ] Add `cachetools>=5.3`, `events>=0.3.0` to dependencies. Version bump 0.1.0 → 0.2.0.
  - [ ] Re-export `CacheHit`, `IdempotencyCacheStore`, `IdempotencyConflict`.
  - [ ] Run `uv sync --all-groups` to refresh the lockfile.

- [x] **Task 4: `packages/idempotency/src/idempotency/test_cache.py`** (AC: #11)
  - [ ] 6 test classes: TestBasicGetSet (5), TestGetOrRun (4), TestTTL (4), TestUPSERT (2), TestValidationsAndErrors (3), TestColumnConsistency (1) = 19 tests.
  - [ ] Use Story 2.2's `FrozenClock` + `TickingClock` for deterministic TTL tests.
  - [ ] Use in-memory SQLite (`sqlite+aiosqlite:///:memory:`) for unit tests; create the table via `_IDEMPOTENCY_TABLE.create(engine)` (synchronous DDL is fine for test fixtures).
  - [ ] **100× concurrency test** uses `asyncio.gather` + a `factory_call_counter: list[int]` to verify factory ran exactly once.

- [x] **Task 5: Verify `check_single_writer.py` exclusion** (AC: #14)
  - [ ] Read `scripts/check_single_writer.py`. If `packages/` is excluded, no change needed. If `packages/idempotency/` writes trip the gate, add `packages/idempotency/` to the exclusion list with a comment explaining "cache is a writer to its OWN cache table per FR28; NOT to tasks/sessions/events".

- [x] **Task 6: Regression + atomic commit** (AC: #15, #16, #17)
  - [ ] `just test` ≥333 passed.
  - [ ] `just lint` 7/7 green; mypy strict on ≥53 files.
  - [ ] `just bootstrap-verify` → `idempotency 0.2.0`.
  - [ ] `just check-gates-self-test` 3/3.
  - [ ] Single atomic commit per AC-17.

## Dev Notes

### Architecture patterns for this story

- **In-process cache + SQLite source-of-truth** (Arch line 205). The fast path is cachetools.TTLCache; the durable path is SQLite. They MUST stay consistent — write-through pattern (SQLite first, in-process second) ensures durability is the gate.
- **No Redis** (Arch line 205 explicit). Single-operator, docker-compose Phase 1; Redis adds operational complexity without benefit at this scale.
- **TTL = 7 days, no exceptions** (PRD line 85, line 128). Configurable in tests, but production default is locked.
- **`packages/` MUST NOT depend on `services/`** (architecture invariant). The cache lives in `packages/idempotency/`; it cannot import `IdempotencyCache` from `registry_state.schema`. Workaround: define the table as SQLAlchemy Core `Table` in cache.py. Duplication risk mitigated by the column-consistency test (AC-11).
- **NFR-R4 = zero duplicate executions under 100× retry storm**. The `get_or_run` per-key lock is THE mechanism that proves this property.

### `get_or_run` per-key locking pattern

```python
class IdempotencyCacheStore:
    def __init__(self, ...):
        self._key_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._key_locks_dict_lock = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._key_locks_dict_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
        return lock

    async def get_or_run(
        self, key: str, *, request_id: str, factory: Callable[[], Awaitable[str]],
    ) -> tuple[CacheHit, bool]:
        lock = await self._lock_for(key)
        async with lock:
            cached = await self.get(key)
            if cached is not None:
                return cached, False
            result_event_id = await factory()
            stored = await self.store(key, result_event_id=result_event_id, request_id=request_id)
            return stored, True
```

The `WeakValueDictionary` lets unused locks GC themselves once all `get_or_run` calls for that key return.

### What this story does NOT do

- No HTTP middleware integration (Story 3.6 / 2.9).
- No Telegram bot integration (Epic 3).
- No 100× HTTP-level test (Story 2.13).
- No background TTL-sweep task (deferred; startup-only sweep).
- No cross-process coordination (FR26 single-writer is the architectural constraint).

### Previous Story Intelligence

- **Story 2.6** (`f83307d` done) shipped snapshots + recovery; established the `dict[str, object]` typing pattern + `_str_field` / `_opt_str_field` helpers + `cap-at-1 + modulo` capture semantics.
- **Story 2.5** (`bc700f7` done) shipped Materializer + apply_many + ON-CONFLICT idempotency on `events.id`. Different scope (event-level dedupe) but similar pattern.
- **Story 2.3** (`cc915d2` + fixes `f139dca`) shipped the `idempotency_cache` ORM model + `ix_idempotency_cache_expires_at` index (explicitly for Story 2.7's TTL sweep).
- **Story 2.2** shipped `Clock` Protocol + `FrozenClock` + `TickingClock` for tests.
- **Story 2.1** shipped `EventEnvelope` — `request_id` is bare UUIDv7 (36 chars, no prefix); matches `idempotency_cache.request_id_on_first_hit` String(36).

### Latest Tech Information

- **`cachetools>=5.3`**: stable Python TTL cache. `TTLCache(maxsize=N, ttl=seconds)` lazy-evicts on access. Not thread-safe; wrap with asyncio.Lock for concurrent safety.
- **`weakref.WeakValueDictionary`**: stdlib. Auto-GC's entries when values are no longer strongly referenced. Perfect for per-key lock storage.
- **SQLAlchemy Core `Table`**: schema definition without ORM. Use `from sqlalchemy import Table, Column, MetaData, String, ...` + `_meta = MetaData()` + `Table("idempotency_cache", _meta, Column("idempotency_key", String(36), primary_key=True), ...)`.
- **`sqlite_insert.on_conflict_do_nothing(index_elements=["idempotency_key"])`**: race-safe insert; subsequent SELECT recovers the winner's row.

### References

- `epics.md` Story 2.7 (lines 785-804).
- `architecture.md` lines 205 (decision), 215 (middleware order), 594-598 (file layout).
- `prd.md` FR28 (852), NFR-R4 (915), idempotency contract (lines 85, 128).
- `2-3-registry-state-sqlite-schema.md` — `IdempotencyCache` ORM model (the source of truth this story duplicates as a Table).

## Dev Agent Record

### Agent Model Used

**Claude Sonnet 4.6** (executor subagent). Two implementation deviations surfaced during lint-gate iteration; both documented below.

### Debug Log References

The IMP001 (`packages/` → `services/`) lint gate caught the AC-11 schema-drift test's import direction. Two suppression mechanisms (custom-scanner `# noqa: IMP001` + ruff `# noqa: I001` for in-function imports) had incompatible formats; resolution was to relocate the test to `services/registry-state/` where the import direction is naturally allowed. Functionally identical.

### Completion Notes List

All 17 ACs satisfied.

- **AC-1 (`IdempotencyCacheStore` public surface):** `CacheHit` dataclass + class with `__init__(*, session_maker, clock, ttl_seconds=604800, max_in_process=100_000)` + `get` / `store` / `get_or_run` / `sweep_expired` methods. Validates `ttl_seconds > 0` AND `max_in_process > 0`.
- **AC-2 (per-key locking via WeakValueDictionary):** `_key_locks: WeakValueDictionary[str, asyncio.Lock]` + `_key_locks_dict_lock` for the dict mutation. `get_or_run` acquires/creates the per-key lock then runs the cache-check + factory + store sequence.
- **AC-3 (TTL = 7 days exact):** 604800-second default; configurable. Both in-process cachetools.TTLCache AND SQLite expires_at use the same boundary.
- **AC-4 (Core Table not ORM):** `_IDEMPOTENCY_TABLE: Table` defined locally in cache.py; mirrors Story 2.3's schema. Module docstring documents the duplication risk + AC-11 mitigation pointer.
- **AC-5 (UPSERT on `store`):** `sqlite_insert.on_conflict_do_nothing(index_elements=["idempotency_key"])` + post-execute SELECT to recover the winner's row.
- **AC-6 (in-process = optimization, SQLite = source of truth):** write-through (SQLite first); read-through (in-process first, SQLite fallback, then populate in-process on hit).
- **AC-7 (`IdempotencyConflict`):** in `errors.py` with `key: str` field. Documented as "should never fire under FR26 single-writer".
- **AC-8 (cachetools wrapping):** `_global_lock: asyncio.Lock` for cachetools internal-state; per-key locks for high-level operations. Two-layer concurrency model.
- **AC-9 (deps + version bump):** `cachetools>=5.3` + `sqlalchemy[asyncio]>=2.0.30` + `events>=0.3.0` workspace dep added. Version 0.1.0 → 0.2.0.
- **AC-10 (re-exports):** `CacheHit`, `IdempotencyCacheStore`, `IdempotencyConflict` re-exported from package `__init__`. `__all__` alphabetical.
- **AC-11 (column-consistency test):** schema-drift detector test moved to `services/registry-state/src/registry_state/test_idempotency_schema_drift.py` (deviation #1 — see below). Verifies column names + nullability + primary keys match between Core Table and ORM model.
- **AC-12 (subscriber loop integration deferred):** correct — Story 3.6 / 6.x is the integration point.
- **AC-13 (mypy strict):** zero `Any`, zero `cast()`, zero `# type: ignore`. mypy --strict clean on 54 files.
- **AC-14 (single-writer green):** zero `# noqa: SW001`. The CI gate's `_EXCLUDED_ROOTS` does not include `packages/idempotency/` because the cache writes only to its OWN `idempotency_cache` table, not to tasks/sessions/events; that's an intentional single-writer boundary that the scanner correctly does not flag (no cross-table writes).
- **AC-15 (scan-secrets):** clean.
- **AC-16 (regression):** `just test` 315+6 → **334 passed + 6 skipped** (+19 exact). `just lint` 7/7. mypy strict 51 → 54 files. `just bootstrap-verify` confirms `idempotency 0.2.0`. `just check-gates-self-test` 3/3.
- **AC-17 (atomic commit):** `f52e991 feat(idempotency): story 2.7 — cache library (TTLCache + SQLite durability) · FR28 NFR-R4`.

**Empirical 100× probe (AC-2 / NFR-R4):** `asyncio.gather(*[get_or_run(same_key, factory)] * 100)` → factory_call_count == 1; all 100 results equal; exactly one caller saw `was_run=True`.

### File List

**New (4):**
- `packages/idempotency/src/idempotency/cache.py` (~330 LOC) — IdempotencyCacheStore + CacheHit + _IDEMPOTENCY_TABLE.
- `packages/idempotency/src/idempotency/errors.py` (~36 LOC) — IdempotencyConflict.
- `packages/idempotency/src/idempotency/test_cache.py` (~530 LOC, 18 tests across 5 classes).
- `services/registry-state/src/registry_state/test_idempotency_schema_drift.py` (~50 LOC, 1 test) — AC-11 schema-drift detector.

**Modified (4):**
- `packages/idempotency/pyproject.toml` — deps + version 0.1.0 → 0.2.0.
- `packages/idempotency/src/idempotency/__init__.py` — re-exports + version bump.
- `pyproject.toml` (workspace lockfile metadata).
- `uv.lock` — cachetools 5.5.x + transitive pins locked.

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-25 | 0.1 | Initial story draft (create-story). |
| 2026-04-25 | 1.0 | Implementation complete. 19 new tests (315+6 → **334+6**). `idempotency` 0.1.0 → 0.2.0. mypy scope 51 → 54 files. **First request-level idempotency primitive** in the platform — combines `cachetools.TTLCache` (in-process fast path) with SQLAlchemy Core access to the `idempotency_cache` table (durability), serialized via per-key `WeakValueDictionary[str, asyncio.Lock]` so factory runs at most once per (key, ttl-window). Empirical 100× probe: `asyncio.gather(*[get_or_run] * 100)` → factory called exactly once, all 100 callers returned same CacheHit. Two deviations: (1) AC-11 schema-drift test relocated from `packages/idempotency/test_cache.py` to `services/registry-state/src/registry_state/test_idempotency_schema_drift.py` due to incompatible noqa formats between IMP001 (custom scanner) and ruff I001/PLC0415; services→packages import direction is allowed, so the test runs cleanly there. (2) `# noqa: N818` on `IdempotencyConflict` matches Story 2.1's `EventSchemaUnknown` precedent. Status → review. Scaffold commit: `f52e991`. |
