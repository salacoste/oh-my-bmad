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
| `registry/events/current.jsonl` | Append-only event log (JSONL, one event per line) | Story 2.4 |
| `artifacts/<TASK_ID>/` | Per-task artifact blobs (patches, PR drafts, logs) | Story 2.6 |

**What is NOT backed up:** `.env` (secrets live outside the volume),
`upstream/` vendored source (re-fetchable via `just sync-upstream`), and Python
virtual environments (re-creatable via `uv sync`).

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
→ stopping stack…
→ taring volume to oh-my-bmad-backup-20260115T143022Z.tgz…
→ restarting stack…
✓ backup complete: oh-my-bmad-backup-20260115T143022Z.tgz
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
delete after a confirmed transfer.

### S3 / Backblaze B2

```sh
# AWS S3 (requires aws CLI + configured credentials):
aws s3 cp oh-my-bmad-backup-<TIMESTAMP>.tgz s3://<BUCKET>/oh-my-bmad/

# Backblaze B2 (requires b2 CLI):
b2 upload-file <BUCKET> oh-my-bmad-backup-<TIMESTAMP>.tgz oh-my-bmad/oh-my-bmad-backup-<TIMESTAMP>.tgz
```

Exact credentials and bucket configuration are outside the scope of this guide.
Both providers offer server-side encryption at rest; enable it for any backup
that may include event payloads containing task details.

---

## Restore to a fresh host

Follow these steps on a host with no existing oh-my-bmad data.

1. **Clone and install prerequisites:**
   ```sh
   git clone <THIS_REPO_URL> oh-my-bmad && cd oh-my-bmad
   uv sync --dev
   uv run pre-commit install
   ```
   See [`docs/deployment/vps.md`](./deployment/vps.md) or
   [`docs/deployment/macos.md`](./deployment/macos.md) for full
   prerequisite installation steps.

2. **Create the named volume:**
   ```sh
   docker volume create oh-my-bmad_oh-my-bmad-data
   ```

3. **Extract the backup archive into the volume:**
   ```sh
   docker run --rm \
     -v oh-my-bmad_oh-my-bmad-data:/dest \
     -v "${PWD}:/src" \
     alpine:3 \
     tar -xzf "/src/<ARCHIVE_FILE>" -C /dest
   ```
   Replace `<ARCHIVE_FILE>` with the actual filename, e.g.
   `oh-my-bmad-backup-20260115T143022Z.tgz`.

4. **Restore secrets:** The archive does not contain `.env`. Copy and edit it:
   ```sh
   cp .env.example .env
   $EDITOR .env   # fill in TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, etc.
   ```
   See the `.env` field reference in
   [`docs/deployment/vps.md`](./deployment/vps.md#configure-env).

5. **Start the platform:**
   ```sh
   just deploy-vps      # Linux / VPS
   # or
   just deploy-macos    # local macOS
   ```

6. **Verify:**
   ```sh
   docker compose ps
   # Expected: 6/6 containers Up (healthy) within ~60 s.
   ```

---

## Backup cadence

- **Weekly:** run `just backup` and copy the archive off-host. Aligns with
  NFR-SC2 (weekly snapshot cadence).
- **Monthly:** rotate old event-log archives from `registry/events/` inside
  the volume. The event log grows unboundedly until Story 2.6 (snapshot
  capture) lands a compaction mechanism; until then, monthly manual rotation
  prevents unbounded disk growth.
- **Before any migration or upgrade:** always run `just backup` before
  `docker compose run --rm migrator ...` or `docker compose pull && up -d`.

---

## Quarterly restore drill

A backup that has never been tested is not a backup — it is an untested
hypothesis. Run a restore drill quarterly:

1. Provision a scratch VPS or local VM (Ubuntu 24.04 recommended).
2. Copy a recent backup archive to the scratch host.
3. Follow the [Restore to a fresh host](#restore-to-a-fresh-host) steps above.
4. Verify `docker compose ps` shows 6/6 healthy.
5. (Optional) run `just bootstrap-verify` + `just test` to confirm the
   workspace is intact.
6. Tear down the scratch host.

The backup archive is the recovery asset only after a successful drill
confirms it round-trips cleanly. Document the drill date and result in your
operational log.

---

## See also

- [Operator runbook](./operator-runbook.md) — SQLite WAL recovery + per-service restart procedures.
- [VPS deployment](./deployment/vps.md) — full prerequisite installation for a fresh host.
- [macOS deployment](./deployment/macos.md) — macOS-specific deployment steps.
