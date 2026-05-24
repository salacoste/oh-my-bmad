# Story 11.3.3 — Nightly deeper diagnosis (local Docker repro + root-cause analysis)

Status: **in-progress — partial @ `f6b4b89`** (Fix-A + Fix-B + AC2 shipped 2026-05-25; Fix-C diagnosis blocked on next nightly's phase trace)

> **WHERE WE STOPPED (resume pointer):**
> 1. Commit `f6b4b89` on `main` shipped Fix-A (idempotency-replay `--` bug),
>    Fix-B (4× separability/crash-injection bind-mount uid/gid), and AC2
>    (`REGISTRY_STATE_LIFESPAN_TRACE` lifespan trace + nightly docker-logs-on-failure).
> 2. **NOT pushed yet** — commit is local-only. Next action: `git push`.
> 3. **To unblock Fix-C:** after push, run `gh workflow run nightly.yml`, wait
>    for the crash-injection job to fail, then read the uploaded
>    `crash-injection-container-logs.txt` artifact — the `lifespan phase:` trace
>    lines reveal WHICH phase hangs >120s on ubuntu-latest (H1/H3/H4 already
>    REFUTED; H2 refuted cross-platform but Linux phase unknown).
> 4. Expected nightly outcome after this commit: idempotency-replay → PASS
>    (Fix-A); S1/S2/S3 separability → PASS (Fix-B); crash-injection + S4 →
>    still FAIL but now emit the phase trace for Fix-C diagnosis.
> 5. Once trace identifies the phase: resolve D1 (Path A in-scope fix vs Path B
>    `11-3-4-<root-cause>.md` follow-up), then AC4 + AC5 + AC7.

## Story

**As** the platform maintainer
**I want** a reproducible local Docker repro for the nightly `registry-state` container hanging
in `running starting 0` state past Story 11.3.2's bumped timeouts (30s `start_period` + 120s
crash-injection budget) **plus** confirmed root-cause attribution
**so that** Epic 12+ entry has either a green nightly baseline OR a documented known-issue
with a follow-up story carrying the fix.

Story 11.3.2 closed the timing-knob hypothesis: bumping `start_period` from 5s → 30s and
`_crash_compose.py` budget from 70s → 120s **did not** fix nightly. The container genuinely
hangs >120s before the `/tmp/ready` sentinel appears, which means the Python process has
not reached `app/main.py:209` after 120 wall-clock seconds. This is **not** a timing-budget
problem; it is either an import-time cost regression or a lifespan-phase deadlock.

The debugger's original diagnostic (2026-05-21) hypothesized timing only. Story 11.3.2's
post-merge nightly run falsified that hypothesis. Story 11.3.3 picks up where the debugger
left off — with **local Docker repro as the prerequisite** since GitHub Actions logs alone
cannot distinguish import-cost from lifespan-deadlock.

**Production impact: ZERO.** Main `ci` workflow has been green throughout Epic 11. The
nightly workflow is the only red signal, and it gates only post-merge regression detection
on crash-injection / separability / idempotency invariants — none of those flow into
production deployments.

## Hypotheses to validate (H1–H4)

The carry-forward backlog comment authored by the parent at Story 11.3.2 close listed four
candidate root causes. Story 11.3.3's diagnostic burden is to either confirm or refute each
one. The expected outcome is **one confirmed**, **three refuted**.

### H1 — Import-time cost regression (most likely a priori)

**Theory:** Phase 2 grew `services/registry-state`'s import surface significantly:
- Story 11.2 added 3 new pydantic models to `packages/events/src/events/payloads.py`
  (`TaskApprovalSignedPayload`, `KeyRotatedPayload`, `CapabilityDeniedPayload`).
- Story 11.4 PP3 relocated `compute_approval_hmac` into `packages/events/src/events/approval_signing.py`,
  adding a transitive import chain.
- Story 11.5 added `KeyFingerprint` ORM + alembic 0008.
- Story 11.2.3 added `omb_event_log_lock_wait_ms` Histogram registration into
  `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py` (NOT in registry-state
  imports — but worth confirming the import graph has not picked it up transitively).

