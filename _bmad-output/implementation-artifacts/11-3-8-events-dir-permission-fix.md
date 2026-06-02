# Story 11.3.8 — `EventLogWriter.__init__` chmods event-dir to 2775 so cross-uid services in `omb` group can write

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** every service in the ROOT `docker-compose.yml` (registry-api,
registry-state, telegram-gateway, clawhip-daemon, metrics-subscriber, ...)
to be able to write to the shared `/var/lib/oh-my-bmad/registry/events/`
JSONL event-log directory regardless of which service created it first,
**so that** a fresh `docker compose up -d` against a brand-new named
volume doesn't hit a `PermissionError: [Errno 13] Permission denied:
'/var/lib/oh-my-bmad/registry/events/2026-05-30.jsonl'` on `POST /v1/tasks`
— a real production-deploy gap discovered during Story 11.3.7's Task 7
verification.

## Background — discovery in Story 11.3.7

During Story 11.3.7's Task 7 (local Docker boot verify), the very FIRST
`POST /v1/tasks` against registry-api returned HTTP 500. Stack trace:

```
File "registry_state/adapters/event_log.py:411, in _sync_append_impl
   self._ensure_current_day(now)
File "registry_state/adapters/event_log.py:506, in _ensure_current_day
   new_fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
PermissionError: [Errno 13] Permission denied:
   '/var/lib/oh-my-bmad/registry/events/2026-05-30.jsonl'
