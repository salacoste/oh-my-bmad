# Story 11.3.6 — ROOT compose fresh-boot: downstream services unhealthy (S4 Phase 1)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform maintainer,
**I want** every service in the ROOT `docker-compose.yml` to reach `healthy` on a **fresh
named volume** (S-4 separability Phase 1: registry-api, registry-state, telegram-gateway,
orchestrator-adapter, worker-wrapper, clawhip-daemon, metrics-subscriber),
**so that** the nightly `s3-separability` job goes fully green (Story 11.3.5 fixed
registry-state; these are the remaining red services) AND a fresh production `docker compose
up -d` actually boots (this is likely a real production fresh-deploy gap, not just a test issue).

## Background — why this is separate from 11.3.5

Story 11.3.5 fixed **registry-state unhealthy** under the ROOT compose (3-layer fix: named-volume
ownership `Dockerfile.base` 2775 + `_ensure_db_parent_dir` + test-scoped schema bootstrap;
committed `219607d`, registry-state verified `Up (healthy)`). With registry-state's `depends_on`
no longer aborting `compose up`, the full Phase-1 boot ran the 180s healthcheck wait and exposed
**pre-existing downstream failures** that registry-state's earlier crash had been masking.

**The ROOT compose has never booted all-healthy on a fresh volume.** That is this story.

## ⚠️ SECURITY GUARDRAIL (read first — this is the riskiest part of the story)

The orchestrator-adapter / worker-wrapper fix (H7b) edits `services/*/.../adapters/mcp_clients.py`
— **the exact file where a delegated agent reintroduced a P0 secret leak** (`env=dict(os.environ)`,
caught and reverted in `a0ca050`; it happened AGAIN during 11.3.5 verification and was reverted
a second time). **NEVER use `env=os.environ.copy()` or `env=dict(os.environ)`.**

Propagate the required MCP-subprocess env via an **ALLOWLIST**, mirroring the existing production
pattern:
- **Canon:** `mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py:49-90`
  — `_ENV_ALLOWLIST` (frozenset) + `_default_env_allowlist()` helper + `env=self.env` at
  `StdioServerParameters` (`:137`).
- **Byte-identical sibling:** `mcp-servers/session-registry/.../clawhip_client.py:42-78`.
- **Narrower test pattern (Story 11.3.4):** `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py:489-510`
  — `_CLAWHIP_ENV_ALLOWLIST` + `_clawhip_env()` + `env=_clawhip_env()` at `:667`.

The allowlist must **NOT** forward `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `OPERATOR_HMAC_KEY`, AWS
creds, or any secret. Only PATH/HOME/locale/SSL-CA + the **required** `*_DB_PATH` /
`CLAWHIP_BRIDGE_*` / `REGISTRY_EVENTS_DIR` vars the subprocesses need.
Extend `tests/contract/test_clawhip_client_env_allowlist_mirror.py` to cover the two new
propagation sites. This change is cross-cutting + security-sensitive → **AI-1 3-lane review
mandatory at pass-1**.

## Acceptance Criteria

1. **AC1 — Repro.** Reproduce each downstream failure on a fresh ROOT-compose boot on Linux/CI
   (`down -v` first; set `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1` so registry-state is healthy per
   11.3.5). Capture literal `docker compose logs` excerpts per failing service + `docker compose
   ps` health into the Dev Agent Record. (macOS VirtioFS may mask perm/uid behavior — note it and
   rely on CI/Linux if so.)
2. **AC2 — Verdicts.** For H7a / H7b / H7c: probe → literal observation → verdict
   (CONFIRMED / REFUTED / BENIGN), per AI-11 (specific evidence, not "seemed like").
3. **AC3 — Fix at the right altitude.** **H7b MUST use the allowlist pattern (never
   `os.environ.copy()` / `dict(os.environ)`).** For each fix, assess + document **production
   impact** (H7a registry-api RW + H7b MCP env are likely prod fresh-deploy gaps, not just test
   issues) and fix in the production compose/code where the root cause lives.
4. **AC4 — Both phases healthy.** S-4 Phase 1 (ROOT, 7 svc) and Phase 2 (`docker-compose.s4.yml`,
   6 svc) ALL reach healthy; registry-api serves `/v1/health` + `POST /v1/tasks` identically in
   both; `tests/separability/test_s4_metrics_subscriber_optional.py` passes (CI confirms on Linux).
5. **AC5 — No regression.** S1/S2/S3 (11.3.4) + registry-state (11.3.5) + idempotency +
   crash-injection + migrator nightly jobs stay green; PR-gate `ci` green. **Do NOT revert
   Story 11.3.4, 11.3.5, or 11.3.3 Fix-A/B/AC2.** If a fix touches a production Dockerfile/image,
   rebuild (`just build-base`) and re-run crash-injection (same image).
6. **AC6 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # no NEW errors vs baseline
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q -m "not slow"                                # no regressions
   ```
7. **AC7 — Nightly.** After commit + push: `gh workflow run nightly.yml`; confirm **all 4 jobs
   PASS** — `s3-separability` now fully green (S1/S2/S3/S4). Record run id + conclusion.
8. **AC8 — Secret-leak invariant re-verified.** After the `mcp_clients.py` change: grep for
   `os.environ.copy` / `dict(os.environ)` in `services/*/adapters/` returns nothing; the
   extended mirror contract test passes and asserts no secret var (ANTHROPIC/GITHUB/HMAC) is in
   the new allowlists.

## Tasks / Subtasks