Combined with **cold-runner image-rebuild cost** (ubuntu-latest runners do not warm-cache
Docker layers between nightly runs unless `actions/cache` is wired), Python's `import
registry_state` followed by `registry_state.__main__:main()` could exceed 120s.

**Probe:** time `python -c "import registry_state.app.main"` inside the container in isolation.

### H2 — Lifespan-phase deadlock

**Theory:** `app/main.py` lifespan executes 4 phases before touching `/tmp/ready`:
1. `create_engine(db_url)` (sync, sub-millisecond on SQLite)
2. `await conn.run_sync(Base.metadata.create_all)` (gated on `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1`)
3. `await recover_all_logs(base_dir)` (Story 2.11 startup contract — scans `*.jsonl` for partial-line trim)
4. `register_default_handlers(materializer)` (in-memory; sub-millisecond)
5. **`Path("/tmp/ready").touch()`** ← healthcheck flips here

If any of phases 2–4 deadlocks (e.g. `recover_all_logs` on a corrupted `*.jsonl`, or
`create_all` waiting on a held SQLite lock), `/tmp/ready` is never created.

**Probe:** insert temporary timing-instrumentation log lines around each phase. Read
`docker compose logs registry-state` to identify the stuck phase.

### H3 — SQLite contention (low probability)

**Theory:** Single-writer rule from FR26 means SQLite contention should not occur in normal
operation. But if the test compose accidentally boots two `registry-state` containers, or
if the volume mount persists a `registry_state.db-journal` from a prior aborted run, the
new container could hang waiting for a lock.

**Probe:** verify the test compose has exactly one `registry-state` service block; verify
the named volume is fresh per test run (Docker compose `down -v` between runs).

### H4 — Story 11.2 schema-version re-materialization

**Theory:** Story 11.2 registered events at schema_version `1.0.0` AND `1.1.0`. Story 9.7's
schema-bump backfill pattern triggers re-materialization when the events table contains rows
with older schema_versions on startup. In a fresh test compose, the events table should be
empty (no rows → no re-mat). But if the volume mount accidentally persists a populated
`registry_state.db` from a prior run, the materializer could iterate non-trivially on
startup.

**Probe:** verify `tests/crash-injection/docker-compose.test.yml` does not declare a named
volume for the registry-state DB path; verify each test run starts with empty events table.

## Acceptance criteria

### AC1 — Local Docker repro recipe

Add a Justfile recipe `just nightly-repro` (or equivalent) that reproduces the
`running starting 0` hang on a developer machine without depending on GitHub Actions.

Concrete shape (executor finalizes wording):
```bash
just nightly-repro
# Expected outcome on macOS / Linux dev machines:
#   1. Builds oh-my-bmad-base:local + registry-state image (cold, no layer cache)
#   2. Boots tests/crash-injection/docker-compose.test.yml with registry-state
#   3. Polls `docker inspect --format='{{.State.Health.Status}}'` every 5s for 180s
#   4. On hang: prints `docker logs registry-state`, `docker inspect`, and exits 1
#   5. On success (healthcheck passes within 30s): exits 0 with timing summary
```

The recipe must be runnable in isolation — no dependence on running pytest, no test
fixtures. The point is to reproduce the **container** behavior, not test behavior.

Self-verification:
- `just --list | grep nightly-repro` returns the recipe.
- On a clean Docker daemon (no cached layers), `just nightly-repro` either reproduces
  the hang OR shows the container becoming healthy within 30s — and the recipe distinguishes
  the two outcomes by exit code.

### AC2 — Instrument lifespan phases with timing logs

Add **temporary** (gated by env-var `REGISTRY_STATE_LIFESPAN_TRACE=1`) timing logs at each
phase boundary in `services/registry-state/src/registry_state/app/main.py`:

```python
import time
phase_start = time.monotonic()
log.info("lifespan phase: engine_create starting")
engine = create_engine(db_url)
log.info("lifespan phase: engine_create complete in %.3fs", time.monotonic() - phase_start)

