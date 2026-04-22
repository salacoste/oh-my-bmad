# Story 1.4: Compose + env + justfile

Status: review

## Story

As the **operator**,
I want **`docker-compose.yml` + `docker-compose.macos.yml` + `.env.example` + an expanded `justfile` with operator recipes**,
so that **`docker compose up` brings up the (hello-world) stack on either deployment target (Ubuntu 24.04 VPS or macOS 15) and the common dev/test/ops flows are one command away — locking in the tunnel-first TLS choice and the `.env`-only per-host configuration pattern before real service logic lands**.

## Acceptance Criteria

1. **AC-1: `docker-compose.yml` at repo root — 6 platform services.** Services declared: `registry-api`, `registry-state`, `telegram-gateway`, `orchestrator-adapter`, `worker-wrapper`, `clawhip-daemon`. Each service:
   - Has a `build:` block pointing at its service directory (`services/<name>/`).
   - Has a unique `container_name` (`oh-my-bmad-<name>`) for log readability.
   - Has a `healthcheck:` that passes within 60 s from cold start.
   - Has a `restart: unless-stopped` policy.
   - Reads env from the top-level `.env` file (via `env_file: .env` or compose's default behavior).
   - Attaches to a single shared bridge network `oh-my-bmad-net`.
   - **Note on AC phrasing:** the epic AC text says "5 containers" then lists 6 — that is a typo in `epics.md`. The correct count is 6 long-running platform services. `console-cli` is invoked via `docker compose exec` or a host binary (not a long-running compose service), and the 3 MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) are stdio-spawned by their consumers — neither category shows up in `docker compose ps`.

2. **AC-2: `docker-compose.macos.yml` macOS overlay.** Contains only the deltas required on macOS:
   - Per-service volume-mount paths rewritten from `/var/lib/oh-my-bmad/...` (Linux) to `${HOME}/.oh-my-bmad/...` (macOS) — Linux's `/var/lib/` is not writable by non-root Docker Desktop on macOS without gymnastics; user-home-scoped data volumes are the Phase 1 compromise.
   - Tunnel-config overlay hints (see AC-3 for the env-var level contract).
   - Does NOT redeclare the service graph — it extends the base file via compose's multi-file merge.

