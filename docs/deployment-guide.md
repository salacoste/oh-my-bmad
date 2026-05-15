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

## Verifying releases

Phase 2 Epic 8 (supply-chain hardening, Stories 8.1–8.5) ships every Platform image with **three independent cryptographic attestations**: cosign keyless signature, SLSA L2 build provenance, and CycloneDX SBOM. Before every `docker compose pull`, the operator runs `just verify-images` to enforce all three at the deploy boundary. **Pulling without verification is a supply-chain regression**; the gates are cheap (~3s per image) and document-driven (recoverable error messages).

### One-time setup: install cosign

`just verify-images` requires the `cosign` binary locally. Install one of:

```sh
# macOS (Homebrew)
brew install cosign

# Ubuntu / Debian (PPA may be needed on older distros)
apt install cosign

# Generic Linux / fallback (download signed binary from sigstore releases)
curl -L https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 -o cosign
chmod +x cosign && sudo mv cosign /usr/local/bin/
```

Verify the install: `cosign version` should print v2.x.

### Per-release workflow

1. **Find the release digests.** Every release published to GHCR includes `<image>@sha256:...` references in the GitHub release notes. Copy the digest for each of the 8 images.
2. **Update `.env`.** Populate `OMB_IMAGE_DIGEST_<service>` for all 8 services (base + 7 from the matrix):

   ```sh
   OMB_GHCR_OWNER=salacoste                          # override only if you forked + re-published
   OMB_IMAGE_DIGEST_base=sha256:abc123...
   OMB_IMAGE_DIGEST_registry_api=sha256:def456...
   OMB_IMAGE_DIGEST_registry_state=sha256:...
   OMB_IMAGE_DIGEST_telegram_gateway=sha256:...
   OMB_IMAGE_DIGEST_orchestrator_adapter=sha256:...
   OMB_IMAGE_DIGEST_worker_wrapper=sha256:...
   OMB_IMAGE_DIGEST_clawhip_daemon=sha256:...
   OMB_IMAGE_DIGEST_console_cli=sha256:...
   ```

3. **Run verification.** `just verify-images` iterates over the 8 images and runs three cosign checks per image:

   ```sh
   just verify-images
   ```

   - Exit 0 = green: safe to proceed with `docker compose pull`.
   - Exit 1 = any verification failed: **do NOT pull**. Triage per the failure-mode table below.

4. **On green, deploy.**

   ```sh
   docker compose pull
   docker compose up -d
   ```

### Failure-mode triage

If `just verify-images` reports any failure, the recipe identifies **which image** and **which attestation type** failed. Map to the owning Phase 2 story:

| Failure type | Owning story | Likely cause | Fix-forward |
|---|---|---|---|
| `cosign verify (signature) FAILED` | Story 8.3 | Image not signed at all (release published before Story 8.3 landed), OR signature certificate identity doesn't match canonical workflow (fork-spoofing attempt, or repo rename) | Re-tag a release on the canonical repo; for spoofing attempts, do not deploy |
| `cosign verify-attestation slsaprovenance FAILED` | Story 8.2 | SLSA attestation missing (Sigstore Fulcio outage during release), or attestation cert identity doesn't match canonical workflow | Re-tag once Sigstore recovers (https://status.sigstore.dev); rerun is independent of signing |
| `cosign verify-attestation cyclonedx FAILED` | Story 8.4 | SBOM attestation missing (Sigstore outage, or `cosign attest` step failed mid-release), or empty SBOM file caused signed-but-empty attestation | Re-run cosign attest step independently against the published image digest from a local cosign install (cosign attest is digest-bound, not workflow-bound) |
| `OMB_IMAGE_DIGEST_<service> not set in .env` | (operator config) | Operator hasn't populated digest entries for the current release | Update `.env` per the release-notes digest list and re-run |

For the 8 possible terminal states across `{signature} × {SLSA} × {SBOM-attest}`, see [`docs/adr/0008-cosign-slsa-sbom.md`](./adr/0008-cosign-slsa-sbom.md) §"Failure asymmetry across the supply-chain triumvirate (F12)" — every state has a documented operator action.

### Recording a verification failure

When `just verify-images` reports a failure (Story 8.5), append a structured `deployment.signature_rejected` event to the audit trail BEFORE re-running verification. Epic 11's `just verify-approval` (a future capability) will replay these events to compute supply-chain rejection history; emitting the event now means later audits can see what the operator caught and acted on.

```sh
# Replace the digest below with the actual sha256 value from your release
# notes (64 hex chars including the `sha256:` prefix).
uv run python scripts/emit_signature_rejected.py \
  --image ghcr.io/salacoste/oh-my-bmad-registry-api \
  --digest sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --attestation-type signature \
  --error-message "no matching signatures: cert subject doesn't match" \
  --omb-version v0.1.5 \
  --ghcr-owner salacoste
```

The helper writes a single envelope to today's daily-log file (`${EVENT_LOG_DIR}/<YYYY-MM-DD>.jsonl`, defaults to `/var/lib/oh-my-bmad/events`) and prints the new event-id on stdout. All 6 args are required; `--attestation-type` accepts `signature | slsaprovenance | cyclonedx` matching the three triage rows above. `--operator-id` is optional (reserved for Epic 11's HMAC-keyed non-repudiation).

**Live-stack invocation refuses.** If `registry-state` is the live writer (Platform stack is running), the helper acquires an exclusive `flock` on the daily-log file with `LOCK_NB` — contention causes the helper to abort with exit code 3 and a recoverable message naming the held path. This is defense-in-depth for FR26 single-writer; the expected workflow runs the helper while the stack is down (operator runs `verify-images` BEFORE `docker compose up`).

### Sigstore outage policy

If Sigstore Fulcio (cert minting) or Rekor (transparency log) is unreachable, `just verify-images` fails for the affected attestation type. **This is intentional, not a gap** — silently shipping unverified images would defeat the entire supply-chain triumvirate. Monitor https://status.sigstore.dev during stuck releases; verification typically resumes once Sigstore recovers (usually <1 hour for transient outages).

See [`docs/adr/0008-cosign-slsa-sbom.md`](./adr/0008-cosign-slsa-sbom.md) §"Operational policy notes" for the full outage + tag-retry + duplicate-attestation policy.

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