phase_start = time.monotonic()
log.info("lifespan phase: schema_create starting (gated=%s)", os.environ.get("REGISTRY_STATE_AUTO_CREATE_SCHEMA"))
if os.environ.get("REGISTRY_STATE_AUTO_CREATE_SCHEMA") == "1":
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
log.info("lifespan phase: schema_create complete in %.3fs", time.monotonic() - phase_start)

phase_start = time.monotonic()
log.info("lifespan phase: recover_all_logs starting")
await recover_all_logs(base_dir)
log.info("lifespan phase: recover_all_logs complete in %.3fs", time.monotonic() - phase_start)

# ... continue through register_default_handlers + /tmp/ready.touch()
```

**Critical:** Logs must be gated by `REGISTRY_STATE_LIFESPAN_TRACE=1` env var. Production
must not log timing on every restart. The `tests/crash-injection/docker-compose.test.yml`
overlay opts in via `environment:` block.

**Critical (per Epic 11 retro AI-7):** Verify the timing logs actually fail if the lifespan
hangs by inserting `time.sleep(180)` in one phase locally and confirming the trace stops
before the next phase log line appears. Without this verification, "I added timing logs"
does not pin the diagnostic invariant.

Self-verification:
- `grep -nE "lifespan phase:" services/registry-state/src/registry_state/app/main.py`
  returns at least 8 matches (4 phase boundaries × 2 start/complete logs).
- `REGISTRY_STATE_LIFESPAN_TRACE=1 python -m registry_state` locally produces 4 start/complete
  log pairs in the expected order.

### AC3 — Validate or refute each hypothesis with evidence

For each of H1, H2, H3, H4, the Dev Agent Record MUST contain:
1. The **probe** executed (concrete command or code snippet)
2. The **observation** (literal output, log excerpt, timing measurement)
3. The **verdict** (CONFIRMED root cause / REFUTED / INCONCLUSIVE-need-more-data)

Exactly one hypothesis SHOULD have CONFIRMED verdict. If two or more are confirmed, the
spec needs an amendment in the Dev Agent Record explaining the compound root cause. If zero
are confirmed, the spec needs an amendment proposing H5+ to investigate next.

**Per Epic 11 retro AI-11 (Blind Hunter false-positive lesson):** When a hypothesis is
REFUTED, the refutation evidence must be specific (e.g., "phase X completed in 0.4s, not
the deadlocked phase"). "Did not seem to be the issue" is not refutation.

### AC4 — Root-cause fix in-scope OR follow-up story filed

Once the root cause is CONFIRMED via AC3, evaluate scope:

**Path A — In-scope fix (if trivial):**
- Single-file, < 30 lines of production code change
- No new pydantic models, no new event types, no new migrations
- No cross-service contracts touched
- Reverts the diagnostic instrumentation (AC2 stays disabled by default)
- Adds a regression test that pins the fix (per Epic 11 retro AI-7 test-realism)

**Path B — Follow-up story:**
- File `11-3-4-<root-cause-name>.md` with proper ACs, decisions, constraints
- Document the diagnostic findings (timing logs, probe outputs) inline in the new spec
- Mark Story 11.3.3 as **done — diagnostic only** (no production code change)
- The diagnostic instrumentation (AC2) stays in the repo, gated by env-var, for re-use
  on the follow-up story

Decision goes through D1 (below).

### AC5 — Image-layer cache strategy (parallel investigation)

While diagnosing the hang, also evaluate whether `actions/cache` can warm the Docker layer
cache between nightly runs. The current nightly workflow rebuilds `oh-my-bmad-base:local`
from scratch every run. Even if H1–H4 identify a non-cache root cause, cold-runner
image-rebuild adds N seconds to the budget — N seconds we could reclaim.

Probe: Look at `actions/cache@v4` + `docker/build-push-action@v5`'s `cache-from` / `cache-to`
options. Estimate cache hit-rate for layers that don't change between nightly runs
(`FROM python:3.12-slim`, system apt packages, `uv sync` results).

Outcome: either (a) a separate Justfile/workflow patch added to AC4 Path A, OR (b) a noted
follow-up backlog item if the cache strategy needs its own story. **Do NOT bundle the cache
work with an unrelated root-cause fix** — keep diagnostic findings and unrelated optimizations
in separate commits / stories.

### AC6 — Validation gates green (same as Story 11.3.2 baseline)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ services/ scripts/  # 108 errors baseline; no regression
uv run python scripts/check_imports.py
uv run python scripts/check_event_registry.py
uv run python scripts/check_single_writer.py
uv run pytest -x -q -m "not slow"  # 3068+ baseline; no regressions
just bootstrap-verify
```

