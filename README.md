# oh-my-bmad

> **A self-hosted personal development OS for autonomous software engineering.**
>
> Telegram + local console drive a Claude Code worker through a typed event bus, backed by a persistent task registry that survives restarts. Architected so additional CLI agents (Codex, Gemini, GLM) and a dedicated browser automation plane can be added later without changing the spine.

This repo is the **Phase 1 implementation**. Planning artifacts (product brief, PRD, architecture, epics, sprint status) live under `_bmad-output/`. See `_bmad-output/planning-artifacts/architecture.md` for the full system design.

---

## Quickstart

```sh
# Prereqs: Docker Engine ≥ 24 + Docker Compose v2.24+ + uv ≥ 0.5 + just ≥ 1.14
#   brew install uv just                                  # macOS
#   curl -LsSf https://astral.sh/uv/install.sh | sh       # Linux (uv)
git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
uv sync --dev
uv run pre-commit install
just bootstrap-verify
cp .env.example .env
$EDITOR .env                                             # fill in secrets + tunnel choice
just dev                                                 # macOS: overlay; Linux: base compose
docker compose ps                                        # expect 6/6 Up (healthy) within 60 s
```

**For detailed deployment guides:** [`docs/deployment/vps.md`](docs/deployment/vps.md) · [`docs/deployment/macos.md`](docs/deployment/macos.md).

---

## Full operator documentation

- [Operator runbook](docs/operator-runbook.md) — paging conditions + per-service recovery playbooks.
- [Schema evolution](docs/schema-evolution.md) — add an event type + ship a migrator + roll-back procedure.
- [Exceptions](docs/exceptions.md) — documented naming-rule + convention exceptions.
- [Testing guide](docs/testing-guide.md) — test-tree layout + harness usage + contract-fixture recording workflow.
- [Backup / restore](docs/backup-restore.md) — volume snapshot + off-host rsync + fresh-host restore.
- [Message design](docs/message-design.md) — Telegram template catalog + character budgets.

---

## Directory structure

| Folder | Purpose |
|---|---|
| `services/` | Deployable backend processes (registry-api, registry-state, telegram-gateway, console-cli, orchestrator-adapter, worker-wrapper, clawhip-daemon). All 7 scaffolded as of Story 1.2. |
| `mcp-servers/` | MCP servers exposing tool/resource contracts to agents. Distinct from `services/` because they have an MCP protocol surface, not an HTTP surface. All 3 scaffolded as of Story 1.2. |
| `packages/` | Shared libraries imported by multiple services and MCP servers (`events`, `secret-hygiene`, `idempotency`). All 3 scaffolded as of Story 1.2. |
| `upstream/` | Vendored upstream-fork source trees (`omc/`, `clawhip/`), synced via `just sync-upstream <name>`. Empty until Story 1.3 (upstream vendoring). |
| `tests/` | Cross-service test trees: `separability/`, `crash-injection/`, `idempotency/`, `integration/`, `contract/`, `migrator/`. Empty until Story 1.5 (test tree + CI skeleton). |
| `docs/` | Operator documentation: [deployment guides](docs/deployment/) (Story 1.10a), [runbook](docs/operator-runbook.md) + [schema evolution](docs/schema-evolution.md) + [exceptions](docs/exceptions.md) + [testing guide](docs/testing-guide.md) + [backup-restore](docs/backup-restore.md) + [message design](docs/message-design.md) (Story 1.10b). |
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

Full deployment guides live at [`docs/deployment/vps.md`](docs/deployment/vps.md) + [`docs/deployment/macos.md`](docs/deployment/macos.md). The 6-step summary:

### VPS (Linux)

- [ ] Provision a VPS (Ubuntu 24.04 LTS recommended, ≥ 2 GB RAM, public IPv4).
- [ ] Install Docker Engine ≥ 24 + Docker Compose v2.24+ + git + `uv ≥ 0.5` + `just ≥ 1.14`.
- [ ] Choose a tunnel for the Telegram webhook ingress: Cloudflare Tunnel (default), ngrok, or BYO reverse proxy.
- [ ] `git clone` + `cp .env.example .env` + edit secrets (Telegram bot token, Anthropic API key, GitHub PAT, allowlisted user IDs).
- [ ] `just deploy-vps` → wait for 6/6 healthy.
- [ ] Verify `/v1/health` (arrives in Story 2.9) + send `/ping` to the Telegram bot (arrives in Story 3.5).

### Local macOS

- [ ] Install Docker Desktop (or Colima ≥ 0.6) + git + `uv ≥ 0.5` + `just ≥ 1.14`.
- [ ] Same `.env` setup as VPS.
- [ ] `just deploy-macos` → wait for 6/6 healthy.
- [ ] Same verification.

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

## Upgrading

Released images live on GHCR (`ghcr.io/<owner>/oh-my-bmad-<service>`).
To upgrade a running deployment:

1. Edit `.env`:

       OMB_IMAGE_REGISTRY=ghcr.io/<owner>
       OMB_VERSION=0.2.0                  # or whichever tag you want

2. Pull + restart:

       docker compose pull
       docker compose up -d

Compose stops each service, pulls the new tag, and starts it with preserved
volumes. Persistent data (registry DB, event log, artifacts) survives the
upgrade.

Notes:

- **`console-cli` is published but not in compose.** The image
  (`ghcr.io/<owner>/oh-my-bmad-console-cli:<version>`) can be pulled ad-hoc
  (`docker pull ghcr.io/<owner>/oh-my-bmad-console-cli:<version>`) but isn't
  brought up by `docker compose up -d`. Story 4.6 will wire a host shim that
  ties `oh-my-bmad console <cmd>` to the image.
- **`:latest` only advances on stable semver tags.** Prerelease tags (e.g.
  `v0.2.0-rc1`, containing a `-`) publish the versioned tag but do NOT move
  `:latest` — a fork to `v0.2.0-rc1` requires setting `OMB_VERSION=0.2.0-rc1`
  explicitly.

Phase 1 uses tag-based versioning; digest-pinning + signed-image verification
land in a Phase 2 hardening story.

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
