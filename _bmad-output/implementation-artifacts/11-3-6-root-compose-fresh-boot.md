# Story 11.3.6 — ROOT compose fresh-boot: downstream services unhealthy (S4 Phase 1)

Status: **backlog** (split from Story 11.3.5, 2026-05-28)

## Story

**As** the platform maintainer
**I want** every service in the ROOT `docker-compose.yml` to reach `healthy` on a **fresh
named volume** (S-4 separability Phase 1: registry-api, registry-state, telegram-gateway,
orchestrator-adapter, worker-wrapper, clawhip-daemon, metrics-subscriber)
**so that** the nightly `s3-separability` job goes fully green (Story 11.3.5 fixed
registry-state; these are the remaining red services) AND a fresh production `docker compose
up -d` actually boots (this is likely a real production fresh-deploy gap, not just a test issue).

## Background — why this is separate from 11.3.5

Story 11.3.5 fixed **registry-state unhealthy** under the ROOT compose (3-layer: named-volume
ownership + DB-parent dir + schema bootstrap; committed `219607d`, registry-state verified
healthy). With registry-state's `depends_on` no longer aborting `compose up`, the full
Phase-1 boot ran the 180s healthcheck wait and exposed **pre-existing downstream failures**
that registry-state's crash had been masking. **The ROOT compose has never booted all-healthy
on a fresh volume.**

## ⚠️ SECURITY GUARDRAIL (read first)

The orchestrator-adapter / worker-wrapper fix (H7b) touches `services/*/adapters/mcp_clients.py`
— **the exact file where a delegated agent reintroduced a P0 secret leak** (`env=dict(os.environ)`,
reverted in `a0ca050`). **NEVER use `env=os.environ.copy()` / `dict(os.environ)`.** Propagate
the required MCP-subprocess env via an **ALLOWLIST**, mirroring:
- `mcp-servers/task-registry/.../adapters/clawhip_client.py` `_ENV_ALLOWLIST` / `_default_env_allowlist()`
- the Story 11.3.4 scripted-worker `_clawhip_env()` (`tests/fixtures/scripted_worker_stub/`)
The allowlist must NOT forward HMAC keys, AWS creds, OPENAI/ANTHROPIC keys, or `github_token`.
The existing `tests/contract/test_clawhip_client_env_allowlist_mirror.py` is the pattern to
extend. This change is cross-cutting + security-sensitive → **AI-1 3-lane review mandatory**.

## Root causes (diagnosed locally @ 219607d — confirm + fix each)

### H7a — registry-api: `unable to open database file` (crash loop)
`docker-compose.yml:50` mounts registry-api `oh-my-bmad-data:/var/lib/oh-my-bmad:**ro**`, but
registry-api is the event-log WRITER (POST /v1/tasks → task.created via EventLogWriter) and
also opens the SQLite DB. The S-1/S-2/S-3 composes mount it RW ("registry-api writes JSONL").
**Probe:** confirm whether registry-api opens the DB RW (needs RW mount) or read-only (then
the crash is the DB file not existing yet — an ordering dep on registry-state). **Fix
candidates:** RW mount; and/or `depends_on: registry-state: service_healthy`; and/or open the
DB read-only for the materialized-state reads. Preserve FR26 single-writer.

### H7b — orchestrator-adapter + worker-wrapper: MCP subprocess can't start (crash loop)
Logs: `task-registry: TASK_REGISTRY_DB_PATH is required but not set or empty` →
`mcp.shared.exceptions.McpError: Connection closed` (orchestrator-adapter); `TimeoutError` on
MCP init (worker-wrapper). TWO-part cause:
1. The ROOT compose does not set `TASK_REGISTRY_DB_PATH` / `SESSION_REGISTRY_DB_PATH` /
   `CLAWHIP_BRIDGE_LOG_DIR` / `CLAWHIP_BRIDGE_ACTOR_KIND` / `CLAWHIP_BRIDGE_ACTOR_ID` on
   orchestrator-adapter + worker-wrapper.
2. `mcp_clients.py` spawns the task-registry / session-registry / clawhip-bridge MCP servers
   with **env-less** `StdioServerParameters` (correctly env-less per the a0ca050 P0 revert),
   so the MCP SDK forwards only `get_default_environment()` (POSIX safe-list) and those vars
   are stripped → the subprocess exits "required but not set".