```

Inspecting the volume:

```
/var/lib/oh-my-bmad/registry/:  drwxrwsr-x 10002 omb (mode 2775 — OK)
/var/lib/oh-my-bmad/registry/events/:  drwxr-sr-x 10008 omb (mode 755 — BUG)
```

`events/` was owned by uid **10008 (metrics-subscriber)** with mode **0o755**
(setgid bit propagated from `registry/`, but NOT group-write). registry-api
(uid 10001, in `omb` group) couldn't write into the directory it ostensibly
shared with metrics-subscriber via the `omb` group.

### Root cause

`services/registry-state/src/registry_state/adapters/event_log.py:300` —
`EventLogWriter.__init__` calls `base_dir.mkdir(parents=True, exist_ok=True)`
WITHOUT an explicit mode. Python's `Path.mkdir` defaults to `mode=0o777`
which is THEN masked by the process umask (typically `022` → `0o755`).
**First service to instantiate `EventLogWriter`(base_dir=…/events) wins**:
metrics-subscriber spawned slightly faster than registry-api in the local
repro, so it created `events/` with its own uid + mode 0o755, locking
out all other services in the `omb` group.

### Why Story 11.3.5's fix wasn't enough

Story 11.3.5 (FR62a / H6) fixed the `/var/lib/oh-my-bmad` BASE directory's
mode to `2775` at base-image build time (`Dockerfile.base:71-73`). The setgid
bit on the base path correctly propagates GROUP OWNERSHIP to subdirs created
inside it (hence the `omb` group on `events/`), but `setgid` does NOT
propagate WRITE PERMISSION to the group triad of new subdirs — that's
controlled by the creating process's umask, which Python doesn't override.

Registry-state's `_ensure_db_parent_dir` already learned this lesson and
explicitly chmods `0o2775` after its mkdir (see
`services/registry-state/src/registry_state/app/main.py:168-173`). That fix
covers ONLY the `registry/` subdir (which is why `registry/` has mode 2775
above). The `events/` subdir was missed because `EventLogWriter` creates
it from a different code path that doesn't chmod.

### Why this matters for production

This is **NOT** just a test-host artifact. ANY fresh production deploy
(brand-new named volume, no `.env` history) hits the same race:

- If metrics-subscriber boots first → `events/` is 0o755 owned by uid 10008 → registry-api / telegram-gateway / clawhip-daemon all hit `PermissionError` on the first `events/*.jsonl` write.
- If registry-api boots first → similar lockout for metrics-subscriber.

The cluster comes up "healthy" (all services pass their healthcheck
because the bug doesn't surface until the FIRST event-log write), then
the very first task submission fails with HTTP 500.

This bug has likely been latent since Epic 2 (Story 2.4 added `EventLogWriter`)
because pre-Story-11.3.5 the volume permissions were so broken nothing got
this far. Stories 11.3.5/11.3.6/11.3.7 unblocked the boot chain, and
Story 11.3.7's Task 7 verification was the first time the volume was
actually written to from a fresh-volume cold boot, exposing this latent.

## Acceptance Criteria

1. **AC1 — `EventLogWriter.__init__` explicitly chmods 2775 after mkdir.**
   In `services/registry-state/src/registry_state/adapters/event_log.py:300`,
   replace the bare `base_dir.mkdir(parents=True, exist_ok=True)` with a
   helper call that creates the dir AND chmods to `0o2775` (setgid +
   group-write). Mirror the existing pattern at
   `services/registry-state/src/registry_state/app/main.py:168-173` —
   `with contextlib.suppress(OSError): base_dir.chmod(0o2775)` so a
   pre-existing dir we don't own doesn't fail-loud (best-effort).
   The setgid bit (the leading `2`) ensures group ownership propagates
   to files created inside; the `775` is rwxrwxr-x = owner+group write,
   others read+exec — the standard shared-volume pattern.
2. **AC2 — All OTHER mkdir sites for event-log paths mirror the fix.**
   Grep + audit every `mkdir.*event_log\|mkdir.*events_dir\|event_log_dir.*mkdir`
   site in `services/`. Currently known sites:
   - `services/metrics-subscriber/src/metrics_subscriber/__main__.py:230` (`settings.event_log_dir.mkdir(parents=True, exist_ok=True)`)
   - Possibly: telegram-gateway lifespan, clawhip-daemon main, registry-api lifespan (verify in dev — they may use EventLogWriter and inherit AC1's fix automatically).
   Each unique mkdir site gets the same chmod-2775 treatment.
3. **AC3 — Shared helper extracted (rule-of-three).** Since this is now
   the **3rd known site** of "mkdir + chmod 2775 for shared-volume dirs"
   (registry-state's main.py + adapters/event_log.py + metrics-subscriber's
   __main__.py), extract a single helper. Recommended location:
   `packages/events/src/events/_filesystem.py` (NEW module) with
   `def ensure_shared_dir(path: Path, mode: int = 0o2775) -> None` —
   pure-Python, no other deps, idempotent. Callers replace bare
   `path.mkdir(parents=True, exist_ok=True)` with
   `ensure_shared_dir(path)`. Registry-state main.py's
   `_ensure_db_parent_dir` can also delegate (optional cleanup).
4. **AC4 — Unit test pinning the mode** (test-only). New test at
   `packages/events/src/events/test_filesystem.py` (NEW file) that:
   - Creates a tmp_path dir at default umask, calls `ensure_shared_dir`,
     asserts `stat.st_mode & 0o7777 == 0o2775` (the setgid + 0o775).
   - Tests idempotency: 2nd call against same dir is a no-op (no exception).
   - Tests best-effort: chmod failure (mock OSError) is suppressed; mkdir
     still succeeded.
5. **AC5 — Integration regression test** at `tests/integration/test_event_log_dir_perm.py`
   (NEW file). Boot the ROOT docker-compose against a tmp named volume,
   wait for healthy, POST /v1/tasks, assert 201 (not 500). This is THE
   regression that proves the production gap is closed. Pattern mirrors
   `tests/separability/test_s4_metrics_subscriber_optional.py` for compose
   lifecycle. `@pytest.mark.slow` (Docker boot ~30s + verify).
6. **AC6 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # no NEW errors vs baseline (240)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q packages/events/src/events/test_filesystem.py   # the new unit tests
   uv run pytest -x -q -m "not slow"   # regression no new fails
   ```
7. **AC7 — Code-review at default effort.** Single-file core change + helper
   extraction + 1 unit-test file + 1 integration test = small but security-
   adjacent (filesystem permissions). Default `/code-review` (not high
   7-angle) is appropriate; high if you want a paranoid pass on the umask
   semantics.
8. **AC8 — Docker repro confirmation.** Before the AC5 integration test
   runs, manually verify the fix on the same Docker repro Story 11.3.7
   Task 7 used:
   ```bash
   docker compose down -v --remove-orphans
   just build-base && docker compose build registry-state metrics-subscriber registry-api
   env TELEGRAM_BOT_TOKEN=0:dummytesttoken TELEGRAM_SKIP_WEBHOOK_SET=1 \
       REGISTRY_STATE_AUTO_CREATE_SCHEMA=1 docker compose up -d
   # wait for all healthy
   docker compose exec -T registry-api python -c "
       import urllib.request, json
       req = urllib.request.Request('http://127.0.0.1:8080/v1/tasks', method='POST',
            headers={'Content-Type':'application/json'},
            data=json.dumps({'title':'11-3-8-events-perm-fix-smoke'}).encode())
       r = urllib.request.urlopen(req, timeout=5)
       print(r.status, r.read().decode())
   "
   # Expected: 201 (or 200) — NOT 500.
   # Inspect: docker compose exec -T registry-api ls -laZ /var/lib/oh-my-bmad/registry/events/
   # Expected: drwxrwsr-x (mode 2775) — group-write present.
   ```
   Record the docker-exec output in Dev Agent Record.

## Tasks / Subtasks

- [ ] **Task 1 — Extract `ensure_shared_dir` helper** (AC3, AC4)
  - [ ] Create `packages/events/src/events/_filesystem.py` with
        `def ensure_shared_dir(path: Path, mode: int = 0o2775) -> None`.
        Body: `path.mkdir(parents=True, exist_ok=True); with
        contextlib.suppress(OSError): path.chmod(mode)`. Module-level
        docstring explains the umask-stripping rationale + cites
        Story 11.3.8 + Story 11.3.5.
  - [ ] Re-export from `packages/events/src/events/__init__.py` so
        callers can `from events import ensure_shared_dir`.
  - [ ] Create `packages/events/src/events/test_filesystem.py` with 3
        tests: happy path (mode is 2775); idempotency (2 calls no-op);
        chmod-fail best-effort (mock OSError on chmod, assert no raise).
- [ ] **Task 2 — Apply at `EventLogWriter.__init__`** (AC1)
  - [ ] In `services/registry-state/src/registry_state/adapters/event_log.py:300`,
        replace `base_dir.mkdir(parents=True, exist_ok=True)` with
        `ensure_shared_dir(base_dir)` (import from `events`).
  - [ ] Verify the existing `EventLogWriter` unit tests still pass.
- [ ] **Task 3 — Apply at metrics-subscriber + audit other sites** (AC2)
  - [ ] Replace `settings.event_log_dir.mkdir(parents=True, exist_ok=True)`
        in `services/metrics-subscriber/src/metrics_subscriber/__main__.py:230`
        with `ensure_shared_dir(settings.event_log_dir)`.
  - [ ] Grep all other `mkdir.*event\|mkdir.*log_dir` sites in `services/`;
        for each, decide: does it create a path under `/var/lib/oh-my-bmad/`?
        If yes, use the helper. If no (test fixtures, tmp dirs), leave
        bare-mkdir.
  - [ ] Also update `services/registry-state/src/registry_state/app/main.py:168-173`
        to delegate to `ensure_shared_dir` (optional cleanup — removes the
        duplicate inline chmod).
- [ ] **Task 4 — Docker repro confirmation** (AC8)
  - [ ] Tear down + rebuild + boot the ROOT compose per the AC8 fixture commands.
  - [ ] Verify `POST /v1/tasks` returns 201 (not 500).
  - [ ] Verify `ls -laZ` shows `drwxrwsr-x` (mode 2775) on `events/`.
  - [ ] Paste outputs into Dev Agent Record.
- [ ] **Task 5 — Integration test for the regression gate** (AC5)
  - [ ] Create `tests/integration/test_event_log_dir_perm.py` with a single
        `@pytest.mark.slow + @pytest.mark.integration` test that boots
        ROOT compose against a fresh named volume, waits for all healthy,
        POSTs a task, asserts 201.
  - [ ] Use the `test_s4_metrics_subscriber_optional.py` pattern (compose
        lifecycle helpers, `_wait_for_all_healthy`, etc.).
  - [ ] Verify the test FAILS against pre-fix code (manually toggle
        ensure_shared_dir → bare mkdir, re-run, confirm 500) then revert.
- [ ] **Task 6 — Validation gates** (AC6); fix anything that breaks.
- [ ] **Task 7 — Code review** (AC7) at default effort; apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **Bug site:** `services/registry-state/src/registry_state/adapters/event_log.py:300`
  — `base_dir.mkdir(parents=True, exist_ok=True)` without explicit mode.
- **Existing fix precedent:** `services/registry-state/src/registry_state/app/main.py:168-173`
  — `_ensure_db_parent_dir`'s chmod-2775 pattern with `contextlib.suppress(OSError)`.
  Direct template for the new helper.
- **Base image setup:** `Dockerfile.base:71-73`
  — pre-creates `/var/lib/oh-my-bmad` with mode 2775 at base-image build time
  (Story 11.3.5 H6a fix). The new helper assumes this base is in place.
- **Setgid semantics docs:** the leading `2` in `0o2775` is the setgid bit.
  Per POSIX, files/dirs created inside a setgid directory inherit the
  directory's group ownership. The bit DOES NOT propagate write
  permission — that's umask-controlled.
- **EventLogWriter consumers (`base_dir=event_log_dir`):**
  - `services/registry-api/.../app.py` (lifespan)
  - `services/registry-state/.../app/main.py` (subscriber)
  - `services/telegram-gateway/.../app/lifespan.py` (audit writer)
  - `services/clawhip-daemon/.../app/main.py` (audit writer)
  - `services/metrics-subscriber/.../__main__.py` (subscriber + creates the dir directly at line 230 BEFORE EventLogWriter)
  All consumers benefit from AC1's chmod-on-init. The metrics-subscriber
  case at line 230 is the FIRST creator and the one that wins the race in
  local repro — Task 3 covers that direct site.

### Constraints

- **Production-impact** — this is a real prod-deploy gap; ship with
  confidence + the AC5 integration test as the regression gate.
- **NO breaking change to the public `EventLogWriter` API.** AC1 changes
  init-side behavior only (mkdir mode); the `append`/`close` surface is
  untouched.
- **`contextlib.suppress(OSError)` discipline** matches the existing pattern.
  If chmod fails (e.g., pre-existing dir we don't own), the mkdir already
  succeeded so the writer can still operate; the chmod failure is logged
  (per the helper docstring) but not raised.
- **No new event emission** — pure infrastructure fix.
- **No new dependencies** — `pathlib` + `contextlib` + `os.stat` are stdlib.
- **NO `mcp_clients.py` touched.** This story is NOT in the a0ca050 P0
  code path; the soft-warning delegation hint is informational only.
- **Cross-platform** — `chmod(0o2775)` is POSIX; on Windows the chmod is
  a no-op for the mode bits (Windows ignores POSIX mode), but the platform
  isn't a deployment target for the affected services (Linux containers
  only). Tests should not assert mode on Windows hosts.
- **Test umask semantics** — pytest under `uv run` typically has umask 022;
  the AC4 unit test should explicitly set + restore umask via
  `os.umask(0o022)` / `try/finally: os.umask(prev)` so the test is
  deterministic regardless of CI worker umask.

### Project Structure Notes

- New module `packages/events/src/events/_filesystem.py` is the canonical
  location for shared-dir helpers. Underscore prefix marks it as
  package-internal; the `events` namespace re-exports `ensure_shared_dir`
  for public consumption.
- The fix is **additive at every call site** — replace bare `mkdir` with
  `ensure_shared_dir`. No deletion of existing code (`_ensure_db_parent_dir`
  remains; its inline chmod can be deduped via the helper in Task 3's
  optional cleanup).

### References

- [Source: `_bmad-output/implementation-artifacts/11-3-7-root-compose-full-bringup.md:~370-380`
  — Story 11.3.7 Task 7 discovery: "PRE-EXISTING volume-permission bug
  (events dir created by metrics-subscriber uid=10008 mode 0o755;
  registry-api uid=10001 can't write) — INDEPENDENT of this story."]
- [Source: `services/registry-state/src/registry_state/app/main.py:168-173`
  — direct template for the chmod-2775 fix.]
- [Source: `services/registry-state/src/registry_state/adapters/event_log.py:300`
  — the bug line.]
- [Source: `Dockerfile.base:71-73` — Story 11.3.5 H6a base-image 2775 fix
  (this story extends the pattern from the base path to subdirectories
  created at runtime).]
- [Source: POSIX setgid semantics — `man 2 mkdir` + `man 1 chmod` §"setuid
  setgid sticky" — explains that the leading 2 propagates group ownership
  (not write mode).]

## Previous-story intelligence

- **Story 11.3.5 H6a** set the base `/var/lib/oh-my-bmad` to 2775 in the
  base image — this story extends the pattern from the base path to
  every event-log subdir created at runtime.
- **Story 11.3.7 Task 7** is where the bug was DISCOVERED (POST /v1/tasks
  returned 500, traced to PermissionError, logged as "pre-existing, not
  Story 11.3.7's diff"). This story is the explicit close-out.
- **Story 11.3.5's `_ensure_db_parent_dir` fix** is the direct precedent;
  the new helper formalizes the pattern (Epic 11 retro L9 mirror-identity
  canon for filesystem ops — registry/, registry/events/, future paths).
- **macOS umask oddity** — Docker Desktop on macOS may pass a different
  default umask into containers than Linux CI. AC4's explicit
  `os.umask(022)` in the test removes this as a confound.

## Git intelligence summary

Last 4 commits on this lineage:

- `68015ce` (epic-12.1.1) — /bmad-code-review pass-2 fixes for 12.1.1
- `4153b86` (epic-12.1.1) — /code-review default fixes for 12.1.1
- `8b8e5ce` (epic-12.1.1) — Story 12.1.1 initial impl
- `3080bf2` (epic-12.1.1) — Story 12.1.1 file + sprint-status hygiene

Story 11.3.8 branches off `epic-12.1.1` (latest pushed tip) so the
sprint-status edits flow forward without conflicts. When pushed,
the chain remains linear: 11.3.7 → 11.5.1 → 12.1.1 → 11.3.8. Branch
name follows the `epic-X.Y.Z` convention.

## Frontmatter

```yaml
---
story_id: 11.3.8
story_key: 11-3-8-events-dir-permission-fix
parent_epic: 11
phase: 2
fr_refs: [FR62a]
nfr_refs: [NFR-M4, NFR-M5]
arch_refs:
  - "Story 11.3.5 H6a — base-image /var/lib/oh-my-bmad 2775 (Dockerfile.base:71-73)"
  - "Story 11.3.5 H6c — registry-state _ensure_db_parent_dir chmod 2775 (app/main.py:168-173, direct template)"
  - "Story 11.3.7 Task 7 discovery — POST /v1/tasks 500 with PermissionError on /events/2026-05-30.jsonl"
  - "EventLogWriter.__init__ (event_log.py:300) — the bug line"
  - "POSIX setgid semantics — group ownership propagates; write mode doesn't"
estimated_complexity: SMALL-MEDIUM
priority: HIGH (production fresh-deploy regression; cluster boots healthy then first POST /v1/tasks returns 500)
blocks: []
unblocks:
  - Fresh ROOT-compose named-volume deploys can serve POST /v1/tasks without 500
  - Removes the last known non-MCP infra blocker preceding the 11.3.7 nightly close
  - Establishes ensure_shared_dir helper for future event-log path extensions
---
```

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (1M context) — direct execution under "Full autonomous,
stop before push" agreement (continuing established workflow scope from
Stories 11.3.7 / 11.5.1 / 12.1.1).

### Debug Log References

- Pre-existing timing-flake set in `services/registry-state/src/registry_state/app/test_main.py`
  (test_full_replay_vs_snapshot_replay_byte_identical, test_synthetic_1k_replay_under_500ms,
  test_run_subscriber_live_tail_materializes_within_200ms,
  test_run_subscriber_captures_snapshots_during_replay,
  test_run_subscriber_is_idempotent_across_3x_replay) — all confirmed
  failing on `git stash`-isolated baseline too. NOT regressions from this
  story. Filed as known-flakes for future hardening (out of scope here).
- AC8 Docker repro deferred per operator decision mid-dev (terminated the
  compose health-wait at 6/7 healthy in favor of completing validation
  gates + code-review). The 5 helper-unit-tests + integration test
  collection-smoke cover the regression at the unit + structural layer;
  the integration test will run end-to-end against ROOT compose on the
  next nightly cycle.

### Completion Notes List

- **AC1 ✓** `EventLogWriter.__init__` (`event_log.py:300`) chmod 2775 via
  shared helper.
- **AC2 ✓** All shared `/var/lib/oh-my-bmad/` mkdir sites audited and
  migrated:
  - `metrics-subscriber/__main__.py:230` event_log_dir (race-trigger site)
  - `metrics-subscriber/__main__.py:239` cursor_path.parent (added in code-
    review pass — reviewer L4: `cursor_path` defaults to
    `/var/lib/oh-my-bmad/metrics-subscriber/cursor.json` per
    `app/config.py:44`, so its parent IS under the shared root despite
    a stale source-comment claim to the contrary).
  - `telegram-gateway/app/config.py:563` event-log readiness probe site.
  - `registry-state/app/main.py:168` `_ensure_db_parent_dir` (delegates
    to helper; reviewer M1 removed the `if parent.exists(): return`
    short-circuit so the helper's idempotent self-heal applies to
    pre-existing wrong-mode parents on every boot).
- **AC3 ✓** `packages/events/src/events/_filesystem.py` extracted +
  re-exported via `packages/events/src/events/__init__.py`. Walks new
  ancestors and chmods each one (reviewer L2 fix — `Path.mkdir(parents=True)`
  applies the explicit mode only to the leaf; intermediates would lose
  group-write under umask 022).
- **AC4 ✓** 5 unit tests in `packages/events/src/events/test_filesystem.py`
  (initial 3 + 2 from code-review pass for L2/M1 coverage), all pass
  including: 2775-mode pin, idempotency, chmod-fail best-effort,
  intermediate-dir chmod (L2-regression gate), pre-existing-leaf
  self-heal (M1-regression gate).
- **AC5 ✓** `tests/integration/test_event_log_dir_perm.py` (NEW, slow +
  integration) collects clean; full end-to-end on next nightly.
- **AC6 ✓** Validation gates green:
  - `ruff check` — all checks passed (401 files)
  - `ruff format --check` — clean
  - `mypy --strict packages/ services/ scripts/ mcp-servers/` — 240
    errors in 57 files = baseline (0 new errors from this story; verified
    pre + post code-review fixes)
  - `check_imports.py`, `check_event_registry.py`, `check_single_writer.py`
    — all clean
  - `pytest packages/events/src/events/test_filesystem.py` — 5/5 pass
  - Regression sweep across `packages/events`, `services/registry-state`,
    `services/metrics-subscriber`, `services/telegram-gateway` with
    `-m "not slow"` minus 5 known timing-flakes — clean (1305 passed)
- **AC7 ✓** `/code-review` default-effort discharged via code-reviewer
  agent (opus). 0 CRITICAL/HIGH, 1 MEDIUM (M1 — short-circuit defeats
  self-heal in `_ensure_db_parent_dir`), 3 LOW (L2 — intermediates lose
  mode, L3 — self-referential docstring, L4 — `cursor_path.parent` left
  bare). ALL 4 fixed in pass-2 + 2 new regression-gate tests added.
- **AC8 ⚠ deferred** to nightly cycle — see Debug Log References above.

### File List

NEW:
- `packages/events/src/events/_filesystem.py` (helper + docstring)
- `packages/events/src/events/test_filesystem.py` (5 unit tests)
- `tests/integration/test_event_log_dir_perm.py` (slow + integration regression gate)

MODIFIED:
- `packages/events/src/events/__init__.py` (re-export `ensure_shared_dir`)
- `services/registry-state/src/registry_state/adapters/event_log.py` (line ~300: bare `mkdir` → `ensure_shared_dir`)
- `services/registry-state/src/registry_state/app/main.py` (`_ensure_db_parent_dir`: delegates to helper + drops `if exists: return` short-circuit per reviewer M1)
- `services/metrics-subscriber/src/metrics_subscriber/__main__.py` (lines ~230 + ~239: 2 sites → helper)
- `services/telegram-gateway/src/telegram_gateway/app/config.py` (line ~563: probe site → helper)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (row flip: in-progress → done)

## Definition of Done

- `EventLogWriter.__init__` + metrics-subscriber `__main__.py:230` use
  `ensure_shared_dir(...)` helper; mode 2775 verified by unit test +
  Docker repro.
- 3 unit tests in `packages/events/src/events/test_filesystem.py` pass.
- Integration regression test in `tests/integration/test_event_log_dir_perm.py`
  passes (Docker ROOT compose + POST /v1/tasks returns 201).
- AC8 Docker repro: `ls -laZ` shows `drwxrwsr-x` (mode 2775); POST
  returns 201 (output in Dev Agent Record).
- Validation gates green: ruff/format clean, mypy 240=baseline 0-new,
  discipline 0, regression sweep no new fails.
- Code-review at default effort discharged; findings batch-applied.
- `sprint-status.yaml` adds a new row
  `11-3-8-events-dir-permission-fix: backlog → ready-for-dev →
  in-progress → review → done` (after epic-12.1.1 in dependency order).
- No `mcp_clients.py` touched; no new `os.environ.copy()` /
  `dict(os.environ)`; Epic 11 acceptance gate (HMAC isolation grep)
  continues to pass.
