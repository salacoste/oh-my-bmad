# Deployment guide (entry point)

This file is the AI-context entry into deployment. The platform-detailed runbooks are in [deployment/vps.md](./deployment/vps.md) (Linux VPS) and [deployment/macos.md](./deployment/macos.md) (local macOS).

For operating the deployed stack (paging, on-call playbooks, recovery), see [operator-runbook.md](./operator-runbook.md).

## What deploys

The Compose stack brings up **6 long-running services**:

1. `registry-api`
2. `registry-state` (single writer; reaches healthy first)
3. `telegram-gateway`
4. `worker-wrapper`
5. `orchestrator-adapter`
6. `clawhip-daemon`

**Not deployed via Compose:**
- The 3 MCP servers — they're orchestrator-spawned subprocesses, not containers.
- `console-cli` — published as a GHCR image but invoked ad-hoc on the host. See [exceptions.md](./exceptions.md).

## Image registry

Released images live on GHCR: `ghcr.io/<owner>/oh-my-bmad-<service>:<version>`.

`OMB_VERSION` in `.env` drives image tags. Pre-release tags (`v0.X.Y-rcN`) publish the versioned tag but do NOT move `:latest`. **Tag immutability** is required in GHCR settings — "allow tag overwrite" must be disabled until digest-pinning lands in Phase 2.

## VPS quickstart (Linux)

```sh
# Ubuntu 24.04 LTS recommended; ≥ 2 GB RAM; public IPv4
# Install Docker Engine ≥ 24, Docker Compose v2.24+, git, uv ≥ 0.5, just ≥ 1.14
git clone <repo> oh-my-bmad && cd oh-my-bmad
cp .env.example .env
$EDITOR .env                   # Telegram bot token, Anthropic API key, GitHub PAT, allowlisted user IDs, tunnel choice
just deploy-vps                # wait for 6/6 healthy
# Verify
curl -fsS http://localhost:<port>/v1/health
# Send /ping to the Telegram bot
```

Tunnel choice for the Telegram webhook ingress: Cloudflare Tunnel (default), ngrok, or BYO reverse proxy. See [deployment/vps.md](./deployment/vps.md) for the full setup.

## macOS quickstart (local development host)

```sh
# Docker Desktop (or Colima ≥ 0.6), git, uv ≥ 0.5, just ≥ 1.14
git clone <repo> oh-my-bmad && cd oh-my-bmad
cp .env.example .env
$EDITOR .env
just deploy-macos              # uses docker-compose.macos.yml overlay (bind-mounts permitted)
```

Bind mounts to host paths are permitted **only** in `docker-compose.macos.yml`. The base `docker-compose.yml` uses the named volume `oh-my-bmad-data` exclusively.

## Compose discipline

- `depends_on` uses the mapping form with `condition: service_healthy` — bare list form is a start-order hint only, NOT a health gate, and is rejected in review.
- `registry-state` reaches healthy first; its healthcheck validates the WAL file exists and `/readyz` returns 200.
- `restart: unless-stopped` for all long-running services; `console-cli` is the only one with `restart: "no"` (it's not in compose `up`).
- Environment from `.env` only; no inline `environment:` blocks except non-secret defaults documented in `.env.example`.
- Named volumes only (`oh-my-bmad-data`). Bind mounts in `docker-compose.macos.yml` only.
- MCP stdio servers MUST NOT appear in `docker-compose.yml` — they are orchestrator-spawned subprocesses.

See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 3 for the full docker-compose ruleset.

## Upgrading

```sh
# Edit .env
OMB_IMAGE_REGISTRY=ghcr.io/<owner>
OMB_VERSION=0.X.Y

docker compose pull
docker compose up -d
```

Volumes survive; persistent data (DB, event log, artifacts) is preserved. For schema migrations within a major, see [schema-evolution.md](./schema-evolution.md). For breaking changes, the one-shot migrator container:

```sh
docker compose run --rm migrator <from-version>-to-<to-version>
```

## Rollback

**Decision criteria:** initiate rollback if the post-deploy smoke fails OR error rate in the first 10 min exceeds baseline by >2×.

**Path:**
1. Revert `OMB_VERSION` in `.env` to the prior tag.
2. `docker compose pull && docker compose up -d`.
3. Volumes survive — schema rollback follows [schema-evolution.md](./schema-evolution.md).

The two most recent release tags are pre-pulled on runner startup so rollback isn't cold-cache-bound.

See `just rollback-drill` for the nightly automated rehearsal (Cat 6).

## Backups

`just backup` snapshots the `oh-my-bmad-data` named volume to a local `.tgz` via a throwaway `alpine` container (works identically on Linux and macOS):

```sh
just backup                       # oh-my-bmad-backup-<utc-ts>.tgz
just backup pre-upgrade           # oh-my-bmad-backup-<utc-ts>-pre-upgrade.tgz
```

The optional suffix must match `[A-Za-z0-9._-]+`. The recipe stops the stack, tars the volume contents, then brings the stack back up (even if tar fails — restart runs in an `EXIT` trap).

Restore: see [backup-restore.md](./backup-restore.md).

## CI/CD

- `ci.yml` — PR-gate test + lint on every push / PR to `main`.
- `nightly.yml` — full test matrix + slow tests + `rollback-drill` at 03:00 UTC.
- `release.yml` — release build + GHCR push on `v*` tag push.

See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 6 for required-status-checks list, release procedure, and the break-glass policy for incident bypass.

## Phase-2 hardening (deferred — do not pre-implement)

- Digest pinning + `cosign` + SLSA L2 attestation + CycloneDX SBOM on every published image. Until adopted, **tag immutability in GHCR is the only line of defense.**
- Self-hosted runners — Phase 2 ADR required (attack-surface considerations).
- OpenTelemetry / Prometheus exporters — explicit ban in Phase 1 (placeholder spans create false coverage signals).

## Cross-references

- [deployment/vps.md](./deployment/vps.md) — full Linux VPS runbook.
- [deployment/macos.md](./deployment/macos.md) — full macOS host runbook.
- [operator-runbook.md](./operator-runbook.md) — paging conditions + per-service recovery.
- [backup-restore.md](./backup-restore.md) — volume snapshot + off-host rsync + fresh-host restore.
- [schema-evolution.md](./schema-evolution.md) — schema migration workflow.
- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) — Cat 3 (compose rules) + Cat 6 (release + rollback + break-glass).