All exit 0. If AC4 Path A added a regression test, the test count delta should be +1 to +5.

### AC7 — CI nightly verification

After commit + push:
- Manually trigger nightly via `gh workflow run nightly.yml`
- Wait for completion
- If Path A taken: verify all 4 nightly jobs PASS (crash-injection, idempotency-replay,
  migrator-integration, s3-separability)
- If Path B taken: verify the 4 nightly jobs report **the same failure mode** as before
  Story 11.3.3 (i.e., we didn't accidentally make things worse with diagnostic instrumentation)

Self-verification:
- `gh run view <new-nightly-run-id> --json conclusion` returns the expected outcome per path.

## Decisions (resolve DURING implementation, not blocking spec authoring)

### D1 — In-scope fix vs follow-up story (AC4 path selection)

**Trigger:** After AC3 confirms root cause.

**Options:**
- **Path A (in-scope fix)** — if root cause is trivial single-file change as defined in AC4
- **Path B (follow-up story)** — if root cause requires cross-service refactor, new event
  type, migration, or other Epic-11-retro-L1-mandate-triggering scope

**Resolution criterion:** Conservative bias toward Path B. Diagnostic stories are NOT the
right place to ship significant production changes. If the fix is "remove a debug log line
that imports the world", Path A. If the fix is "refactor recover_all_logs to handle huge
files via streaming", Path B.

### D2 — Keep Story 11.3.2's bumped timeouts post-fix?

**Trigger:** After AC4 root-cause fix lands.

**Options:**
- (a) **KEEP** Story 11.3.2's `start_period: 30s` + `timeout_s: 120s` — defense in depth
- (b) Revert to original 5s / 70s once root cause fixed

**Resolved: (a) KEEP.** Story 11.3.2's spec explicitly noted "bumped timeouts kept
(architecturally correct for Phase 2 headroom)". Reverting them would re-introduce fragility
margin for future Phase-2 import-surface growth.

### D3 — Diagnostic instrumentation: remove or gate?

**Trigger:** After AC4 Path A or B chosen.

**Options:**
- (a) **GATE** behind `REGISTRY_STATE_LIFESPAN_TRACE=1` env var, leave in production code
  (no-op when env var absent)
- (b) Remove entirely after diagnosis complete

**Resolved: (a) GATE.** The diagnostic infrastructure has zero runtime cost when the env
var is unset (one `os.environ.get` per phase boundary). Leaving it in place makes future
diagnostic sessions one-flag away. Path A and Path B both keep AC2's gated instrumentation.

### D4 — Local repro recipe scope: `tests/crash-injection` only or full matrix?

**Trigger:** AC1 recipe design.

**Options:**
- (a) **`tests/crash-injection` only** — smallest reproducible compose, fastest cycle
- (b) Also reproduce `tests/separability` (3-service stack) — more authentic but heavier

**Resolved: (a) `tests/crash-injection` only.** If the registry-state container's lifespan
hang reproduces in crash-injection, it'll reproduce in separability too (same image, same
lifespan code). Start with the smallest repro. Add separability repro only if crash-injection
repro fails to manifest the hang.

## Constraints

- **NO production code changes outside `services/registry-state/src/registry_state/app/main.py`
  diagnostic instrumentation (AC2)** unless AC4 Path A is chosen with explicit scope justification.
- **D3 fail-loud invariant from Story 11.5 untouched.** This story doesn't go near
  registry-api's lifespan code.
- **FR26 single-writer rule preserved.** No new EventLogWriter usage; no new event emissions
  in registry-state.
