---
id: ADR-0007
status: accepted
date: 2026-06-01
supersedes: null
---

# ADR-0007: litestream WAL replication — disaster recovery, NOT high availability

## Status

**Accepted** — 2026-06-01. Authored alongside Story 13.1 as the opening
artefact of Epic 13 (litestream WAL replication / FR69–FR71a + NFR-R7). This
ADR is one of the five Phase-2 forward-referenced ADR acceptance-gate items
declared in [ADR-0003](./0003-phase-2-gate.md) (ADR-0004 through ADR-0008). It
MUST be `accepted` before any litestream-touching story merges to `main`, and
the Epic 13 acceptance gate requires the explicit **"replication ≠ HA"** framing
recorded below.

## Context

oh-my-bmad's durable state is a single-writer SQLite database
(`/var/lib/oh-my-bmad/registry/state.sqlite3`, FR26 single-writer owned by
registry-state) in WAL mode, plus the append-only JSONL event log. Today the
only recovery story is `just backup` (volume snapshot) + `just restore` — a
point-in-time copy taken on the operator's cadence. Between snapshots, an
unrecoverable host failure loses every event since the last snapshot.

Phase-2 FR69–FR71a + NFR-R7 add **continuous** off-host replication of the
SQLite WAL stream to an operator-configured S3-compatible endpoint (S3, B2, R2,
MinIO), so that a destroyed host can be rebuilt from object storage with a
recovery-point objective of seconds-to-a-minute rather than
since-last-snapshot. [litestream](https://litestream.io) is the chosen tool: it
tails the WAL of a live SQLite database and ships frames to object storage,
with a `litestream restore` inverse that rebuilds the database file.

The architecturally critical question this ADR settles: **what does litestream
replication give us, and — more importantly — what does it NOT give us?**

## Decision

### 1. litestream is DISASTER RECOVERY, not HIGH AVAILABILITY. (replication ≠ HA)

This is the load-bearing decision of Epic 13 and the framing the acceptance
gate requires.

- **What it IS:** an asynchronous, off-host **copy** of the WAL stream enabling
  *cross-host disaster recovery* — rebuild a fresh host from object storage
  after the primary host is lost. RPO ≈ the replication interval (seconds).
- **What it is NOT:** high availability. There is **no automatic failover**, no
  hot standby serving reads/writes, no quorum, no consensus, and **no second
  live writer**. The replica in object storage is *data at rest*, not a running
  Platform. Recovery is an **operator-initiated, manual** procedure
  (`just restore-from-litestream`, Story 13.3) that stops the stack, recreates
  the volume, restores, and restarts.
- **FR26 single-writer is PRESERVED — with a precise caveat.** FR26 means
  registry-state is the sole **author of application rows**. litestream does NOT
  author rows, but it is *not* a pure reader either: it disables SQLite
  autocheckpoint (`PRAGMA wal_autocheckpoint=0`) and **takes over checkpointing**,
  issuing its own `wal_checkpoint` which physically writes the `.db` + `-wal`/
  `-shm` files (it relocates already-committed WAL frames into the main file —
  it never originates data). This is the standard, intended litestream model and
  is safe under SQLite WAL (checkpoints are lock-coordinated; one writer of new
  data at a time). So FR26 holds at the application layer (registry-state is the
  only INSERT/UPDATE source) while litestream co-manages the WAL/checkpoint
  lifecycle. The consequence for deployment is that litestream needs **write**
  access to the database directory (see §3). Running two *Platforms* (two
  row-authors) against one replicated database would corrupt it — so recovery
  brings the old host DOWN before the new one comes UP; we never run primary +
  replica live simultaneously.

Operators MUST NOT treat litestream as a way to run a warm second site. Any
future true-HA design (multi-writer, failover) is out of scope for Phase 2 and
would require a different substrate (this ADR does not authorise it).

### 2. The sidecar is OPTIONAL and OFF by default.

Replication is opt-in. The litestream service is gated behind a docker-compose
`profiles: ["litestream"]` tag (mirroring the migrator pattern) AND activated by
the operator setting `OMB_LITESTREAM_CONFIG_PATH` to a filled-in `litestream.yml`
(Story 13.2). With neither set, the core stack runs unchanged — replication adds
zero default footprint, matching the "doesn't depend on any other Phase-2 epic;
orthogonal" placement of Epic 13.

### 3. The sidecar mounts the data volume READ-WRITE (it must).

litestream **requires write access to the database directory** — a `:ro` mount
would break replication. Two concrete reasons (verified against litestream
0.3.x/0.5.x source, `db.go`):

1. It creates and continuously writes a metadata directory **alongside** the DB:
   `/var/lib/oh-my-bmad/registry/.state.sqlite3-litestream/` (shadow WAL in
   0.3.x; LTX log in 0.5.x). The path is fixed next to the DB
   (`meta-path` can relocate it, but the `-wal`/`-shm` + checkpoint writes below
   still need the DB dir writable, so relocation buys nothing here).
2. It takes over checkpointing (§1), writing the `.db`/`-wal`/`-shm` files.

An **earlier draft of this ADR (and Story 13.1) wrongly specified a `:ro` mount
as "FR26 defence-in-depth."** That was incorrect — corrected here after
verifying litestream's actual behaviour. The data volume is mounted **read-write**
for the sidecar. FR26 is upheld by the application-layer single-author invariant
(§1), not by the mount mode. The same-group access works because registry-state
fixes the DB file to mode 0o660 at startup (Story 11.3.12) and the sidecar runs
in the shared `omb` group; the litestream container's uid must be a member of
that group (deployment detail to confirm when the sidecar is first enabled —
tracked for Story 13.2's setup runbook).

The `litestream.yml` config file is bind-mounted **read-only** (`:ro`) — only the
*data* volume needs write; the config is never mutated.

### 4. Replication is driven by an upstream image, config-not-code.

The sidecar runs the upstream `litestream/litestream` public image (pinned),
not a custom `oh-my-bmad-base` derivative — it carries no Platform Python and
needs no per-service uid/umask logic. Behaviour is entirely config-file driven
(`litestream.yml`, Story 13.2), keeping target-specific credentials and bucket
layout out of the repo and out of the image.

## Consequences

**Positive.**
- Cross-host disaster recovery with a seconds-scale RPO, opt-in, zero default
  cost.
- FR26 single-writer holds at the application layer (registry-state is the sole
  row-author; litestream only co-manages checkpoints — §1/§3); no second
  *Platform* runs against the replica.
- Clear operator mental model: "this is a backup that streams, not a failover."

**Negative / accepted trade-offs.**
- No automatic failover — recovery is a manual, stack-down procedure (by
  design; see Decision 1).
- Asynchronous replication means a small window of un-replicated frames can be
  lost on abrupt host loss (RPO > 0); acceptable for this workload.
- Operators can misread "replication" as "HA"; this ADR + the operator-runbook
  section (Story 13.2) exist specifically to prevent that.
- Replication health must itself be observed (a silently-dead replica is
  dangerous) — addressed by the `litestream-lag-check` recipe +
  `replication.lagging` event (Story 13.4).
- **Checkpoint coordination:** litestream takes over checkpointing (disables
  autocheckpoint on its connection + issues its own `wal_checkpoint`). This is
  the standard litestream model and is lock-safe under SQLite WAL, but it means
  litestream becomes a second process that physically writes the DB/WAL files
  (relocating committed frames, never authoring data — see §1). To verify once
  the sidecar is first run live: that registry-state and litestream do not fight
  over checkpoints (litestream is designed to coexist with an app that also
  checkpoints; worst case is a redundant checkpoint, not corruption). Tracked as
  a verification item for Story 13.2's first live enablement.
- **Container group membership:** the litestream uid must join the shared `omb`
  group to read/write the 0o660 DB files — a deployment detail to confirm at
  first enablement (Story 13.2 runbook).

## References

- [Source: epics.md — Epic 13 goal "Replication ≠ HA — explicitly framed in ADR" + Stories 13.1–13.4 + the Epic 13 acceptance gate requiring ADR-0007 accepted.]
- [Source: prd.md — FR69 (sidecar), FR70 (config template), FR71/FR71a (restore), NFR-R7 (lag observability).]
- [Source: ADR-0003 §Phase-2 gate — the ADR-0004..0008 forward-reference list this ADR closes.]
- [Source: Story 11.3.12 — state.sqlite3 mode 0o660 (the same-group read litestream relies on) + WAL-sidecar inheritance.]
- [Source: FR26 single-writer — the invariant that makes single-direction replication safe and double-live-writer forbidden.]