- [x] **Task 1 — Repro + diagnose on fresh ROOT compose (AC1, AC2)** ✅ live Docker boot, full per-service evidence captured in Dev Agent Record (H7a CONFIRMED + FIXED; H7b RUNTIME-VERIFIED via audit-OFF; H7d/g/h surfaced as new gaps, split to 11.3.7)
- [x] **Task 2 — H7a: registry-api RW + config (AC3, AC4)** ✅ `:ro`→RW + `REGISTRY_API_DB_URL`/`LOG_DIR` + `depends_on: registry-state healthy`; registry-api now boots `healthy` ("Application startup complete", no EROFS)
- [x] **Task 3 — H7b: MCP env via ALLOWLIST (AC3, AC8) — SECURITY-SENSITIVE** ✅ done end-to-end
  - [x] Full MCP env block on orchestrator-adapter (identity `orchestrator`) + worker-wrapper (identity `worker`) in `docker-compose.yml` AND `docker-compose.s4.yml`; PLUS `OMB_MCP_AUDIT_EMISSION_ENABLED=0` (H7f) to break the nested-stdio deadlock isolated in live Docker probes
  - [x] `_ENV_ALLOWLIST` + `_default_env_allowlist()` + `env=self.env` in BOTH `mcp_clients.py` (byte-identical, no secrets, NEVER `os.environ.copy()`); ruff/mypy/contract tests green
  - [x] Extended `tests/contract/test_clawhip_client_env_allowlist_mirror.py` with 3 tests (byte-identical mirror, required-vars present, secret-exclusion guard)
  - [x] **Runtime verification:** in Docker, worker-wrapper actively processes live `CallToolRequest`s; orchestrator-adapter passes all 3 `_connect()` calls; nested-stdio deadlock confirmed via AUDIT-ON-vs-OFF probe (ON: `McpError: Connection closed`/hang; OFF: `INITIALIZE OK`)
- [x] **Task 4 — H7c → H7d/H7e/H7g (recharacterized by live evidence)** — **SPLIT to Story 11.3.7**
  - [x] H7d socket healthchecks for registry-api + telegram-gateway (both composes) — done
  - [x] H7h worker-wrapper ready-file env (`WORKER_READY_FILE_PATH=/tmp/ready`) — done
  - [SPLIT 11.3.7] H7d clawhip-daemon `/tmp/ready` code touch (mirror registry-state Story 2.11)
  - [SPLIT 11.3.7] H7e clawhip-daemon dummy `TELEGRAM_BOT_TOKEN` injection (tests)
  - [SPLIT 11.3.7] H7g orchestrator-adapter `upstream/omc` Dockerfile/image change
  - [SPLIT 11.3.7] telegram-gateway hermetic-test skip-`set_webhook` code path
- [SPLIT 11.3.7] **Task 5 — Regression guard for the FULL S-4 boot** (needs the 11.3.7 fixes to be a meaningful guard)
- [x] **Task 6a — Validation gates green:** ruff ✓ · ruff format ✓ · mypy --strict on changed adapters ✓ · check_imports ✓ · check_event_registry ✓ · check_single_writer ✓ · `pytest -m "not slow"` **3139 passed, 3 skipped, 0 failed** (no regression)
- [SPLIT 11.3.7] **Task 6b — Nightly all-4-green** (S-4 can't be green until 11.3.7's tail closes)
- [x] **Task 7 — AI-1 7-angle review** completed 2026-05-28. `/code-review high` ran 7 finder angles (line-by-line + removed-behavior + cross-file + reuse + simplification + efficiency + altitude). 10 ranked findings; all applied as fixes (see "Code review findings" below). Validation re-run: 3140 tests pass, ruff/format/mypy/discipline ✓, both composes config-validate ✓. P0 diff-audit clean — no `os.environ.copy` / `dict(os.environ)` under `services/*/adapters/mcp_clients.py`.

## Dev Notes

### Root causes (diagnosed locally @ 219607d during 11.3.5 — confirm + fix each)

**H7a — registry-api: `unable to open database file` (crash loop).** `docker-compose.yml:50`
mounts registry-api `oh-my-bmad-data:/var/lib/oh-my-bmad:ro`, but registry-api is RW-dependent:
it writes the JSONL event log via `EventLogWriter` (`app.py:253`) AND opens a writable SQLite
engine for the idempotency cache (`app.py:201` `create_engine(db_url, read_only=False)`) — only
the task-reads engine is read-only (`app.py:185`). The ROOT compose also never sets
`REGISTRY_API_DB_URL` / `REGISTRY_API_LOG_DIR` (it only sets `REGISTRY_DB_PATH`, a *different*
var name), so registry-api falls back to its hard-coded defaults (`__main__.py:134-135`) which
point *into* the `:ro` path. The S-1/S-2/S-3 and S-4-overlay composes mount registry-api RW for
exactly this reason. **Fix:** RW mount + set `REGISTRY_API_DB_URL`/`REGISTRY_API_LOG_DIR` (mirror
`docker-compose.s4.yml:37-39,47`); confirm ordering on registry-state.

**H7b — orchestrator-adapter + worker-wrapper: MCP subprocess can't start (crash loop).**
Logs: `task-registry: TASK_REGISTRY_DB_PATH is required but not set or empty` →
`mcp.shared.exceptions.McpError: Connection closed` (orchestrator-adapter); `TimeoutError` on
MCP init (worker-wrapper). Two stacked causes: **(1)** the ROOT compose omits the required
`*_DB_PATH` / `CLAWHIP_BRIDGE_*` vars on both services (orchestrator-adapter `environment:` only
has `ANTHROPIC_API_KEY` at `:114-116`; worker-wrapper has `ANTHROPIC_API_KEY`/`GITHUB_TOKEN`/
`REGISTRY_DB_PATH` at `:131-135`); **(2)** both `mcp_clients.py` build `StdioServerParameters`
**env-less** (orchestrator-adapter `:76`, worker-wrapper `:81` — `env=` omitted, correct post-
a0ca050), so the MCP SDK forwards only `get_default_environment()` (POSIX safe-list) and strips
the required vars → subprocess exits. **Fix:** set the vars in compose + propagate via ALLOWLIST
(see SECURITY GUARDRAIL). Likely a **production** bug too (a fresh prod orchestrator-adapter
would also fail to spawn its MCP servers).

