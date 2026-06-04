# Operator runbook

First-responder runbook for when something is wrong with a running oh-my-bmad stack.
Start here before escalating to architecture docs or filing a bug.

---

## Running-state check

Run these three probes in order to establish a baseline before diving into per-service playbooks.

```sh
# 1. Are all containers up and healthy?
docker compose ps

# Expected: 6 rows, STATUS = "Up (healthy)" for all.
# registry-api, registry-state, telegram-gateway,
# orchestrator-adapter, worker-wrapper, clawhip-daemon

# 2. Does the workspace pass static checks?
just lint

# Expected: 7 sub-commands complete, exit 0.
# ruff check, ruff format --check, mypy, check_imports, check_event_registry,
# check_single_writer, secret-hygiene-precommit.

# 3. Does the Python workspace resolve cleanly?
just bootstrap-verify

# Expected: 19 workspace-member import lines then:
# ✓ bootstrap OK (19 workspace-member imports verified)
```

If all three pass, the stack is structurally healthy. A persistent failure in
`just lint` or `just bootstrap-verify` after a code change points to a broken
dependency or import regression, not a runtime failure.

---

## Phase 3 fleet MCP servers (optional stdio members)

Phase 3 (Epics 15–19) added **five fleet MCP tool servers** — `git`, `github`,
`verification`, `memory`, `artifact`. They are **not** containers and **not** public
services: each is a stdio subprocess **spawned by `worker-wrapper`** (and carried in
the same base image), so there is no new compose row, port, or Dockerfile (P3-I3).
Every one is **OFF by default** — the worker only spawns a fleet server when its
`WORKER_<SERVER>_COMMAND` is non-blank. A fresh boot with none of them set behaves
exactly as Phase 2.

### Enabling a fleet server

Set its spawn command (any non-blank value; `python` is the convention) **and**
forward its REQUIRED env. The required vars are forwarded to the subprocess via the
explicit `_ENV_ALLOWLIST` (NEVER `os.environ.copy` — no broad secret is forwarded);
they are validated at the server's `__main__`, which **exits 2** if any is missing,
so a half-configured server fails loud rather than silently misbehaving.

| Server | Enable (worker env) | REQUIRED subprocess env | Tiers |
|---|---|---|---|
| `git` | `WORKER_GIT_COMMAND=python` | `GIT_MCP_WORKTREE_ROOT`, `GIT_MCP_ACTOR_KIND`, `GIT_MCP_ACTOR_ID` | read 1 / add+commit 2 / push+rebase 3 |
| `github` | `WORKER_GITHUB_COMMAND=python` | `GITHUB_MCP_ACTOR_KIND`, `GITHUB_MCP_ACTOR_ID`, **`GITHUB_MCP_SCOPED_TOKEN`** | list/get 1 / writes 3 |
| `verification` | `WORKER_VERIFICATION_COMMAND=python` | `VERIFICATION_MCP_WORKTREE_ROOT`, `VERIFICATION_MCP_ACTOR_KIND`, `VERIFICATION_MCP_ACTOR_ID` | run_build/run_tests 2 |
| `memory` | `WORKER_MEMORY_COMMAND=python` | `MEMORY_MCP_STORE_PATH`, `MEMORY_MCP_ACTOR_KIND`, `MEMORY_MCP_ACTOR_ID` | read/search 1 / write 2 |
| `artifact` | `WORKER_ARTIFACT_COMMAND=python` | `ARTIFACT_MCP_STORE_PATH`, `ARTIFACT_MCP_ACTOR_KIND`, `ARTIFACT_MCP_ACTOR_ID` | get/list 1 / put 2 / delete 3 |

`ACTOR_KIND` is one of `operator|orchestrator|worker|system|clawhip`; `ACTOR_ID` is a
non-empty instance identifier. Tier-3 tools (`git push`/`rebase`, all `github` writes,
`artifact delete`) are **denied without a matching `approval.granted` event** — the
operator approves via the normal approval flow.

### `github` — scoped-credential setup (G-SEC-2)

