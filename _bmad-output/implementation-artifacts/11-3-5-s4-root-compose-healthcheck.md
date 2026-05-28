# Story 11.3.5 — S4 separability: registry-state unhealthy under ROOT compose

Status: **ready-for-dev** (create-story 2026-05-27 — comprehensive context pass; supersedes
the 2026-05-25 D3-split stub)

## Story

**As** the platform maintainer
**I want** the nightly `s3-separability` job's **S4** sub-test
(`tests/separability/test_s4_metrics_subscriber_optional.py`) to pass — `registry-state`
must reach `healthy` under the **ROOT** `docker-compose.yml` (Phase 1, 7 services incl.
metrics-subscriber) and the `docker-compose.s4.yml` overlay (Phase 2, 6 services)
**so that** the `s3-separability` job goes fully green (Story 11.3.4 fixed S1/S2/S3; S4 is the
last red sub-test) and the FR62a / NFR-M4/M5 "metrics-subscriber is optional" invariant
regains regression protection.

## Background — why this is separate from 11.3.4

Story 11.3.4 root-caused and fixed the S1/S2/S3 **task-progression** stall (null-orchestrator
`trace_id`; scripted-worker MCP env-strip + `task.plan.ready` tuple coercion). S4 is a
**different failure class** — the stack never finishes booting:

```
S4: Phase 1 compose up failed (rc=1); ... dependency failed to start:
    container omb-registry-state is unhealthy
```

S4 Phase 1 boots the **ROOT** `docker-compose.yml` directly
(`test_s4_metrics_subscriber_optional.py:77` `_ROOT_COMPOSE_FILE`), with `down -v` between
phases (fresh `oh-my-bmad-data` **named volume** each run). `registry-state` never reaches
`healthy`, so `compose up` fails before any task is posted.

## The decisive delta (passing S1/S2/S3 vs failing S4)

The S1/S2/S3 composes that Story 11.3.4 got GREEN and the ROOT compose differ in exactly
the two ways most likely to break registry-state's boot:

| | S1/S2/S3 test composes (PASS) | ROOT compose — S4 Phase 1 (FAIL) |
|---|---|---|
| volume | **bind mount**, harness pre-creates the dir `0o777` (so uid 10002 can write) | **named volume** `oh-my-bmad-data` (docker-compose.yml:69, defined :249-251), no pre-creation |
| `user:` | explicit `user: ${OMB_S*_UID:-10002}:...` | **none** (image-default uid 10002) |
| schema bootstrap | `REGISTRY_STATE_AUTO_CREATE_SCHEMA: "1"` (in-process `create_all`) | **unset** — production relies on the `migrator` service running `alembic upgrade head` (Story 2.14) |

These two deltas are the H6a / H6b hypotheses below.

## Hypotheses (H6a / H6b — confirm with the AC2 lifespan trace from Story 11.3.3)

> The gated `REGISTRY_STATE_LIFESPAN_TRACE=1` instrumentation (shipped 11.3.3, in-tree)
> logs each lifespan phase boundary: `engine_create → schema_create → recover_all_logs →
> handlers_register → ready_touch`. Enable it on the ROOT-compose registry-state and read
> `docker compose logs registry-state` to see WHICH phase stalls — that disambiguates H6a
> from H6b deterministically (AI-11: specific evidence, not "seemed like").

### H6a — named-volume ownership (LEADING; strong Dockerfile evidence)

`services/registry-state/Dockerfile` creates the service user (`useradd --uid 10002`,
`USER registry-state` at lines 5-6) but does **NOT** `mkdir`/`chown` `/var/lib/oh-my-bmad`.
Docker initializes a **named** volume's ownership from the image's content at the mount path
*only if that path exists in the image*; when it does not, the volume mountpoint is created
**root-owned**. So `registry-state` (uid 10002) cannot write the root-owned
`oh-my-bmad-data` volume → it fails to create `registry/events/` or `state.sqlite3` →
lifespan errors before `Path("/tmp/ready").touch()` → healthcheck never flips.

Fix-B (Story 11.3.3) does NOT cover this: Fix-B's `OMB_*_UID/GID` host-uid override applies
to the *bind-mount test composes*; the ROOT compose has no `user:` and a *named* volume.