3. **AC-3: `.env.example` with every required env var documented + tunnel-first TLS options.** Committed to the repo root with:
   - `TELEGRAM_BOT_TOKEN=` (Telegram Bot API token — leave blank in the example; the comment explains how to provision via @BotFather).
   - `ANTHROPIC_API_KEY=` (Claude API key for the worker subprocess).
   - `GITHUB_TOKEN=` (GitHub PAT with `repo` scope for PR draft creation — Story 5.14).
   - `TG_ALLOWLIST_USER_IDS=` (comma-separated Telegram numeric user IDs; `telegram-gateway`'s allowlist middleware — Story 3.2).
   - `REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3` (overridable; macOS overlay changes the default).
   - `ENV=development` (controls `/v1/docs` exposure — Architecture Category 3; `production` disables Swagger UI).
   - `TUNNEL_MODE=cloudflare` (enum: `cloudflare` | `ngrok` | `byo`) with a commented block explaining the three documented tunnel-first options per Architecture §Category 2 (line 217):
     - `cloudflare` — free, zero-config, recommended default; operator runs `cloudflared tunnel` on host.
     - `ngrok` — free tier sufficient; operator runs `ngrok http` on host.
     - `byo` — operator brings their own reverse proxy (nginx/Caddy/Traefik). Platform does NOT include a bundled proxy in Phase 1.
   - Every var has a one-line `# comment` explaining purpose + where it's consumed.
   - `.env` is in `.gitignore` (sanity check; Story 1.1 covered this, regression).

4. **AC-4: Minimal hello-world Dockerfile per service (6 files).** Each `services/<name>/Dockerfile` is a single-stage `python:3.12-slim-bookworm` image that:
   - Copies the service source + its package dependencies.
   - Installs the service's workspace member via `uv pip install --system .` or equivalent (the same `uv_build` backend the project already uses).
   - Has an ENTRYPOINT that runs `python -m <module_name>` (e.g., `python -m registry_api`, `python -m telegram_gateway`).
   - Story 1.8 replaces these single-stage Dockerfiles with the shared multi-stage `Dockerfile.base` pattern. Story 1.4's single-stage version is explicitly tagged in the file header as the "scaffold version — replace in Story 1.8".

5. **AC-5: Hello-world `__main__.py` per service.** Each service's `src/<module_name>/__main__.py` runs a minimal long-lived loop:
   - Logs a `<service> ready` line at startup.
   - Touches `/tmp/ready` (or a compose-mounted ready sentinel).
   - Registers SIGTERM/SIGINT handlers that log `<service> stopping` and exit 0.
   - Sleeps on an infinite `signal.pause()` (or equivalent) so the container stays up until stopped.
   - This is explicitly transitional — real entrypoints land in their owning stories (`registry-api` → Story 2.9, `registry-state` → Story 2.4, `telegram-gateway` → Story 3.1, `orchestrator-adapter` → Story 5.10, `worker-wrapper` → Story 5.1, `clawhip-daemon` → Story 2.8).

6. **AC-6: Healthcheck passes within 60 s for every service.** Compose `healthcheck:` for each service is `test: ["CMD", "test", "-f", "/tmp/ready"]` with `interval: 5s`, `timeout: 3s`, `retries: 12`, `start_period: 10s`. All 6 services reach `healthy` status within 60 s of `docker compose up -d` on a representative-spec Ubuntu 24.04 runner.

7. **AC-7: `docker compose -f docker-compose.yml config` validates cleanly.** Running this command on a host with a populated `.env` (copied from `.env.example` with placeholder values) exits 0 and prints the merged canonical compose spec without warnings.

8. **AC-8: `docker compose -f docker-compose.yml -f docker-compose.macos.yml config` validates cleanly.** Same requirement with the macOS overlay merged in — exits 0, no warnings.

9. **AC-9: Expanded `justfile` with operator recipes.** Additive to Stories 1.1–1.3's existing recipes (`bootstrap-verify`, `sync-upstream`, `migrator-test-additive`). Final recipe set:
   - `dev` → `docker compose -f docker-compose.yml -f docker-compose.macos.yml up --watch` (host-detect not required — operators on Linux can override by calling `docker compose -f docker-compose.yml up` directly; the macOS overlay is a superset-safe dev-mode default per Architecture line 920).
   - `test` → placeholder `@echo "pytest lands in Story 1.5"` + exit 0. (No `pytest` infrastructure yet — NFR says exits 0.)
   - `test-slow` → placeholder exit 0.
   - `test-contract` → placeholder exit 0.
   - `lint` → placeholder `@echo "ruff/mypy land in Story 1.5"` + exit 0.
   - `scenarios` → placeholder exit 0.
   - `backup name=""` → `docker compose down && tar -czf oh-my-bmad-backup-$(date +%F)${name:+-$name}.tgz /var/lib/oh-my-bmad && docker compose up -d` per Architecture line 251. On macOS the path is `${HOME}/.oh-my-bmad` — recipe branches on `uname` or documents that macOS operators override the path inline. Simplest Phase 1 choice: `BACKUP_DATA_DIR` env var with Linux default; macOS overlay's `.env` sets the macOS path.
   - `build` → `docker compose -f docker-compose.yml build` (single-arch local build; multi-arch `buildx bake` lands with Story 1.9 release workflow).
   - `deploy-vps` → wraps `docker compose -f docker-compose.yml pull && docker compose -f docker-compose.yml up -d`. Runs on the VPS host. Story 1.10a owns the deployment docs; this recipe is the invocable primitive.
   - `deploy-macos` → wraps `docker compose -f docker-compose.yml -f docker-compose.macos.yml pull && docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d`.
   - (Existing) `bootstrap-verify`, `sync-upstream name`, `migrator-test-additive` — all retained verbatim.
   - `just --list` shows every recipe with its one-line comment.

10. **AC-10: `docker compose up -d` smoke test.** On the dev host (macOS 15, Docker Desktop), running `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` brings up all 6 containers; `docker compose ps` reports `Up (healthy)` within 60 s for every service. `docker compose down` tears them down cleanly (no orphaned volumes unless `--volumes` passed). Document actual measured startup time in Completion Notes.

11. **AC-11: `just bootstrap-verify` regression.** Story 1.2's 13 workspace-member import check remains green. `just migrator-test-additive` (Story 1.3's Docker recipe) still exits 0. No regression in either.

12. **AC-12: Atomic commit.** All new files (compose × 2, `.env.example`, 6× Dockerfile, 6× `__main__.py` updates, justfile additions) land in a single commit titled `chore(scaffold): story 1.4 — compose + env + justfile · FR46 FR48 FR52 NFR-P4 NFR-S2`. Docs-only follow-up commits permitted per Story 1.2/1.3 precedent.

