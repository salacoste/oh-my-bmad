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

## Definition of Done

- S4 Phase 1 + Phase 2 healthy; `s3-separability` nightly job fully PASS (S1/S2/S3/S4).
- H6a/H6b documented with probe evidence (which lifespan phase, volume ownership).
- S1/S2/S3 + other nightly jobs + PR-gate ci remain green.
- AI-1 3-lane review complete; findings batch-applied.
- `sprint-status.yaml` `11-3-5-...: ready-for-dev → … → done`.