`github-mcp` authenticates with **`GITHUB_MCP_SCOPED_TOKEN`**, a *narrowly-scoped*
credential — a fine-grained PAT or GitHub App installation token **scoped to the
target repo only**. The broad operator `GITHUB_TOKEN` is **forbidden** from every MCP
subprocess env (it is in `_FORBIDDEN_SECRET_ENV_VARS`; a contract test enforces its
absence). Set up:

1. Create a fine-grained PAT (GitHub → Settings → Developer settings → Fine-grained
   tokens) scoped to *only* the target repository, with the minimum permissions the
   tools need (Issues / Pull requests: read+write).
2. Provide it as `GITHUB_MCP_SCOPED_TOKEN` in the worker's env.
3. **Do NOT** reuse the operator's broad `GITHUB_TOKEN` — if it leaked from a
   subprocess, the blast radius would be the operator's entire account; the scoped
   token's leak radius is one repo.

> **Note (G-SEC-2 is half-closed):** this closes the *MCP-subprocess* half. The
> spawned `claude` agent (`claude_code_runner.py`) still receives the broad
> `GITHUB_TOKEN` for its own `git push`; migrating that to a scoped git-credential
> helper is tracked in `deferred-work.md`.

### `memory` / `artifact` — store paths + retention

Each store-backed server owns an **isolated subtree of the existing `oh-my-bmad-data`
volume** — never the registry DB (P3-I2). No new volume is required.

- **`memory`**: `MEMORY_MCP_STORE_PATH` → its own SQLite FTS5 DB file (e.g.
  `oh-my-bmad-data/memory-mcp/store.db`). WAL mode; created group-writable (0o660)
  for cross-uid recovery.
- **`artifact`**: `ARTIFACT_MCP_STORE_PATH` → its own content-store **root dir** (e.g.
  `oh-my-bmad-data/artifact-mcp/`); objects land at `objects/<hash[:2]>/<hash>`,
  content-addressed by sha256. **Retention** (optional operator policy, swept at
  startup + after each `put`): `ARTIFACT_MCP_RETENTION_MAX_BYTES` (total-size cap) and
  `ARTIFACT_MCP_RETENTION_TTL_SECONDS` (age cap) — unset ⇒ unbounded. Each retention
  eviction emits an `artifact.deleted` spine event (the only deletion path that runs
  without a Tier-3 approval — system-initiated, policy-bounded). The store is
  **regenerable build output**, deliberately NOT litestream-replicated.

### Separability check

Each fleet server is provable-optional: `tests/separability/test_s{5,6,7,8,9}_*.py`
boot a real subprocess in the SPAWNED state and confirm the worker still completes a
scripted task in the ABSENT state. To confirm a server is live in a running stack,
the worker logs `mcp_client_connected server=<name>` at startup for each spawned member.

---

## Service-down playbooks

All six services currently run a hello-world `signal.pause()` loop (Story 1.4
scaffold). Real process logic lands per the owning story listed under each
subsection. The playbooks below are valid for both the hello-world phase and
after real logic ships.

### registry-api

- **Phase-1 state:** hello-world `__main__.py`; HTTP API (`/v1/health`, `/v1/tasks`)
  arrives in Story 2.9.
- **Typical cause:** Python import error after a workspace change, or missing
  required env var in `.env`.
- **Log grep:**
  ```sh
  docker compose logs registry-api 2>&1 | grep -E '(error|Error|Traceback|import)'
  ```
- **Restart:**
  ```sh
  docker compose restart registry-api
  ```
- **Verify:**
  ```sh
  docker compose ps registry-api
  # STATUS column shows "Up (healthy)" within 30 s.
  ```

### registry-state

- **Phase-1 state:** hello-world `__main__.py`; SQLite schema + WAL writer
  arrives in Stories 2.3–2.4.