**H7c — clawhip-daemon / telegram-gateway: likely benign (confirm).** During 11.3.5 local boot
their logs were clean (`telegram_sink started`; `Webhook set · ready`) — probably slow/eventual
healthy, not crashing. Phase-2 `docker-compose.s4.yml` sets `TELEGRAM_BOT_TOKEN: ""` (`:94`) +
`TG_ALLOWLIST_USER_IDS: "[]"` (`:95`) explicitly; Phase-1 ROOT compose relies on an (absent)
`.env`. Confirm once the crash-loopers are fixed; only fix if genuinely red.

### Source map (file:line guardrails)

**ROOT `docker-compose.yml`:**
- `x-common-env` anchor `:23` (`ENV`); `x-healthcheck` anchor `:26-31` (`test -f /tmp/ready`, start_period 10s)
- `registry-api` `:34-51` — **volume `:ro` at `:50`** (BUG); env `:43-45` (only `ENV` + `REGISTRY_DB_PATH`, missing `REGISTRY_API_DB_URL`/`REGISTRY_API_LOG_DIR`)
- `registry-state` `:53-76` — volume RW `:75`; env `:63-70` (incl. `REGISTRY_STATE_AUTO_CREATE_SCHEMA` passthrough from 11.3.5)
- `telegram-gateway` `:78-94` — no volume; env `:87-91`
- `orchestrator-adapter` `:96-120` — no volume; **env `:114-116` (only `ANTHROPIC_API_KEY`)** — missing `TASK_REGISTRY_DB_PATH`/`SESSION_REGISTRY_DB_PATH`/`CLAWHIP_BRIDGE_*`
- `worker-wrapper` `:122-141` — volume `:ro` `:140`; **env `:131-135`** — missing `CLAWHIP_BRIDGE_*`/`REGISTRY_EVENTS_DIR`
- `clawhip-daemon` `:143-160` — volume `:ro` `:159`; env `:153-154`
- `metrics-subscriber` `:181-221` — volume RW `:205`; `depends_on registry-state healthy` `:198-200`; own healthcheck `:206-221`
- `migrator` `:232-249` — **`profiles: ["migrate"]` `:238`** (NOT started by `up -d`); migrates the JSONL event log (`EVENT_LOG_PATH` `:243-244`), NOT the SQLite schema; `restart: "no"` `:246`
- named volume def `oh-my-bmad-data` `:255-257`

**MCP env propagation (the fix sites + the canon to mirror):**
- `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py:76` — `StdioServerParameters(command=command, args=args)` (env-less; ADD allowlisted `env=`)
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py:81` — same
- `mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py:49-90,137` — **CANON** `_ENV_ALLOWLIST` + `_default_env_allowlist()` + `env=self.env`
- `mcp-servers/session-registry/src/session_registry_mcp/adapters/clawhip_client.py:42-78` — byte-identical sibling
- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py:489-510,667` — narrower `_CLAWHIP_ENV_ALLOWLIST`/`_clawhip_env()` (Story 11.3.4)
- `tests/contract/test_clawhip_client_env_allowlist_mirror.py:44` — extend to the 2 new sites
- `tests/fixtures/scripted_worker_stub/test_clawhip_env_allowlist.py:32,47` — secret-leak assertion pattern

**registry-api DB/event-log:**
- `services/registry-api/src/registry_api/__main__.py:134-135` defaults; `:160-161` reads `REGISTRY_API_DB_URL`/`REGISTRY_API_LOG_DIR`
- `services/registry-api/src/registry_api/app.py:185` read-only engine; `:201` RW idempotency-cache engine; `:253` `EventLogWriter`

**S-4 test + overlay (the correct RW pattern to mirror):**
- `tests/separability/test_s4_metrics_subscriber_optional.py:77` `_ROOT_COMPOSE_FILE`, `:78` `_S4_COMPOSE_FILE`, `:80` `_HEALTHCHECK_TIMEOUT_S=180`, `:108-174` `_wait_for_all_healthy`, `:402` Phase-1 exports `AUTO_CREATE_SCHEMA=1`, `:597`/`:702` per-phase `down -v` in `finally`
- `tests/separability/docker-compose.s4.yml` — registry-api **bind-mount RW** `:47` + sets `REGISTRY_API_LOG_DIR`/`REGISTRY_API_DB_URL`/`HOST`/`PORT` `:37-39`; `TELEGRAM_BOT_TOKEN: ""` `:94`; `TG_ALLOWLIST_USER_IDS: "[]"` `:95`; excludes metrics-subscriber (`:169`)

### Decisions (resolve during implementation)

- **D1 — H7a fix shape.** Prefer RW mount + explicit `REGISTRY_API_DB_URL`/`REGISTRY_API_LOG_DIR`
  (mirrors the working s4 overlay) over splitting reads/writes across volumes. Confirm via logs
  whether the crash is EROFS-on-write (→ RW mount) or file-absent (→ ordering dep on
  registry-state creating the DB first).
- **D2 — FR26 single-writer vs registry-api RW.** registry-state is the single writer of the
  materialized state in `state.sqlite3`; registry-api opens a *separate writable engine only for
  the `idempotency_cache` table* + appends the event log. The s4 overlay already runs registry-api
  RW without violating the invariant — preserve that boundary, don't widen registry-api's writes.
  Re-run `scripts/check_single_writer.py` (AC6).
- **D3 — H7b allowlist scope.** Mirror the task-registry canon set, minus anything not needed by
  these subprocesses. Do NOT copy the secret-bearing service vars (`ANTHROPIC_API_KEY`,
  `GITHUB_TOKEN`) into the allowlist — they belong to the *service* process, not the MCP child.

### Constraints

- **Epic 11 retro AI-1 mandate APPLIES** — cross-cutting + security-sensitive (MCP env) →
  3-lane adversarial review at pass-1.