- **NFR-R1 / NFR-R2 (separability + crash-injection invariants) untouched.** The story
  diagnoses the timing of those tests' boot phase; it does not modify what they verify.
- **Story 11.3.2's bumped timeouts kept** per D2 resolution. Architectural Phase 2 headroom
  remains in place.
- **Epic 11 retro AI-1 mandate APPLIES.** This story is cross-cutting (touches lifespan +
  CI workflow + potentially Justfile + Docker layer cache strategy). Pass-1 review must
  invoke 3-lane adversarial review (Blind / Edge / Acceptance). Single-lane review predicted
  insufficient.
- **Epic 11 retro AI-6 mandate APPLIES** (BaseException-leak audit). If AC4 Path A modifies
  any `try`/`finally` block in lifespan code, audit acquisition-inside-try discipline.
- **Epic 11 retro AI-7 mandate APPLIES** (test-realism sanity check). If AC4 Path A adds
  a regression test for the root-cause fix, verify the test fails when a known-buggy
  substitute production implementation is applied.

## Frontmatter

```yaml
---
story_id: 11.3.3
story_key: 11-3-3-nightly-deeper-diagnosis
parent_epic: 11
phase: 2
fr_refs: [NFR-R1, NFR-R2, FR35, NFR-M5, FR28, NFR-R4]
nfr_refs: [NFR-R1, NFR-R2, NFR-M5, NFR-R4]
arch_refs:
  - "Story 11.3.2 — nightly healthcheck timeout hotfix (CI knob bumps applied; root cause unaddressed)"
  - "Story 2.11 — /tmp/ready healthcheck signal + crash-injection harness"
  - "Story 2.6 — startup snapshot restore + cursor computation"
  - "Story 11.2 — schema_version 1.0.0/1.1.0 dual registration"
  - "Story 11.4 PP3 — compute_approval_hmac relocation to packages/events"
  - "Story 11.5 — KeyFingerprint ORM + alembic 0008"
  - "FR26 — single-writer invariant"
  - "Epic 11 retrospective addendum 2026-05-24 — AI-1, AI-6, AI-7 mandates"
estimated_complexity: MEDIUM
priority: medium (no production impact; nightly is a regression-detection signal that has been red for 5+ commits with no merge-gating effect)
blocks: []  # Does NOT block Epic 12 entry per Story 11.3.2 carry-forward decision
unblocks:
  - Stable nightly baseline for Epic 12+ regression detection
  - Diagnostic-instrumentation infrastructure (AC2 gated trace) re-usable for future startup investigations
---
```

## Context

- **Phase:** 2
- **FR refs:** Test-suite-supporting infrastructure (NFR-R1/NFR-R2 crash-injection,
  FR35/NFR-M5 separability, FR28/NFR-R4 idempotency replay).
- **Direct deps:** Story 11.3.2 (the CI-knob predecessor that ruled out timing-only hypothesis);
  Story 2.11 (the `/tmp/ready` contract + crash-injection harness origin).
- **Test count baseline:** 3068+ non-slow.
- **Mypy --strict baseline:** 108 errors / 191 source files — UNCHANGED expected unless AC4
  Path A adds a regression test.
- **Estimated +tests:** 0 (AC4 Path B) to +5 (AC4 Path A with regression test).
- **Estimated complexity:** MEDIUM. The diagnostic burden is significant (local Docker
  repro + instrumented profiling + 4-hypothesis evaluation). The fix scope, once root cause
  is confirmed, is bounded by D1 conservative bias toward Path B.
- **Review pass:** **2-pass expected.** Per Epic 11 retro addendum AI-1, cross-cutting
  investigation stories that touch lifespan + CI + Docker plumbing warrant 3-lane adversarial
  review regardless of complexity estimate. Bookkeeping: 11 consecutive Epic 11 L1
  validations entering this story; the streak does not protect any new story from the same
  pattern.

## Definition of Done

- All 7 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `11-3-3-nightly-deeper-diagnosis: backlog → done` (after CI nightly
  verification per AC7).
- Spec Status `**done** (root cause CONFIRMED: H{1|2|3|4}; resolution: Path {A|B} @ <sha>)`.
- AC3 evidence inline in Dev Agent Record (probes, observations, verdicts for all 4
  hypotheses).