**Fix (see SECURITY GUARDRAIL):** (1) set the required vars on both services in the ROOT
compose; (2) propagate them to the subprocess via an ALLOWLISTED `env=` in `mcp_clients.py`
(the SDK merges over `get_default_environment()`). Likely a **production** bug too (a fresh
prod orchestrator-adapter would also fail to spawn its MCP servers).

### H7c — clawhip-daemon / telegram-gateway: likely benign (confirm)
Logs showed clean startup (`telegram_sink started`; `Webhook set · ready`) with no error —
probably slow-healthcheck or eventual-healthy rather than crashing. **Probe:** with the
crash-loopers fixed, re-check whether these reach healthy within the window; only fix if they
genuinely don't (e.g. healthcheck `/tmp/ready` never touched, or empty `TELEGRAM_BOT_TOKEN`
handling — the Phase-2 `docker-compose.s4.yml` sets `TELEGRAM_BOT_TOKEN: ""` + `TG_ALLOWLIST_USER_IDS: "[]"`
explicitly; Phase-1 relies on an absent `.env`).

## Acceptance criteria

- **AC1** — Local repro of each downstream failure on a fresh ROOT-compose boot (with
  `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1` so registry-state is healthy, per 11.3.5).
- **AC2** — H7a / H7b / H7c verdicts with evidence (logs, mounts, env).
- **AC3** — Fixes at the right altitude. **H7b MUST use the allowlist pattern (never
  `os.environ.copy()`).** Assess + document production impact of each fix (H7a/H7b are likely
  prod fresh-deploy gaps).
- **AC4** — S-4 Phase 1 (ROOT, 7 svc) + Phase 2 (`docker-compose.s4.yml`, 6 svc) ALL healthy;
  `test_s4_metrics_subscriber_optional.py` passes.
- **AC5** — No regression: S1/S2/S3 (11.3.4) + registry-state (11.3.5) + idempotency +
  crash-injection + migrator nightly jobs stay green; PR-gate `ci` green.
- **AC6** — Validation gates green (ruff/format/mypy/discipline/pytest).
- **AC7** — Nightly: all 4 jobs PASS — `s3-separability` fully green (S1/S2/S3/S4).
- **AC8** — `_ENV_ALLOWLIST` secret-leak invariant re-verified after the mcp_clients change
  (grep + the contract test); extend the mirror contract test to the new propagation.

## Constraints
- **Epic 11 retro AI-1 mandate APPLIES** — cross-cutting + security-sensitive (MCP env) →
  3-lane adversarial review at pass-1.
- **NEVER reintroduce `env=os.environ.copy()` / `dict(os.environ)`** (the a0ca050 P0).
- Do NOT revert Story 11.3.4 (S1/S2/S3) or 11.3.5 (registry-state) or 11.3.3 Fix-A/B/AC2.
- **FR26 single-writer invariant preserved** (relevant to the registry-api RW-mount fix).
- Image-arch: all code is in the base venv (`uv sync --no-editable`); `services/*` code
  changes require `just build-base` (rebuilding the per-service image alone is insufficient).
- Production-impact: H7a/H7b are likely prod fresh-deploy gaps — fix in the production
  compose/code where correct, flag explicitly for review.

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
  - "Story 11.3.4 / a0ca050 — the MCP env-propagation P0 (env=os.environ.copy reverted); use the ALLOWLIST pattern"
  - "mcp-servers/*/adapters/clawhip_client.py — _ENV_ALLOWLIST canon + tests/contract mirror test"
  - "Story 10.6 — S-4 separability harness origin"
estimated_complexity: MEDIUM-HIGH
priority: medium (last red nightly job; likely also a production fresh-deploy gap)
blocks: []
unblocks:
  - Fully-green nightly s3-separability (S1/S2/S3/S4) + a bootable fresh ROOT-compose deploy
---
```

## Definition of Done
- All 7 ROOT-compose services reach healthy on a fresh volume; S-4 Phase 1 + Phase 2 pass.
- H7a/H7b/H7c documented with evidence; production impact assessed.
- `s3-separability` nightly job fully PASS; other jobs + PR-gate ci green.
- `_ENV_ALLOWLIST` secret invariant re-verified; mirror contract test extended; NO
  `os.environ.copy()`.
- AI-1 3-lane review complete; findings batch-applied.
- `sprint-status.yaml` `11-3-6-...: backlog → done`.
