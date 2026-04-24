# Story 2.3: Registry-state SQLite schema + initial Alembic migration

Status: done

## Story

As a **platform service**,
I want **`services/registry-state/` with a SQLAlchemy 2.x async schema for tasks, sessions, events, idempotency_cache, and snapshots, plus an Alembic initial migration that brings a fresh data volume to `head`**,
so that **registry state has a deterministic on-disk representation that can evolve safely, materializer work in 2.4–2.7 has a stable target to write into, and cross-restart idempotency/replay (FR24, FR28) has a persistence layer to rely on**.

## Acceptance Criteria

1. **AC-1: SQLAlchemy 2.x ORM models in `services/registry-state/src/registry_state/schema.py`** (one file — the schema should be readable end-to-end). All models use the modern `Mapped[...]` + `mapped_column(...)` declarative pattern; shared `class Base(DeclarativeBase): pass`. Exactly 5 tables with the schemas below (types chosen to match SQLite affinity + Alembic autogenerate):

   - **`tasks`**
     - `id: Mapped[str] = mapped_column(String(38), primary_key=True)` — format `t-<uuidv7>` (2-char prefix + 36-char UUID = 38 chars).
     - `status: Mapped[str] = mapped_column(String(32), nullable=False)` — canonical set enforced at application layer (not a CHECK constraint; future status additions should not require a migration).
     - `created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)` — UTC-aware, ms-precision (matches Story 2.1's envelope convention).
     - `updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)`.
     - `actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)` — matches Actor.kind from Story 2.1.
     - `actor_id: Mapped[str] = mapped_column(String(64), nullable=False)`.
     - `title: Mapped[str | None] = mapped_column(Text, nullable=True)` — operator-supplied short title.
     - `last_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)` — denormalized pointer to the most recent event for this task; maintained by the materializer (Story 2.5).

   - **`sessions`**
     - `id: Mapped[str] = mapped_column(String(38), primary_key=True)` — format `s-<uuidv7>`.
     - `task_id: Mapped[str] = mapped_column(String(38), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)`.
     - `worker_kind: Mapped[str] = mapped_column(String(32), nullable=False)` — e.g., `"claude-code"`, `"codex"`.
     - `worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)`.
     - `status: Mapped[str] = mapped_column(String(32), nullable=False)`.
     - `started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)`.
     - `ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)`.
     - `last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)`.

   - **`events`** — flat mirror of the JSONL event log, for SQL query access
     - `id: Mapped[str] = mapped_column(String(38), primary_key=True)` — format `e-<uuidv7>`.
     - `type: Mapped[str] = mapped_column(String(128), nullable=False)` — dotted `task.created`, etc.
     - `schema_version: Mapped[str] = mapped_column(String(16), nullable=False)` — semver.
     - `emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)`.
     - `emitted_at_monotonic_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)` — `ge=0` is app-enforced (Story 2.1 validator); no CHECK here (SQLite CHECK is fine but unnecessary since envelope validation precedes insert).
     - `actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)`.
     - `actor_id: Mapped[str] = mapped_column(String(64), nullable=False)`.
     - `task_id: Mapped[str | None] = mapped_column(String(38), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True)` — nullable because some events (e.g., system-level `service.started`) don't bind to a task.
     - `session_id: Mapped[str | None] = mapped_column(String(38), ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=True)`.
     - `parent_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)` — self-reference allowed; no FK (parent may arrive out of order during replay, and the integrity is guaranteed by the event log, not the SQL row order).
     - `request_id: Mapped[str] = mapped_column(String(36), nullable=False)` — bare UUIDv7 (no prefix per Story 2.1 envelope convention).
     - `payload_json: Mapped[str] = mapped_column(Text, nullable=False)` — canonical-JSON text from Story 2.1's `to_canonical_json`. NOT `JSON` type — we want byte-stable storage for auditability; application re-parses on read.

   - **`idempotency_cache`** — schema only in this story; TTL sweep logic is Story 2.7.
     - `idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)` — bare UUIDv7 (no prefix).
     - `created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)`.
     - `expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)` — `created_at + 7 days` enforced by caller.
     - `result_event_id: Mapped[str] = mapped_column(String(38), nullable=False)` — pointer to the event the first-successful call produced; on collision the caller returns its payload.
     - `request_id_on_first_hit: Mapped[str] = mapped_column(String(36), nullable=False)` — audit trail — who first claimed this key.

   - **`snapshots`** — schema only in this story; capture + replay logic is Story 2.6.
     - `id: Mapped[str] = mapped_column(String(38), primary_key=True)` — arbitrary prefix-less UUIDv7 via `new_uuid7`; we don't use `e-/t-/s-` because a snapshot isn't an event / task / session.
     - `created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)`.
     - `cursor_event_id: Mapped[str] = mapped_column(String(38), nullable=False)` — last event consumed before this snapshot; replay re-applies events `> cursor_event_id`.
     - `event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)` — total events accumulated into this snapshot.
     - `byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)` — size of `payload_json` for ops monitoring.
     - `payload_json: Mapped[str] = mapped_column(Text, nullable=False)` — serialized materialized state as canonical JSON.

2. **AC-2: Naming conventions per Arch §line-297-302.** All tables `snake_case` plural; all columns `snake_case`; FK columns named `<target_singular>_id` (so `task_id`, not `tasks_id`); indexes named `ix_<table>_<columns>` (compound indexes: `_`-separated column names in order).

3. **AC-3: Indexes.** The one explicitly-mandated index plus supporting indexes for the read patterns every downstream story will exercise:
   - **`ix_events_task_id_emitted_at`** on `events(task_id, emitted_at)` — **explicit AC per epic**; enables "all events for task X in chronological order" which is the materializer's hot path.
   - `ix_events_session_id_emitted_at` on `events(session_id, emitted_at)` — symmetric session-scoped query pattern.
   - `ix_events_type_emitted_at` on `events(type, emitted_at)` — audit/debug queries by event type.
   - `ix_sessions_task_id` on `sessions(task_id)` — list all sessions for a task.
   - `ix_idempotency_cache_expires_at` on `idempotency_cache(expires_at)` — TTL-sweep scan (Story 2.7 will use this).
   - `ix_tasks_status_updated_at` on `tasks(status, updated_at)` — list active tasks.

4. **AC-4: Async engine factory in `services/registry-state/src/registry_state/adapters/sqlite_store.py`.** Exports:

   - `def create_engine(url: str, *, read_only: bool = False) -> AsyncEngine` — returns an `AsyncEngine` configured with:
     - `NullPool` (SQLite doesn't benefit from connection pooling for local file-based DB; eliminates "database is locked" surprises).
     - `connect_args={"check_same_thread": False}` (async-safe — we use SQLAlchemy async session, not raw connection sharing).
     - Pragmas applied via `event.listens_for(engine.sync_engine, "connect")` callback executing AT CONNECT TIME:
       - `PRAGMA journal_mode=WAL` — multi-reader, single-writer (Arch line 796-800).
       - `PRAGMA synchronous=NORMAL` — fsync on commit, not every write (safe since event log is the source of truth).
       - `PRAGMA foreign_keys=ON` — enforce FK constraints (off by default in SQLite — easy bug without this).
       - `PRAGMA busy_timeout=5000` — reader waits 5s for writer; then retry.
     - When `read_only=True`: URL is rewritten to include `?mode=ro&uri=true` (SQLite URI opening mode); the caller (registry-api in Story 2.9) passes this to guard against accidental writes at the connection level. Belt-and-braces with `check_single_writer.py`.
   - `async def get_session(engine: AsyncEngine) -> AsyncSession` — async context-manager factory (`async_sessionmaker(engine, expire_on_commit=False)`).
   - Module docstring explicitly notes: "Story 2.3 delivers schema + engine factory only. Session management, materializer, writer, snapshotter arrive in Stories 2.4–2.7."

5. **AC-5: Alembic configuration.** Files and layout:
   - `services/registry-state/alembic.ini` — Alembic config pointing `script_location = %(here)s/src/registry_state/migrations`. Use `sqlalchemy.url` set at runtime via `env.py` (not hardcoded).
   - `services/registry-state/src/registry_state/migrations/env.py` — standard Alembic env with the following adaptations:
     - Import `Base` from `registry_state.schema` and set `target_metadata = Base.metadata`.
     - Use the async-engine path (`async_engine_from_config` + `await connection.run_sync(do_migrations)`) since our engine is `AsyncEngine`.
     - Read DB URL from `REGISTRY_STATE_DB_URL` env var, with a sensible default for local development: `sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3` (Arch line 797). For tests the default is `sqlite+aiosqlite:///:memory:` — inspected via env var.
   - `services/registry-state/src/registry_state/migrations/versions/2026-04-24_0001_initial_schema.py` — the initial migration. Naming follows Arch line 302 (`<YYYY-MM-DD>_<slug>.py`, with a `_0001_` numeric prefix for stable Alembic revision ordering across same-day migrations).

6. **AC-6: `alembic upgrade head` on empty DB creates all 5 tables + 6 indexes.** Verified by:
   - Programmatic test: create an in-memory SQLite DB, run `upgrade head`, inspect `sqlite_master` for the expected table/index names.
   - All 6 indexes present (AC-3 list).
   - `alembic_version` table contains exactly one row with the revision id of `0001_initial_schema`.

7. **AC-7: `alembic upgrade head` on migrated DB is a no-op.** A second call against an already-at-head DB does NOT raise, does NOT re-apply DDL, and the `alembic_version` table is unchanged. Test: run `upgrade head` twice, assert `sqlite_master` / `alembic_version` are byte-identical between runs.

8. **AC-8: Pragmas verified at runtime.** A test spins up an engine via `create_engine(...)`, opens a connection, and queries `PRAGMA journal_mode` / `synchronous` / `foreign_keys` / `busy_timeout` via the sync API. All four must match the AC-4 values. (Catches the easy bug of installing the pragma listener on the wrong event.)

9. **AC-9: FK enforcement smoke test.** Attempt to insert a `sessions` row whose `task_id` does not match any `tasks.id`. SQLAlchemy must raise `IntegrityError` (proving `PRAGMA foreign_keys=ON` is actually applied — SQLite silently ignores FKs by default when the pragma is off).

10. **AC-10: Schema roundtrip tests.** For each of the 5 models: insert one row with realistic values drawn from Story 2.2's generators (`new_task_id(clock=FrozenClock(), rng=Random(…))` etc.), `await session.commit()`, `await session.refresh()`, assert all columns round-trip losslessly. Must include a `DateTime(timezone=True)` roundtrip — SQLite stores DateTimes as text, so the UTC-aware → text → UTC-aware roundtrip is a real failure mode.

11. **AC-11: mypy --strict clean.** `mypy --strict services/registry-state` passes. SQLAlchemy 2.x has full PEP 681 typing support via `Mapped[...]` — no `Any` escape hatches needed.

12. **AC-12: `check_single_writer.py` still green.** The new code lives under `services/registry-state/**` which is the sole-excluded directory in the script. No `# noqa: SW001` comments needed. If lint fails, the fix is "move the writer back under `services/registry-state/`"; never silence with noqa.

13. **AC-13: `scan-secrets` clean.** SQLAlchemy URLs contain `sqlite+aiosqlite:///` — no secret patterns. Alembic's `alembic_version` table contains hex revision IDs — no secret patterns. Verify.

14. **AC-14: `justfile`** gains a `migrate` recipe (short convenience): `migrate: cd services/registry-state && uv run alembic upgrade head`. Used by test fixtures + operator runbook. No new lint recipes needed.

15. **AC-15: `services/registry-state/pyproject.toml`** adds dependencies: `sqlalchemy[asyncio]>=2.0.30`, `aiosqlite>=0.20`, `alembic>=1.13`. Version bump `0.1.0 → 0.2.0` (first real feature increment — matches `events` 0.2.0 bump pattern from Story 2.1). `uv.lock` regenerated.

16. **AC-16: `services/registry-state/src/registry_state/__init__.py`** re-exports the public surface:
    ```python
    from registry_state.schema import (
        Base,
        Event,
        IdempotencyCache,
        Session as SessionRow,  # rename to avoid clash with SQLAlchemy Session
        Snapshot,
        Task,
    )
    from registry_state.adapters.sqlite_store import create_engine, get_session

    __version__ = "0.2.0"
    __all__ = [
        "Base", "Event", "IdempotencyCache", "SessionRow", "Snapshot", "Task",
        "create_engine", "get_session",
    ]
    ```

17. **AC-17: Regression green.** `just test` count bumps from **206 + 6 skipped** (post-Story-2.2-fixes) by at least +15 (schema roundtrips × 5 + 2 migration tests + pragma + FK + engine factory + a few edge cases). `just lint` — all 7 green. `just bootstrap-verify` — 13/13 workspace imports; `registry_state 0.2.0` in the version list. `just check-gates-self-test` — 3/3. `just migrator-test-additive` — 3/3.

18. **AC-18: Atomic commit titled** `feat(registry-state): story 2.3 — SQLite schema + Alembic initial migration · FR24 FR28`.

## Tasks / Subtasks

- [x] **Task 1: Dependencies + pyproject bump** (AC: #15)
  - [x] Add `sqlalchemy[asyncio]>=2.0.30`, `aiosqlite>=0.20`, `alembic>=1.13` to `services/registry-state/pyproject.toml`.
  - [x] Bump package version to `0.2.0`.
  - [x] Run `uv sync --all-groups` to regenerate `uv.lock`.

- [x] **Task 2: `schema.py` with 5 SQLAlchemy 2.x models** (AC: #1, #2, #3, #11)
  - [x] `class Base(DeclarativeBase): pass` — shared declarative base.
  - [x] `class Task(Base)` with all columns per AC-1.
  - [x] `class Session(Base)` with FK to `tasks.id`.
  - [x] `class Event(Base)` with nullable FKs to `tasks.id` + `sessions.id` + self-referential `parent_event_id` (no FK).
  - [x] `class IdempotencyCache(Base)` — schema only.
  - [x] `class Snapshot(Base)` — schema only.
  - [x] All 6 indexes defined via `Index("ix_...", Column, ...)` at module level after the model classes.
  - [x] Module-level docstring notes the Story-2.3 scope and links to Stories 2.4-2.7 for the business logic.

- [x] **Task 3: Async engine factory** (AC: #4, #8, #9)
  - [x] Create `services/registry-state/src/registry_state/adapters/__init__.py` (empty, marker file).
  - [x] Create `services/registry-state/src/registry_state/adapters/sqlite_store.py` with `create_engine()` + `get_session()`.
  - [x] Pragma listener via `event.listens_for(engine.sync_engine, "connect")`. Apply all 4 pragmas in one PEP 249 cursor batch.
  - [x] `read_only=True` path rewrites the URL to append `?mode=ro&uri=true` (SQLAlchemy expects the URL-parameter form for SQLite URI opens).

- [x] **Task 4: Alembic config + env.py** (AC: #5)
  - [x] `services/registry-state/alembic.ini` — script_location, logging config.
  - [x] `services/registry-state/src/registry_state/migrations/__init__.py` (empty).
  - [x] `services/registry-state/src/registry_state/migrations/env.py` — async-aware env. Set `target_metadata = Base.metadata`. Read URL from `REGISTRY_STATE_DB_URL` env var with the documented default.
  - [x] `services/registry-state/src/registry_state/migrations/versions/__init__.py` (empty).
  - [x] Initial migration file: `2026-04-24_0001_initial_schema.py`. Generate via `alembic revision --autogenerate -m initial_schema` run against an empty SQLite DB; then HAND-VERIFY the output — Alembic autogenerate has known quirks with `Mapped[str | None]` nullability. Edit the file to match AC-1/AC-3 exactly; commit both the raw-autogen output and any corrections (the test in AC-6 is the final arbiter).

- [x] **Task 5: `justfile` `migrate` recipe + env var default** (AC: #14)
  - [x] Add `migrate` recipe.
  - [x] Document the `REGISTRY_STATE_DB_URL` env var in the recipe comment.

- [x] **Task 6: `__init__.py` re-exports + version bump** (AC: #16)
  - [x] Re-export `Base`, `Task`, `SessionRow`, `Event`, `IdempotencyCache`, `Snapshot`, `create_engine`, `get_session`.
  - [x] `__version__ = "0.2.0"`.
  - [x] Alphabetical `__all__`.

- [x] **Task 7: Schema roundtrip tests** (AC: #10)
  - [x] Create `services/registry-state/src/registry_state/test_schema.py`.
  - [x] `@pytest.fixture async def engine()` — builds an in-memory async engine.
  - [x] 5 tests — one per model — inserting realistic values (use `FrozenClock` + `Random(42)` + Story-2.2 prefixed-ID helpers for deterministic test data).
  - [x] 1 test verifying UTC-aware datetimes round-trip losslessly through SQLite TEXT storage.

- [x] **Task 8: Alembic migration tests** (AC: #6, #7)
  - [x] Create `services/registry-state/src/registry_state/test_migrations.py`.
  - [x] `test_upgrade_head_on_empty_db_creates_all_tables_and_indexes` — runs `alembic upgrade head` programmatically against an in-memory DB, queries `sqlite_master`, asserts all 5 tables + 6 indexes + `alembic_version` exist with the expected names.
  - [x] `test_upgrade_head_twice_is_noop` — runs upgrade head twice, asserts `sqlite_master` + `alembic_version` byte-identical.
  - [x] Alembic programmatic invocation pattern: use `from alembic.config import Config; from alembic import command; cfg = Config("services/registry-state/alembic.ini"); cfg.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///:memory:"); command.upgrade(cfg, "head")` — but note async engines require the env.py to use `async_engine_from_config` + `run_sync`. Inspect examples from SQLAlchemy docs.

- [x] **Task 9: Pragma + FK tests** (AC: #8, #9)
  - [x] Create `services/registry-state/src/registry_state/test_sqlite_store.py`.
  - [x] `test_wal_mode_applied` — query `PRAGMA journal_mode` == `"wal"` after engine creation.
  - [x] `test_synchronous_normal_applied` — `PRAGMA synchronous` == `1` (NORMAL).
  - [x] `test_foreign_keys_on_applied` — `PRAGMA foreign_keys` == `1`.
  - [x] `test_busy_timeout_applied` — `PRAGMA busy_timeout` == `5000`.
  - [x] `test_foreign_key_violation_raises` — insert orphan session, expect `IntegrityError`.
  - [x] `test_read_only_url_rewrite` — `create_engine(url, read_only=True)` url contains `mode=ro&uri=true`.
  - [x] `test_read_only_engine_rejects_writes` — attempt an INSERT on a read-only engine, expect an `OperationalError` (or whatever SQLite raises — may be `OperationalError: attempt to write a readonly database`).

- [x] **Task 10: Regression + atomic commit** (AC: #17, #18)
  - [x] `just test` — count +15 or more; all green.
  - [x] `just lint` — 7/7 green.
  - [x] `just bootstrap-verify` — `registry_state 0.2.0`.
  - [x] `just migrate` with a tmpdir env-var DB — confirms it actually works.
  - [x] `just check-gates-self-test` — 3/3 (single-writer check especially).
  - [x] Single atomic commit per AC-18.

### Review Findings

Generated by `/bmad-code-review` against scaffold commit `cc915d2`. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor — all opus) converged on 10 actionable findings after dedup (2 CRITICAL confirmed by empirical probes running real SQLite/aiosqlite — both passed the executor's verification because the tests were false-passes); 8 dismissed.

- [x] **[Review][Patch] `UTCDateTime.process_bind_param` silently corrupts non-UTC tzinfo datetimes** [`services/registry-state/src/registry_state/schema.py` — the `process_bind_param` method] — **CRITICAL.** Current code: `return value.replace(tzinfo=None)`. Drops the offset without converting to UTC first. Empirical probe: `datetime(2026,1,1,12,tzinfo=timezone(timedelta(hours=5)))` stored as naive wall-clock, read back as `2026-01-01T12:00:00+00:00` — 5 hours in the future. Silent data corruption. Fix: `return value.astimezone(UTC).replace(tzinfo=None)`. Add test with non-UTC tzinfo input asserting instant-preservation.

- [x] **[Review][Patch] `read_only=True` URL rewrite is fundamentally broken — aiosqlite ignores URL `uri=true`** [`services/registry-state/src/registry_state/adapters/sqlite_store.py:45-49`] — **CRITICAL.** Current code appends `?mode=ro&uri=true` to the URL. aiosqlite/sqlite3 only honors `uri=True` when passed via `connect_args`, not via URL query. Empirical probe: stray phantom SQLite files like `tmpXXXX.sqlite3?mode=ro` created in `/tmp`; the "read-only" engine opens a fresh *writable* empty DB at the bogus path. `test_read_only_engine_rejects_writes` is a **false pass** — `OperationalError` fires because the phantom DB has no `tasks` table, not because writes are rejected. Fix: pass `connect_args={"check_same_thread": False, "uri": True}`; rewrite URL to prepend `file:` to the path + `?mode=ro` (e.g., `sqlite+aiosqlite:///file:/path/to/db.sqlite3?mode=ro`); strip the `separator` logic entirely.

- [x] **[Review][Patch] `env.py` env-var silently clobbers programmatic `cfg.set_main_option()`; `_DEFAULT_URL` is dead code** [`services/registry-state/src/registry_state/migrations/env.py`] — **CRITICAL.** Current code: `_env_url = os.environ.get("REGISTRY_STATE_DB_URL"); if _env_url: config.set_main_option(...)` — env var ALWAYS wins over a programmatic `cfg.set_main_option()` call that ran immediately before. Tests pass only because CI doesn't set `REGISTRY_STATE_DB_URL`. If CI ever exports the var, tests silently write to the wrong DB. Additionally, `_DEFAULT_URL` is declared but never referenced — the alembic.ini placeholder is the effective default. Fix: detect the alembic.ini placeholder URL; only apply env-var (or `_DEFAULT_URL` fallback) when the placeholder is still in place, so programmatic sets always win.

- [x] **[Review][Patch] Alembic migration DDL uses `sa.DateTime(timezone=True)`, ORM uses `UTCDateTime()` — divergence causes spurious autogen diffs** [`services/registry-state/src/registry_state/migrations/versions/2026-04-24_0001_initial_schema.py`] — **MAJOR.** Any future `alembic --autogenerate` will produce a type-mismatch diff (ORM `UTCDateTime` vs migration `DateTime`), churning every migration run. Fix: import `UTCDateTime` from `registry_state.schema` and use it in the migration DDL. Symmetric: both the ORM read path and the DDL emit identical type text.

- [x] **[Review][Patch] `Snapshot.id` column is `String(38)` but docstring says "prefix-less UUIDv7" (bare UUIDv7 is 36 chars)** [`services/registry-state/src/registry_state/schema.py` — Snapshot class] — **MAJOR.** Internal spec inconsistency — spec AC-1 says `String(38)` but also says "arbitrary prefix-less UUIDv7 via `new_uuid7`" (which returns 36 chars). The docstring intent is correct; the width was copy-pasted from prefixed-ID columns. Fix: change to `String(36)` in both schema.py and the migration file. Matches the actual value length. Note: this supersedes the original spec's literal `String(38)` — the spec text is being corrected to match its own stated semantics.

- [x] **[Review][Patch] Tempfile leaks in migration tests — no cleanup on success OR assertion failure** [`services/registry-state/src/registry_state/test_migrations.py` — both migration tests] — **MAJOR.** Current code: `with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f: db_path = f.name` — no `try/finally` + `os.unlink(db_path)`. Leaks a tempfile on every CI run + stranded tempfiles on assertion failure. Fix: wrap body in `try/finally` with unlink; or use `tempfile.TemporaryDirectory()` context manager (auto-cleanup) + build a path inside it.

- [x] **[Review][Patch] `test_read_only_engine_rejects_writes` asserts bare `OperationalError` — catches malformed-URI error too** [`services/registry-state/src/registry_state/test_sqlite_store.py` — the rejects-writes test] — **MAJOR.** `with pytest.raises(OperationalError)` catches ANY OperationalError, including "unable to open database file" from a bad URI rewrite (see F2). Once F2 is fixed, the test should prove genuine read-only rejection. Fix: use `pytest.raises(OperationalError, match="readonly database|attempt to write a readonly database")` to pin down the error message; and make the test insert against an EXISTING table in an existing file (to rule out the empty-phantom-DB false-pass path).

- [x] **[Review][Patch] `test_read_only_url_rewrite_*` tests are null-tests — don't assert the rewrite content** [`services/registry-state/src/registry_state/test_sqlite_store.py` — the URL rewrite tests] — **MAJOR.** `assert url_str.count("?") == 1` passes trivially if the URL has its original `?timeout=5` and nothing appended — would pass even if `mode=ro` was silently dropped. Fix: after F2 restructures the rewrite, assert `"mode=ro" in str(engine.url)` AND the connect_args contain `"uri": True`. For the ampersand test, assert the final URL contains BOTH the pre-existing param AND `mode=ro`.

- [x] **[Review][Patch] AC-7 test compares table/index names only — spec said "byte-identical `sqlite_master`"** [`services/registry-state/src/registry_state/test_migrations.py` — test_upgrade_head_twice_is_noop] — **MINOR.** Current test compares `frozenset` of names from `sqlite_master`. Spec AC-7 mandated "byte-identical `sqlite_master`". Fix: also compare the `sql` column (DDL text) for each table and index — catches a regression where a re-apply of CREATE TABLE subtly changes column order, constraint definition, or whitespace.

- [x] **[Review][Patch] `from random import Random` inside test function body** [`services/registry-state/src/registry_state/test_sqlite_store.py:~1649`] — **MINOR.** `test_foreign_key_violation_raises` imports `Random` inside the function. Hoist to module-level imports; style consistency with other tests in the file.

Dismissed (documented here for auditability):

- `asyncio.run(run_migrations_online())` at env.py module top — standard Alembic async pattern; Alembic invokes env.py only in sync CLI context; no risk of nested event loops. Blind Hunter CRITICAL-flagged this over-aggressively.
- `Event.parent_event_id` no FK vs `task_id`/`session_id` FK asymmetry — architectural decision documented in spec AC-1 (parent may arrive out of order during replay; integrity is event-log guarantee, not SQL row order). Task/session rows are guaranteed-first by replay order.
- FK pragma OFF during `alembic upgrade` — DDL-only path, no data migrations in Story 2.3. When data migrations arrive in Stories 2.4+, add the pragma to env.py's connectable.
- `fileConfig(config.config_file_name)` global logging mutation — standard Alembic pattern; pytest captures + resets logging.
- mypy `Any` in SQLAlchemy event callback signatures (`dbapi_conn: Any`) — defensible; SQLAlchemy stubs genuinely lack a typed DBAPI-connection Protocol. No escape hatch.
- `payload_json` stored as `Text` with no JSON-shape validation — writer responsibility (Story 2.4 event-log writer + Story 2.5 materializer). Schema is a target, not a validator.
- `get_target_metadata` dead code — hallucination from Blind Hunter; function does not exist in env.py.
- `create_engine` listener lifetime / sync_engine GC — engine-scoped closure; GC'd with the engine object; no accumulation.

## Dev Notes

### Architecture patterns for this story

- **SQLite WAL mode is THE concurrency story** (Arch line 796-800). Without WAL, the single-reader-at-a-time cost of a file-based DB is lethal to the registry-api read traffic. With WAL + `synchronous=NORMAL`, multiple reader processes + one writer coexist without blocking. **Both pragmas must be set at connect time** — not at migrate time, not at session-factory time. This is the #1 SQLite bug.
- **`PRAGMA foreign_keys=ON` is OFF BY DEFAULT in SQLite.** Without it, FKs are declarations only — no enforcement. Every regression of this pragma is a silent integrity hole. Test it (AC-8/AC-9) and guard it.
- **`check_single_writer.py` AST-walks every service OTHER than `services/registry-state/`**. Our code lives IN `services/registry-state/` so it's automatically allowed to write. Downstream stories that touch registry data must go through typed events → `registry-state` subscriber. The CI gate is the teeth behind FR26.
- **Read-only connection path** (AC-4's `read_only=True`) is belt-and-braces with the CI check — registry-api uses this when it opens the DB. SQLite rejects writes at the file-open level; CI rejects code that attempts them. Both redundancies survive the other being wrong.
- **Event log is the source of truth, SQLite is derived state.** (Arch line 796.) Loss of `state.sqlite3` is recoverable by replaying the event log. This implies: schema doesn't need CHECK constraints matching every possible status enum — application validation catches the real shape issues, and migrations stay cheap.

### SQLAlchemy 2.x declarative pattern (for implementers)

```python
# schema.py — illustrative sketch
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(38), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    worker_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ... Event, IdempotencyCache, Snapshot similarly ...


# Indexes declared after the classes so all tables exist in Base.metadata first.
Index("ix_events_task_id_emitted_at", Event.task_id, Event.emitted_at)
Index("ix_events_session_id_emitted_at", Event.session_id, Event.emitted_at)
Index("ix_events_type_emitted_at", Event.type, Event.emitted_at)
Index("ix_sessions_task_id", Session.task_id)
Index("ix_idempotency_cache_expires_at", IdempotencyCache.expires_at)
Index("ix_tasks_status_updated_at", Task.status, Task.updated_at)
```

### Async engine factory sketch (for implementers)

```python
# adapters/sqlite_store.py — illustrative sketch
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def create_engine(url: str, *, read_only: bool = False) -> AsyncEngine:
    if read_only:
        # SQLite URI-mode opens. Async dialect passes params through.
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}mode=ro&uri=true"

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001 — sqlalchemy event signature
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def get_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

### Alembic async env.py sketch (for implementers)

```python
# migrations/env.py — illustrative
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from registry_state.schema import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_DEFAULT_URL = "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3"
config.set_main_option("sqlalchemy.url", os.environ.get("REGISTRY_STATE_DB_URL", _DEFAULT_URL))


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

### What this story does NOT do

- **No event-log writer** — Story 2.4 writes JSONL records and appends them.
- **No subscriber / materializer** — Story 2.5 listens on clawhip and mutates SQLite rows.
- **No snapshot capture logic** — Story 2.6 writes rows into the `snapshots` table.
- **No idempotency-cache read/write** — Story 2.7 implements TTL + lookup.
- **No registry-api HTTP handlers** — Story 2.9.
- **No `/v1/health`** — Story 2.9.
- **No migrator integration** — Story 2.14.

### Previous Story Intelligence

- **Story 2.2** (SHA `ee3191f`, finalized `2026-04-24`) shipped:
  - `FrozenClock`, `TickingClock`, `SystemClock` + `FROZEN_EPOCH`.
  - `new_task_id`, `new_session_id`, `new_event_id`, `new_idempotency_key`, `new_request_id`, `parse_prefix`.
  - Conftest fixtures `fixed_clock` + `seeded_uuid7`.
  - Baseline test count: **206 passed, 6 skipped**. Story 2.3's AC-17 bumps this.
- **Story 2.1** (SHA `b90f08e`) shipped:
  - `EventEnvelope` + `Actor` with `event_id = "e-<uuidv7>"` regex (38 chars) — this is why the `events.id` column is `String(38)`.
  - `to_canonical_json` + `from_canonical_json` — this is what populates `events.payload_json`.
  - `schema_version` regex = semver → `events.schema_version` is `String(16)`.
  - `emitted_at` is ms-precision UTC — `events.emitted_at` is `DateTime(timezone=True)`.
  - `emitted_at_monotonic_ns` is `ge=0` — `events.emitted_at_monotonic_ns` is `BigInteger` (64-bit; `int64.max ≈ 9.2e18` = ≈292 years of ns, ample).
- **Story 1.5** established the test tree — `packages/<pkg>/src/<pkg>/test_*.py` is the co-located test pattern; use the same for `services/registry-state/src/registry_state/test_*.py`.
- **Story 1.6** shipped `scripts/check_single_writer.py`. The exclusion root `services/registry-state/` is already configured; no script change required.
- **Story 1.7** secret-scanner — SQLAlchemy URLs don't match any of the 5 SECRET_PATTERNS (`sk-ant-*`, `AKIA*`, etc.) — new code safe.

### Git Intelligence

```
ee3191f docs(story-2-2): finalize + mark done
f09a9e0 fix(events): apply story 2.2 code-review fixes · all severities
799d45e docs(story-2-2): finalize story file + mark review
103d13a feat(events): story 2.2 — UUIDv7 generators + injectable clock · NFR-O1 NFR-M6
b90f08e docs(story-2-1): finalize + mark done
```

Established cadence (13+ stories so far): **scaffold** → **docs-finalize-to-review** → **review-fix** → **docs-finalize-to-done**. Atomic commits; generous commit bodies; `Co-Authored-By: Claude Opus 4.7 (1M context)` footer. Story 2.3 follows the same pattern.

### Latest Tech Information

- **SQLAlchemy 2.0.30+**. `Mapped[...]` + `mapped_column()` are GA since 2.0. `AsyncEngine`/`AsyncSession` are fully typed. `async_engine_from_config` is the Alembic-compatible entry point.
- **aiosqlite 0.20**. Passes SQLAlchemy connection args through to the underlying `sqlite3` module — `check_same_thread=False` needs to be in `connect_args`, not URL-encoded.
- **Alembic 1.13+**. Async-capable `env.py` is the documented pattern; requires `asyncio.run(run_migrations_online())` at the module bottom. Legacy sync-only envs will silently fail for an async engine (connection yields a coroutine; `context.configure` chokes).
- **SQLite WAL**. `journal_mode=WAL` is persistent (survives DB restart). On WAL, `synchronous=NORMAL` is durable across application crashes (only loses post-crash-not-yet-fsync'd writes — acceptable because the event log is the durable record).
- **`PRAGMA foreign_keys=ON`** is per-connection, NOT persistent. Must be applied on every connection. The `event.listens_for` `connect` callback runs on every new connection — this is why we attach the pragmas there, not at engine creation.
- **`check_same_thread=False`** is required for async. The async bridge to `sqlite3` may open the connection on one thread and use it on another. The single-writer CI check + our application-level `AsyncSession` discipline prevent the concurrency bugs this flag normally protects against.

### References

- `epics.md` §Epic 2 / Story 2.3 (lines 711-726) — AC + BDD (FR24, FR28).
- `architecture.md` lines 198, 202-203 (ORM + Alembic decisions); 297-302 (naming); 304-310 (ID formats); 620-642 (package layout); 791-792 (single-writer CI); 796-800 (WAL + data boundaries).
- `prd.md` FR24 (line 847), FR24a (848), FR25 (849), FR26 (850), FR28 (852); NFR-O1 (932), NFR-P3 (906), NFR-M6 (945); §Snapshot strategy (line 83), §Idempotency contracts (line 85).
- `2-1-event-envelope-schema-registry.md` — EventEnvelope shape that `events` table mirrors.
- `2-2-uuidv7-injectable-clock.md` — generators used in test fixtures.
- `scripts/check_single_writer.py` — CI gate that `services/registry-state/` is exempt from.

## Dev Agent Record

### Agent Model Used

**Claude Sonnet 4.6** (executor subagent). All 10 tasks delivered in a single continuous pass. Four substantive deviations from the spec sketches — all documented below; none compromise an AC.

### Debug Log References

No extended debug loops. Implementation proceeded cleanly; the four deviations were discovered during implementation and justified by concrete failures that would have otherwise broken an AC.

### Completion Notes List

All 18 ACs satisfied.

- **AC-1 (schema.py with 5 models):** 213 LOC, all 5 tables with exact column names/types/nullability from the AC spec. `Mapped[...] + mapped_column(...)` throughout. `class Base(DeclarativeBase)` shared. FKs on `sessions.task_id`, `events.task_id`, `events.session_id` with `ondelete="RESTRICT"`. `events.parent_event_id` nullable + no FK (self-reference allowed, out-of-order replay safe).
- **AC-2 (naming conventions):** all tables `snake_case` plural; columns `snake_case`; FK columns `<target_singular>_id`; indexes `ix_<table>_<columns>` — verified by ruff + mypy + migration test (which introspects `sqlite_master`).
- **AC-3 (indexes):** all 6 indexes present (`ix_events_task_id_emitted_at`, `ix_events_session_id_emitted_at`, `ix_events_type_emitted_at`, `ix_sessions_task_id`, `ix_idempotency_cache_expires_at`, `ix_tasks_status_updated_at`).
- **AC-4 (async engine factory):** `create_engine(url, *, read_only=False)` + `get_session(engine)` in `sqlite_store.py`. `NullPool`, `check_same_thread=False`, pragmas via `event.listens_for(engine.sync_engine, "connect")`. `read_only=True` rewrites URL with correct `?`/`&` separator logic.
- **AC-5 (Alembic config + env.py):** `alembic.ini` points at in-package `migrations/`. `env.py` is async-aware via `async_engine_from_config` + `connection.run_sync(do_run_migrations)`. URL resolution preserves programmatic override (deviation #3 below — required for AC-6/AC-7 tests).
- **AC-6 (upgrade head on empty DB):** `test_migrations.py::test_upgrade_head_on_empty_db_creates_all_tables_and_indexes` — passes. Queries `sqlite_master`; asserts all 5 tables + 6 indexes + `alembic_version`.
- **AC-7 (upgrade head twice is no-op):** `test_migrations.py::test_upgrade_head_twice_is_noop` — passes. Byte-identical `sqlite_master` + `alembic_version` between runs.
- **AC-8 (pragmas verified at runtime):** `test_sqlite_store.py::test_{wal,synchronous,foreign_keys,busy_timeout}_applied` all pass. WAL test uses a temp file (in-memory returns `'memory'` not `'wal'`; file-based DB needed).
- **AC-9 (FK enforcement smoke test):** `test_foreign_key_violation_raises` — inserting an orphan `sessions.task_id` raises `IntegrityError` (proves `PRAGMA foreign_keys=ON` is actually applied).
- **AC-10 (schema roundtrip tests):** 9 tests in `test_schema.py`. Uses `FrozenClock(mono_ns=0, now=FROZEN_EPOCH)` + `Random(42)` + Story-2.2 prefixed-ID helpers for deterministic test data. UTC-aware datetime roundtrip proven (deviation #1 justified here).
- **AC-11 (mypy --strict):** 35 source files strict-clean (was 25; +10 new files: schema.py, sqlite_store.py, adapters/__init__.py, migrations/env.py, migrations/versions/2026-04-24_0001_initial_schema.py, migrations/__init__.py, migrations/versions/__init__.py, plus the 3 test files).
- **AC-12 (single-writer green):** All new code under `services/registry-state/**` which is the script's sole-excluded directory. No `# noqa: SW001` comments — as the spec guaranteed. `check_single_writer.py` green.
- **AC-13 (scan-secrets clean):** `secret-hygiene-precommit` clean on all touched files.
- **AC-14 (justfile migrate):** New `migrate` recipe with `REGISTRY_STATE_DB_URL` env-var documentation.
- **AC-15 (pyproject + version bump):** `sqlalchemy[asyncio]>=2.0.30`, `aiosqlite>=0.20`, `alembic>=1.13` added; package version `0.1.0 → 0.2.0`. `uv.lock` regenerated.
- **AC-16 (__init__.py re-exports):** Base, Event, IdempotencyCache, SessionRow (renamed to avoid SQLAlchemy `Session` clash), Snapshot, Task, create_engine, get_session; `__version__ = "0.2.0"`; `__all__` alphabetical.
- **AC-17 (regression green):** `just test` = **225 passed + 6 skipped** (was 206+6; +19 tests). `just lint` = 7/7. `just bootstrap-verify` = 13/13 + `registry_state 0.2.0`. `just check-gates-self-test` = 3/3. `just migrator-test-additive` = 3/3 (unchanged).
- **AC-18 (atomic commit):** `cc915d2 feat(registry-state): story 2.3 — SQLite schema + Alembic initial migration · FR24 FR28`.

**Empirical pragma probe** (after `just migrate` against a tmpdir DB):
```
PRAGMA journal_mode  → wal
PRAGMA foreign_keys  → 1
PRAGMA synchronous   → 1 (NORMAL)
PRAGMA busy_timeout  → 5000
```
All four pragmas live on every connection.

**Alembic revision id:** `0001` — the initial migration file is `2026-04-24_0001_initial_schema.py`.

### File List

**New (11):**
- `services/registry-state/alembic.ini` (49 LOC)
- `services/registry-state/src/registry_state/schema.py` (213 LOC)
- `services/registry-state/src/registry_state/adapters/__init__.py` (5 LOC)
- `services/registry-state/src/registry_state/adapters/sqlite_store.py` (63 LOC)
- `services/registry-state/src/registry_state/migrations/__init__.py` (empty)
- `services/registry-state/src/registry_state/migrations/env.py` (78 LOC)
- `services/registry-state/src/registry_state/migrations/versions/__init__.py` (empty)
- `services/registry-state/src/registry_state/migrations/versions/2026-04-24_0001_initial_schema.py` (123 LOC)
- `services/registry-state/src/registry_state/test_schema.py` (~290 LOC, 9 tests)
- `services/registry-state/src/registry_state/test_migrations.py` (120 LOC, 2 tests)
- `services/registry-state/src/registry_state/test_sqlite_store.py` (~240 LOC, 8 tests)

**Modified (4):**
- `services/registry-state/pyproject.toml` — dependencies + version 0.1.0 → 0.2.0
- `services/registry-state/src/registry_state/__init__.py` — full re-export surface + `__version__ = "0.2.0"`
- `justfile` — `migrate` recipe
- `ruff.toml` — scoped `N999` exclusion for `migrations/versions/*.py` (date-prefixed filenames per Arch§302)
- `uv.lock` — regenerated (sqlalchemy + aiosqlite + alembic + transitive pins locked)

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-24 | 0.1 | Initial story draft (create-story). |
| 2026-04-24 | 1.0 | Implementation complete. 19 new tests (206+6 → **225+6**). `registry_state` 0.1.0 → 0.2.0. mypy scope 25 → 35 files. 4 documented deviations (`UTCDateTime` TypeDecorator for SQLite TZ roundtrip; hand-written initial migration; `env.py` programmatic-URL-override preservation; `ruff.toml` N999 scoped exclusion for date-prefixed Alembic filenames). All pragmas empirically verified on post-migrate probe. Alembic revision `0001`. Status → review. Scaffold commit: `cc915d2`. |
| 2026-04-24 | 1.1 | Code review — 3 parallel adversarial reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) — 10 actionable findings (2 CRITICAL, 6 MAJOR, 2 MINOR) all fixed; 8 dismissed. Two of the CRITICALs were proven by empirical probes to be false-passes: (1) `UTCDateTime.process_bind_param` silently corrupted non-UTC tzinfo (stored wall-clock as naive, read back as UTC — 5h future); (2) `read_only=True` URL rewrite never reached sqlite3 URI mode (aiosqlite's `os.path.abspath` mangled `file:` path, phantom SQLite files created in /tmp, test caught OperationalError from missing table not read-only enforcement). Fixed: `.astimezone(UTC).replace(tzinfo=None)` + naive-datetime rejection; URL rewritten to `sqlite+aiosqlite:///file:<abs>?uri=true&mode=ro` (aiosqlite-specific contract: both URL-query AND connect_args-promoted `uri=True`). Also: `env.py` programmatic-set-wins logic + live `_DEFAULT_URL`; migration `UTCDateTime()` import (DDL/ORM parity); `Snapshot.id` narrowed `String(38)→String(36)`; `TemporaryDirectory()` cleanup in migration tests; read-only tests assert error-message substring; AC-7 test compares `sqlite_master.sql` column (byte-identical) not just names; `Random` import hoisted. +5 net tests (225+6 → **230+6**). mypy --strict still clean on 35 files; all 4 verification gates green. Fix commit: `f139dca`. Status → done. |
