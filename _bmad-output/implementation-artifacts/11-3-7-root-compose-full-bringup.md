# Story 11.3.7 — ROOT compose full bring-up: orchestrator OMC image + telegram offline-mode + clawhip-daemon ready+token + S4 green

Status: **backlog** (split from Story 11.3.6, 2026-05-28)

## Story

**As** the platform maintainer,
**I want** every service in the ROOT `docker-compose.yml` to reach `healthy` on a **fresh
named volume** AND the nightly `s3-separability` job's S-4 sub-test to go GREEN (Phase 1 + Phase 2
all-healthy + registry-api `/v1/health` + `POST /v1/tasks` identical across phases),
**so that** the S-4 separability claim (FR62a / NFR-M4/M5) is CI-verified and a fresh production
`docker compose up -d` actually boots — which 11.3.6 proved was previously impossible because
multiple services had never been wired/run in containers.

## Background — what 11.3.6 did and what's still red

Story 11.3.6 closed the **MCP-spine** bring-up (H7a + H7b + H7d socket healthchecks + H7h worker
ready-file), with the **security-critical H7b allowlist** runtime-verified in Docker
(orchestrator-adapter + worker-wrapper actively connect their MCP servers — worker processes live
`CallToolRequest`s). What's still red is a separate, broader set of "service never actually ran"
gaps that surfaced once H7b let these services start at all:

- **H7g** — orchestrator-adapter `ValueError: omc_path does not exist: upstream/omc`. The vendored
  OMC dir exists in the repo but is **NOT copied into the image** → `OMCRunner.__init__` raises →
  crash in `adapter_loop`. **Image-build / Dockerfile change** (largest piece).
- **telegram-gateway hermetic-test feasibility** — its lifespan unconditionally calls
  `bot.set_webhook(url=audited.webhook_url, …)` against `api.telegram.org` (lifespan.py:287). With
  a dummy token / no network it raises → lifespan fails → no :8080 → unhealthy. Needs either an
  empty/test-token **skip-set_webhook** code path OR a mock-Telegram fixture. Also needs
  `TELEGRAM_WEBHOOK_URL` + `TELEGRAM_WEBHOOK_SECRET_TOKEN` in compose/test env (not currently set).
- **H7d clawhip-daemon ready-signal** — log-tail daemon with no HTTP port; doesn't touch
  `/tmp/ready`. Convention-consistent fix: code change to `clawhip_daemon/app/main.py` to touch
  `/tmp/ready` after sinks start (mirror registry-state Story 2.11 pattern).
- **H7e clawhip-daemon TELEGRAM_BOT_TOKEN** — `sys.exit(2)` if empty/malformed (`app/main.py:232-242`).
  The S-4 test (no `.env` in CI) needs an injected dummy valid-format token (e.g. `0:dummytesttoken`)
  for clawhip-daemon in BOTH the s4 overlay env AND the test's `phase1_env`. telegram-gateway
  tolerates an empty token; clawhip-daemon does not.

## ⚠️ SECURITY note (carried)

The MCP-env allowlist in `services/orchestrator-adapter/.../mcp_clients.py` +
`services/worker-wrapper/.../mcp_clients.py` (Story 11.3.6) is the a0ca050 P0 code path. **Do NOT
introduce `os.environ.copy()` / `dict(os.environ)` ANYWHERE under `services/*/adapters/mcp_clients.py`.**
Pre-existing `dict(os.environ)` sites in `omc_runner.py:90` and `claude_code_runner.py:185` are
**intentional** (OMC / Claude-Code child subprocesses; deferred-work.md story-9.6 D1/D4 tracks
their separate hardening) and are out of scope for this story too.

## Acceptance criteria

1. **AC1 — H7g orchestrator-adapter image carries OMC.** Decide + apply the right shape: vendor
   `upstream/omc` into the orchestrator-adapter image (Dockerfile `COPY upstream/omc /app/upstream/omc`
   or include in base) OR move OMC into a sidecar/volume. Image size + base-image impact assessed.
   `docker compose exec orchestrator-adapter ls /app/upstream/omc` shows a populated dir.
2. **AC2 — telegram-gateway hermetic boot.** Add a skip-`set_webhook` code path gated on
   empty/test token (or a clean `TELEGRAM_SKIP_WEBHOOK_SET=1` flag) so the lifespan completes
   without external network. Add `TELEGRAM_WEBHOOK_URL` / `TELEGRAM_WEBHOOK_SECRET_TOKEN` to the
   test composes / phase env so config validation passes. telegram-gateway reaches `healthy` on
   the socket probe (Story 11.3.6 H7d) without network.
3. **AC3 — H7d clawhip-daemon /tmp/ready code change.** Add `Path("/tmp/ready").touch()` after
   sinks are built in `clawhip_daemon/app/main.py:run()` (mirror registry-state Story 2.11 at
   `main.py:289` + unlink on shutdown at `:392`). clawhip-daemon reaches `healthy` under the
   shared `*healthcheck` `test -f /tmp/ready`.