**Probe:** local ROOT-compose boot on Linux (or CI) with `REGISTRY_STATE_LIFESPAN_TRACE=1`;
`docker volume inspect omb_oh-my-bmad-data` + `docker compose exec registry-state ls -lan
/var/lib/oh-my-bmad`; expect a root-owned mountpoint + an `engine_create`/early-phase
failure (can't open/create the SQLite path).

**Verify the base image first:** confirm `oh-my-bmad-base` (the FROM) does not itself
`mkdir`/`chown` `/var/lib/oh-my-bmad` — if it does, H6a is weakened and H6b becomes primary.

### H6b — missing schema bootstrap under ROOT compose

The ROOT compose omits `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1`; production gets the schema
from the `migrator` service (`alembic upgrade head`). If the S4 test does not order the
migrator before registry-state's lifespan needs the schema (registry-state `depends_on` the
migrator? the test boots all services together), `recover_all_logs`/materialize may run
against a schema-less DB → lifespan error → `/tmp/ready` never touched.

**Probe:** with the trace on, see whether the stall is at `recover_all_logs` (→ H6b) vs
`engine_create` (→ H6a). Check whether `migrator` runs to completion before registry-state
in the ROOT compose ordering.

## Acceptance criteria

### AC1 — Local repro + phase localization
Reproduce S4 Phase-1 `registry-state unhealthy` on Linux/CI (note macOS VirtioFS masks
raw-uid perms — if H6a won't repro on macOS, say so and rely on CI). Enable
`REGISTRY_STATE_LIFESPAN_TRACE=1`; capture `docker compose logs registry-state`,
`docker inspect` health, and `docker volume inspect` + mountpoint `ls -lan`. Record literal
excerpts in the Dev Agent Record.

### AC2 — H6a / H6b verdicts (AI-11-specific evidence)
For H6a and H6b: probe, literal observation, verdict (CONFIRMED / REFUTED). Exactly one
expected primary; if both contribute, document the compound cause.

### AC3 — Fix at the right altitude
Apply the fix where the root cause lives:
- If **H6a**: prefer `services/registry-state/Dockerfile` `RUN mkdir -p
  /var/lib/oh-my-bmad && chown registry-state:omb /var/lib/oh-my-bmad` **before** `USER`
  (and audit the sibling services that mount the same volume: registry-api, worker-wrapper,
  clawhip-daemon, metrics-subscriber, migrator — whichever WRITES the volume needs the
  ownership). This fixes the test AND real fresh-named-volume production deploys. This is a
  **production Dockerfile change** → flag explicitly for the 3-lane review.
- If **H6b**: ensure schema ordering — either the S4 test/ROOT compose guarantees the
  migrator completes before registry-state serves, or document why AUTO_CREATE_SCHEMA is
  test-only and add the ordering. Do NOT blanket-enable AUTO_CREATE_SCHEMA in the production
  ROOT compose (that bypasses the migrator contract — Story 2.14).
- Add a regression guard (AI-7: must fail against the pre-fix image/compose).

### AC4 — Both phases healthy + serve identically
S4 Phase 1 (ROOT, 7 svc) and Phase 2 (s4.yml, 6 svc) reach healthy; registry-api serves
`/v1/health` + `POST /v1/tasks` identically in both. `test_s4_metrics_subscriber_optional.py`
passes locally (to the extent the platform allows; CI confirms on Linux).

### AC5 — No regression
S1/S2/S3 (11.3.4 fixes) + idempotency + migrator + crash-injection nightly jobs stay green;
PR-gate `ci` stays green. **Do NOT revert 11.3.4 or 11.3.3 Fix-A/B/AC2.** If H6a fix touches
the registry-state Dockerfile, rebuild + re-run crash-injection (it boots the same image).

### AC6 — Validation gates green
```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ services/ scripts/   # no NEW errors vs tree baseline
uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
uv run pytest -x -q -m "not slow"                    # no regressions
```

### AC7 — Nightly verification
After commit + push: `gh workflow run nightly.yml`; confirm **all 4 jobs PASS** — the
`s3-separability` job now fully green (S1/S2/S3/S4). Record run id + conclusion.

## Decisions (resolve during implementation)

### D1 — Dockerfile vs compose vs test-harness fix (after AC2)
If H6a: the Dockerfile `mkdir+chown` is the deepest fix (benefits production) — prefer it
over a test-only `user:`/init-container band-aid, unless the base image already owns the dir
(then the cause is elsewhere). If H6b: ordering/migrator fix, NOT production AUTO_CREATE_SCHEMA.

### D2 — Scope of the volume-ownership fix
If H6a confirmed, audit ALL services mounting `oh-my-bmad-data` read-write (registry-state,
metrics-subscriber, migrator) — they share the volume; the writer(s) need the ownership.
Read-only mounters (registry-api, worker-wrapper, clawhip-daemon — `:ro`) don't.

## Constraints

- **Epic 11 retro AI-1 mandate APPLIES** — cross-cutting (ROOT compose + Dockerfile +
  healthcheck) → **3-lane adversarial review at pass-1** (Blind = `code-reviewer`; Edge +
  Acceptance = `general-purpose`).
- **AI-6** (BaseException-leak audit) if any lifespan `try/finally` is touched.
- **AI-7** (test-realism) — regression guard must fail against the pre-fix image/compose.
- Do **NOT** revert Story 11.3.4 (S1/S2/S3) or 11.3.3 Fix-A/B/AC2.
- Do **NOT** enable `REGISTRY_STATE_AUTO_CREATE_SCHEMA` in the production ROOT compose
  (preserves the Story 2.14 migrator-owns-schema contract).
- **FR26 single-writer invariant preserved.**
- Production Dockerfile changes (H6a path) MUST be flagged explicitly for the review.

## Dev Notes

### Source map (file:line guardrails)
- **ROOT compose registry-state:** `docker-compose.yml:53-69` — named volume
  `oh-my-bmad-data` (:69), volume def `:249-251`, no `user:`, no `REGISTRY_STATE_AUTO_CREATE_SCHEMA`.
  Shared healthcheck anchor `x-healthcheck` (:27-31, `start_period: 10s`). Sibling writers of
  the volume: metrics-subscriber (:199), migrator (:243); read-only mounters use `:ro`.
- **registry-state Dockerfile:** `services/registry-state/Dockerfile:5-6` — `useradd --uid
  10002` + `USER registry-state`; **no `mkdir`/`chown` of `/var/lib/oh-my-bmad`** (H6a). Check
  the `FROM` base image (`oh-my-bmad-base`) for whether it pre-creates the dir.
- **registry-state lifespan:** `services/registry-state/src/registry_state/app/main.py` —
  phases `engine_create → schema_create (gated REGISTRY_STATE_AUTO_CREATE_SCHEMA) →
  recover_all_logs → handlers_register → /tmp/ready touch`; `REGISTRY_STATE_LIFESPAN_TRACE=1`
  gated trace at each boundary (Story 11.3.3 AC2, in-tree).
- **S4 test:** `tests/separability/test_s4_metrics_subscriber_optional.py` — `_ROOT_COMPOSE_FILE`
  (:77) Phase 1; `_S4_COMPOSE_FILE` (:78) Phase 2; per-phase `try/finally` → `down -v` (:26-28).
- **Audit-log write mode:** `event_log.py:506` writes JSONL at `0o640` (the same perm that
  drove Fix-B) — relevant if the volume is writable but cross-uid reads fail.
- **macOS overlay:** `docker-compose.macos.yml` bind-mounts to `${HOME}/.oh-my-bmad` — why
  macOS may not reproduce H6a.

### Testing standards
Reuse the S4 test's compose-driven fixtures; honor `down -v` between phases (named-volume
contamination is the documented hazard, test header :26-28). If adding a Dockerfile fix,
rebuild the base + registry-state images (`just build-base` + `docker compose build
registry-state`) before re-running — stale images are a known 11.3.3 footgun.