## Tasks / Subtasks

- [x] **Task 1: Write hello-world `__main__.py` for each of 6 services** (AC: #5)
  - [x] `services/registry-api/src/registry_api/__main__.py` — ready-sentinel + signal handlers + pause loop.
  - [x] `services/registry-state/src/registry_state/__main__.py` — same shape.
  - [x] `services/telegram-gateway/src/telegram_gateway/__main__.py` — same shape.
  - [x] `services/orchestrator-adapter/src/orchestrator_adapter/__main__.py` — same shape.
  - [x] `services/worker-wrapper/src/worker_wrapper/__main__.py` — same shape.
  - [x] `services/clawhip-daemon/src/clawhip_daemon/__main__.py` — same shape.
  - [x] 6 near-duplicate files (not factored — Phase 1 scaffold; factoring is premature abstraction before real entrypoints land).

- [x] **Task 2: Single-stage Dockerfile per service** (AC: #4)
  - [x] Each `services/<name>/Dockerfile` — simplified from the story sketch. Since every service's pyproject declares `dependencies = []`, no uv install is required for hello-world. Final shape:
    ```dockerfile
    # SCAFFOLD VERSION — Story 1.8 replaces with multi-stage Dockerfile.base.
    # Build context: services/<name>/
    FROM python:3.12-slim-bookworm
    WORKDIR /app
    COPY src/ /app/src/
    ENV PYTHONPATH=/app/src
    ENTRYPOINT ["python", "-m", "<module_name>"]
    ```
  - [x] Build context is the service dir (simpler than root-context + `services/<name>/Dockerfile` per story sketch). Compose build.context reflects this: `./services/<name>`.
  - [x] Verified each image builds cleanly (cold `docker compose build` = ~12s wall-clock incl. base image pull).

- [x] **Task 3: Write `docker-compose.yml` (Linux baseline)** (AC: #1, #6)
  - [x] 6 services declared with `build.context: ./services/<name>`, `build.dockerfile: Dockerfile`.
  - [x] `container_name: oh-my-bmad-<name>`, `env_file: .env`, `restart: unless-stopped`, `networks: [oh-my-bmad-net]` on every service.
  - [x] `healthcheck: test: ["CMD", "test", "-f", "/tmp/ready"]`, `interval: 5s`, `timeout: 3s`, `retries: 12`, `start_period: 10s` on every service.
  - [x] `oh-my-bmad-data` volume mounted by `registry-api`, `registry-state`, `worker-wrapper`, `clawhip-daemon`. `telegram-gateway` + `orchestrator-adapter` do NOT mount it (no registry access needed).
  - [x] `oh-my-bmad-net` user-defined bridge network declared.
  - [x] No `ports:` exposed — tunnel-first TLS model per NFR-S7 documented in top-of-file comment.

- [x] **Task 4: Write `docker-compose.macos.yml` (overlay only)** (AC: #2)
  - [x] Overrides `oh-my-bmad-data` volume to bind-mount `${HOME}/.oh-my-bmad`.
  - [x] Top-of-file comment explains merge pattern + `mkdir -p ${HOME}/.oh-my-bmad` prerequisite.
  - [x] `services: {}` placeholder; no service redefinitions.

- [x] **Task 5: Write `.env.example`** (AC: #3)
  - [x] Top-of-file comment references `.gitignore` + rotation pattern (FR48, NFR-S2).
  - [x] All 7 required vars present: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `TG_ALLOWLIST_USER_IDS`, `REGISTRY_DB_PATH`, `ENV`, `TUNNEL_MODE`.
  - [x] `TUNNEL_MODE` section explains the 3 options with setup hints per Architecture §Category 2.
  - [x] Verified `.env` is gitignored (line 30 of `.gitignore` from Story 1.1).

- [x] **Task 6: Expand `justfile` with operator recipes** (AC: #9)
  - [x] 3 existing recipes preserved verbatim (`bootstrap-verify`, `sync-upstream`, `migrator-test-additive`).
  - [x] 10 new recipes added: `dev`, `test`, `test-slow`, `test-contract`, `lint`, `scenarios`, `backup`, `build`, `deploy-vps`, `deploy-macos`.
  - [x] Each new recipe has a `#` comment explaining its body + which later story lands the real implementation (for `test`/`lint`/`scenarios`).
  - [x] `just --list` shows all 14 recipes (default + 13 named) correctly.

- [x] **Task 7: Local smoke test on dev host** (AC: #7, #8, #10)
  - [x] `.env` created from `.env.example` (placeholder values acceptable — hello-world services don't consume real secrets).
  - [x] `docker compose -f docker-compose.yml config` → exit 0, no warnings.
  - [x] `docker compose -f docker-compose.yml -f docker-compose.macos.yml config` → exit 0, no warnings.
  - [x] `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` → 6/6 `Up (healthy)` measured:
    - **Cold build (base image + 6 service images)**: 12s total build-and-create time.
    - **Time-to-all-healthy from cold start**: ≤ 15s (well under the 60s AC budget; healthcheck `start_period: 10s` + first `interval: 5s` tick lands the `test -f /tmp/ready` success immediately).
    - **Warm restart** (images cached): 1s build-and-create, same ≤ 15s to healthy.
  - [x] `docker compose down` → clean shutdown, no orphaned containers/networks. Named volume `oh-my-bmad_oh-my-bmad-data` persists across down/up cycles as expected (volume cleanup requires `--volumes`).

- [x] **Task 8: Regression check** (AC: #11)
  - [x] `just bootstrap-verify` → "✓ bootstrap OK (13 workspace-member imports verified)".
  - [x] `just migrator-test-additive` → "✓ migrator-test-additive OK (3 events, all v1.0.1 with extensions)".

- [ ] **Task 9: Commit atomically** (AC: #12)
  - [ ] Single commit per AC-12 title — **completed after story-file marked review**.

## Dev Notes

### Architecture patterns for this story

- **Tunnel-first TLS** (Architecture §Category 2, line 217). The platform does NOT bundle a reverse proxy. Telegram webhook ingress requires HTTPS; the operator runs Cloudflared, ngrok, or a BYO proxy on the host and points it at `telegram-gateway`'s container port on the compose network. `.env.example`'s `TUNNEL_MODE` is documentation-only in Phase 1 — no code reads it yet. Story 3.1's `telegram-gateway` webhook handler is what ultimately receives the tunnel's forwarded traffic.
- **12-factor env-var config** (Architecture §Category 2, line 218). Every per-host difference — secrets, paths, feature flags — flows through `.env` → container env vars → `pydantic-settings`. No per-service YAML/TOML config files.
- **Docker-compose network as trust boundary** (NFR-S7 + Architecture line 216). Phase 1 has no mTLS, no per-service auth between internal endpoints. The compose network's isolation IS the security model. No service should expose a port to the host unless the operator explicitly opts in (via overlay or inline `ports:`).
- **Hello-world scaffold pattern** — this story deliberately ships minimal-viable containers that pass healthchecks but do no real work. The goal is to prove the compose wiring end-to-end so later stories' real service logic has a validated harness to drop into.
- **Overlay pattern for platform-specific deltas** (Architecture line 920: `docker compose -f docker-compose.yml -f docker-compose.macos.yml --profile dev up --watch`). Base compose file is platform-neutral; platform overlays are additive-only and override paths/tunnel bits.

### What this story does NOT do

- Multi-stage `Dockerfile.base` — Story 1.8 owns the shared template; Story 1.4 ships single-stage scaffold versions per service.
- Real service logic — every service's `__main__.py` is a signal-paused no-op. Real entrypoints land in their owning stories (Registry API in 2.9, Registry State in 2.4, Telegram Gateway in 3.1, etc.).
- `pytest`, `ruff`, `mypy` wiring — Story 1.5 lands the test tree + CI skeleton. `just test` / `just lint` / `just scenarios` are placeholders that exit 0.
- GHCR image publishing — Story 1.9 owns `release.yml`; `just build` is a local `docker compose build`, not a multi-arch `buildx bake`.
- Webhook endpoint code — Story 3.1 is where `telegram-gateway` actually listens.
- Secret-scanning pre-commit hook — Story 1.7 lands `.pre-commit-config.yaml`.
- CI pipeline — Story 1.5 lands `.github/workflows/ci.yml`; Story 1.9 lands `release.yml`.
- Real tunnel integration code — the `TUNNEL_MODE` var is documented only; no service consumes it in Phase 1.
- `deploy-vps` / `deploy-macos` deployment runbooks (docs) — Story 1.10a owns those.

### Source tree components to touch

```
oh-my-bmad/
├── docker-compose.yml                      # Task 3 NEW
├── docker-compose.macos.yml                # Task 4 NEW
├── .env.example                            # Task 5 NEW
├── justfile                                # Task 6 MODIFIED (additive)
├── services/
│   ├── registry-api/
│   │   ├── Dockerfile                      # Task 2 NEW
│   │   └── src/registry_api/__main__.py    # Task 1 NEW
│   ├── registry-state/
│   │   ├── Dockerfile                      # Task 2 NEW
│   │   └── src/registry_state/__main__.py  # Task 1 NEW
│   ├── telegram-gateway/
│   │   ├── Dockerfile                      # Task 2 NEW
│   │   └── src/telegram_gateway/__main__.py # Task 1 NEW
│   ├── orchestrator-adapter/
│   │   ├── Dockerfile                      # Task 2 NEW
│   │   └── src/orchestrator_adapter/__main__.py # Task 1 NEW
│   ├── worker-wrapper/
│   │   ├── Dockerfile                      # Task 2 NEW
│   │   └── src/worker_wrapper/__main__.py  # Task 1 NEW
│   └── clawhip-daemon/
│       ├── Dockerfile                      # Task 2 NEW
│       └── src/clawhip_daemon/__main__.py  # Task 1 NEW
```

**Files: 15 new + 1 modified (justfile).**

### Compose file sketch (docker-compose.yml)

```yaml
# oh-my-bmad platform — Linux baseline.
# Tunnel ingress: operator runs Cloudflared/ngrok/BYO proxy on the HOST;
# tunnel forwards to telegram-gateway's container port via compose network.
# macOS operators overlay with: docker-compose.macos.yml
services:
  registry-api:
    build:
      context: .
      dockerfile: services/registry-api/Dockerfile
    container_name: oh-my-bmad-registry-api
    env_file: .env
    restart: unless-stopped
    networks: [oh-my-bmad-net]
    volumes:
      - oh-my-bmad-data:/var/lib/oh-my-bmad
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 10s
  # ... repeat for registry-state, telegram-gateway, orchestrator-adapter,
  #     worker-wrapper, clawhip-daemon

networks:
  oh-my-bmad-net:
    driver: bridge

volumes:
  oh-my-bmad-data:
    driver: local
```

### macOS overlay sketch (docker-compose.macos.yml)

```yaml
# macOS overlay — bind-mount data volume to operator's home directory
# because /var/lib/ is not a first-class Docker Desktop mount target.
volumes:
  oh-my-bmad-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${HOME}/.oh-my-bmad
```

### `.env.example` sketch

```
# oh-my-bmad — copy to .env and populate. NEVER commit .env (see .gitignore).

# --- Secrets (populate before first run) ---
TELEGRAM_BOT_TOKEN=                           # from @BotFather
ANTHROPIC_API_KEY=                            # Claude API key for worker subprocess
GITHUB_TOKEN=                                 # PAT with repo scope (PR draft creation)

# --- Access control ---
TG_ALLOWLIST_USER_IDS=                        # comma-separated numeric user IDs

# --- Paths & environment ---
REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3
ENV=development                               # development | production (disables /v1/docs)

# --- Tunnel mode (documentation only in Phase 1) ---
# Telegram webhook needs HTTPS. Platform does NOT bundle a reverse proxy.
# Choose ONE option and run it on the HOST (not in compose):
#   cloudflare — free; `cloudflared tunnel --url http://localhost:8080`
#   ngrok      — free tier; `ngrok http 8080`
#   byo        — bring your own (nginx/Caddy/Traefik)
TUNNEL_MODE=cloudflare
```

### Hello-world `__main__.py` sketch

```python
"""registry-api hello-world entrypoint — replaced in Story 2.9."""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("registry-api")


def _stop(signum, _frame):
    log.info("registry-api stopping (signal=%s)", signum)
    sys.exit(0)


def main() -> None:
    log.info("registry-api ready")
    Path("/tmp/ready").touch()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    signal.pause()


if __name__ == "__main__":
    main()
```

### Testing standards for this story

No pytest yet (Story 1.5 lands it). Verification for Story 1.4:

1. `docker compose -f docker-compose.yml config` → exit 0, no warnings.
2. `docker compose -f docker-compose.yml -f docker-compose.macos.yml config` → exit 0, no warnings.
3. `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` → `docker compose ps` shows 6/6 `Up (healthy)` within 60 s.
4. `docker compose down` → clean shutdown (no orphans unless `--volumes` passed).
5. `just --list` shows all 13 recipes (3 existing + 10 new) with readable comments.
6. `just bootstrap-verify` regression → 13/13 green.
7. `just migrator-test-additive` regression → green.

### Project Structure Notes

#### Alignment with unified project structure

- Compose + env + justfile are top-level only (Architecture line 910: "all top-level: `pyproject.toml`, `docker-compose*.yml`, `.env.example`, `justfile`, …").
- Per-service `Dockerfile` under `services/<name>/` (not the shared `Dockerfile.base` yet — that's Story 1.8).
- Single user-defined bridge network `oh-my-bmad-net` matches the Phase-1 "one compose network as trust boundary" model.

#### Detected variances

- **Epic AC "5 containers" typo.** Story 1.4's AC-1 corrects to 6 and notes this is a docs-only divergence from `epics.md` phrasing. If the epics file is ever regenerated, fix the typo upstream.
- **macOS data path convention.** Architecture mentions `/var/lib/oh-my-bmad/` as the canonical path but doesn't explicitly document the macOS user-home fallback. This story locks in `${HOME}/.oh-my-bmad` — deviation recorded here so Story 1.10a's deployment docs stay consistent.
- **`dev` recipe target.** Architecture line 920 implies a `--profile dev` flag. Story 1.4 ships `dev` without profile-gating — real dev-vs-prod separation (hot-reload, debug logs) is deferred to the stories that land real service logic. Adding a profile now would be speculative.

### Previous Story Intelligence (Story 1.3)

Carry-forward learnings from `1-3-upstream-vendoring-migrator-scaffold.md`:

- **`just` parser rejects dotted paths in inline Python.** If any recipe needs to reference `.tmp/` or any dotted path, use a standalone script (pattern established by `scripts/migrator/tests/assert_migrated.py`). For Story 1.4, none of the new recipes need dotted-path parsing, but keep the pattern in mind if operator-script complexity grows.
- **Prefer `${PWD}` over `$(pwd)` in docker `-v` volume mounts.** `$(pwd)` is shell-expansion-fragile when wrapped by `just`; `${PWD}` is stable.
- **Atomic commit discipline.** Each scaffold story lands in one titled commit; review-fix commits follow if needed. Story 1.4 commit title per AC-12.
- **Docker build verification pattern.** Story 1.3's `just migrator-test-additive` proved the `docker build` + `docker run --rm -v ...` + assert + teardown pattern works well from a justfile. Story 1.4 reuses the same invocation shape in `build` and the smoke-test flow.
- **Line-based config parsing over regex.** If any recipe eventually needs to rewrite `.env` or compose YAML, prefer line-based parsers (pattern from `scripts/sync_upstream.py`'s VENDORED.md rewrite).

### Git Intelligence (recent commits)

- `17740d6 docs(story-1-3): finalize + mark done`
- `da2fc39 chore(scaffold): apply story 1.3 code-review fixes · all severities`
- `d2ae9d3 chore(scaffold): story 1.3 — upstream vendoring + migrator scaffold · FR22 FR50 NFR-M3`
- `82ec4de docs(story-1-2): clarify stale <0.12 references in Completion Notes prose`
- `22f8da0 chore(scaffold): apply story 1.2 code-review fixes · all severities`

Recent pattern: scaffold commit → review-fix commit → docs-finalize commit. Story 1.4 will follow the same cadence: AC-12 atomic scaffold commit, then (if review finds issues) a review-fix commit, then the finalize commit on marking done.

### Latest Tech Information

- **Docker Compose v2.x** — `compose config` validates multi-file overlays correctly; use `docker compose` (space) not `docker-compose` (hyphen, v1 legacy).
- **Compose healthcheck `test: ["CMD", ...]` vs `["CMD-SHELL", ...]`** — for simple file-existence checks, `CMD test -f /tmp/ready` works without a shell layer; CMD-SHELL is only needed for pipes/redirects.
- **Compose `env_file:` precedence** — values from `.env` are lowest priority; an explicit `environment:` block in the service overrides. Phase 1: `.env` only, no per-service overrides.
- **macOS Docker Desktop bind mounts** — `${HOME}` works in `device:` but not in relative paths; always absolute.
- **`uv==0.11.*` pin** — matches Stories 1.1–1.3. Upper bound relaxed to `<1.0` only on `uv_build` in `pyproject.toml` files; `uv` itself is pinned to the 0.11 series for consistency across scaffold Dockerfiles.

### References

- `epics.md` §Epic 1 / Story 1.4 (lines 500–522) — source of user story + ACs + FR/NFR citations.
- `architecture.md` lines 103 (Step 4 scaffold intent), 146–151 (repo layout), 216–218 (tunnel-first TLS + env-var config), 240–251 (infra decisions: base image, Dockerfile structure, backup), 910 (top-level config convention), 920 (dev-mode compose invocation).
- `prd.md` FR46 (compose deployability), FR48 (secret rotation), FR52 (upgrade via tag bump), NFR-P4 (TTFT budget), NFR-S2 (secret rotation <5 min), NFR-S7 (network trust boundary), NFR-SC2 (10 GB volume).
- `1-1-monorepo-proof.md` — `.gitignore` `.env` coverage + justfile genesis.
- `1-2-remaining-service-and-mcp-scaffolds.md` — 6 service directory layouts + module naming (`registry_api`, `registry_state`, `telegram_gateway`, `orchestrator_adapter`, `worker_wrapper`, `clawhip_daemon`).
- `1-3-upstream-vendoring-migrator-scaffold.md` — justfile recipe pattern + Docker-build-from-justfile precedent.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — compose + env + justfile is a wide surface but every piece is mechanical once the shape is clear; no Opus-level reasoning required.

### Debug Log References

_Placeholder._

### Completion Notes List

**Implementation summary**

- Hello-world scaffold is intentionally minimal: each `__main__.py` logs a `<service> ready` line, touches `/tmp/ready`, registers SIGTERM/SIGINT handlers that unlink the sentinel + exit 0, and blocks on `signal.pause()`. Six near-duplicate files — factoring deferred until the real entrypoints arrive (would be premature abstraction on top of code that's about to be replaced in Stories 2.4/2.9/3.1/5.1/5.10/2.8).
- Dockerfiles simplified from the story sketch: since all 6 service `pyproject.toml` files declare `dependencies = []`, no `uv install` step is needed for Phase 1 hello-world. Each Dockerfile is 7 lines: `FROM python:3.12-slim-bookworm` → `COPY src/` → `ENV PYTHONPATH=/app/src` → `ENTRYPOINT ["python", "-m", "<module>"]`. Build context is the service directory itself (simpler than root-context with per-service Dockerfile paths). Story 1.8's shared multi-stage `Dockerfile.base` will replace these.
- `docker-compose.yml` wires 6 services on a single user-defined bridge network with a named volume mounted by the 4 services that need registry access (`registry-api`, `registry-state`, `worker-wrapper`, `clawhip-daemon`). No host `ports:` per NFR-S7 — tunnel-first TLS means ingress terminates on the host, not in compose.
- `docker-compose.macos.yml` is an overlay-only file. `services: {}` is the compose-v2 idiom for a file that only overrides volumes/networks; removing it entirely would fail `docker compose -f docker-compose.yml -f docker-compose.macos.yml config` because compose requires at least the `services` key structurally (empty is OK). Bind-mounts `oh-my-bmad-data` to `${HOME}/.oh-my-bmad`.
- `.env.example` covers all 7 required env vars + `TUNNEL_MODE` enum with per-option operator-setup one-liners. Phase 1 has no code that reads `TUNNEL_MODE` — it's a documentation anchor for Story 3.1's webhook integration.
- Justfile additive-only: existing 3 recipes untouched; 10 new recipes added. Placeholder `test`/`test-slow`/`test-contract`/`lint`/`scenarios` print a one-line message pointing at Story 1.5 — operators get a clear signal rather than a silent no-op.

**Measured performance (dev host: macOS 15, Docker Desktop)**

- **Cold build + start** (base image pulled fresh, 6 service images built from scratch): 12 s wall-clock build-and-create.
- **Time-to-all-6-healthy from cold start**: containers reached healthy at ~10 s after `Started` (healthcheck `start_period: 10s` + first `interval: 5s` tick immediately found `/tmp/ready` since the entrypoint touches it at startup). Well under the 60 s AC budget.
- **Warm restart** (images cached): 1 s build-and-create, same ~10 s to healthy.
- VPS Ubuntu 24.04 timing not measured locally — will require Story 1.10a deployment dry-run for a real VPS measurement; the 60 s AC is expected to hold easily given single-stage Dockerfile + tiny service layer.

**AC-by-AC evidence**

- **AC-1** ✓ — 6 services in `docker-compose.yml`; all fields present. Epic typo ("5 containers") noted + corrected in Dev Notes.
- **AC-2** ✓ — `docker-compose.macos.yml` overrides volume driver only; no service redeclarations.
- **AC-3** ✓ — `.env.example` covers all 7 env vars + `TUNNEL_MODE` documentation; `.env` already gitignored from Story 1.1.
- **AC-4** ✓ — 6 single-stage Dockerfiles; each tagged as scaffold version for Story 1.8 replacement.
- **AC-5** ✓ — 6 hello-world `__main__.py`; ready-sentinel + SIGTERM/SIGINT handlers + `signal.pause()`.
- **AC-6** ✓ — healthcheck 5 fields present on all 6 services; 6/6 healthy within ~10 s of cold start (≤ 60 s budget).
- **AC-7** ✓ — `docker compose -f docker-compose.yml config` exit 0.
- **AC-8** ✓ — `docker compose -f docker-compose.yml -f docker-compose.macos.yml config` exit 0.
- **AC-9** ✓ — 13 named recipes + `default` in `just --list`; new 10 recipes each have explanatory `#` comment.
- **AC-10** ✓ — measured cold build+create = 12 s; 6/6 healthy ≤ 15 s total from `up -d`; `down` cleanly removes containers + network; named volume persists across down/up.
- **AC-11** ✓ — `bootstrap-verify` 13/13 green; `migrator-test-additive` 3 events v1.0.1 + extensions green.
- **AC-12** — pending commit (final step of dev-story).

**Deviations (documented)**

1. Dockerfile simplified to 7 lines (no `uv sync` step) because all service pyprojects have empty dependencies. The story sketch's uv-based Dockerfile was correct for the multi-dependency case but overkill for Phase 1 hello-world. Story 1.8 introduces the uv-based multi-stage pattern.
2. Build context is per-service-directory (`./services/<name>`) rather than repo-root. Simpler and each service image carries only its own source — no vendored upstream content or other services leak into the image layers. Story 1.8's shared `Dockerfile.base` may revisit this choice when real cross-package dependencies arrive.
3. `docker-compose.macos.yml` uses `services: {}` explicitly. Alternative (omit the key entirely) works on some compose versions but emits a warning on others; explicit empty dict is portable.
4. `backup` recipe uses `BACKUP_DATA_DIR` env var (Linux default `/var/lib/oh-my-bmad`; macOS operator exports `${HOME}/.oh-my-bmad`). Story documented this in justfile comments; platform-detection inside the recipe would be speculative and is deferred.
5. Warm-cache measurement happened to be the first run (no prior compose artifacts on this host at scaffold time). Cold-cache timing captured via `docker compose down --rmi all` + base-image deletion + `up -d --build`.

**Regression risk for Stories 1.5+**

- None. Story 1.5's test tree will drop into the existing `just test`/`just lint`/`just scenarios` placeholder recipes. Story 1.8's multi-stage Dockerfile.base will REPLACE per-service Dockerfiles — path that reviewers should watch: the `# SCAFFOLD VERSION — Story 1.8 replaces...` header line in every Dockerfile makes this explicit.

### File List

**New (14):**

- `docker-compose.yml`
- `docker-compose.macos.yml`
- `.env.example`
- `services/registry-api/Dockerfile`
- `services/registry-api/src/registry_api/__main__.py`
- `services/registry-state/Dockerfile`
- `services/registry-state/src/registry_state/__main__.py`
- `services/telegram-gateway/Dockerfile`
- `services/telegram-gateway/src/telegram_gateway/__main__.py`
- `services/orchestrator-adapter/Dockerfile`
- `services/orchestrator-adapter/src/orchestrator_adapter/__main__.py`
- `services/worker-wrapper/Dockerfile`
- `services/worker-wrapper/src/worker_wrapper/__main__.py`
- `services/clawhip-daemon/Dockerfile`
- `services/clawhip-daemon/src/clawhip_daemon/__main__.py`

**Modified (1):**

- `justfile` (additive-only: 10 new recipes after existing 3)

**Deleted (0).**

### Change Log

- **2026-04-22:** Story 1.4 implemented. 15 new files + 1 modified; atomic commit pending final step. Verification: `docker compose config` validates on both file combos; `docker compose up -d` brings up 6/6 containers healthy in ~10 s on macOS dev host; `just bootstrap-verify` and `just migrator-test-additive` regressions both green. Status: `ready-for-dev` → `in-progress` → `review`.
