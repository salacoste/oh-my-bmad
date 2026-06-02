# Story 13.3 — `just restore-from-litestream` recipe + restore drill (FR71)

Status: done

<!-- Epic 13, story 3. Validation-first: the litestream replicate→restore
MECHANISM was PROVEN locally (hermetic file-replica round-trip, exit 0) before
writing the recipe — commands are not guessed. -->

## Story

**As** the platform operator,
**I want** a `just restore-from-litestream` recipe that rebuilds the
`oh-my-bmad-data` volume from the litestream replica, plus a hermetic restore
drill that runs in nightly CI,
**so that** I can recover a lost/corrupt host from object storage and have
continuous proof the restore path actually works.

## Validation-first note (why this is trustworthy)

Before writing anything, the exact litestream commands were **empirically
proven** with a real round-trip (litestream 0.3.13 image, hermetic `file`
replica): seed a 1000-row WAL db → `replicate` → wipe → `restore -config <cfg>
<db>` → row-count/integrity verified PASS. Corroborated against litestream
source (`restore.go`/`replica.go` 0.3.13) by a doc-specialist. The drill script
that encodes this round-trip was then **run green locally** (`just
litestream-restore-drill` → exit 0, integrity=ok, 500 rows). So the recipe's
commands are verified, not guessed.

## Acceptance Criteria

1. **AC1 — `just restore-from-litestream` recipe.** Mirrors the `backup` idiom
   (bash shebang, `set -euo pipefail`, `compose_files=()` macos-aware array,
   `*_oh-my-bmad-data` volume detection). Flow: stop → empty volume + recreate
   `registry/` dir (2775, omb gid 10000) → `litestream restore -config
   litestream.yml /var/lib/oh-my-bmad/registry/state.sqlite3` (throwaway
   container, latest generation) → `up -d` → `bootstrap-verify`. Config-based:
   bucket/key + creds from `litestream.yml` / `LITESTREAM_*` env (no positional
   `<bucket>/<key>` arg — the title's arg lives in the config; documented).
   DESTRUCTIVE, guarded by clear messaging.

2. **AC2 — hermetic restore drill, VERIFIED GREEN.** `scripts/litestream-restore-drill.sh`
   + `just litestream-restore-drill`: seed WAL db → file replica → wipe →
   restore → assert `PRAGMA integrity_check=ok` + exact row count. No cloud
   creds. **VERIFIED:** run locally, exit 0, "integrity=ok, 500 rows recovered".

3. **AC3 — drill runs in nightly.yml.** New `litestream-restore-drill` job
   (ubuntu-latest, checkout → install just → `just litestream-restore-drill`).
   Docker + python3 preinstalled; no workspace sync. **VERIFIED:** nightly.yml
   parses; job present with correct steps.

4. **AC4 — docs.** `docs/backup-restore.md` gains a "Restore from a litestream
   replica" section (recipe usage, what it does, the drill, replication≠HA
   reminder). operator-runbook already covers enablement (13.2).

5. **AC5 — recipe validity.** `just --list` parses both recipes (the `{{config}}`
   default-arg + `{{{{.Name}}` escaping render correctly). **VERIFIED.**

6. **AC6 — full operator restore on a real host (live AC).** "Fresh-host restore
   from a real S3 bucket → stack reaches healthy" needs a real bucket + the live
   7-service stack + cross-uid perm reconstruction; deferred to operator/nightly
   (consistent with the Epic-11.3 AC8 precedent). The hermetic drill (AC2)
   proves the restore MECHANISM; the operator recipe wraps it for the real
   volume. The cross-uid perm handling (registry dir 2775/omb) is best-effort
   here and is the ADR-0007 first-enable verification item.

7. **AC7 — gates + review.** discipline gates green; YAML valid; just parses;
   drill green; code review discharged.

## Constraints
- **NO Python service code touched** — recipe + script + workflow + docs only.
- **DESTRUCTIVE recipe** — replaces the live volume; only for recovery.
- **Replication ≠ HA** — stack-down recovery; reiterated in docs + ADR-0007.
- **Drill must stay hermetic** — file replica, no cloud creds, CI-runnable.

