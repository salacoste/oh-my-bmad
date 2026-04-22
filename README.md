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
| `services/` | Deployable backend processes (registry-api, registry-state, telegram-gateway, console-cli, orchestrator-adapter, worker-wrapper, clawhip-daemon). All 7 scaffolded as of Story 1.2. |
| `mcp-servers/` | MCP servers exposing tool/resource contracts to agents. Distinct from `services/` because they have an MCP protocol surface, not an HTTP surface. All 3 scaffolded as of Story 1.2. |
| `packages/` | Shared libraries imported by multiple services and MCP servers (`events`, `secret-hygiene`, `idempotency`). All 3 scaffolded as of Story 1.2. |
| `upstream/` | Vendored upstream-fork source trees (`omc/`, `clawhip/`), synced via `just sync-upstream <name>`. Empty until Story 1.3 (upstream vendoring). |
| `tests/` | Cross-service test trees: `separability/`, `crash-injection/`, `idempotency/`, `integration/`, `contract/`, `migrator/`. Empty until Story 1.5 (test tree + CI skeleton). |
| `docs/` | Operator documentation: deployment guides, runbooks, schema-evolution, exceptions, testing-guide. Empty until Stories 1.10a and 1.10b. |
| `_bmad-output/` | Planning artifacts (product brief, PRD, architecture, epics, sprint status). Authoritative source of design decisions. |
| `_bmad/`, `.claude/`, `.cursor/`, `.gemini/`, `.opencode/`, `.pi/`, `.agent/`, `.agents/`, `.omc/` | BMad framework + IDE/skill integration files (kept for ongoing planning amendments). |

### MCP-server naming convention

MCP servers use three names — directory, project, module — that intentionally differ. This is not a typo; it's an accommodation for `uv_build`'s kebab→snake module derivation plus architectural convention.

| Directory (group-scoped) | Project (in `pyproject.toml`) | Python module |
|---|---|---|
| `mcp-servers/task-registry/` | `task-registry-mcp` | `task_registry_mcp` |
| `mcp-servers/session-registry/` | `session-registry-mcp` | `session_registry_mcp` |
| `mcp-servers/clawhip-bridge/` | `clawhip-bridge-mcp` | `clawhip_bridge_mcp` |

**Rule:** when you see one form, the other two are derivable:
- Directory = unsuffixed kebab (parent `mcp-servers/` folder already names the contract type).
- Project name = directory name with `-mcp` suffix.
- Python module = project name with `-` → `_` (so `task-registry-mcp` → `task_registry_mcp`).

Services and packages follow the simpler 1:1 kebab ↔ snake convention (e.g., `secret-hygiene` ↔ `secret_hygiene`).

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

`just backup` snapshots the `oh-my-bmad-data` named volume to a local `.tgz`
via a throwaway `alpine` container (works identically on Linux and macOS):

    just backup              # oh-my-bmad-backup-<utc-ts>.tgz
    just backup pre-upgrade  # oh-my-bmad-backup-<utc-ts>-pre-upgrade.tgz

The optional suffix must match `[A-Za-z0-9._-]+` (safe filename chars only).

The recipe stops the stack, tars the volume contents, then brings the stack
back up (even if tar fails — the restart runs in an `EXIT` trap).

To restore, extract into a fresh volume before first `compose up`:

    docker volume create oh-my-bmad_oh-my-bmad-data
    docker run --rm -v oh-my-bmad_oh-my-bmad-data:/dest -v "$PWD:/src" alpine:3 \
        tar -xzf "/src/oh-my-bmad-backup-<timestamp>.tgz" -C /dest

Then `just dev` (or `just deploy-vps` / `just deploy-macos`).

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