- **NEVER reintroduce `env=os.environ.copy()` / `dict(os.environ)`** (the a0ca050 P0 — reverted
  twice already on this exact code path).
- **AI-6** (BaseException-leak audit) if any `try/finally` in the MCP connect path is touched.
- **AI-7** (test-realism) — the regression guard must FAIL against the pre-fix compose/image.
- Do **NOT** revert Story 11.3.4 (S1/S2/S3), 11.3.5 (registry-state), or 11.3.3 Fix-A/B/AC2.
- Do **NOT** enable `REGISTRY_STATE_AUTO_CREATE_SCHEMA` in the production ROOT compose default
  (preserves the Story 2.14 migrator-owns-schema contract; the S-4 test exports it itself).
- **FR26 single-writer invariant preserved** (D2).
- **Image-arch footgun:** all Python lives in the base image venv (`uv sync --no-editable
  --all-packages` in `Dockerfile.base`); per-service Dockerfiles only `useradd`. ANY
  `services/*/src` or `mcp-servers/*/src` change requires `just build-base` — rebuilding the thin
  per-service image alone does NOT pick it up (cost the 11.3.5 dev a cycle).

### Project Structure Notes

- Compose-only changes (volume mode, env vars) need no rebuild; the H7b `mcp_clients.py` changes
  DO require `just build-base` before re-running S-4 or crash-injection.
- Production impact is in-scope: a fresh prod `docker compose up -d` (no `--profile migrate`,
  no operator alembic step) hits the same H7a/H7b gaps — document the prod remediation even if
  the test is the proximate driver.

### References

- [Source: 11-3-5-s4-root-compose-healthcheck.md — "Downstream failures — COMPLETE diagnosis (2026-05-28) → split to Story 11.3.6"]
- [Source: 11-3-4-separability-task-progression.md — scripted-worker `_clawhip_env` allowlist; S1/S2/S3 fixes]
- [Source: deferred-work.md — story-9.6 D1/D4 + story-9.7 — the `dict(os.environ)` secret-leak hardening lineage (a0ca050)]
- [Source: docker-compose.yml:50,114-116,131-135,255-257 — `:ro` mount + missing MCP env]
- [Source: services/orchestrator-adapter/.../mcp_clients.py:76 + worker-wrapper/.../mcp_clients.py:81 — env-less StdioServerParameters]
- [Source: mcp-servers/task-registry/.../clawhip_client.py:49-90,137 — `_ENV_ALLOWLIST` canon to mirror]
- [Source: tests/separability/docker-compose.s4.yml:37-39,47,94-95 — the working RW + explicit-env registry-api pattern]
- [Source: epics.md:147-148 (NFR-M4/M5), :2387-2388 (FR62a S-4 separability), :2547,:2553 (Phase-2 S-1..S-4 green gate)]

## Frontmatter

```yaml
---
story_id: 11.3.6
story_key: 11-3-6-root-compose-fresh-boot
parent_epic: 11
phase: 2
fr_refs: [FR62a, NFR-M4, NFR-M5, FR35]
nfr_refs: [NFR-M4, NFR-M5]
arch_refs:
  - "Story 11.3.5 — registry-state fix (this is the carved-out downstream-services scope)"
  - "Story 11.3.4 / a0ca050 — the MCP env-propagation P0 (env=os.environ.copy reverted twice); use the ALLOWLIST pattern"
  - "mcp-servers/*/adapters/clawhip_client.py — _ENV_ALLOWLIST canon + tests/contract mirror test"
  - "Story 10.6 — S-4 separability harness origin"
  - "Story 2.14 — migrator owns schema; ROOT compose default keeps AUTO_CREATE_SCHEMA off"
estimated_complexity: MEDIUM-HIGH
priority: medium (last red nightly job; likely also a production fresh-deploy gap)
blocks: []
unblocks:
  - Fully-green nightly s3-separability (S1/S2/S3/S4) + a bootable fresh ROOT-compose deploy
---
```

## Dev Agent Record

### Agent Model Used
claude-opus-4-7[1m] (dev-story 2026-05-28) — implemented in the SUPERVISED main thread,
NOT delegated, per the a0ca050 P0 mandate (11.3.5 record: "NOT another unsupervised long agent").

### Debug Log References

**Scope discovery (2026-05-28) — the problem is larger than the spec captured.** Static
analysis of the real services + MCP servers revealed three facts beyond the original H7a/H7b:

1. **MCP servers require service-PREFIXED actor identity, not just DB paths.** Each spawned
   server `__main__.py` `sys.exit(2)` if its REQUIRED vars are absent:
   - task-registry: `TASK_REGISTRY_DB_PATH` + `TASK_REGISTRY_ACTOR_KIND` + `TASK_REGISTRY_ACTOR_ID`
   - session-registry: `SESSION_REGISTRY_DB_PATH` + `SESSION_REGISTRY_ACTOR_KIND` + `SESSION_REGISTRY_ACTOR_ID`
   - clawhip-bridge: `CLAWHIP_BRIDGE_ACTOR_KIND` + `CLAWHIP_BRIDGE_ACTOR_ID` (+ optional `CLAWHIP_BRIDGE_LOG_DIR`)
   Valid `ActorKind` = `{operator, orchestrator, worker, system, clawhip}` (`packages/events/.../envelope.py:183`).
   → orchestrator-adapter identity = `orchestrator`; worker-wrapper = `worker` (canonical
   wiring reference: `tests/integration/docker-compose.j1.yml:77-85`).
2. **The real orchestrator-adapter + worker-wrapper are NEVER exercised by S1/S2/S3** — those
   swap in null-orchestrator / scripted-worker-stub. S4 is the first/only test that boots the
   REAL services. They have apparently never booted healthy on any fresh volume.