4. **AC4 — H7e clawhip-daemon token in test env.** Inject `TELEGRAM_BOT_TOKEN=0:dummytesttoken`
   (valid-format) for clawhip-daemon in (a) `docker-compose.s4.yml` per-service env (NOT
   telegram-gateway's — keep that empty), AND (b) the S-4 test's `phase1_env` (so the ROOT
   compose's `${TELEGRAM_BOT_TOKEN:-}` resolves to a non-empty value for clawhip-daemon's read,
   while telegram-gateway tolerates it via AC2). Production sets the real token via `.env`,
   unchanged.
5. **AC5 — S-4 BOTH phases pass.** `tests/separability/test_s4_metrics_subscriber_optional.py`
   passes (Phase 1 ROOT compose 7 svc + Phase 2 s4 overlay 6 svc, all reach healthy; registry-api
   serves `/v1/health` + `POST /v1/tasks` identically).
6. **AC6 — No regression.** S1/S2/S3 (11.3.4) + registry-state (11.3.5) + idempotency +
   crash-injection + migrator jobs stay green; PR-gate `ci` green.
7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # no NEW errors vs baseline
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q -m "not slow"
   ```
8. **AC8 — Nightly.** `gh workflow run nightly.yml`; all 4 jobs PASS — `s3-separability` fully
   green (S1/S2/S3/S4). Record run id + conclusion.
9. **AC9 — AI-1 3-lane adversarial review at pass-1** (Blind = `code-reviewer`; Edge + Acceptance
   = `general-purpose`). Covers the telegram-gateway lifespan change (skip-webhook is a small but
   user-facing config-shape change), the clawhip-daemon ready/unlink (mirror registry-state), and
   the orchestrator OMC packaging.

## Constraints

- **Epic 11 retro AI-1 mandate APPLIES** — multi-service code + image-build change → 3-lane review.
- **Do NOT** revert Story 11.3.6, 11.3.5, 11.3.4, 11.3.3 Fix-A/B/AC2.
- **Do NOT** introduce `os.environ.copy()` / `dict(os.environ)` in `services/*/adapters/mcp_clients.py`
  (the a0ca050 P0 path). Pre-existing sites in `omc_runner.py` / `claude_code_runner.py` are
  intentional + out of scope (story-9.6 D1/D4).
- **Image-arch footgun:** any `services/*/src` or `mcp-servers/*/src` change requires
  `just build-base` — per-service image rebuilds alone do not pick up venv changes.
- Telegram-gateway change: prefer the most conservative shape (gated flag or empty-token guard
  that maps cleanly to production behavior — `.env` sets a real token, flag unset, normal webhook
  set). Avoid changing the production happy-path.

## Frontmatter

```yaml
---
story_id: 11.3.7
story_key: 11-3-7-root-compose-full-bringup
parent_epic: 11
phase: 2
fr_refs: [FR62a, NFR-M4, NFR-M5, FR35]
nfr_refs: [NFR-M4, NFR-M5]
arch_refs:
  - "Story 11.3.6 — H7a registry-api RW + H7b MCP allowlist (verified) + H7d socket healthchecks + H7h worker ready-file (this story picks up the orthogonal tail)"
  - "Story 11.2.3 — fcntl event-log writer (FR26)"
  - "Story 2.11 — registry-state /tmp/ready touch/unlink convention (clawhip-daemon mirror)"
  - "Story 10.6 — S-4 separability harness origin"
estimated_complexity: MEDIUM
priority: medium (last red nightly job; likely also a production fresh-deploy gap for orchestrator-adapter OMC)
blocks: []
unblocks:
  - Fully-green nightly s3-separability (S1/S2/S3/S4) + a bootable fresh ROOT-compose deploy
---
```

## Dev Notes (file:line map)

- `services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py:62` —
  `ValueError("omc_path does not exist: …")` on missing `upstream/omc`.
- `services/orchestrator-adapter/Dockerfile` — currently does not `COPY upstream/omc/`.
- `Dockerfile.base` — base image build; check whether OMC should live here vs per-service.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py:287` — unconditional
  `bot.set_webhook(url=…)` — add the skip-gate here.
- `services/telegram-gateway/src/telegram_gateway/app/config.py:14-15,161-162,417` — required
  webhook URL / secret token / bot token validation.
- `services/clawhip-daemon/src/clawhip_daemon/app/main.py:189-198` — `run()` builds sink + runs;
  touch `/tmp/ready` after `build_app(...)` returns, mirror `registry_state/.../main.py:277-291`
  pattern; unlink on shutdown mirror `:373-394`.
- `services/clawhip-daemon/src/clawhip_daemon/app/main.py:232-242` — `TELEGRAM_BOT_TOKEN` required
  + format check (H7e).
- `tests/separability/docker-compose.s4.yml` clawhip-daemon block — add
  `TELEGRAM_BOT_TOKEN: "0:dummytesttoken"` per-service (keep telegram-gateway's `""`).
- `tests/separability/test_s4_metrics_subscriber_optional.py:402` — `phase1_env` export site;
  add `TELEGRAM_BOT_TOKEN=0:dummytesttoken` for Phase 1.

## References
- [Source: 11-3-6-root-compose-fresh-boot.md — "H7b VERIFIED FIXED + remaining tail" + scope discoveries through H7h]
- [Source: services/registry-state/.../main.py:277-394 — /tmp/ready touch+unlink convention to mirror]
- [Source: services/telegram-gateway/.../lifespan.py:287 — set_webhook call site]
- [Source: tests/separability/test_s4_metrics_subscriber_optional.py:86-92 — _PRODUCER_SERVICES expected healthy in both phases]

## Definition of Done
- All 7 ROOT-compose services reach `healthy` on a fresh named volume.
- S-4 Phase 1 + Phase 2 BOTH pass; `s3-separability` nightly job fully PASS (S1/S2/S3/S4).
- Other nightly jobs + PR-gate `ci` green.
- AI-1 3-lane review complete; findings batch-applied.
- `sprint-status.yaml` `11-3-7-root-compose-full-bringup: backlog → done`.
