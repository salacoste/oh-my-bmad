# Story 11.3.11 — event-log JSONL files created 0o660 (group-write) so cross-uid `omb` services can append/recover

Status: in-progress (AC1-AC7+AC9 done & green; AC8 PARTIAL — 0o660 fix PROVEN on live stack but 7/7 blocked by a newly-surfaced systemic sqlite-WAL cross-uid bug, out of scope — see Dev Agent Record)

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** event-log day-files (`/var/lib/oh-my-bmad/registry/events/YYYY-MM-DD.jsonl`)
to be created group-writable (`0o660`, not `0o640`) so that ANY service
in the shared `omb` group can append to and recover the day's file
regardless of which uid created it first,
**so that** a fresh ROOT `docker compose up` doesn't leave `registry-state`
in a `PermissionError` crash-loop when a DIFFERENT `omb`-uid service
(e.g. worker-wrapper) created the day's file before registry-state's
recovery pass tries to open it `r+b`.

## Background — discovery during Story 11.3.10 AC5

Story 11.3.10 fixed the MCP-init healthcheck-budget flake, which let the
orchestrator-adapter + worker-wrapper spawners finally reach `healthy`.
That success exposed the NEXT latent bug: with the spawners up, the stack
reached multi-uid event-log WRITES, and `registry-state` entered a
persistent crash-loop (observed 15→20 restarts, climbing) on:

```
PermissionError: [Errno 13] Permission denied:
   '/var/lib/oh-my-bmad/registry/events/2026-06-01.jsonl'
File "registry_state/adapters/event_log.py:191, in _recover_file
   with open(path, "r+b") as f:
```

Live filesystem evidence (captured from inside the running stack):

```
events/ DIR:  drwxrwsr-x  10008:omb  (mode 2775 — Story 11.3.8 dir fix IS in effect)
2026-06-01.jsonl FILE:  -rw-r-----  10005:omb  (mode 0o640 — THE BUG)
registry-state uid = 10002 (omb group) — can READ but NOT WRITE the file
```

### Root cause

`services/registry-state/src/registry_state/adapters/event_log.py:514` —
`EventLogWriter._ensure_current_day` creates day-files via:

```python
new_fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
```

`0o640` = `-rw-r-----` = owner read+write, **group read-only**, no others.
The intent (docstring lines 56-58) is correct — audit logs contain task
contents + approval trails and MUST NOT be world-readable. But `0o640`
also denies **group-WRITE**, so whichever `omb`-uid creates the day's
file first owns it `0o640` and every OTHER `omb`-uid that later needs to
append (`O_APPEND` write) or recover (`r+b` read-write at line 191) is
locked out — even though they share the `omb` group via the 2775 setgid
directory.

This is the **FILE-level sibling of Story 11.3.8**, which fixed the
event-log DIRECTORY to `2775` (setgid + group-write) but left file
creation at `0o640`. It was latent behind 11.3.8's dir bug AND behind
11.3.10's MCP-init flake — only surfaces once the stack reaches the
multi-uid event-log-write path.

### Why `0o660` is the correct fix (not wider)

`0o660` = `-rw-rw----` = owner rw + **group rw** + no others. This:
- ADDS group-write (closes the cross-uid append/recover gap).
- KEEPS the audit-log security invariant: **still NOT world-readable**
  (the `0` others-triad is preserved). The docstring's stated goal
  ("group-readable, not world-readable") is upgraded to "group-RW, not
  world-anything" — strictly tighter on the others-triad, looser only
  within the already-trusted `omb` group.

### umask caveat (mirrors Story 11.3.8's chmod lesson)