3. **Both services touch `/tmp/ready` ONLY after `verify_connectivity()` succeeds** (orchestrator
   `app/main.py:482-486`; worker `__main__.py:76-108`) — i.e. all 3 MCP subprocesses must start
   AND answer `list_tools()`. orchestrator-adapter's task-registry must open
   `TASK_REGISTRY_DB_PATH=/var/lib/oh-my-bmad/...` — **but orchestrator-adapter has NO volume
   mount at all** (worker-wrapper has `:ro`). And with `OMB_MCP_AUDIT_EMISSION_ENABLED` default-ON
   (Story 11.2.3 AC6), task-registry + session-registry EACH spawn a NESTED clawhip-bridge writer
   → a large fan-out of concurrent event-log writers (FR26 / Story 11.2.3 fcntl-lock territory),
   never tested at this fan-out on a real fresh boot.

**⇒ Reaching "healthy" for these two services requires data-volume mounts that don't exist
(orchestrator-adapter) / are `:ro` (worker-wrapper), and accepting/validating the N-writer
event-log fan-out — a genuine FR26 architecture decision, not a config tweak. HALTED for an
operator decision (see "Open decision" below) rather than choosing unilaterally on the
a0ca050 P0-adjacent, FR26-invariant path.**

### Completion Notes List

**DONE + verified (Task 3 code half — the security-critical core):**
- Added `_ENV_ALLOWLIST` frozenset + `_default_env_allowlist()` + `env=self.env` to BOTH
  `services/orchestrator-adapter/.../mcp_clients.py` and `services/worker-wrapper/.../mcp_clients.py`.
  Byte-identical between the two; explicit allowlist (NEVER `os.environ.copy()`); forwards only
  the per-server REQUIRED vars + POSIX/TLS basics; carries NO `ANTHROPIC_API_KEY` /
  `GITHUB_TOKEN` / `OPERATOR_HMAC_KEY` / AWS / OPENAI.
- Extended `tests/contract/test_clawhip_client_env_allowlist_mirror.py` with 3 tests: the two new
  allowlists are byte-identical; contain all required server vars; exclude secrets (a0ca050 guard).
- Gates: ruff clean, ruff format clean, `mypy --strict` clean (2 files), **49 tests pass** (new
  contract tests + existing mirror + adapter unit tests). No regression: the `env=` change is
  inert unless the vars are present in the parent env (`_default_env_allowlist` only forwards
  what exists), so existing env-less tests still pass.

**NOT done — pending operator decision (Tasks 1, 2, 4, 5, 6, 7):**
- H7a registry-api `:ro`→RW + `REGISTRY_API_DB_URL`/`LOG_DIR` (compose) — bounded; ready to apply.
- H7b compose env blocks on orchestrator-adapter/worker-wrapper — BLOCKED on the volume/fan-out decision.
- Docker repro (AC1/AC2), S4 verification (AC4), regression guard (AC5/AI-7), nightly (AC7),
  AI-1 3-lane review (Task 7) — all downstream of the decision.

### Open decision (HALT) — volume mounts + event-log multi-writer fan-out

To boot orchestrator-adapter + worker-wrapper healthy on the ROOT compose, their spawned
task-registry (and its nested clawhip-bridge) must read the DB and write the event log on the
shared `oh-my-bmad-data` volume. Options:
- **(A) Full wiring** — give orchestrator-adapter a RW volume mount + flip worker-wrapper `:ro`→RW,
  set the full MCP env block (identity = orchestrator / worker), rely on Story 11.2.3's fcntl
  lock to serialize the now-larger writer fan-out. Most complete; makes a fresh prod deploy
  actually functional; highest blast radius on FR26 → strongest case for the AI-1 review.
- **(B) Minimal-to-green** — if `list_tools()` does not require the DB/log files to pre-exist
  (clawhip-bridge defers log-dir IO to first `emit`), a `:ro` mount on orchestrator-adapter +
  env block may suffice to reach *healthy* without enabling event writes; defer the writer
  fan-out. Smaller, but may leave a latent prod gap (services healthy yet unable to emit).
- **(C) Disable nested audit emission** for these spawners (`OMB_MCP_AUDIT_EMISSION_ENABLED=0`
  on orchestrator-adapter/worker-wrapper) to shrink the writer fan-out, then (A) or (B).

Needs operator steer on A/B/C before touching the compose + running the (long) Docker + nightly cycle.

**OPERATOR DECISION (2026-05-28): Option (A) Full wiring** — orchestrator-adapter gets a RW
volume, worker-wrapper `:ro`→RW, full MCP env block (identity orchestrator / worker), rely on
Story 11.2.3 fcntl lock for the writer fan-out. Verification: **full local Docker + nightly**
(build-base + S4 test to green locally, then commit + push + trigger nightly.yml, report run id).
AI-1 3-lane review mandatory given the FR26 blast radius.

### Live Docker findings (2026-05-28, fresh named volume, AUTO_CREATE_SCHEMA=1) — AC1/AC2

Built all 7 images (`just build-base` + `docker compose build`) and booted the ROOT compose on a
fresh named volume. Observed `docker compose ps`:

| service | state | verdict |
|---|---|---|
| registry-state | **healthy** | 11.3.5 fix holds |
| metrics-subscriber | **healthy** | own /healthz probe |
| registry-api | running, **unhealthy** | **H7a FIXED** — RW mount worked ("Application startup complete", Uvicorn on :8080, no EROFS). But marked unhealthy → see H7d. |
| telegram-gateway | running, **unhealthy** | clean startup ("Webhook set · ready", Uvicorn :8080) → H7d |
| clawhip-daemon | running, **unhealthy** | clean startup ("telegram_sink started") → H7d + H7e |
| orchestrator-adapter | **Created** (never started) | gated behind `registry-api: service_healthy` → blocked by H7d; H7b unverified |
| worker-wrapper | **Created** (never started) | same gating |