## Dev Agent Record

### Agent Model Used
claude-opus-4-8[1m] (create-story + dev-story, validation-first, 2026-06-02).

### Completion Notes List
- PROVEN the litestream replicate→restore round-trip locally before coding
  (hermetic file replica); commands verified, not guessed.
- `scripts/litestream-restore-drill.sh` (NEW): the round-trip as a CI/local test;
  run green (exit 0). Uses `/tmp` (not `$TMPDIR`) for Docker-Desktop share compat.
- justfile: `restore-from-litestream` (operator, config-based) +
  `litestream-restore-drill` (thin wrapper). Both parse via `just --list`.
- nightly.yml: `litestream-restore-drill` job (validated YAML).
- docs/backup-restore.md: litestream-restore section.
- Deferred (live): full fresh-host restore from real S3 + stack-healthy +
  cross-uid perm reconstruction → operator/nightly (ADR-0007 first-enable item).

### Code Review (AC7) — 2026-06-02, code-reviewer (separate context)

- **code-reviewer:** CHANGES-REQUIRED → all addressed. This caught real defects
  in the operator recipe (which could NOT be live-validated — exactly why it was
  reviewed):
  - **CRITICAL FIXED:** litestream restore runs as root → restored state.sqlite3
    was root-owned → registry-state (uid 10002) couldn't write → readonly-DB
    crash loop (the Epic-11.3 bug). Added a post-restore chown 10002:10000 +
    chmod 0660 on state.sqlite3 (+ -wal/-shm).
  - **HIGH FIXED:** no confirmation on the DESTRUCTIVE op → added a typed
    `yes-restore` prompt with `OMB_RESTORE_CONFIRM` env bypass for automation.
  - **HIGH FIXED:** no failure trap → added `trap fail_guidance EXIT` (cleared on
    success) so a failed restore prints recovery guidance instead of stranding.
  - **MEDIUM FIXED:** echo quoting bug (`${compose_files[@]}`→`[*]`); only
    registry/ recreated → now also registry/events/ (2775/omb); drill
    snapshot-wait 15s→30s for CI margin.
  - **LOW FIXED:** post-wipe emptiness check (no silent masking).
  - Reviewer confirmed `{{{{.Name}}` escaping correct, pinned action SHAs, drill
    cleanup trap sound.
- Re-validated: `just --list` parses, shellcheck clean, drill re-run green (exit 0).
- Operator recipe full live run (real S3 + 7-service stack + perms) still = AC6
  deferred operator/nightly; the crash-loop logic bug is now fixed.

### File List
- scripts/litestream-restore-drill.sh (NEW — hermetic drill, run green)
- justfile (M — restore-from-litestream + litestream-restore-drill recipes)
- .github/workflows/nightly.yml (M — drill job)
- docs/backup-restore.md (M — litestream restore section)

## Definition of Done
- `just restore-from-litestream` recipe (config-based, mirrors backup idiom).
- `just litestream-restore-drill` + script, VERIFIED green locally.
- nightly.yml drill job (valid YAML).
- docs/backup-restore.md section.
- code review discharged; `sprint-status.yaml` flips `13-3-just-restore-from-litestream-recipe` to done.

## Frontmatter

```yaml
---
story_id: 13.3
story_key: 13-3-just-restore-from-litestream-recipe
parent_epic: 13
phase: 2
fr_refs: [FR71]
nfr_refs: []
arch_refs:
  - "ADR-0007 — litestream WAL replication, replication ≠ HA, restore = stack-down DR"
  - "Story 13.1/13.2 — the sidecar + config this restore consumes"
  - "justfile backup recipe — the volume-lifecycle idiom mirrored"
  - "epics.md Story 13.3 — restore recipe + nightly drill + backup-restore.md"
estimated_complexity: SMALL-MEDIUM (recipe + drill script + nightly job + docs; no service code)
priority: MEDIUM (FR71)
blocks: []
unblocks:
  - operators can recover a lost host from the litestream replica
  - Story 13.4 (lag-check + replication.lagging) closes the Epic-13 observability gap
---
```