### References
- [Source: 11-3-4-separability-task-progression.md — S1/S2/S3 fixes; D3 split rationale]
- [Source: 11-3-3-nightly-deeper-diagnosis.md — Fix-B bind-mount uid; AC2 lifespan trace]
- [Source: docker-compose.yml:53-69,249-251 — ROOT registry-state + named volume]
- [Source: services/registry-state/Dockerfile:5-6 — uid 10002, no dir chown]

## Frontmatter

```yaml
---
story_id: 11.3.5
story_key: 11-3-5-s4-root-compose-healthcheck
parent_epic: 11
phase: 2
fr_refs: [FR62a, NFR-M4, NFR-M5]
nfr_refs: [NFR-M4, NFR-M5]
arch_refs:
  - "Story 11.3.4 — S1/S2/S3 fixes (this is the carved-out S4 ROOT-compose cause, D3)"
  - "Story 10.6 — S-4 separability harness origin (metrics-subscriber optional)"
  - "Story 11.3.3 Fix-B — bind-mount uid (does NOT cover the ROOT named volume)"
  - "Story 11.3.3 AC2 — REGISTRY_STATE_LIFESPAN_TRACE gated trace (re-usable probe)"
  - "Story 2.14 — migrator owns schema (alembic upgrade head); ROOT compose has no AUTO_CREATE_SCHEMA"
estimated_complexity: LOW-MEDIUM
priority: medium (last red sub-test of s3-separability; no production impact — PR-gate ci green throughout — UNLESS H6a also affects fresh-named-volume prod deploys, which the fix would close)
blocks: []
unblocks:
  - Fully-green nightly s3-separability job (S1/S2/S3/S4)
---
```

