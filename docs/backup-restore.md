# Backup / restore

How to back up and restore the oh-my-bmad platform data volume. The platform
stores all persistent state — task registry, event log, artifacts — in a single
named Docker volume. A tarball of that volume is the complete backup artifact.

---

## What is backed up

The `oh-my-bmad_oh-my-bmad-data` named Docker volume contains:

| Path inside volume | Content | Added |
|--------------------|---------|-------|
| `registry/state.sqlite3` | Task registry SQLite database (+ WAL sidecars) | Story 2.3 |
| `registry/events/*.jsonl` | Hot append-only event-log segments (JSONL, one event per line) | Story 2.4+ |
| `registry/events/lifecycle-manifest.json` | Optional archive manifest for replay lifecycle, if operator stores it in-volume | Phase 13 |
| `artifacts/<TASK_ID>/` | Per-task artifact blobs (patches, PR drafts, logs) | Story 2.6 |

**What is NOT backed up:** `.env` (secrets live outside the volume),
`upstream/` vendored source (re-fetchable via `just sync-upstream`), and Python
virtual environments (re-creatable via `uv sync`).

Phase 13 note: replay archives may live outside the hot volume depending on operator policy. If `REPLAY_ARCHIVE_MANIFEST` points outside `oh-my-bmad-data`, back up the manifest and every referenced archive segment alongside the volume tarball. Replay validates `sha256`, so a missing or modified archive segment fails closed.

---

## `just backup`

```sh
just backup              # → oh-my-bmad-backup-<utc-ts>.tgz
just backup pre-upgrade  # → oh-my-bmad-backup-<utc-ts>-pre-upgrade.tgz
```

The optional suffix must match `[A-Za-z0-9._-]+`. Anything else causes the
recipe to exit with a validation error before touching the stack.

### What the recipe does

1. **Validates the suffix** (if provided) against the allowed character class.
2. **Stops the stack** via `docker compose down` — macOS overlay bind-mounts
   (added by `docker-compose.macos.yml`) are preserved by stopping compose
   rather than removing volumes.
3. **Tars the volume** via a throwaway Alpine container:
   ```sh
   docker run --rm \
     -v oh-my-bmad_oh-my-bmad-data:/source:ro \
     -v "${PWD}:/dest" \
     alpine:3 \
     tar -czf "/dest/<archive>" -C /source .
   ```
4. **Restarts the stack** in an `EXIT` trap — the stack comes back up even if
   tar fails, so an interrupted backup never leaves the platform down.
5. **Timestamps** the archive to UTC second-precision so multiple same-day
   backups do not overwrite each other.

### Example output

```
→ stopping stack
→ archiving volume oh-my-bmad_oh-my-bmad-data → /path/oh-my-bmad-backup-2026-01-15T143022Z.tgz
→ restarting stack
✓ backup written to /path/oh-my-bmad-backup-2026-01-15T143022Z.tgz
```

---

## Off-host copy

Copy the `.tgz` to a different host or object storage after each backup.

### rsync to a remote host

```sh
rsync -avz oh-my-bmad-backup-*.tgz <USER>@<BACKUP_HOST>:~/backups/
```

Replace `<USER>` and `<BACKUP_HOST>` with your backup server credentials.
The glob copies all local backup archives; add `--remove-source-files` to
delete after a confirmed transfer. Never pass `--remove-source-files` until
you've verified the destination has a readable copy.

### S3 / Backblaze B2

```sh
# AWS S3 (requires aws CLI + configured credentials):
aws s3 cp oh-my-bmad-backup-2026-01-15T143022Z.tgz s3://<BUCKET>/oh-my-bmad/

# Backblaze B2 (requires b2 CLI):
b2 upload-file <BUCKET> oh-my-bmad-backup-2026-01-15T143022Z.tgz oh-my-bmad/oh-my-bmad-backup-2026-01-15T143022Z.tgz
```

Exact credentials and bucket configuration are outside the scope of this guide.
Both providers offer server-side encryption at rest; enable it for any backup
that may include event payloads containing task details.

---

## Restore to a fresh host

The restore path depends on the target platform because the volume shape
differs (see `docker-compose.yml` vs `docker-compose.macos.yml`).

### Linux (named volume)

1. Install prerequisites (see `docs/deployment/vps.md`).
2. Clone the repo + copy `.env.example → .env` + edit secrets.
3. Create the volume Docker Compose expects:

       docker volume create oh-my-bmad_oh-my-bmad-data

4. Extract the backup into it:

       docker run --rm \
           -v oh-my-bmad_oh-my-bmad-data:/dest \
           -v "${PWD}:/src" \
           alpine:3 \
           tar -xzf "/src/<ARCHIVE_FILE>" -C /dest

5. `just deploy-vps` (includes `build-base` + sync).
6. Verify `docker compose ps` → core services healthy.

### macOS (bind-mount overlay)

