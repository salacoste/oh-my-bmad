# oh-my-bmad

> **A self-hosted personal development OS for autonomous software engineering.**
>
> Telegram + local console drive a Claude Code worker through a typed event bus, backed by a persistent task registry that survives restarts. Architected so additional CLI agents (Codex, Gemini, GLM) and a dedicated browser automation plane can be added later without changing the spine.

This repo is the **Phase 1 implementation**. Planning artifacts (product brief, PRD, architecture, epics, sprint status) live under `_bmad-output/`. See `_bmad-output/planning-artifacts/architecture.md` for the full system design.

---

## Quickstart

```sh
# 1. Prereqs: Docker Engine ≥ 24 + Docker Compose v2 + uv ≥ 0.5 + just
#    Install uv:   curl -LsSf https://astral.sh/uv/install.sh | sh
#    Install just: brew install just  (macOS)  |  cargo install just  (anywhere)
#    (or: brew install uv just  on macOS to grab both)
git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
just bootstrap-verify                          # confirms workspace wires up

# 2. Configure (Story 1.4 will land .env.example):
# cp .env.example .env && $EDITOR .env

# 3. Deploy (Story 1.4 will land docker-compose.yml):
# docker compose up -d                         # VPS (Linux)
# docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d  # macOS
```

> Story 1.1 ships only the workspace skeleton + this README. The full deploy quickstart is finished in Stories 1.4 (compose + env + justfile) and 1.10a (deployment quickstart docs).

---

## Directory structure

| Folder | Purpose |
|---|---|
| `services/` | Deployable backend processes (registry-api, registry-state, telegram-gateway, console-cli, orchestrator-adapter, worker-wrapper, clawhip-daemon). Phase 1 has only `registry-api` scaffolded; the rest land in Story 1.2. |
| `mcp-servers/` | MCP servers exposing tool/resource contracts to agents (`task-registry`, `session-registry`, `clawhip-bridge`). Distinct from `services/` because they have an MCP protocol surface, not an HTTP surface. Empty in Story 1.1 — populated in Story 1.2. |
| `packages/` | Shared libraries imported by multiple services and MCP servers (`events`, `secret_hygiene`, `idempotency`). Story 1.1 ships `events/` skeleton; rest land in Story 1.2. |
| `upstream/` | Vendored upstream-fork source trees (`omc/`, `clawhip/`), synced via `just sync-upstream <name>`. Empty until Story 1.3 (upstream vendoring). |
| `tests/` | Cross-service test trees: `separability/`, `crash-injection/`, `idempotency/`, `integration/`, `contract/`, `migrator/`. Empty until Story 1.5 (test tree + CI skeleton). |
| `docs/` | Operator documentation: deployment guides, runbooks, schema-evolution, exceptions, testing-guide. Empty until Stories 1.10a and 1.10b. |
| `_bmad-output/` | Planning artifacts (product brief, PRD, architecture, epics, sprint status). Authoritative source of design decisions. |
| `_bmad/`, `.claude/`, `.cursor/`, `.gemini/`, `.opencode/`, `.pi/`, `.agent/`, `.agents/`, `.omc/` | BMad framework + IDE/skill integration files (kept for ongoing planning amendments). |

---

## Deployment checklist

Both targets are populated as stubs in Story 1.1 and filled in detail by Story 1.10a (`docs/deployment/{vps,macos}.md`).

### VPS (Linux)

- [ ] Provision a VPS (Ubuntu 24.04 LTS recommended, ≥2 GB RAM, public IPv4).
- [ ] Install Docker Engine ≥ 24 + Docker Compose v2 + git + uv ≥ 0.5.
- [ ] Choose a tunnel for the Telegram webhook ingress: **Cloudflare Tunnel (default)**, ngrok, or BYO reverse proxy. (Story 1.4 docs the three options.)
- [ ] `git clone` + `cp .env.example .env` + edit secrets (Telegram bot token, Anthropic API key, GitHub PAT, allowlisted user ids).
- [ ] `docker compose up -d`.
- [ ] Verify `curl http://localhost:<port>/v1/health` (Story 2.9).
- [ ] Send `/ping` to the Telegram bot; expect `pong · …` within 2 s (Story 3.5).

### Local macOS

- [ ] Install Docker Desktop (or Colima ≥ 0.6) + git + uv ≥ 0.5.
- [ ] Same `.env` setup as VPS.
- [ ] `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d`.
- [ ] Same `/v1/health` and `/ping` verification.

---

## Backup / restore

Platform data (event log, registry SQLite, artifacts) lives at the **system path** `/var/lib/oh-my-bmad/` on the deployment host — *not* inside the repo working tree. The repo's `.gitignore` reserves repo-local `/var/` only as a guard against accidental in-tree data dumps; production data is always at the system location.

`just backup` (lands in Story 1.4) creates a timestamped tarball of the system data path. Recommended cadence: **daily**, rotated to off-host storage of the operator's choice (S3, Backblaze B2, rsync to NAS, etc.).

```sh
# Manual backup of the system data path (placeholder until Story 1.4 lands `just backup`):
# Run on the host where the platform is deployed:
docker compose down
sudo tar -czf "oh-my-bmad-backup-$(date +%F).tgz" /var/lib/oh-my-bmad
docker compose up -d

# Restore on a fresh host (re-creates /var/lib/oh-my-bmad from the tarball):
docker compose down
sudo tar -xzf oh-my-bmad-backup-<date>.tgz -C /
docker compose up -d
```

On macOS, the system path is identical (`/var/lib/oh-my-bmad/`); Docker Desktop / Colima mounts the host filesystem so the same `tar` works. The same `sudo` requirement applies. Full backup runbook with off-host rotation strategies lands in `docs/backup-restore.md` (Story 1.10b).

---

## Schema evolution / event-log migrator

The event log uses a versioned schema (`schema_version` field on every event envelope). Within a major version, only **additive** changes are permitted. Breaking changes require a one-shot migrator container:

```sh
docker compose run --rm migrator <from-version>-to-<to-version>
```

The migrator scaffold lands in Story 1.3; the full runbook lives at `docs/schema-evolution.md` (Story 1.10b).

---

## License

MIT. See `LICENSE`.

---

## Status: scaffold (Story 1.1 of Epic 1)

This README and the workspace skeleton are the entirety of Story 1.1. The platform itself ships incrementally across 7 epics / 98 stories — see `_bmad-output/planning-artifacts/epics.md` for the full backlog and `_bmad-output/implementation-artifacts/sprint-status.yaml` for current state. The MVP Ship-Blocker Checklist at the bottom of `epics.md` is the definitive "Phase 1 shipped" criterion.
