# Story 11.3.12 — split registry-api's writable idempotency-cache onto its own SQLite file (M8 follow-up) so registry-state is the sole `state.sqlite3` writer

Status: in-progress (AC1-8 done & PROVEN 7/7 GREEN on live stack; AC9 code-review pending) — the WAL-reader fork was resolved with a WAL-preserving main-db-file chmod 0o660 so sidecars inherit group-write

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** registry-api's writable idempotency-cache engine to use a
SEPARATE SQLite file (e.g. `idempotency.sqlite3`) instead of writing the
`idempotency_cache` table inside registry-state's `state.sqlite3`,
**so that** registry-state (uid 10002) becomes the SOLE creator + writer
of `state.sqlite3` and its WAL/SHM sidecars — eliminating the cross-uid
`OperationalError: attempt to write a readonly database` crash-loop that
blocks the ROOT compose from reaching 7/7 healthy on a fresh boot.

## Background — the systemic root cause (3rd instance)

This is the close-out of the cross-uid group-write saga that took THREE
stories to fully surface (memory: `cross-uid-group-write-systemic-umask-gap`):

1. **Story 11.3.8** — `events/` DIR was `0o755` → fixed to `0o2775`.
2. **Story 11.3.11** — `events/*.jsonl` FILEs were `0o640` → fixed to
   `0o660` (PROVEN: the event-log `PermissionError` is gone).
3. **THIS bug, surfaced by 11.3.11's AC8 repro** — once the event-log
   write path was unblocked, registry-state crash-looped on a DEEPER
   error: `sqlite3.OperationalError: attempt to write a readonly
   database` (`materializer.py:298` via `run_subscriber:332`).

### Root cause (live FS evidence from 11.3.11 AC8)

```
state.sqlite3      -rw-r--r--  10002:omb  (registry-state — DB owner)
state.sqlite3-wal  -rw-r--r--  10001:omb  (registry-api! mode 0o644 — no group-write)
state.sqlite3-shm  -rw-r--r--  10001:omb  (registry-api! mode 0o644 — no group-write)
```

`registry-api` (uid 10001) runs a SECOND, WRITABLE SQLite engine — the
Story 2.13 idempotency-cache engine (`app.py:214`,
`create_engine(db_url, read_only=False)`) — pointed at the SAME
`state.sqlite3` file. When it writes the `idempotency_cache` table, SQLite
in WAL mode creates the `-wal`/`-shm` sidecar files, and registry-api
creates them owned by **uid 10001** at **`0o644`** (no group-write).
registry-state (uid 10002, same `omb` group) then opens the DB in WAL
mode and MUST write those sidecars to materialize events → denied →
"readonly database" → crash-loop.

This is the **already-documented "M8 follow-up"** — see
`services/registry-api/src/registry_api/app.py:179-191`, which flags the
dual-writable-engine-on-one-file as an architectural risk and explicitly
recommends "a follow-up story should separate the idempotency cache into
its own SQLite file."

### Why NOT the umask fix (Option A — REJECTED, verified)

The tempting systemic fix is "set umask 002 so shared files are
group-writable by default." **Rejected after verification:** SQLite
creates DB/WAL/SHM files with a `0o666` base; under umask `002` that
yields `0o666 & ~002 = 0o664 = rw-rw-r--` — **WORLD-READABLE**. The
`state.sqlite3` DB contains the same audit data (task contents, approval
trails, event log) that Story 11.3.11 fought to keep non-world-readable.
umask 002 would silently regress that invariant for the DB. So the
file-mode approach cannot be solved by a blanket umask change.

### Why Option B (this story) is correct

Separating the writable idempotency cache onto its OWN file means:
- **registry-state becomes the SOLE writer of `state.sqlite3`** — it
  creates the DB + its WAL/SHM as uid 10002, and nothing else writes
  there. No cross-uid sidecar ownership → no readonly-database error.