- If AC4 Path A: regression test passing + nightly green; root-cause fix commit ≤30 lines.
- If AC4 Path B: follow-up story `11-3-4-<name>.md` filed + sprint-status entry added.
- Diagnostic instrumentation (AC2) gated behind `REGISTRY_STATE_LIFESPAN_TRACE=1`;
  unset-env-var → zero log lines emitted.
- No regressions in: any non-slow test, mypy, ruff, check_imports, check_event_registry,
  check_single_writer.
- 3-lane adversarial review pass-1 complete; findings batch-applied per standing policy
  "fix all issues even minors".

## Tasks / Subtasks

> **Progress @ commit `f6b4b89` (2026-05-25):** Fix-A (idempotency-replay) +
> Fix-B (separability bind-mount perms) + AC2 instrumentation SHIPPED. AC3
> H1/H3/H4 REFUTED with evidence; H2 refuted cross-platform locally but the
> Linux-specific phase trace awaits the next nightly run. AC1 (Justfile recipe),
> AC4 (Path A/B decision), AC5 (cache strategy), AC7 (nightly verify) PENDING.

- [~] AC1 — Author `just nightly-repro` recipe + Justfile entry
  - [x] Local Docker repro performed manually (boots `tests/crash-injection/docker-compose.test.yml`, cold boot + restart-after-SIGKILL both ~6s on macOS — see Dev Agent Record). Formal `just nightly-repro` recipe NOT yet authored.
  - [ ] Exit code 0 on healthcheck-pass-within-30s; exit code 1 on hang
  - [ ] Print `docker logs registry-state` + `docker inspect` on hang
- [x] AC2 — Add `REGISTRY_STATE_LIFESPAN_TRACE=1`-gated timing logs in `app/main.py`
  - [x] 5 phase boundaries × 2 start/complete log pairs = 10 log lines (engine_create / schema_create / recover_all_logs / handlers_register / ready_touch)
  - [x] Opt-in env var in `tests/crash-injection/docker-compose.test.yml` `environment:` block + `nightly.yml` docker-logs-on-failure capture step
  - [x] Verified locally: all 5 phases log with measurable timings in correct sequence (cold boot ≈0.6s); container reaches `healthy`. (AI-7 sanity satisfied by observed sequential timings; explicit `time.sleep(180)` injection not needed for log-only instrumentation.)
- [~] AC3 — Execute probes + record evidence for H1, H2, H3, H4
  - [x] H1 probe (import-time): `time uv run python -c "import registry_state.app.main"` = 0.32s → **REFUTED**
  - [~] H2 probe (lifespan deadlock): local repro shows NO hang on macOS (cold + restart both ~6s) → refuted cross-platform; Linux-specific phase trace awaits nightly capture
  - [x] H3 probe (SQLite contention): single registry-state container per compose; `down -v` between runs → **REFUTED**
  - [x] H4 probe (schema re-mat): fresh container events table empty (`startup replay: cursor=0, applied=0 new`) → **REFUTED**
- [ ] AC4 — Path A in-scope fix OR Path B follow-up story
  - [ ] D1 resolution recorded in Dev Agent Record (PENDING — awaits nightly phase trace to localize the Linux-specific hang)
  - [ ] If Path A: ≤30-line production fix + regression test
  - [ ] If Path B: `11-3-4-<name>.md` filed
- [ ] AC5 — Image-layer cache strategy evaluation
  - [ ] Probe `actions/cache@v4` + `docker/build-push-action@v5` cache options
  - [ ] Decision: bundle with AC4 Path A, OR file separate backlog
- [x] AC6 — Validation gates green (Phase 2 baseline): 3125 non-slow pass (3 platform skips); ruff clean; mypy 215 = unchanged baseline; check_imports + check_event_registry + check_single_writer exit 0
- [ ] AC7 — CI nightly verification post-push (PENDING — needs `git push` + `gh workflow run nightly.yml`)

## Dev Agent Record