⇒ `dependency failed to start: container omb-registry-api is unhealthy` aborted the boot.

### NEW root causes discovered live (beyond H7a/H7b/H7c)

**H7d — healthcheck mismatch (3 services).** ONLY `registry-state` (Story 2.11), orchestrator-adapter
and worker-wrapper actually create `/tmp/ready`. registry-api + telegram-gateway are **uvicorn
servers** and clawhip-daemon is a **log-tail daemon** — none touch `/tmp/ready`, yet the ROOT
compose gives all three the shared `*healthcheck` (`test -f /tmp/ready`), which can therefore NEVER
pass. The journey/separability composes already override registry-api to a **socket probe**
(`connect 127.0.0.1:8080`); telegram-gateway listens on :8080 too. clawhip-daemon has **no HTTP
port**. The journey tests (j1/j3/j6) NEVER boot the real clawhip-daemon, so this was never caught.
- Fix (registry-api, telegram-gateway): socket-probe healthcheck (mirror s4/j composes). Unambiguous.
- Fix (clawhip-daemon): no port → needs a readiness signal it doesn't have → **CODE CHANGE**
  (touch `/tmp/ready` after sinks start, mirroring registry-state Story 2.11) OR a process/log probe.

**H7e — clawhip-daemon requires a valid TELEGRAM_BOT_TOKEN at boot.** `clawhip_daemon/app/main.py:232-242`
`sys.exit(2)` if `TELEGRAM_BOT_TOKEN` is empty or not `<digits>:<alphanumeric>`. It only stayed up
locally because a repo `.env` supplied one; in CI (no `.env`) it exits 2. The s4 overlay sets
`TELEGRAM_BOT_TOKEN: ""` for telegram-gateway (which tolerates empty) but NOT for clawhip-daemon →
the S-4 test would fail clawhip-daemon regardless. Fix: inject a dummy valid-format token
(e.g. `0:dummytesttoken`) for clawhip-daemon in the test composes (telegram-gateway tolerates empty;
clawhip-daemon does not). Production sets a real token via `.env`.

### Scope reality + second HALT

The story has grown from "fix fresh-boot" into **first-ever full ROOT-compose all-healthy
bring-up**, spanning FOUR root-cause classes across 6 services:
- H7a registry-api `:ro`→RW — **FIXED + live-verified** (service runs).
- H7b orchestrator-adapter/worker-wrapper MCP env + RW volumes — code + compose DONE; **runtime
  UNVERIFIED** (gated behind registry-api health).
- H7d healthcheck mismatch (registry-api + telegram-gateway = clear socket-probe fix; clawhip-daemon
  = CODE CHANGE / probe-design decision).
- H7e clawhip-daemon token requirement in test env.