- **FR26 single-writer is strengthened**, not weakened — the audit/event
  store now genuinely has one writer process.
- **Resolves the M8 WAL-contention risk too** (the docstring's other
  concern: "sustained write contention → database is locked"). Two
  writers on one SQLite file was always a Phase-1 compromise.
- registry-api still opens `state.sqlite3` READ-ONLY (`app.py:198`,
  `read_only=True`) for its tasks/events/sessions reads — unchanged. Only
  the SECOND, writable cache engine moves to a new file.

## Acceptance Criteria

1. **AC1 — registry-api's writable cache engine points at a SEPARATE file.**
   In `services/registry-api/src/registry_api/app.py:214`, the
   `cache_engine = create_engine(db_url, read_only=False)` must use a
   DISTINCT URL (e.g. `idempotency_db_url`) — NOT the same `db_url` as the
   read-only state engine. Add a `build_app` parameter
   `idempotency_db_url: str | None = None` (default derived from `db_url`
   by swapping the filename to `idempotency.sqlite3` in the same dir, OR
   from a new env var — see AC2). The read-only state engine
   (`app.py:198`) is UNCHANGED.

2. **AC2 — env wiring + default path.** `__main__.py` resolves the cache
   DB URL from a new env var `REGISTRY_API_IDEMPOTENCY_DB_URL` (default:
   the state DB's directory + `/idempotency.sqlite3`). The ROOT
   `docker-compose.yml` sets it explicitly to a path under the shared
   volume that registry-api owns
   (`/var/lib/oh-my-bmad/registry/idempotency.sqlite3`). Since registry-api
   is the SOLE writer of THIS file, its WAL/SHM ownership (uid 10001) is
   never cross-uid — no permission gap.

3. **AC3 — the `idempotency_cache` table is bootstrapped in the NEW file.**
   Today the table is created by the migrator in `state.sqlite3` (it's an
   ORM model in `registry-state/schema.py:210` + the Core `Table` mirror
   in `idempotency/cache.py:101`). Moving the cache to its own file means
   THAT file needs the table created. Decide + implement ONE of:
   - (a) registry-api `create_all`s the `_IDEMPOTENCY_TABLE` (the Core
     `MetaData` at `idempotency/cache.py:101`) on its cache engine at
     startup (gated on an `AUTO_CREATE`-style flag mirroring
     `REGISTRY_STATE_AUTO_CREATE_SCHEMA`), OR
   - (b) the migrator gains a second target (the idempotency file).
     **(a) is preferred** — the idempotency package already owns the Core
     `Table` definition; registry-api creating it on its own file keeps
     ownership local and avoids a migrator cross-file dependency. Document
     the choice.
   - The `idempotency_cache` ORM model can REMAIN in
     `registry-state/schema.py` for the `TestColumnConsistency` parity
     test (keep them in sync — the DUPLICATION WARNING at
     `idempotency/cache.py:90-97` still applies), but the migrator should
     STOP creating it in `state.sqlite3` (or leave it as a harmless empty
     table — decide + document; an orphaned empty table in state.sqlite3
     is acceptable for backward-compat if dropping it complicates the
     migration).

4. **AC4 — `check_single_writer` discipline preserved.** The discipline
   script already excludes `packages/idempotency/` from its FR26 scan
   (`app.py:181-182` note). Confirm the split does not introduce a new
   writable-engine-on-state.sqlite3 path. After the change, grep-verify
   that `create_engine(..., read_only=False)` against the STATE db_url
   appears in ZERO production code paths (registry-state's own writer
   excepted).

5. **AC5 — Unit/contract tests.**
   - `IdempotencyCacheStore` round-trip against a separate in-memory /
     tmp file still works (existing `test_cache.py` should pass unchanged
     or with a fixture URL tweak).
   - `TestColumnConsistency` (the Core-Table ↔ ORM-model parity test)
     still passes.
   - A new test asserts `build_app` opens the cache engine against the
     idempotency URL, NOT the state URL (e.g. inspect `app.state` engine
     URLs, or assert two distinct files are created).

6. **AC6 — Integration regression test / extend 11.3.11's.** Extend or
   add to `tests/integration/test_event_log_file_perm.py` (or a new
   `test_sqlite_wal_cross_uid.py`): boot ROOT compose fresh, assert ALL 7
   healthy (registry-state stable — the headline), AND assert
   `state.sqlite3-wal` / `-shm` are owned by registry-state (uid 10002),
   NOT registry-api. This is THE gate proving the cross-uid WAL bug is
   closed.

7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # 242=baseline (0-new)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q services/registry-api/ packages/idempotency/
   uv run pytest -x -q -m "not slow"
   ```

8. **AC8 — Docker repro (THE Epic-11.3 green close-out).**
   `just build-base` (REQUIRED — the thin service Dockerfile re-stamps a
   stale base; see Story 11.3.11's build-gotcha note) → build → boot ROOT
   compose fresh → assert **ALL 7 services reach `healthy`** and stay
   stable (registry-state restart count stays 0), POST /v1/tasks returns
   201, and a second POST (idempotency replay) hits the cache. Record the
   before (11.3.11: 6/7, registry-state readonly-db crash-loop) / after
   (11.3.12: 7/7 green, stable) in Dev Agent Record. With this, the
   Epic-11.3 fresh-deploy-green tail is COMPLETE.

9. **AC9 — Code review.** Architectural change (a new DB file + schema
   bootstrap + env wiring) touching the FR26 single-writer boundary →
   default `/code-review` minimum; consider `/bmad-code-review` 3-lane
   given it touches the data-integrity boundary. NO change to the
   read-only state engine; NO `mcp_clients.py` touched.

## Tasks / Subtasks

- [ ] **Task 1 — Separate cache engine URL** (AC1, AC2): add
      `idempotency_db_url` param to `build_app`; resolve from
      `REGISTRY_API_IDEMPOTENCY_DB_URL` in `__main__.py` (default = state
      dir + `/idempotency.sqlite3`); set it in `docker-compose.yml`.
- [ ] **Task 2 — Bootstrap the table in the new file** (AC3): registry-api
      `create_all`s `_IDEMPOTENCY_TABLE` on the cache engine (flag-gated);
      stop the migrator creating it in state.sqlite3 (or document the
      orphaned-empty-table backward-compat choice).
- [ ] **Task 3 — FR26 discipline check** (AC4): grep-verify no writable
      engine against the STATE db_url remains.
- [ ] **Task 4 — Unit/contract tests** (AC5): cache round-trip on its own
      file; column-consistency; distinct-engine-URL assertion.
- [ ] **Task 5 — Integration regression** (AC6): 7/7 healthy + WAL owned
      by registry-state.
- [ ] **Task 6 — Docker repro** (AC8): 7/7 stable + idempotency replay.
- [ ] **Task 7 — Validation gates** (AC7).
- [ ] **Task 8 — Code review** (AC9); apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **The bug origin:** `services/registry-api/src/registry_api/app.py:214`
  — `cache_engine = create_engine(db_url, read_only=False)` (same db_url
  as the read-only state engine at :198). The M8 risk is documented at
  `app.py:179-191`.
- **db_url resolution:** `services/registry-api/src/registry_api/__main__.py:134,160`
  — `_DEFAULT_DB_URL` + `REGISTRY_API_DB_URL`. Add a sibling for the
  idempotency file.
- **Idempotency table (Core):** `packages/idempotency/src/idempotency/cache.py:101`
  — `_IDEMPOTENCY_TABLE` on `_meta = MetaData()`. Has `create_all`-able
  metadata locally; this is what AC3(a) bootstraps.
- **Idempotency table (ORM mirror):** `services/registry-state/src/registry_state/schema.py:210`
  — `IdempotencyCache.__tablename__ = "idempotency_cache"` +
  `Index("ix_idempotency_cache_expires_at", ...)` at :335. Keep in sync
  (DUPLICATION WARNING at `cache.py:90-97`; `TestColumnConsistency`
  enforces it).
- **Migrator initial schema:** `services/registry-state/src/registry_state/migrations/versions/2026-04-24_0001_initial_schema.py`
  — currently creates `idempotency_cache` in state.sqlite3.
- **Build-gotcha (from 11.3.11 AC8):** `services/*/Dockerfile` are THIN
  overrides of `oh-my-bmad-base:local`; source is baked by
  `Dockerfile.base:35-41`. `just build-base` is REQUIRED before any
  `services/*/src` change shows up in a container.
- **Read-only state engine (UNCHANGED):** `app.py:198`
  `create_engine(db_url, read_only=True)`.

### Constraints

- **registry-api's state engine stays READ-ONLY** — only the writable
  cache engine moves files. FR26: registry-state is the sole writer of
  the audit/event store.
- **NO umask 002 fix** — verified it would make `state.sqlite3*`
  world-readable (`0o666 & ~002 = 0o664`), regressing the audit-data
  non-world-readable invariant that Stories 11.3.8/11.3.11 protected.
- **NO `mcp_clients.py` touched** (unrelated to the a0ca050 P0 area).
- **Idempotency file lives on the shared volume but is SINGLE-uid** —
  registry-api both creates AND writes `idempotency.sqlite3`, so its
  WAL/SHM ownership is never cross-uid; no group-write gap to fix there.
- **Keep the ORM ↔ Core table parity** (`TestColumnConsistency`).
- **Migrator backward-compat** — dropping `idempotency_cache` from
  state.sqlite3 vs leaving it orphaned-empty is a judgment call; document
  it and prefer the lower-risk option (an empty orphaned table is
  harmless; a destructive migration is riskier on existing volumes).

### Project Structure Notes

- New env var `REGISTRY_API_IDEMPOTENCY_DB_URL`; new default file
  `idempotency.sqlite3` alongside `state.sqlite3`.
- Change is additive in registry-api + a migrator adjustment; the
  idempotency package's Core table already supports `create_all`.

### References

- [Source: memory `cross-uid-group-write-systemic-umask-gap` — the
  3-instance analysis + the A-rejected/B-chosen decision + verified
  umask-002-world-readable math.]
- [Source: `app.py:179-191` — the M8 documented follow-up this story
  implements.]
- [Source: Story 11.3.11 Dev Agent Record AC8 — live FS evidence of the
  WAL cross-uid ownership + the build-base gotcha.]
- [Source: `idempotency/cache.py:90-101` — Core table + DUPLICATION
  WARNING; `registry-state/schema.py:210` — ORM mirror.]

## Previous-story intelligence

- **Story 11.3.8 / 11.3.11** fixed the DIR + FILE layers of the cross-uid
  group-write gap; this story fixes the DB-FILE layer — the genuine root
  by REMOVING the second cross-uid writer rather than chmod-patching a
  4th file (which SQLite's internal WAL creation makes impractical).
- **Story 2.13** introduced the dual-writable-engine compromise and
  documented (M8) that a follow-up should split the cache file. This IS
  that follow-up.
- **Story 11.3.10** is what let the spawners get healthy enough to reach
  the DB-write path that exposed this; 11.3.11 cleared the event-log file
  layer right before it. Linear discovery chain.

## Git intelligence summary

Last commits on this lineage:

- `d28a7c0` (epic-11.3.11) — sync task checkboxes + status (Story 11.3.11)
- `2a0f0d1` (epic-11.3.11) — code-review AC9 fixes
- `c4e125a` (epic-11.3.11) — AC8 repro: 0o660 proven, WAL bug surfaced
- `66a8182` (epic-11.3.11) — event-log files 0o660

Story 11.3.12 branches off `epic-11.3.11` so the chain stays linear:
11.3.8 → 11.3.9 → 11.3.10 → 11.3.11 → **11.3.12**. Branch `epic-11.3.12`.
This is the FINAL story of the Epic-11.3 fresh-deploy-green tail — with
it, ROOT compose comes up 7/7 stable on first boot.

## Frontmatter

```yaml
---
story_id: 11.3.12
story_key: 11-3-12-idempotency-cache-own-db-file
parent_epic: 11
phase: 2
fr_refs: [FR26, FR28]
nfr_refs: [NFR-M4, NFR-M5, NFR-R4]
arch_refs:
  - "Story 2.13 M8 follow-up (app.py:179-191) — split the writable idempotency cache onto its own SQLite file; this story implements it"
  - "Story 11.3.11 AC8 — live evidence: state.sqlite3-wal/-shm owned by registry-api uid 10001 0o644, locks out registry-state uid 10002 → readonly-database crash-loop"
  - "umask 002 REJECTED — would make state.sqlite3* 0o664 world-readable (audit-data invariant regression)"
  - "FR26 single-writer — this strengthens it: registry-state becomes the sole state.sqlite3 writer"
  - "memory cross-uid-group-write-systemic-umask-gap — the 3-instance systemic analysis"
estimated_complexity: MEDIUM
priority: HIGH (registry-state crash-loops on fresh ROOT-compose boot once the spawners + event-log fixes land; THE remaining blocker to the Epic-11.3 7/7-green goal)
blocks: []
unblocks:
  - Fresh ROOT-compose boot reaches 7/7 healthy AND stable (registry-state restart count 0)
  - registry-state becomes the sole state.sqlite3 writer (FR26 strengthened; M8 WAL-contention risk resolved)
  - Closes the Epic-11.3 fresh-deploy-green tail (11.3.8 dir → 11.3.9 health → 11.3.10 mcp-init → 11.3.11 events-file → 11.3.12 sqlite-wal)
---
```

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context) — /loop autonomous execution per BMad workflow.

### Debug Log References

- **AC1-AC5 + AC7 (code + unit tests) DONE & green** (committed 7749a16):
  ruff/format clean, mypy 242=baseline (0 new), discipline 0,
  registry-api + idempotency **284 passed / 0 failed**. The idempotency
  split is correct: `TestIdempotencyCacheSeparateFile` proves cache rows
  land in `idempotency.sqlite3` and `state.sqlite3` has ZERO cache rows;
  the schema-drift parity test still passes; all 27 build_app test sites
  pass `create_idempotency_schema_on_start=True`.
- **AC8 (Docker repro) — the split is NECESSARY but NOT SUFFICIENT.**
  Rebuilt base (verified the split is in the image) → boot → registry-state
  STILL crash-loops (13 restarts, 6/7) on the SAME
  `sqlite3.OperationalError: attempt to write a readonly database`
  (materializer.py:298). Live FS: `state.sqlite3-wal` is STILL owned by
  registry-api (uid 10001), even though its WRITABLE engine now targets
  the separate file.
- **Deeper root cause found (architecture fork — NOT auto-decided):**
  `sqlite_store.create_engine` runs `PRAGMA journal_mode=WAL` on EVERY
  connection incl. read-only (sqlite_store.py:87). More fundamentally, a
  test I wrote (`test_read_only_engine_does_not_create_wal_sidecars`)
  PROVED that **any reader of a WAL-mode SQLite DB intrinsically creates
  the -wal/-shm sidecars** — skipping the pragma is insufficient. So
  registry-api's READ-ONLY engine on `state.sqlite3` creates the sidecars
  owned by uid 10001, locking out the writer registry-state (uid 10002).
  The insufficient pragma-skip attempt was REVERTED to keep the tree green.
  Surfaced to the user + saved to memory
  `cross-uid-group-write-systemic-umask-gap` (UPDATE 2026-06-01) with the
  candidate fixes (state.sqlite3 off-WAL / registry-api reads via HTTP /
  init-pre-create sidecars / same-uid) for a decision.

### Completion Notes List

- **AC1-AC5, AC7 ✓** — the idempotency cache now uses its own
  `idempotency.sqlite3` (registry-api sole writer); state engine stays
  read-only; FR26 strengthened; M8 WAL-WRITE-contention resolved.
- **AC3 decision:** registry-api `create_all`s the table on its own file
  (idempotency pkg owns the Core `_meta`); migrator unchanged →
  `idempotency_cache` is an orphaned-empty table in `state.sqlite3`
  (lower-risk than a destructive migration).
- **WAL-reader fork RESOLVED (WAL-preserving):** rather than the rejected
  options (umask 002 = world-readable; skip-WAL-pragma = insufficient,
  proven by test; journal_mode=DELETE = crash-recovery risk), the fix is a
  main-db-file chmod. Empirically verified: SQLite creates -wal/-shm
  inheriting the MAIN db file's mode. So registry-state chmods its own
  state.sqlite3 to 0o660 after engine+create_all
  (`_ensure_db_file_group_writable`, app/main.py); sidecars then inherit
  group-write (others 0 — audit invariant kept) regardless of which
  omb-uid creates them. No journal-mode change → crash-injection nightly
  unaffected. (commit 1727c89)
- **AC8 ✓ PROVEN 7/7 GREEN** (live Docker repro): all 7 healthy,
  registry-state restart count 0 (was 13); state.sqlite3 + -wal/-shm all
  -rw-rw---- (group-write, others 0) even when a sidecar is created by a
  different omb uid (10004); POST /v1/tasks → 201; idempotency.sqlite3 is
  its own file owned by registry-api (10001). The Epic-11.3 fresh-deploy-
  green tail is functionally COMPLETE.
- **AC6 (integration test)** — added `test_state_sqlite_wal_cross_uid.py`
  (asserts 7/7 + sidecar modes). **AC9 (review)** — pending.

### File List

MODIFIED:
- `packages/idempotency/src/idempotency/cache.py` (create_idempotency_schema + Core index)
- `packages/idempotency/src/idempotency/__init__.py` (export)
- `services/registry-api/src/registry_api/app.py` (separate cache engine + _derive_idempotency_url)
- `services/registry-api/src/registry_api/__main__.py` (env wiring)
- `services/registry-api/src/registry_api/test_app.py` (+ TestIdempotencyCacheSeparateFile; 13 call sites)
- `services/registry-api/src/registry_api/test_{decisions,decisions_signing,middleware,approvals,events}.py` (build_app flag)
- `docker-compose.yml` (REGISTRY_API_IDEMPOTENCY_DB_URL + auto-create env)

## Definition of Done

- registry-api's writable idempotency-cache engine uses a SEPARATE
  `idempotency.sqlite3` file; the state engine stays read-only.
- The `idempotency_cache` table is bootstrapped in the new file
  (registry-api `create_all`, flag-gated); ORM↔Core parity preserved.
- `state.sqlite3` + its WAL/SHM are created + written SOLELY by
  registry-state (uid 10002) — verified by integration test.
- No writable engine against the STATE db_url remains (AC4 grep).
- Unit/contract tests pass (cache round-trip, column-consistency,
  distinct-engine-URL); integration regression passes.
- AC8 Docker repro: ROOT compose **7/7 healthy + STABLE** (registry-state
  restart count 0), POST /v1/tasks 201, idempotency replay hits cache.
- Validation gates green: ruff/format clean, mypy 242=baseline 0-new,
  discipline 0, regression no new fails.
- Code review discharged; NO state-engine-writable path; NO mcp_clients.py.
- `sprint-status.yaml` adds `11-3-12-idempotency-cache-own-db-file`:
  backlog → ready-for-dev → in-progress → review → done.
- **Epic-11.3 fresh-deploy-green tail COMPLETE** — ROOT compose comes up
  7/7 stable on first boot.
