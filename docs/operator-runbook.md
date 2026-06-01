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

# Expected: 13 workspace-member import lines then:
# ✓ bootstrap OK (13 workspace-member imports verified)
```

If all three pass, the stack is structurally healthy. A persistent failure in
`just lint` or `just bootstrap-verify` after a code change points to a broken
dependency or import regression, not a runtime failure.

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