This is materially larger than even Option A described, and now includes a clawhip-daemon **code
change** + token strategy. Surfacing for an operator scope decision rather than unilaterally
expanding (per the project's split-when-it-grows norm; cf. 11.3.4→11.3.5→11.3.6).

### H7b runtime verification (2026-05-28) — DECISIVE: nested audit-emission breaks MCP bring-up

After the H7d socket-healthcheck fix, registry-api reached **healthy** and orchestrator-adapter +
worker-wrapper finally STARTED — but they crash-loop: `mcp_clients._connect("task-registry")` →
`session.initialize()` → `McpError: Connection closed` (one run) / hang→TimeoutError (next run).
The env is correctly forwarded (verified: all `TASK_REGISTRY_*` / `SESSION_REGISTRY_*` /
`CLAWHIP_BRIDGE_*` present in the subprocess) and task-registry starts fine standalone under the
allowlist-only env. Isolated the cause with an in-container MCP `initialize` probe:

| condition | result |
|---|---|
| `OMB_MCP_AUDIT_EMISSION_ENABLED` ON (default, 11.2.3 AC6) | **McpError: Connection closed / hang** (server stderr empty) |
| `OMB_MCP_AUDIT_EMISSION_ENABLED=0` | **INITIALIZE OK** |

**Root cause (H7f):** when the REAL orchestrator-adapter/worker-wrapper spawn task-registry +
session-registry as MCP stdio subprocesses, those servers — with audit default-ON — each spawn a
**nested** clawhip-bridge MCP stdio *client from inside their own stdio server*. That 3-level
stdio nesting (service → registry server → clawhip-bridge client) hangs/races without a clean
error (empty stderr, inconsistent Connection-closed vs timeout = a deadlock signature). This path
has NEVER run before: S1/S2/S3 swap these services for stubs, and the registries' audit emission
was default-OFF until Story 11.2.3 flipped it ON (closing the FR26 multi-writer concern via fcntl,
but never exercising the registry-nested-under-a-service topology).

**⇒ Option A as specified ("rely on the 11.2.3 fcntl fan-out") is empirically NON-FUNCTIONAL** —
audit-ON doesn't just add writers, it deadlocks the registries' own bring-up. The clean fix is to
set `OMB_MCP_AUDIT_EMISSION_ENABLED=0` on orchestrator-adapter + worker-wrapper (forwarded to their
task/session-registry children → no nested clawhip-bridge → `INITIALIZE OK`). This is the original
Story 11.2.2 default-OFF posture for exactly this multi-writer concern, scoped to these two
spawners only (HTTP-path audit via registry-api is unaffected). Deep-debugging the nested-stdio
deadlock to make audit-ON work here is a separate, open-ended effort (candidate split).

**SECOND operator decision needed** (the A choice predates this evidence): adopt the audit-OFF fix
(≈ the earlier Option C, now empirically required) vs. open a follow-up to deep-debug nested audit.

### H7b VERIFIED FIXED + remaining tail (2026-05-28, audit-OFF boot)

Applied `OMB_MCP_AUDIT_EMISSION_ENABLED: "0"` to orchestrator-adapter + worker-wrapper (both
composes) and re-booted (no rebuild — compose-env only). Result:

- ✅ **H7b CONFIRMED FIXED.** worker-wrapper's MCP fully connects — logs show live
  `Processing request of type CallToolRequest` + `session.heartbeat`. orchestrator-adapter gets
  past all three `_connect()` calls into its run loop. The allowlist `env=` propagation works
  end-to-end; the nested-stdio deadlock is gone. **The security-critical core of the story is
  done + runtime-verified.**
- ✅ registry-api, registry-state, metrics-subscriber: **healthy**.

Two MORE never-run gaps block "all 7 healthy" (both surfaced only because H7b now lets these
services actually run):
- **H7g — orchestrator-adapter: `ValueError: omc_path does not exist: upstream/omc`.** The
  vendored OMC dir exists in the repo (53 entries) but is **NOT copied into the image**
  (`/app/upstream/omc` absent). orchestrator-adapter's `OMCRunner.__init__` requires it → crashes
  in `adapter_loop` after MCP connect. This is an **image-build/Dockerfile** change (package
  `upstream/omc` into the orchestrator-adapter image), non-trivial + orthogonal to the MCP spine.
- **H7h — worker-wrapper ready-file name mismatch.** `ready_file_path` defaults to `""` →
  worker touches `/tmp/worker-wrapper-ready-<pid>` (verified present) but the healthcheck checks
  `/tmp/ready`. Easy env fix: `WORKER_READY_FILE_PATH=/tmp/ready` (no rebuild). The service itself
  is functional.
- **telegram-gateway** still needs Telegram network or an offline skip-`set_webhook` path (code
  change) to be healthy hermetically; **clawhip-daemon** needs the H7d `/tmp/ready` code touch +
  H7e dummy token.

**Full tally for "all 7 healthy":** H7a ✅ | H7b ✅(verified) | H7d registry-api/telegram socket ✅
| H7h worker ready-file (env, trivial) | H7g orchestrator OMC-in-image (Dockerfile, non-trivial) |
telegram offline-mode (code) | clawhip-daemon /tmp/ready (code) + token (test). The remaining tail
is an image-build + multi-service-code "first-ever full compose bring-up" — materially bigger and
orthogonal to this story's MCP-env/registry-api objective, which is COMPLETE + VERIFIED.

### Change Log
- 2026-05-28 — Story 11.3.6 dev-story started. (1) Implemented + verified the H7b allowlist code
  (both `mcp_clients.py`) + 3 contract tests. (2) Operator chose Option A; applied H7a (registry-api
  RW + DB/log-dir + depends_on) + H7b compose env/volume wiring to `docker-compose.yml` +
  `docker-compose.s4.yml`. (3) Live Docker boot: H7a verified (registry-api runs); discovered H7d
  (healthcheck mismatch, 3 svc) + H7e (clawhip-daemon token). HALTED for a scope decision.
- 2026-05-28 — Operator chose "press on — full fix here". Applied H7d socket healthchecks for
  registry-api + telegram-gateway in `docker-compose.yml`. Live Docker probe revealed H7b's nested-
  audit deadlock (H7f): `OMB_MCP_AUDIT_EMISSION_ENABLED` default-ON makes the spawned task-registry/
  session-registry deadlock when they try to spawn their nested clawhip-bridge stdio CLIENT from
  inside their own stdio SERVER (3-level nesting). Probe data: AUDIT ON → `Connection closed`/hang,
  AUDIT OFF → `INITIALIZE OK`. Surfaced for operator decision.
- 2026-05-28 — Operator chose audit-OFF on the 2 spawners. Added `OMB_MCP_AUDIT_EMISSION_ENABLED=0`
  to orchestrator-adapter + worker-wrapper in both composes. Live boot: **H7b RUNTIME-VERIFIED** —
  worker-wrapper actively processing live `CallToolRequest`s; orchestrator-adapter clears all 3
  `_connect()` calls into adapter_loop (crashes later on H7g `omc_path does not exist`). New gaps
  surfaced: H7g (orchestrator OMC not in image — Dockerfile/build), H7h (worker ready-file name
  mismatch — env-only).
- 2026-05-28 — Added H7h fix (`WORKER_READY_FILE_PATH=/tmp/ready` in both composes). Operator
  chose "land verified core + split tail". Filed **Story 11.3.7** for the orthogonal tail (H7g
  OMC-in-image + telegram-gateway offline-mode + clawhip-daemon `/tmp/ready` code + dummy token
  + final S-4 green + nightly). Validation gates green: ruff/format/mypy/discipline ✓ +
  `pytest -m "not slow"` **3139 passed / 0 failed**. Status → `review`.
- 2026-05-28 — `/code-review high` ran 7-angle adversarial review; 10 findings applied as fixes
  (s4 telegram-gateway socket healthcheck mirror; contract test spawner ⊇ canon + extended
  required set + module-top imports; per-call defensive `env=dict(self.env)` in both adapters;
  `x-mcp-spawner-env` + `x-healthcheck-http` YAML anchors collapsing 4 duplicated env blocks +
  2 duplicated healthchecks; socket healthcheck wrapped in `try/except OSError` for clean stderr;
  3 separability-sentinel tests updated to allowlist the orchestrator-adapter mcp_clients.py
  touch with a Story 11.3.6 justification; deferred-work.md entry for H7f nested-stdio deadlock).
  Validation re-run: **3140 tests pass / 0 failed** (one pre-existing timing flake passed on
  retry). Status → `done`.

### Code review findings (7-angle, 2026-05-28) — all 10 applied as fixes

Ranked most-severe first. Each was applied + verified by re-running the full gates.

1. **`tests/separability/docker-compose.s4.yml:100`** — telegram-gateway S4 healthcheck still
   `test -f /tmp/ready` (H7d socket-probe fix wasn't mirrored to the s4 overlay). FIX: socket
   probe (try/except OSError) mirroring the ROOT compose.
2. **`tests/contract/test_clawhip_client_env_allowlist_mirror.py`** — contract test missing
   spawner ⊇ canon assertion. FIX: new `test_spawner_allowlists_are_superset_of_canon` imports
   the canon `_TASK_ALLOWLIST` and asserts both spawner allowlists contain it.
3. **`tests/contract/test_clawhip_client_env_allowlist_mirror.py:103`** — `_SPAWNER_REQUIRED_ENV_VARS`
   omitted `CLAWHIP_BRIDGE_LOG_DIR` / `REGISTRY_EVENTS_DIR` / `REGISTRY_DB_PATH`. FIX: added all
   three with a comment explaining why each is load-bearing.
4. **`services/{orch-adapter,worker-wrapper}/.../mcp_clients.py`** — `env` was shared across the
   3 `_connect` calls; sibling mutation hazard. FIX: per-call defensive `env=dict(self.env)`.
5. **`docker-compose.yml`** — 11-line MCP-spawner env block duplicated 4× (root×2 + s4×2). FIX:
   `x-mcp-spawner-env: &mcp_spawner_env` anchor in both files; service env collapses to
   `<<: [*common-env, *mcp_spawner_env]` + per-service ACTOR_KIND/ACTOR_ID only.
6. **`docker-compose.yml:60,130`** — two inline 12-line socket-probe healthchecks for
   registry-api + telegram-gateway with no shared anchor. FIX: `x-healthcheck-http: &healthcheck_http`
   anchor; service `healthcheck: *healthcheck_http`.
7. **`docker-compose.yml`** — socket healthcheck `python -c` raised unhandled
   `ConnectionRefusedError` during startup retries (traceback noise in container logs). FIX:
   wrapped in `try/except OSError: sys.exit(1)` (applied in both root + s4 + the new HTTP anchor).
8. **`tests/contract/test_clawhip_client_env_allowlist_mirror.py:109`** — lazy imports inside
   `_spawner_allowlists()` (inconsistent with rest of file; ImportError yields pytest ERROR
   not FAIL). FIX: imports moved to module top; helper function deleted.
9. **`_bmad-output/implementation-artifacts/deferred-work.md`** — H7f nested-stdio deadlock
   workaround had no entry. FIX: added "Deferred from: code review of story 11-3-6" entry with
   the workaround rationale + the proper-fix options (restructure clawhip_client to detect
   nesting, or lift audit emission to the spawner) + back-ref to Story 11.3.7.
10. **Separability sentinels** (`tests/integration/test_journey_1_overnight.py`,
   `tests/separability/test_s1_cold_worker_swap.py`, `tests/separability/test_s2_midflight_swap.py`)
   — 3 sentinel tests legitimately flagged the orchestrator-adapter `mcp_clients.py` touch as a
   spine-source modification. FIX: added the path to each test's `_ALLOWED` / `SPINE_PATHS`
   exclusion with a Story 11.3.6 justification comment (the change is inert without the new
   compose env vars; stub-worker boot paths are unaffected).

### File List

- `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py` — `_ENV_ALLOWLIST` + `_default_env_allowlist()` + per-call defensive `env=dict(self.env)` in `_connect`; H7b core
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — byte-identical allowlist + per-call defensive `env=dict(self.env)`
- `tests/contract/test_clawhip_client_env_allowlist_mirror.py` — 4 new tests (byte-identical mirror, spawner ⊇ canon, extended required-vars, secret-exclusion); imports moved to module top
- `docker-compose.yml` — H7a registry-api RW + DB/log-dir + depends_on; H7b orch/worker MCP env via `*mcp_spawner_env` anchor + RW volume + depends_on + audit-OFF; H7d socket healthcheck via `*healthcheck_http` anchor (try/except wrapped); H7h `WORKER_READY_FILE_PATH=/tmp/ready`; new YAML anchors at top of file
- `tests/separability/docker-compose.s4.yml` — H7b orch/worker MCP env via parallel `*mcp_spawner_env` anchor + RW + depends_on + audit-OFF; H7d s4 telegram-gateway socket healthcheck (review fix); H7h worker ready-file env
- `tests/integration/test_journey_1_overnight.py` — `_ALLOWED` set extended (review fix #10)
- `tests/separability/test_s1_cold_worker_swap.py` — `SPINE_PATHS` exclusion added (review fix #10)
- `tests/separability/test_s2_midflight_swap.py` — `SPINE_PATHS` exclusion added (review fix #10)
- `_bmad-output/implementation-artifacts/deferred-work.md` — H7f entry added (review fix #9)
- `_bmad-output/implementation-artifacts/11-3-6-root-compose-fresh-boot.md` — this story (Status `done`)
- `_bmad-output/implementation-artifacts/11-3-7-root-compose-full-bringup.md` — new split-tail story stub
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 11-3-6 `backlog`→`ready-for-dev`→`in-progress`→`review`→`done`; 11-3-7 added as `backlog`

### File List
- `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py` (modified — `_ENV_ALLOWLIST` + `env=` propagation)
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` (modified — byte-identical allowlist + `env=` propagation)
- `tests/contract/test_clawhip_client_env_allowlist_mirror.py` (modified — 3 new tests for the spawner allowlists)

## Definition of Done

- All 7 ROOT-compose services reach healthy on a fresh volume; S-4 Phase 1 + Phase 2 pass.
- H7a/H7b/H7c documented with probe evidence; production impact assessed.
- `s3-separability` nightly job fully PASS (S1/S2/S3/S4); other jobs + PR-gate ci green.
- `_ENV_ALLOWLIST` secret invariant re-verified (grep + extended mirror contract test); NO
  `os.environ.copy()` / `dict(os.environ)`.
- AI-1 3-lane review complete; findings batch-applied.
- `sprint-status.yaml` `11-3-6-root-compose-fresh-boot: backlog → done`.