1. Install prerequisites (see `docs/deployment/macos.md`).
2. Clone the repo + copy `.env.example → .env` + edit secrets.
3. Create the bind-mount target and extract directly into it:

       mkdir -p "${HOME}/.oh-my-bmad"
       tar -xzf "<ARCHIVE_FILE>" -C "${HOME}/.oh-my-bmad"

4. `just deploy-macos` (includes `build-base` + overlay merge + mkdir).
5. Verify `docker compose ps` → core services healthy.

Note: `.env` is NOT restored from the archive — secrets must be re-entered
manually. Archive only contains the `oh-my-bmad-data` volume (registry DB,
event log, artifacts).

---

## Restore from a litestream replica (continuous DR)

This is the **continuous** counterpart to the snapshot `just backup`/restore
above. If you enabled the litestream sidecar (Story 13.1/13.2 — see
[ADR-0007](./adr/0007-litestream-wal-replication.md) and the operator-runbook
litestream section), the registry-state SQLite WAL is streamed off-host with a
seconds-scale RPO. `just restore-from-litestream` rebuilds the
`oh-my-bmad-data` volume from that replica.

> **Replication ≠ HA.** This is an operator-initiated, **stack-down** recovery —
> there is no failover. Bring the lost/old host down before recovering; never run
> two Platforms against one replicated database.

### `just restore-from-litestream`

```bash
# Prereqs: litestream.yml filled in + LITESTREAM_ACCESS_KEY_ID/SECRET in env/.env
# (the SAME config the sidecar replicates with — the bucket/key live there).
just restore-from-litestream                 # uses ./litestream.yml
just restore-from-litestream config=path/to/litestream.yml
```

What the recipe does (DESTRUCTIVE — it replaces the live volume):

1. **Stops** the stack (`docker compose down`).
2. **Empties** the detected `*_oh-my-bmad-data` volume and recreates the
   `registry/` dir at `2775` owned by the `omb` group (gid 10000) so
   registry-state (uid 10002) can write.
3. **Restores** `state.sqlite3` from the litestream replica's latest generation
   (`litestream restore -config litestream.yml …` in a throwaway container — the
   config-based form, latest generation by default).
4. **Restarts** the stack; registry-state fixes `state.sqlite3` to `0o660` on
   startup (Story 11.3.12).
5. **Verifies** the workspace resolves (`just bootstrap-verify`). After it
   returns, confirm the stack reaches healthy: `docker compose ps`.

### Hermetic drill — `just litestream-restore-drill`

A no-cloud-credentials proof of the restore mechanism, runnable locally and in
`nightly.yml`:

```bash
just litestream-restore-drill
```

It seeds a WAL SQLite db, replicates to a local **file** replica, simulates host
loss (wipes the db + meta), runs `litestream restore`, and asserts
`PRAGMA integrity_check = ok` plus the exact recovered row count. This exercises
the same litestream restore code path as `restore-from-litestream`, minus the S3
transport (the transport is your own bucket — validate it live once per
ADR-0007's first-enable checklist). The drill runs nightly
(`.github/workflows/nightly.yml`).

---

## Backup cadence

- **Weekly:** run `just backup` and copy the archive off-host. Aligns with
  NFR-SC2 (weekly snapshot cadence).
- **Monthly:** rotate old event-log archives from `registry/events/` inside
  the volume. The event log grows unboundedly until Story 2.6 (snapshot
  capture) lands a compaction mechanism; until then, monthly manual rotation
  prevents unbounded disk growth.
- **Before any migration or upgrade:** always run `just backup` before
  running the migrator (see [Schema evolution](./schema-evolution.md)) or
  `docker compose pull && up -d`.

---

## Quarterly restore drill

A backup that has never been tested is not a backup — it is an untested
hypothesis. Run a restore drill quarterly:

1. Provision a scratch VPS or local VM (Ubuntu 24.04 recommended).
2. Copy a recent backup archive to the scratch host.
3. Follow the [Restore to a fresh host](#restore-to-a-fresh-host) steps above.
4. Verify `docker compose ps` shows core services healthy.
5. (Optional) run `uv sync --dev` then `just bootstrap-verify` + `just test`
   to confirm the workspace is intact.
6. Tear down the scratch host.

The backup archive is the recovery asset only after a successful drill
confirms it round-trips cleanly. Document the drill date and result in your
operational log.

---

## See also

- [Operator runbook](./operator-runbook.md) — SQLite WAL recovery + per-service restart procedures.
- [VPS deployment](./deployment/vps.md) — full prerequisite installation for a fresh host.
- [macOS deployment](./deployment/macos.md) — macOS-specific deployment steps.

## Deployment change readiness cross-reference

For Story 131.4 deployment change-control readiness, production deployment
profiles must use the digest-pinned recipes `just deploy-vps-digest` or
`just deploy-macos-digest` after `just backup`, release digest verification,
and rollback-profile review. Tag-based deployment recipes remain local/dev or
deprecated production paths only.