- **Typical cause:** SQLite WAL file left in an inconsistent state after an
  unclean shutdown. See [SQLite WAL recovery](#sqlite-wal-recovery) below.
- **Log grep:**
  ```sh
  docker compose logs registry-state 2>&1 | grep -E '(sqlite|database|locked|corrupt)'
  ```
- **Restart:**
  ```sh
  docker compose restart registry-state
  ```
- **Verify:**
  ```sh
  docker compose ps registry-state
  ```

### telegram-gateway

- **Phase-1 state:** hello-world `__main__.py`; aiogram webhook receiver
  arrives in Story 3.1.
- **Typical cause:** Bot token rotation — if `TELEGRAM_BOT_TOKEN` in `.env`
  is stale or incorrect, the gateway cannot authenticate with the Bot API.
- **Log grep:**
  ```sh
  docker compose logs telegram-gateway 2>&1 | grep -E '(token|Unauthorized|401|webhook)'
  ```
- **Restart:**
  ```sh
  docker compose restart telegram-gateway
  ```
- **Verify:**
  ```sh
  docker compose ps telegram-gateway
  ```

### orchestrator-adapter

- **Phase-1 state:** hello-world `__main__.py`; OMC subprocess supervision
  arrives in Story 5.10.
- **Typical cause:** The OMC subprocess fails to start (missing binary in
  `upstream/omc/`) or exits non-zero immediately after launch.
- **Log grep:**
  ```sh
  docker compose logs orchestrator-adapter 2>&1 | grep -E '(subprocess|omc|exit|killed)'
  ```
- **Restart:**
  ```sh
  docker compose restart orchestrator-adapter
  ```
- **Verify:**
  ```sh
  docker compose ps orchestrator-adapter
  ```

### worker-wrapper

- **Phase-1 state:** hello-world `__main__.py`; worker lifecycle management
  arrives in Story 5.1.
- **Typical cause:** The Node.js runtime required by clawhip-bridge-mcp fails
  to initialise (the image carries a 121 MB Node v24 binary; see
  [Exceptions](./exceptions.md#worker-wrapper-283-mb-over-ac-7-200-mb-budget)
  for the documented size deviation).
- **Log grep:**
  ```sh
  docker compose logs worker-wrapper 2>&1 | grep -E '(node|Error|ENOMEM|spawn)'
  ```
- **Restart:**
  ```sh
  docker compose restart worker-wrapper
  ```
- **Verify:**
  ```sh
  docker compose ps worker-wrapper
  ```

### clawhip-daemon

- **Phase-1 state:** hello-world `__main__.py`; clawhip client integration
  arrives in Story 2.8.
- **Typical cause:** The clawhip vendored source in `upstream/clawhip/` is
  absent or at an incompatible version after a `just sync-upstream clawhip`
  that was not followed by a contract-test re-run.
- **Log grep:**
  ```sh
  docker compose logs clawhip-daemon 2>&1 | grep -E '(clawhip|import|version|Error)'
  ```
- **Restart:**
  ```sh
  docker compose restart clawhip-daemon
  ```
- **Verify:**
  ```sh
  docker compose ps clawhip-daemon
  ```

---

## SQLite WAL recovery

The `registry-state` container mounts the `oh-my-bmad-data` named volume.
The SQLite database and its WAL files live at:

```
/var/lib/oh-my-bmad/registry/
  state.sqlite3
  state.sqlite3-wal     # write-ahead log
  state.sqlite3-shm     # shared-memory index
```

The `-wal` and `-shm` sidecar files are normal when the database is open or
was not cleanly checkpointed before container stop. Their presence after an
unclean shutdown is expected, not a sign of corruption.

**What to do after an unclean shutdown:**

1. Restart `registry-state` — SQLite's WAL auto-recovery runs on next open:
   ```sh
   docker compose restart registry-state
   docker compose ps registry-state   # wait for healthy
   ```
2. If the container stays unhealthy after restart, inspect logs for
   `"database disk image is malformed"`. A true corruption requires restoring
   from the latest backup — see [Backup / restore](./backup-restore.md).

Real snapshot capture + replay logic (including WAL checkpoint + consistent
read point) arrives in Story 2.6.

---

## Tunnel failure

The Telegram webhook requires an HTTPS public URL. oh-my-bmad does not bundle
a TLS terminator; the operator runs one of: Cloudflare Tunnel, ngrok, or a BYO
reverse proxy (set via `TUNNEL_MODE` in `.env`).

**Common symptoms:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `telegram-gateway` logs `webhook 404` | Tunnel process died or was restarted with a new URL | Restart tunnel, re-register webhook URL with Bot API |
| Tunnel process exits immediately | Auth token expired (ngrok free tier) or network ACL blocks port 7844 (cloudflared) | Re-authenticate / open firewall port |
| No Telegram messages delivered | `TUNNEL_MODE` in `.env` does not match the running tunnel type | Correct `.env`, restart gateway |
| `manifest unknown` on compose pull | `OMB_VERSION` points at an unpublished tag | Set `OMB_VERSION=dev` or a published semver |

For Cloudflare Tunnel: check `cloudflared` service logs with
`journalctl -u cloudflared -n 50`. For ngrok: the local dashboard at
`http://127.0.0.1:4040` shows tunnel status and forwarded request history.

---

## Budget override — `/approve --override budget` and its sharp edge

When a task is **blocked at the registry gate** for crossing its budget, an
operator can extend the budget and unblock it from either surface:

- **Telegram:** `/approve <task-id> --override budget`
- **console-cli:** `console approve <task-id> --override budget`

Both reach the same registry-api override branch (extends the limit, emits the
`tier3.budget_override` audit event — also registered as `budget.override`
@1.1.0 per Story 12.3 / FR68).

> **SHARP EDGE (Story 12.3, D2=(II)).** `--override budget` only works while the
> task is still `blocked`. Epic-12's `budget_supervisor` enforces the budget by
> **autonomously `SIGTERM`-ing the live subprocess** within ~5s of
> `task.budget_exceeded` — it has no awareness of override events. So if you
> override *after* the subprocess has already been terminated (task `failed`),
> the override **cannot resurrect the task**. Use `/retry` (Telegram) or
> `console retry <task-id>` to start a fresh run with the extended budget.
>
> Preventing enforcement *within* the 5-second grace window (coupling the
> supervisor to inbound override events) is **deferred to Story 12.3a** — it is
> not yet implemented. Until then, treat `--override budget` as "raise the
> ceiling, then `/retry`" for any task that has already been terminated.

### Per-task budget policy defaults (Story 12.4 / FR68a)

Each task can carry its own budget policy. Submitters may set `budget_token_limit`
and `budget_action` on `POST /v1/tasks`; when omitted, the `.env` defaults
(`OMB_DEFAULT_TASK_BUDGET_TOKENS`, `OMB_DEFAULT_TASK_BUDGET_ACTION`) are inherited.
The **token ceiling** is consumed end-to-end now: orchestrator-adapter sources it
per-task with precedence `per-task budget_token_limit > OMB_DEFAULT_TASK_BUDGET_TOKENS
> ORCHESTRATOR_TASK_TOKEN_BUDGET (legacy)`.

> **STORED-BUT-NOT-YET-CONSUMED (Story 12.3a).** The per-task `budget_action` is
> stored on the Task row and surfaced via the API, but worker-wrapper still
> enforces the **global** `OMB_DEFAULT_BUDGET_ACTION` (Story 12.2) — it does not
> yet read the per-task value. Per-task `budget_action` delivery + the
> `awaiting_approval` FSM path land together in **Story 12.3a**. Until then every
> task's effective action is necessarily `failed`.

## litestream WAL replication (optional disaster recovery)

litestream streams registry-state's SQLite WAL off-host to an S3-compatible
target so a destroyed host can be rebuilt from object storage. It is **disaster
recovery, NOT high availability** — no failover, no second live writer; recovery
is a manual stack-down procedure (Story 13.3). See
[ADR-0007](./adr/0007-litestream-wal-replication.md) for the full rationale.

**It is OFF by default.** Nothing below runs unless you opt in.

### Enable it

1. **Create a bucket** on your target (AWS S3 / Backblaze B2 / Cloudflare R2 /
   self-hosted MinIO) and an access key scoped to it
   (`PutObject`/`GetObject`/`ListBucket`).
2. **Write the config:**
   ```bash
   cp litestream.yml.example litestream.yml      # litestream.yml is .gitignored
   ```
   Uncomment ONE replica block, fill `bucket` / `region` / `endpoint`. Leave the
   access keys OUT of the file.
3. **Set credentials + path in `.env`** (gitignored — never commit creds):
   ```
   OMB_LITESTREAM_CONFIG_PATH=./litestream.yml
   LITESTREAM_ACCESS_KEY_ID=...
   LITESTREAM_SECRET_ACCESS_KEY=...
   ```
   litestream reads the two `LITESTREAM_*` vars automatically; the sidecar gets
   them via `env_file: .env`.
4. **Start the sidecar:**
   ```bash
   docker compose --profile litestream up -d litestream
   ```
   Within ~1 minute it uploads an initial snapshot, then ships WAL frames on the
   `sync-interval` (default 1s). Confirm with `docker logs omb-litestream` (look
   for `replicating to` + periodic snapshot/sync lines) and by checking objects
   appear under your bucket's `oh-my-bmad/registry-state` prefix.

### Operational notes / sharp edges

- **Credentials never touch the repo.** `litestream.yml` is gitignored and the
  keys live in `.env` (also gitignored). The committed file is only
  `litestream.yml.example`.
- **The data volume is mounted read-WRITE** for the sidecar — litestream needs
  it (it writes a `.state.sqlite3-litestream/` meta dir next to the DB and takes
  over checkpointing). This does NOT violate FR26: registry-state remains the
  sole *author* of rows; litestream only relocates already-committed WAL frames.
- **Container group / permissions.** The sidecar joins the `omb` group
  (`group_add: ["10000"]`) so it can access the 0o660 DB files. The upstream
  image runs as root by default (works); if you pin a non-root `user:`, ensure
  it is in gid 10000. *(First-live-enable verification item from ADR-0007.)*
- **Missing config sharp edge.** If `OMB_LITESTREAM_CONFIG_PATH` points at a file
  that does not exist when you enable the profile, Docker creates a *directory*
  at that path and litestream fails to parse it. Always `cp` the template first.
- **Checkpoint coexistence.** litestream disables autocheckpoint on its own
  connection and checkpoints itself; registry-state keeps its normal
  autocheckpoint. SQLite WAL locking serialises the two — worst case is a
  redundant checkpoint, never corruption. *(First-live-enable verification item.)*
- **Replication ≠ backup-of-record.** Recovery is `just restore-from-litestream`
  (Story 13.3); the hermetic restore drill runs nightly.

### Lag monitoring (`just litestream-lag-check`)

Story 13.4 (NFR-R7) adds replication-stall detection. Enable litestream's metrics
endpoint by uncommenting `addr: ":9090"` in `litestream.yml`, then run (e.g. from
cron, every ~30s):

```bash
just litestream-lag-check
```

It polls `http://<host>:9090/metrics` (`OMB_LITESTREAM_METRICS_URL` to override),
and if `litestream_sync_count` **stalls for >5 minutes** it emits a single
`replication.lagging` audit event (via the FR26-respecting flock-guarded append)
and stops re-emitting until replication recovers. `sync_count` is a ~1/s
heartbeat (it advances even when the DB is idle — empirically verified), so a
flat counter unambiguously means the sync loop stopped. The event's `signal`
distinguishes the cause (Story 13.4a):
- **`sync_stalled`** — `sync_error_count` rose during the stall → S3/network
  failures (the sidecar is trying and failing).
- **`silent_stall`** — errors stayed flat → the sync loop is **hung/frozen**
  (the dangerous silent failure; the sidecar looks alive but isn't replicating).

litestream 0.3.x exposes no direct lag-seconds gauge, so the sync-counter stall
is the signal. metrics-subscriber counts the event under
`omb_events_appended_total{event_family="replication"}`.

> The script exits 3 if registry-state holds the event-log lock (it is the live
> writer) — that is normal; the next cron tick retries.

### Disable it

```bash
docker compose stop litestream && docker compose rm -f litestream
```
Unset `OMB_LITESTREAM_CONFIG_PATH` (or just stop using `--profile litestream`).
The core stack is unaffected.

## Metrics scraping

The `metrics-subscriber` service (Story 10.3) exposes a Prometheus-format `/metrics`
endpoint on port 9090. This is **internal-only per P2-I5** — there is no host port
published to the operator's network. Scrape the metrics via:

1. **From inside the docker network** (co-located Prometheus, sidecar scraper):
   ```bash
   curl http://omb-metrics-subscriber:9090/metrics
   ```
   This works from any container on the `oh-my-bmad-net` network (defined in
   `docker-compose.yml`). Replace `omb-metrics-subscriber` with the container IP
   if running Prometheus outside compose.

2. **Via SSH tunnel to the host**:
   ```bash
   ssh -L 9090:omb-metrics-subscriber:9090 <operator@host>
   # Then, on your local machine:
   curl http://127.0.0.1:9090/metrics
   ```

3. **Via container exec** (one-off inspection):
   ```bash
   docker compose exec metrics-subscriber curl http://127.0.0.1:9090/metrics
   ```

**Metrics exposed:**

The endpoint returns Prometheus text format with gauges and counters from the JSONL
event log. Key counters for operational monitoring:

- `omb_events_appended_total{event_family="..."}` — cumulative count of events by
  family (`approval`, `task`, `deployment`, `replication`, `metadata`, etc.). Useful
  for verifying approval and budget-override events are being recorded.
- `omb_events_appended_total{event_family="replication"}` — specifically counts
  `replication.lagging` events emitted when litestream WAL sync stalls (Story 13.4).

**Healthcheck endpoint** (monitoring only, not Prometheus format):

```bash
curl http://omb-metrics-subscriber:9090/healthz
# Returns JSON: {"status": "ok", "version": "..."}
```

The `/healthz` endpoint is used by `docker compose` for the metrics-subscriber
healthcheck (Story 10.3). It is independent of `/metrics` to avoid noisy probes on
the hot-path counter endpoint.

---

## Approval signing — offline verification

Task approval decisions are cryptographically signed at decision time via HMAC-SHA256
(Story 11.1 / FR64). An operator can verify any approval offline without booting the
Platform stack, using the `just verify-approval` recipe (Story 11.4 / FR65).

### Setup: OPERATOR_HMAC_KEY

The key is a UTF-8-encoded secret string, minimum 32 bytes, held in the `OPERATOR_HMAC_KEY`
environment variable. Generate it once during initial setup:

```bash
openssl rand -base64 32 > /secure/path/operator_hmac_key.txt
export OPERATOR_HMAC_KEY="$(cat /secure/path/operator_hmac_key.txt)"
```

Store the key file offline in a secure location (not in the repo, not in `.env`).
When the key rotates (see [Rotation](#key-rotation) below), the previous key must be
retained for audit-window duration so pre-rotation approvals remain verifiable.

### Verify an approval — basic usage

```bash
just verify-approval <EVENT_ID>
```

- **EVENT_ID**: The UUID of the `task.approval_signed` event (Story 11.1). This is
  the event that records the operator's decision; it is distinct from the sibling
  `approval.granted` event (both are emitted in sequence).
- **Default log directory**: `/var/lib/oh-my-bmad/registry/events` (the live JSONL log).
- **Requires**: `OPERATOR_HMAC_KEY` set in the current shell environment.

**Example output (match)**:
```
✓ Signature match
  Event ID: 01abc...xyz789
  Event type: task.approval_signed
  Task ID: task-123
  Action: approve
  Actor: alice
  Decided at: 2026-06-03T15:30:45.123Z
  Stored HMAC: a1b2c3d4e5f6...
  Recomputed: a1b2c3d4e5f6...
```

**Exit code 0** = match; exit code 1 = mismatch; other codes indicate errors (see
[Investigation](#investigation) below).

### Verify from an archive (offline)

To verify an approval from a frozen backup (e.g., after a restore):

```bash
just verify-approval <EVENT_ID> /path/to/archive/events
```

- **Log directory**: Points to the archived JSONL files (not the live directory).
  Useful for audit / forensic scenarios.

### Verify with a prior key (pre-rotation approvals)

If the approval was signed before a key rotation, you must supply the prior key file:

```bash
just verify-approval <EVENT_ID> /var/lib/oh-my-bmad/registry/events --key-file /secure/path/prior_key.txt
```

The prior key file content is treated as UTF-8 and trailing whitespace is stripped
(so `echo $KEY > key.txt` works). The key must be at least 32 bytes.

**Finding the prior key**: When you rotated the key, a `key.rotated` event was
emitted. Inspect it to find the `previous_key_fingerprint` (16 hex chars):

```bash
grep '"type":"key.rotated"' /var/lib/oh-my-bmad/registry/events/*.jsonl
# Output includes: "previous_key_fingerprint": "a1b2c3d4e5f6ghij"
```

The fingerprint is a one-way SHA-256 hash of the prior key; it cannot be reversed to
the original key. You must have retained the prior key file separately.

### Machine-readable output (JSON)

For scripting, use the `--json` flag:

```bash
just verify-approval <EVENT_ID> /var/lib/oh-my-bmad/registry/events --json
```

Returns structured JSON with fields: `status`, `reason`, `event_id`, `task_id`,
`action`, `decided_at`, `actor_id`, `stored_hmac`, `recomputed_hmac`,
`investigation_steps`.

### Investigation: mismatch or error

The verifier emits investigation steps when verification fails. Common scenarios:

| Reason | Cause | Next step |
|--------|-------|-----------|
| `signature_mismatch` | Approval payload was tampered with, or wrong key | Verify `OPERATOR_HMAC_KEY` matches the key in effect when the event was signed. Find the corresponding `key.rotated` event to identify which key was current. |
| `event_not_found` | Event ID does not exist in the log | Confirm the event ID is correct (UUIDv7, not a sibling event). Check the date range of the archive. |
| `key_missing` | `OPERATOR_HMAC_KEY` not set and `--key-file` not passed | Set env var or pass `--key-file PATH` to offline key file. |
| `key_too_short` | Key is <32 bytes | Regenerate per FR64. |

### Key rotation

When you rotate the `OPERATOR_HMAC_KEY`, the Platform automatically emits a `key.rotated`
event on next registry-api boot (Story 11.5 / FR65a). The rotation detector:

1. Computes the fingerprint of the supplied current key.
2. Reads the most-recent `key.rotated` event from the JSONL log.
3. Compares fingerprints. If different, emits `key.rotated` before accepting requests.

**Operator procedure**:

1. Retain the prior key file (e.g., rename to `operator_hmac_key_2026-06-03.txt`).
2. Generate a new key:
   ```bash
   openssl rand -base64 32 > /secure/path/operator_hmac_key_new.txt
   export OPERATOR_HMAC_KEY="$(cat /secure/path/operator_hmac_key_new.txt)"
   ```
3. Restart registry-api:
   ```bash
   docker compose restart registry-api
   ```
   The rotation detector runs synchronously in the lifespan startup; registry-api
   will not serve requests until the `key.rotated` event is persisted.
4. Verify the rotation:
   ```bash
   docker compose logs registry-api 2>&1 | grep "key.rotated"
   ```

**Consequences**:

- Pre-rotation approvals remain verifiable via the archived prior key (FR65a).
- The `key.rotated` event is audit-logged (immutable, signed along with future approvals
  via the new key).
- The previous key's fingerprint is recorded in `key.rotated.previous_key_fingerprint`
  so operators can track which approval was signed with which key.

---

## Forward-referenced scenarios

These failure modes are spec'd but the enforcement logic does not exist yet.
When a page fires that matches these descriptions, refer to the owning story.

- **Worktree lock stuck** — a task's worktree lock is held past its TTL,
  blocking subsequent tasks on the same repo. Arrives in Story 5.3.
- **Per-task budget exceeded** — a running task crosses its token or cost
  budget and should be halted. Enforcement arrives in Stories 5.15 + 6.11.
- **License scan flagged** — a dependency introduced by a task has a
  non-approved SPDX identifier. Scan + blocking arrives in Story 6.9.

---

## See also

- [Schema evolution](./schema-evolution.md) — event-log schema versioning + migrator procedure.
- [Backup / restore](./backup-restore.md) — volume snapshot + fresh-host restore.
- [Testing guide](./testing-guide.md) — how to run the regression suite after a change.