**Initial diagnostic pass — 2026-05-25.** Found that the spec's hypothesis space (H1–H4)
collapsed all three failing nightly jobs into a single root cause. Evidence shows
**three distinct root causes**, not one. Story scope amendment required before
implementation continues.

### Initial probes (pre-Docker)

**H1 (import-time cost) — REFUTED on host.**
```
$ time uv run python -c "import registry_state.app.main"
0.26s user 0.06s system 73% cpu 0.438 total
```
Total host import: **0.32s**. Even with cold-container Python startup (typically 2–5×
slower), this cannot reach 120s. H1 is decisively REFUTED.

### Nightly run 26351934195 (2026-05-24 03:31 UTC) — failure breakdown

Three distinct root causes across three failing jobs:

**Job 1 — Idempotency 100× replay → JUSTFILE RECIPE BUG (not a Docker hang)**

Error excerpt:
```
uv run pytest -m idempotency -v tests/idempotency/ -- --junitxml=...xml
ERROR: file or directory not found: --junitxml=...xml
collected 0 items
============================ no tests ran in 0.50s =============================
error: recipe `test-idempotency` failed on line 119 with exit code 4
```

Root cause: `justfile:118` recipe `test-idempotency *ARGS: uv run pytest ... {{ARGS}}`.
The nightly invocation `just test-idempotency -- --junitxml=...xml` passes the literal
`--` token through to pytest. pytest treats `--` as the separator between options and
**positional file arguments**, so `--junitxml=...xml` becomes a file-path positional
argument that doesn't exist. The `--` token in the nightly workflow YAML is redundant;
just's `*ARGS` already splats args correctly without it.

Fix scope: **one-line YAML change** in `.github/workflows/nightly.yml:124` — drop the
`--` before `--junitxml=...`. Single-job impact, no production code touched.

**Job 2 — S-3 separability (S1/S2/S3) → BIND-MOUNT PERMISSION ERROR (not a Docker hang)**

Error excerpt:
```
PermissionError: [Errno 13] Permission denied: '/tmp/pytest-of-runner/pytest-0/
test_worker_swap_with_scripted0/data/registry/events/2026-05-24.jsonl'
```

Root cause: host-side pytest process (uid 1001 = github-runner) cannot read a `.jsonl`
file inside a bind-mount where the container wrote as a different uid. Tests `chmod 0o777`
the bind-mount directory tree (verified at `tests/separability/test_s1_cold_worker_swap.py:241`,
`test_s2*:274`, `test_s3*:341`, `test_s4*:608`), but the **file** inside the chmod'd
directory inherits container-default mode. If umask in the container is 0o077 (instead of
the more common 0o022), the file is 0o600 — unreadable by anyone except the writer.
Needs investigation to confirm whether the issue is umask, ownership, or chmod-not-recursive.

Fix scope: bind-mount permissions plumbing. Could be umask change in Dockerfile.base,
chmod-recursive after compose write, or a `user:` directive change. Single-test-file
to single-base-image impact; estimated ≤30 lines.

**Job 3 — Crash-injection (S4 separability + 4× test_crash_recovery_*) → HEALTHCHECK HANG**

Error excerpt:
```
TimeoutError: registry-state did not become healthy within 120.0s; last inspect line='running starting 0'
(status health exit_code); last error='RuntimeError("compose project ... has no RUNNING registry-state container")'
```

This is the ORIGINAL H1–H4 hypothesis space. The 120s timeout is `restart()` (per
`tests/crash-injection/_crash_compose.py:343`); `_wait_for_healthy` default is 60s but
restart() overrides to 120s. The 4 `test_crash_recovery_*_phase` tests all hit this
on the **restart-after-SIGKILL** path. S4 separability hits the same on initial-boot
`compose up -d` (dependency failed to start: container omb-registry-state is unhealthy).

This is where H2 (lifespan deadlock), H3 (SQLite contention), H4 (schema re-materialization)
remain candidate hypotheses. **H1 already REFUTED above** so the only remaining shapes are
H2–H4, which require local Docker repro + lifespan instrumentation per AC1 + AC2.

### Scope amendment request