## Tasks / Subtasks

- [ ] AC1 — Local/CI repro of S4 Phase-1 registry-state-unhealthy; enable `REGISTRY_STATE_LIFESPAN_TRACE=1`; capture logs + `docker volume inspect` + mountpoint `ls -lan`
- [ ] AC2 — H6a / H6b verdicts with specific evidence (which lifespan phase stalls)
  - [ ] Verify whether the `oh-my-bmad-base` image pre-creates/chowns `/var/lib/oh-my-bmad`
  - [ ] H6a named-volume ownership (root-owned mountpoint vs uid 10002)
  - [ ] H6b missing schema bootstrap / migrator ordering
- [ ] AC3 — Fix at root-cause altitude (Dockerfile mkdir+chown for H6a, or ordering for H6b) + regression guard
- [ ] AC4 — S4 Phase 1 + Phase 2 both healthy; registry-api serves identically; S4 test passes
- [ ] AC5 — No regression in S1/S2/S3 + other nightly jobs + PR-gate ci; 11.3.4 & 11.3.3 untouched
- [ ] AC6 — Validation gates green
- [ ] AC7 — Nightly: all 4 jobs PASS (record run id)
- [ ] AI-1 — 3-lane adversarial review at pass-1; findings batch-applied ("fix all issues even minors")

## Dev Agent Record

### Agent Model Used
claude-opus-4-7[1m] (dev-story 2026-05-28)

### Debug Log References

**H6a CONFIRMED (static) — named-volume root-ownership:**
- `services/registry-state/Dockerfile` is a thin override (FROM `oh-my-bmad-base:local` +
  `useradd --uid 10002 --gid omb` + `USER` + ENTRYPOINT) — no `/var/lib/oh-my-bmad` creation.
- `Dockerfile.base` (runtime-base) creates only `/app` group-owned by `omb` + `chmod 2775`
  (lines 56-59); it does **NOT** create `/var/lib/oh-my-bmad`.
- ∴ the named volume `oh-my-bmad-data` mounts on a path absent from the image → Docker seeds
  the fresh volume **root-owned** → registry-state (uid 10002, gid omb 10000) can't write →
  lifespan never reaches `/tmp/ready` → `unhealthy` (the nightly S-4 Phase-1 error).
- The S-4 test corroborates the bind-vs-named asymmetry: Phase 2 pre-`chmod 0o777`s its
  **bind** dir (`test_s4_*.py:608`) so it can pass; Phase 1's **named** volume can't be
  pre-chmod'd from the host → only Phase 1 fails. Nightly run 26539248575 showed exactly
  `Phase 1 compose up failed (rc=1) ... container omb-registry-state is unhealthy`.

**FIX APPLIED — `Dockerfile.base`:** extend the existing `/app` group-writable pattern to
also `mkdir -p /var/lib/oh-my-bmad && chgrp omb && chmod 2775`. Docker seeds a fresh named
volume from the image dir's ownership/perms, so the volume now mounts `root:omb 2775`;
setgid makes registry-state/migrator-created subdirs+files inherit the omb group → every
omb-group service (all `--gid omb`) can read/write regardless of per-service uid. Production
base-image change (flagged for AI-1 review); fixes fresh-named-volume prod deploys too.