`os.open(..., mode=0o660)` applies `mode & ~umask`. Under the
conventional umask `022`, `0o660 & ~022 = 0o640` — the group-write bit
gets stripped, re-creating the bug. Under umask `002` it survives. So,
exactly as Story 11.3.8 learned for directories, the create alone is
insufficient: the writer must **explicitly `os.fchmod(fd, 0o660)`** right
after `os.open` to defeat whatever umask the process inherited. (fchmod
on the just-opened fd — not a path chmod — avoids a TOCTOU window and is
the fd-native analog of `ensure_shared_dir`'s `path.chmod`.)

## Acceptance Criteria

1. **AC1 — File create mode → `0o660` + explicit `fchmod`.**
   In `services/registry-state/src/registry_state/adapters/event_log.py:514`,
   change the `os.open` mode argument from `0o640` to `0o660`, AND
   immediately after the open add
   `os.fchmod(new_fd, 0o660)` (best-effort, wrapped in
   `contextlib.suppress(OSError)` to mirror the 11.3.8 discipline — a
   pre-existing file we don't own must not crash the writer; the
   `O_APPEND` open already succeeded). Update the docstring (lines 56-58
   + the inline comment at 512-513) to state `0o660` (group-RW, NOT
   world-readable) and cite the umask-strip rationale.

2. **AC2 — Recovery path tolerates pre-existing wrong-mode files (defense-in-depth).**
   `_recover_file` (`event_log.py:168-191`) opens `r+b`. If a file
   created by a PRE-11.3.11 process (or a different tool) is still
   `0o640`, the recovery open will still fail. Add a best-effort
   `os.chmod(path, 0o660)` (suppressed) just before the `open(path, "r+b")`
   at line 191 so recovery self-heals a stale-mode file owned by the
   current uid. (If owned by a DIFFERENT uid, chmod fails → suppressed →
   the open then fails as before, but that's the genuinely-unrecoverable
   case; log it clearly rather than crash-loop — see AC3.)

3. **AC3 — Crash-loop → clear, non-fatal diagnostic on genuine permission denial.**
   If `_recover_file` STILL hits `PermissionError` after the AC2 self-heal
   attempt (i.e. the file is owned by another uid AND not group-writable —
   the exact pre-11.3.11 state on an OLD volume), recovery MUST NOT
   crash-loop the whole subscriber. Log a structured
   `log.error("event_log_recovery_permission_denied", path=..., file_uid=..., file_mode=...)`
   with the offending file's owner-uid + mode, and either skip that file
   (continue recovering the rest) or fail fast with a one-shot clear
   message — NOT an infinite restart oscillation. Decide which in dev
   (skip-and-continue is preferred for resilience; document the choice).
   This converts the 15→20-restart crash-loop into a single actionable
   log line.

4. **AC4 — Shared helper considered (rule-of-three check).** Story 11.3.8
   created `packages/events/src/events/_filesystem.py::ensure_shared_dir`
   for the DIR case. This is now the file-mode analog. Evaluate whether a
   sibling `ensure_shared_file_mode(fd_or_path, mode=0o660)` helper in the
   same module is warranted, OR whether the 1-2 call sites here are too
   few (rule-of-three not yet met — `os.fchmod` inline is fine). Document
   the decision. Do NOT over-abstract for 2 sites.

5. **AC5 — Unit tests** at `services/registry-state/.../adapters/test_event_log.py`
   (extend existing or new):
   - Fresh file created via `EventLogWriter` has mode `0o660` (masked to
     `& 0o777`), with explicit umask pin (`os.umask(0o022)` / restore) so
     the test proves the `fchmod` defeats umask — exactly the 11.3.8
     `test_filesystem` pattern.
   - File is NOT world-readable (`mode & 0o007 == 0`) — the security
     invariant.
   - Recovery (`_recover_file`) self-heals a pre-existing `0o640` file
     owned by the current uid (AC2).
   - POSIX-only mode asserts (`skipif win32`), mirroring `test_filesystem.py`.

6. **AC6 — Integration regression test** at
   `tests/integration/test_event_log_file_perm.py` (NEW, `@slow + @integration`).
   Mirror Story 11.3.8's `test_event_log_dir_perm.py`: boot ROOT compose
   against a fresh volume, wait for all 7 healthy, POST a task, then
   `ls -l` the day-file and assert mode `-rw-rw----` (group-write present,
   others-none). This is THE regression gate proving cross-uid append
   works. THE headline: with this + Story 11.3.10, the ROOT compose
   reaches **7/7 healthy** on fresh boot (closing the Epic-11.3 tail's
   "fully green" goal that 11.3.10's AC5 could not reach).

7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # 240=baseline (0-new)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q services/registry-state/   # event-log tests + recovery tests
   uv run pytest -x -q -m "not slow"   # regression no new fails
   ```

8. **AC8 — Docker repro confirmation (closes 11.3.10's AC5 gap).**
   Re-run the exact AC5 boot from Story 11.3.10 (`docker compose down -v`
   → `just build-base` → build → `up -d` with hermetic env). This time
   assert **ALL 7 services reach `healthy`** (registry-state no longer
   crash-loops), POST /v1/tasks returns 201, and the day-file is
   `-rw-rw----`. Record before (11.3.10: registry-state crash-loop) /
   after (11.3.11: 7/7 green) in Dev Agent Record.

9. **AC9 — Code review.** Security-adjacent (audit-log file permissions).
   Default `/code-review` minimum; bump to `/bmad-code-review` 3-lane if a
   paranoid pass on the world-readable invariant is desired. NO widening
   to world-readable under any finding.

## Tasks / Subtasks

- [x] **Task 1 — Change file-create mode + fchmod** (AC1)
  - [ ] `event_log.py:514` `0o640` → `0o660`; add
        `with contextlib.suppress(OSError): os.fchmod(new_fd, 0o660)`
        immediately after the `os.open`.
  - [ ] Update docstring (56-58) + inline comment (512-513): `0o660`
        group-RW, NOT world-readable, umask-strip rationale.
- [x] **Task 2 — Recovery self-heal + diagnostic** (AC2, AC3)
  - [ ] `_recover_file` (line 191): best-effort `os.chmod(path, 0o660)`
        (suppressed) before `open(path, "r+b")`.
  - [ ] On still-`PermissionError`: structured `log.error` with file uid
        + mode; skip-and-continue (or one-shot fail) — NOT crash-loop.
- [x] **Task 3 — Helper rule-of-three decision** (AC4); document inline-vs-helper.
- [x] **Task 4 — Unit tests** (AC5): mode 0o660, not-world-readable,
        recovery self-heal, umask-pin, POSIX-only.
- [x] **Task 5 — Integration regression test** (AC6):
        `tests/integration/test_event_log_file_perm.py`.
- [~] **Task 6 — Docker repro** (AC8): PARTIAL — day-file 0o660 + event-log PermissionError GONE (fix proven); 7/7 NOT reached, blocked by the out-of-scope systemic sqlite-WAL cross-uid bug (see Dev Agent Record + memory cross-uid-group-write-systemic-umask-gap).
- [x] **Task 7 — Validation gates** (AC7).
- [x] **Task 8 — Code review** (AC9); apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **Bug site:** `services/registry-state/src/registry_state/adapters/event_log.py:514`
  — `os.open(..., O_WRONLY|O_APPEND|O_CREAT, 0o640)`.
- **Recovery open:** `event_log.py:191` — `open(path, "r+b")` (the line
  that crash-loops on a wrong-mode file).
- **Docstring to update:** `event_log.py:56-58` ("File mode 0o640" block).
- **Story 11.3.8 precedent (DIR analog):**
  `packages/events/src/events/_filesystem.py::ensure_shared_dir` +
  `test_filesystem.py` — the umask-pin test pattern + suppress-OSError
  discipline to mirror.
- **Story 11.3.8 integration test template:**
  `tests/integration/test_event_log_dir_perm.py` — copy for AC6.
- **uid map (for the integration assert + AC3 diagnostic):** registry-api
  10001, registry-state 10002, orchestrator-adapter 10004, worker-wrapper
  10005, clawhip-daemon 10006, metrics-subscriber 10008 — all in `omb`
  group (gid 10000).

### Constraints

- **NEVER world-readable.** The fix moves `0o640 → 0o660` (adds GROUP
  write); the others-triad MUST stay `0` (`mode & 0o007 == 0`). Any
  review finding that would expose audit logs world-wide is REJECTED.
- **`os.fchmod` on the fd, not path chmod**, for the create path — avoids
  a TOCTOU window between open and chmod (the file is already open).
  Path `os.chmod` is acceptable in the recovery path (AC2) where there's
  no open fd yet.
- **Best-effort suppress(OSError)** mirrors Story 11.3.8 — a pre-existing
  file owned by another uid must not crash the writer; the `O_APPEND`
  open already gave a usable fd (or, for recovery, the suppressed chmod
  failure falls through to the genuine-permission-denied diagnostic).
- **NO `mcp_clients.py` touched** — unrelated to the a0ca050 P0 area.
- **No new event emission; no new dependency** — `os` + `contextlib` are
  stdlib. Pure infra/permissions fix.
- **FR26 single-writer preserved** — registry-state remains the sole
  materializer-writer; this just makes the files it (and the other
  append-path services) create group-writable within `omb`.
- **Cross-platform** — `fchmod`/`chmod` are POSIX no-ops on Windows;
  tests assert mode only on non-win32 (Linux-container deploy target).

### Project Structure Notes

- Additive change at 2 call sites in one file + 1 unit-test extension +
  1 new integration test. No file moves, no deletions.
- If AC4 decides a helper IS warranted, it lives next to
  `ensure_shared_dir` in `packages/events/src/events/_filesystem.py`.

### ⚠️ AC8 build gotcha (learned during dev — applies to ANY services/ src change)

`services/registry-state/Dockerfile` is a THIN OVERRIDE
(`FROM oh-my-bmad-base:local` + `USER` + `ENTRYPOINT`) — it does NOT
COPY/install the registry-state source. The source is baked into the venv
by `Dockerfile.base:35-41` (`COPY services/ ./services/` + `uv sync
--no-editable`). So `docker compose build registry-state` ALONE picks up
NOTHING — it just re-stamps the thin layer over a STALE base. The AC8
repro (and Story 11.3.10's AC5 before it) initially booted with the OLD
0o640 code for exactly this reason: verified the installed package via
`docker compose exec registry-api grep -c 0o660
/opt/venv/.../registry_state/adapters/event_log.py` → returned 0.
**Correct AC8 sequence: `just build-base` FIRST (re-bakes the venv), then
`docker compose build`, then `up -d`.** Any future story touching
`services/*/src` or `packages/*/src` must rebuild the base, not just the
service.

### References

- [Source: Story 11.3.10 Dev Agent Record + memory
  `event-log-file-mode-0640-cross-uid-gap` — the AC5 discovery with live
  FS evidence (dir 2775 OK, file 0o640 owned by uid 10005, registry-state
  uid 10002 locked out).]
- [Source: `event_log.py:514` — the `0o640` create.]
- [Source: `event_log.py:191` — the `r+b` recovery open that crash-loops.]
- [Source: Story 11.3.8 `ensure_shared_dir` + `test_filesystem.py` —
  umask-defeat discipline + suppress-OSError + umask-pin test pattern.]
- [Source: `event_log.py:56-58` — the audit-log non-world-readable
  invariant that MUST be preserved.]

## Previous-story intelligence

- **Story 11.3.8** fixed the event-log DIRECTORY to 2775 (group-write +
  setgid) but left FILE create at 0o640 — this story is the file-level
  completion of that work. The `ensure_shared_dir` umask lesson (mkdir's
  mode is masked, so an explicit chmod is required) applies identically
  to `os.open` here (fchmod required).
- **Story 11.3.10** surfaced this bug during its AC5 macOS repro: the
  MCP-init fix let the spawners reach healthy, which advanced the stack
  to the multi-uid event-log-write path where this 0o640 gap crash-loops
  registry-state. 11.3.10 explicitly scoped it OUT and recommended this
  story.
- **Epic-11.3 tail close-out:** 11.3.8 (events-perm DIR) → 11.3.9
  (/v1/health) → 11.3.10 (MCP-init flake) → **11.3.11 (events-perm FILE)**.
  This story is what actually delivers the tail's "ROOT compose comes up
  7/7 fully green" goal that 11.3.10's AC5 could not reach.

## Git intelligence summary

Last commits on this lineage:

- `a47f03e` (epic-11.3.10) — /bmad-code-review 3-lane (Story 11.3.10)
- `071b56a` (epic-11.3.10) — x-healthcheck-mcp start_period 100s
- `035d217` (epic-11.3.9) — /v1/health real signals
- `fde786e` (epic-11.3.8) — events/ dir 0o2775 ensure_shared_dir

Story 11.3.11 branches off `epic-11.3.10` so the chain stays linear:
11.3.8 → 11.3.9 → 11.3.10 → **11.3.11**. Branch `epic-11.3.11`. This is
the genuine close-out of the Epic-11.3 fresh-deploy-green tail.

## Frontmatter

```yaml
---
story_id: 11.3.11
story_key: 11-3-11-event-log-file-mode-0660
parent_epic: 11
phase: 2
fr_refs: [FR62a]
nfr_refs: [NFR-M4, NFR-M5, NFR-S10]
arch_refs:
  - "Story 11.3.8 — ensure_shared_dir DIR 2775 fix; this is the FILE-mode analog (0o660)"
  - "Story 11.3.10 AC5 — discovery: spawners healthy → multi-uid event-log writes → registry-state 0o640 crash-loop"
  - "event_log.py:514 — os.open 0o640 create (the bug); event_log.py:191 — r+b recovery open (the crash site)"
  - "event_log.py:56-58 — audit-log non-world-readable invariant (preserved: 0o660 keeps others=0)"
  - "memory event-log-file-mode-0640-cross-uid-gap — live FS evidence"
estimated_complexity: SMALL-MEDIUM
priority: HIGH (registry-state crash-loops on fresh ROOT-compose boot once spawners are healthy; blocks the Epic-11.3 tail's 7/7-green goal)
blocks: []
unblocks:
  - Fresh ROOT-compose boot reaches 7/7 healthy (registry-state no longer crash-loops)
  - Cross-uid omb services can append/recover the shared event log
  - Closes the Epic-11.3 fresh-deploy-green close-out tail (with 11.3.10)
---
```

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context) — direct execution under the established
"full autonomous, stop before push" scope + the /loop directive to keep
advancing the Epic-11.3 tail.

### Debug Log References

- **AC1-AC5 (code + unit tests) — DONE, all gates green:** ruff/format
  clean, mypy 242 = baseline (0 new — the "240" in the story templates was
  stale; the real baseline drifted to 242), discipline 0, registry-state
  312 passed, TestFileMode 3/3 (incl. the umask-022 fchmod-defeat proof),
  integration test collects.
- **AC8 (Docker repro) — MY FIX PROVEN, but surfaced the NEXT systemic
  layer:** Booting the ROOT compose on the rebuilt base (see the build
  gotcha note above — `just build-base` is REQUIRED, the thin service
  Dockerfile alone re-stamps a stale base; verified the base now has 8×
  `0o660` + 3× `fchmod` + the AC3 log marker via
  `docker run --entrypoint sh oh-my-bmad-base:local -c "grep -c ..."`):
  - The event-log `PermissionError` on `*.jsonl` is **GONE** — my 0o660 +
    AC3 skip-and-continue worked. registry-state no longer dies on the
    event-log recovery path.
  - BUT registry-state STILL crash-loops (12 restarts), now on a DEEPER,
    DIFFERENT error: `sqlite3.OperationalError: attempt to write a
    readonly database` (`materializer.py:298` via `run_subscriber:332`).
  - Root cause (live FS evidence): `state.sqlite3-wal` + `state.sqlite3-shm`
    are created by **registry-api uid 10001** (its Story 2.13 writable
    idempotency-cache engine, `read_only=False`, app.py:214) at mode
    **`0o644`** (no group-write). registry-state (uid 10002, same omb
    group) opens the DB in WAL mode and MUST write the -wal/-shm sidecars
    → readonly-database error → crash-loop.
  - This is the **THIRD instance** of the identical cross-uid group-write
    gap (11.3.8 dir → 11.3.11 events-file → now sqlite-WAL). The true root
    cause is **systemic**: containers run umask 022, so every multi-uid
    shared file defaults group-non-writable. Captured in project memory
    `cross-uid-group-write-systemic-umask-gap`.

### Completion Notes List

- **AC1 ✓** event_log.py `_ensure_current_day` creates 0o660 + explicit
  `os.fchmod(fd, 0o660)` to defeat umask; others-triad stays 0.
- **AC2 ✓** `_recover_file` best-effort `os.chmod(path, 0o660)` before the
  `r+b` open (self-heals same-uid stale-mode files).
- **AC3 ✓** `recover_all_logs` catches genuine cross-uid `PermissionError`
  → structured `log.error` with uid/gid/mode/remediation → skip-and-
  continue, NOT crash-loop. `EventLogWriter.recover()` delegates here so
  both writer + subscriber recovery are protected. **PROVEN by AC8:** the
  event-log permission crash is gone.
- **AC4 ✓** rule-of-three not met (fd-fchmod + path-chmod = 2 different
  forms) → inline, no helper. Documented.
- **AC5 ✓** TestFileMode rewritten (superseded the old 0o640 assertion):
  3 POSIX-only tests — exact-0o660-under-umask-022, never-world-accessible,
  recovery-self-heal.
- **AC6 ✓** `tests/integration/test_event_log_file_perm.py` (slow) created,
  mirrors the 11.3.8 dir-perm test; collects clean.
- **AC7 ✓** validation gates green (see Debug Log).
- **AC8 ⚠ PARTIAL — my fix proven, but 7/7 NOT reached** due to the
  newly-surfaced sqlite-WAL cross-uid bug (uid 10001 creates -wal/-shm
  0o644, locks out registry-state uid 10002). This is OUT OF SCOPE for
  11.3.11 (event-log files) — it's the DB-file layer of the same systemic
  umask gap. Recommend (per memory): fix the root cause — (A) umask 002
  for omb services, and/or (B) split registry-api's writable
  idempotency-cache engine onto its own sqlite file (the already-
  documented app.py:179-191 "M8 follow-up"). Do NOT point-fix a 4th file.
- **AC9 ✓** code review (default effort, opus code-reviewer): APPROVE,
  0 CRITICAL/HIGH, 2 LOW — both applied. The world-readable security
  invariant was confirmed INTACT (all 3 sites 0o660, others-triad 0;
  the AC3 skip does NOT mask corruption — the consumer log_reader.py
  opens read-only and tolerates partial tails). L1: added `AttributeError`
  to the fchmod `suppress` (os.fchmod is absent on Windows → would raise
  AttributeError that suppress(OSError) misses). L2: added a hermetic unit
  test `test_recovery_skips_and_continues_on_cross_uid_permission_denied`
  (monkeypatches `_recover_file` to raise PermissionError on one of two
  files; asserts recover_all_logs skips it + still recovers the other +
  returns rather than crash-looping) — the AC3 path was previously only
  covered by the Docker-gated integration test.

### File List

MODIFIED:
- `services/registry-state/src/registry_state/adapters/event_log.py` (0o660 + fchmod + recovery self-heal + AC3 skip-and-continue)
- `services/registry-state/src/registry_state/test_event_log.py` (TestFileMode rewrite + recover_all_logs/sys imports)

NEW:
- `tests/integration/test_event_log_file_perm.py` (slow+integration regression gate)

## Definition of Done

- Event-log day-files are created `0o660` (group-RW) with an explicit
  `fchmod` defeating umask; verified by unit test + Docker repro.
- Files remain NOT world-readable (`mode & 0o007 == 0`) — audit-log
  security invariant preserved.
- Recovery path self-heals a stale-mode file owned by the current uid and
  emits a clear non-fatal diagnostic (not a crash-loop) on genuine
  cross-uid permission denial.
- Unit tests (mode 0o660, not-world-readable, recovery self-heal,
  umask-pin) pass; integration regression test passes.
- AC8 Docker repro: ROOT compose reaches **7/7 healthy** on fresh boot
  (registry-state stable), day-file `-rw-rw----`, POST /v1/tasks 201.
- Validation gates green: ruff/format clean, mypy 240=baseline 0-new,
  discipline 0, regression sweep no new fails.
- Code review discharged; findings applied; NO world-readable widening.
- `sprint-status.yaml` adds `11-3-11-event-log-file-mode-0660`:
  backlog → ready-for-dev → in-progress → review → done (after
  epic-11.3.10 in dependency order).
- Epic-11.3 fresh-deploy-green tail is COMPLETE: ROOT compose comes up
  7/7 on first boot.