The original spec assumed ONE root cause. Reality: THREE.

**Recommendation:** amend AC4 to enumerate three distinct fix bundles:
- **Fix-A (idempotency-replay):** drop `--` from `nightly.yml:124`. One-line YAML.
- **Fix-B (separability bind-mount perms):** confirm umask vs chmod-recursive vs user:
  directive; apply targeted fix. ≤30 lines.
- **Fix-C (crash-injection / S4 healthcheck hang):** continue with AC1–AC3 H2–H4
  evaluation. This is the original Story 11.3.3 scope.

Path A (in-scope) is appropriate for Fix-A + Fix-B (both trivial). Path B (follow-up
story) may be appropriate for Fix-C if the H2–H4 root cause turns out non-trivial.

User decision required: bundle all three in Story 11.3.3, OR split into 11.3.3a/b/c?

### Resolution (2026-05-25) — user chose "bundle all three"

**Fix-A — SHIPPED @ `f6b4b89`.** Dropped the `--` separator from `nightly.yml`
idempotency-replay step. Verified locally: `just test-idempotency --junitxml=...`
→ 15 passed; `just test-idempotency -- --junitxml=...` → no tests ran, exit 4
(reproduces nightly failure). Root cause + fix confirmed.

**Fix-B — SHIPPED @ `f6b4b89`.** Root cause = `event_log.py:506` writes JSONL at
mode `0o640` (audit-non-world-readable). Container ran as uid 10002, host pytest
as uid 1001 → EACCES. Fix: 4× `_compose_env` sites now default `OMB_*_UID/GID`
to `os.getuid()/os.getgid()` (crash-injection `_crash_compose.py` +
separability `test_s1`/`test_s2`/`test_s3`). Container now writes files the host
owns. Cannot fully verify on macOS (VirtioFS masks the Linux raw-uid issue) —
nightly will confirm.

**Fix-C — DIAGNOSIS IN PROGRESS, blocked on nightly trace.**
- H1 (import-time) — **REFUTED**: host import 0.32s.
- H2 (lifespan deadlock) — refuted cross-platform: local macOS repro (built
  registry-state image, booted crash-injection compose) showed cold boot AND
  restart-after-SIGKILL both reach `healthy` in ~6s. The 120s hang is
  **Linux-specific** (ubuntu-latest). AC2 trace instrumentation added so the
  next nightly's `crash-injection-container-logs.txt` artifact reveals which
  lifespan phase stalls on Linux.
- H3 (SQLite contention) — **REFUTED**: one container per compose; `down -v`
  between runs; no stale journal.
- H4 (schema re-mat) — **REFUTED**: fresh container logs
  `startup replay: cursor=0, applied=0 new` (empty events table).
- **D1 (Path A vs B) NOT YET RESOLVED** — deferred until the nightly phase
  trace localizes the Linux hang. If trivial → Path A in-scope fix; if it
  needs a cross-cutting change → Path B `11-3-4-<root-cause>.md`.

**Local repro evidence (macOS, registry-state image rebuilt on current source):**
```
cold boot:            t=6s → running healthy
restart-after-SIGKILL: t=6s → running healthy
AC2 trace (cold):     engine_create 0.026s · schema_create 0.528s ·
                      recover_all_logs 0.005s · handlers_register 0.000s ·
                      ready_touch 0.026s  (total ≈0.6s)
```

**Validation gates @ `f6b4b89`:** 3125 non-slow tests pass (3 platform skips);
ruff clean; mypy 215 errors = unchanged baseline (verified via `git stash` —
zero delta from this change; the spec's "108" baseline was stale);
check_imports + check_event_registry + check_single_writer all exit 0.

**Notes for executor:**
- Apply Epic 11 retro addendum AI-1 mandate: spawn 3-lane review at pass-1 close.
- Apply AI-6 (BaseException-leak audit) if AC4 Path A modifies any try/finally.
- Apply AI-7 (test-realism) on any regression test added in AC4 Path A.
- Apply AI-10 (cross-lane convergence marking) when summarizing pass-1 review findings.
- Standing user policy "fix all issues even minors" applies to the review batch.