**⚠️ Verification attempt #1 ABORTED (2026-05-28):** a delegated background verifier
reintroduced the P0 secret leak (`env=dict(os.environ)` in orchestrator-adapter +
worker-wrapper `mcp_clients.py` — the exact a0ca050 finding) and sprawled across 18 files
(ROOT compose, MCP security contracts, core spine). **All of its changes were reverted**
(`git checkout` to `3ef4a90`); the P0 leak is confirmed gone. The H6a base-image fix
(authored before delegation) was re-applied clean. End-to-end Docker verification is
**pending** — to be done under close supervision, NOT another unsupervised long agent.

### Supervised Docker verification (2026-05-28) — COMPOUND cause, 3 layers

Local ROOT-compose boot of registry-state (fresh named volume) revealed the failure is
**three stacked causes**, not one:

1. **H6a — named-volume root-ownership.** CONFIRMED + FIXED. Base-image change makes the
   data dir `2775`; the fresh named volume now seeds `drwxrwsr-x 0 10000` (verified via
   `docker run -v <vol> ... ls -lan`). registry-state (omb group) can write the root.
2. **H6c — DB parent dir `registry/` never created.** CONFIRMED + FIXED. After H6a, the next
   crash was `sqlite3.OperationalError: unable to open database file` — the volume root was
   writable but `registry/` (parent of `state.sqlite3`) didn't exist and SQLite won't create
   parent dirs. The ROOT compose omits `REGISTRY_STATE_LOG_DIR` (the test composes set it, so
   the log-writer's mkdir incidentally creates `registry/`). FIX: `_ensure_db_parent_dir()` in
   `registry_state/app/main.py` mkdir's the DB parent `2775` before `create_engine`. Confirmed:
   `registry/` (`drwxrwsr-x 10002 10000`) + `state.sqlite3` now created.
3. **H6b — no SQLite schema bootstrap.** CONFIRMED, **decision pending**. Next crash:
   `sqlite3.OperationalError: no such table: snapshots`. The DB opens but has no tables. The
   ROOT compose sets no `REGISTRY_STATE_AUTO_CREATE_SCHEMA`; the **migrator is
   `profiles:["migrate"]`** (NOT started by `docker compose up -d`) and migrates the EVENT LOG
   (`EVENT_LOG_PATH`), not the DB schema; registry-state has **no `depends_on` migrator**. So
   nothing runs `Base.metadata.create_all`/`alembic upgrade head` on a plain `up`. The
   S-1/S-2/S-3 test composes all set `AUTO_CREATE_SCHEMA=1` to self-bootstrap; the S-4 test
   boots the production ROOT compose which doesn't. **This also implies a fresh production
   `docker compose up -d` (no `--profile migrate`, no operator alembic step) would hit the
   same empty-schema crash — a possible production gap, not just a test issue.**

**IMAGE-ARCH NOTE (cost me a cycle):** all Python code lives in the BASE image's venv
(`uv sync --no-editable --all-packages` in `Dockerfile.base` stage 1); per-service Dockerfiles
only `useradd`. So ANY `services/*/src` code change requires `just build-base` — rebuilding
the thin per-service image alone does NOT pick it up.

### Completion Notes List
- [x] H6a confirmed + FIXED (base-image group-writable `/var/lib/oh-my-bmad`); volume seeds 2775.
- [x] H6c confirmed + FIXED (`_ensure_db_parent_dir` mkdir's `registry/` 2775 before engine).
- [ ] H6b (no schema bootstrap) — confirmed; FIX APPROACH PENDING OPERATOR DECISION (test-scoped
      AUTO_CREATE_SCHEMA opt-in vs production schema-bootstrap fix). Likely also a prod gap.
- [ ] Re-verify S-4 Phase 1+2 green after H6b fix; then AC5 nightly + AI-1 3-lane review.

### H6b FIXED + SCOPE REVELATION (2026-05-28)

H6b resolved per operator decision (test-scoped AUTO_CREATE_SCHEMA): ROOT compose reads
`REGISTRY_STATE_AUTO_CREATE_SCHEMA` env (default OFF in prod — unchanged); the S-4 test
exports `=1`. After all three fixes (H6a + H6c + H6b), **registry-state boots HEALTHY** in
the ROOT compose (verified: `Up (healthy)`). metrics-subscriber also healthy.

**BUT the full S-4 Phase-1 now reveals pre-existing downstream failures** that were MASKED
because compose aborted on registry-state first (its `depends_on` failed). With registry-state
healthy, the 180s wait exposed:
- **registry-api** — `Restarting (3)` crash loop (mounts the volume `:ro` in the ROOT compose
  but S-1/S-2/S-3 mount it RW because "registry-api writes JSONL"; likely can't write the
  event log, OR needs the schema/another config).
- **clawhip-daemon** — unhealthy.
- **telegram-gateway** — unhealthy (likely empty `TELEGRAM_BOT_TOKEN` in the test env; the
  Phase-2 `docker-compose.s4.yml` overlay sets `TELEGRAM_BOT_TOKEN: ""` + `TG_ALLOWLIST_USER_IDS: "[]"`
  explicitly, but Phase-1 ROOT compose relies on a (absent) `.env`).
- **orchestrator-adapter / worker-wrapper** — still "starting" at timeout.

These are NOT caused by this story's changes — they are pre-existing ROOT-compose fresh-boot
gaps. **Implication: the ROOT compose has never booted all-healthy on a fresh volume** (a
production fresh-deploy concern beyond the documented "registry-state unhealthy" nightly cause).
This is a materially larger scope than 11.3.5 was filed for (which targeted the registry-state
failure — now fixed). **Scope decision pending operator** (see below): close 11.3.5 on the
registry-state fix + split the downstream multi-service boot failures, vs expand 11.3.5.

### Files (in-flight, uncommitted)
- `Dockerfile.base` (H6a — data-dir 2775) — production base-image fix
- `services/registry-state/src/registry_state/app/main.py` (H6c — `_ensure_db_parent_dir`) — production fix
- `docker-compose.yml` (H6b — `REGISTRY_STATE_AUTO_CREATE_SCHEMA` env passthrough, default OFF)
- `tests/separability/test_s4_metrics_subscriber_optional.py` (H6b — Phase-1 exports `AUTO_CREATE_SCHEMA=1`)

### File List
- `Dockerfile.base` (modified — pre-create `/var/lib/oh-my-bmad` group-owned by omb + 2775; H6a fix)

### Review Findings (`/code-review` 2026-05-28, pre-verification — verification-GATED)

Reviewed the H6a `Dockerfile.base` diff. 2 real/plausible findings — both are precisely
what the pending Docker verification disambiguates, so they are **held (not blind-patched)**.
Rationale: a delegated verifier just reintroduced a P0 by speculatively patching multi-service
S4 permission/env issues; fixing these without runtime evidence of which write conflicts would
repeat that. Confirm-then-fix.

- [ ] [Review][Verify-gated] **F1 — multi-writer cross-uid subdir perms** [Dockerfile.base] —
  setgid 2775 propagates the omb GROUP to new subdirs but NOT group-write (no `umask 002`
  anywhere → new dirs are 2755, group r-x). `registry/events/` is written by BOTH
  registry-state (uid 10002) AND the migrator (`EVENT_LOG_PATH=.../registry/events/current.jsonl`,
  different uid). Whichever creates the shared subdir first (2755) locks the other out (EACCES).
  **Resolve:** if the S4 run shows a service failing EACCES into a peer-created subdir, fix with
  evidence (candidates: pre-create the shared `registry/events` group-writable in base; `umask
  002` in writer entrypoints; or per-service subdir ownership). If S4 passes, non-issue (each
  writer owns its own subdir + FR26 single-writer holds).
- [ ] [Review][Verify-gated] **F2 — H6b (schema bootstrap) not addressed** [Dockerfile.base] —
  the fix is H6a-only; the ROOT compose has no `REGISTRY_STATE_AUTO_CREATE_SCHEMA` (relies on
  the migrator). If registry-state needs the schema before the migrator runs, it could still
  fail at a later lifespan phase. **Resolve:** the `REGISTRY_STATE_LIFESPAN_TRACE` output from
  the S4 run shows whether the stall (if any) is post-volume-write at a schema phase.
- [x] [Review][Dismiss] non-recursive chmod (fresh `down -v` volume has no subdirs); base
  placement / all-images-get-the-dir (intentional, ensures consistent seeding); chgrp/chmod
  order; comment accuracy. No defects.

## Definition of Done

- S4 Phase 1 + Phase 2 healthy; `s3-separability` nightly job fully PASS (S1/S2/S3/S4).
- H6a/H6b documented with probe evidence (which lifespan phase, volume ownership).
- S1/S2/S3 + other nightly jobs + PR-gate ci remain green.
- AI-1 3-lane review complete; findings batch-applied.
- `sprint-status.yaml` `11-3-5-...: ready-for-dev → … → done`.
