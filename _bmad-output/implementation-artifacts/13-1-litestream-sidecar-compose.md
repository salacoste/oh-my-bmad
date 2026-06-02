# Story 13.1 — litestream sidecar in docker-compose (FR69)

Status: review

<!-- Epic 13 opener. Conventional engineering choices (compose profile, upstream
image, read-WRITE mount per litestream's requirement) — no product fork, so implemented directly with the
defaults documented below rather than a needs-scoping-decision pause. ADR-0007
(the Epic-13 prerequisite) authored alongside. -->

## Story

**As** the platform operator,
**I want** an optional litestream sidecar in the compose stack that streams the
registry-state SQLite WAL off-host to an S3-compatible target when I enable it,
**so that** I can rebuild a destroyed host from object storage (disaster
recovery) — WITHOUT changing the default stack footprint when I don't.

## Prerequisite delivered: ADR-0007

The Epic-13 acceptance gate requires `ADR-0007` authored + `accepted` with the
explicit **"replication ≠ HA"** framing. Authored in this story:
`docs/adr/0007-litestream-wal-replication.md` (status: accepted) — DR-not-HA,
single-writer preserved at the app layer, optional/off-by-default, READ-WRITE mount (litestream requirement — corrected from an initial :ro draft), config-not-code.

## Gap analysis (BUILT vs NET-NEW — 2026-06-01)

| Item | Status | Where |
|---|---|---|
| compose stack + `oh-my-bmad-data` named volume | **BUILT** | `docker-compose.yml` (vol def ~435-437; mounted `/var/lib/oh-my-bmad`) |
| state.sqlite3 in WAL @ 0o660 (sidecar inherits) | **BUILT** | registry-state `app/main.py` (journal_mode=WAL + `_ensure_db_file_group_writable`, Story 11.3.12) |
| optional-service pattern (`profiles:`) | **BUILT** (migrator) | `docker-compose.yml` migrator `profiles: ["migrate"]` (~418) |
| `env_file`/`${VAR}` interpolation | **BUILT** | `docker-compose.yml` `env_file: [{path: .env, required: false}]` |
| **litestream sidecar service** | **NET-NEW** | `docker-compose.yml` (added before `networks:`) |
| **`OMB_LITESTREAM_CONFIG_PATH` + `LITESTREAM_VERSION`** | **NET-NEW** | `.env.example` (Epic-13 section) |
| **ADR-0007 (replication ≠ HA)** | **NET-NEW** | `docs/adr/0007-litestream-wal-replication.md` |
| litestream.yml template + credential docs | **Story 13.2** (NOT here) | — |
| restore recipe / lag-check / replication.lagging | **Stories 13.3 / 13.4** (NOT here) | — |

## Design choices (conventional — documented, not forked)

- **Opt-in mechanism:** `profiles: ["litestream"]` (mirrors migrator) AND the
  operator sets `OMB_LITESTREAM_CONFIG_PATH`. Both levers documented; a plain
  `docker compose up` never starts it.
- **Image:** upstream `litestream/litestream:${LITESTREAM_VERSION:-0.3.13}` —
  config-not-code, no `oh-my-bmad-base` derivative (the sidecar has no Platform
  Python / uid logic).
- **Mount:** `oh-my-bmad-data:/var/lib/oh-my-bmad` (READ-WRITE — required).
  CORRECTED after authoritative litestream-docs/source verification: litestream
  needs write access to the DB directory — it writes a `.state.sqlite3-litestream/`
  meta dir AND takes over checkpointing (writes the `.db`/`-wal`/`-shm`). A
  `:ro` mount (the original draft) would break replication. FR26 is preserved at
  the application layer (registry-state is the sole row-author; litestream only
  relocates committed frames), NOT by the mount mode — see ADR-0007 §1/§3. The
  *config* file is still bind-mounted read-only (`:ro`).
- **Healthcheck:** intentionally OMITTED for 13.1 (like the migrator). The
  sidecar is orthogonal to the core "N/N healthy" count; replication-health
  observability (lag) is Story 13.4's `litestream-lag-check` +
  `replication.lagging`. AC framing below reflects this.

## Acceptance Criteria

1. **AC1 — sidecar service added, OFF by default.** A `litestream` service exists
   in `docker-compose.yml` under `profiles: ["litestream"]`. A plain
   `docker compose config` (no profile) does NOT include it. **VERIFIED:**
   `docker compose config --services` → 7 services (6 core + metrics), no
   litestream; `docker compose --profile litestream config --services` → 8
   (litestream present).

2. **AC2 — read-WRITE data mount (litestream requires it); FR26 upheld by
   design.** The sidecar mounts `oh-my-bmad-data` read-write — litestream writes
   its `.state.sqlite3-litestream/` meta dir + checkpoints the DB (verified vs
   litestream source). A `:ro` mount would break replication. FR26 single-writer
   is preserved at the application layer (registry-state sole row-author;
   litestream only relocates committed WAL frames), per ADR-0007 §1/§3.
   **VERIFIED:** rendered config shows the data volume mounted read-write (no
   `read_only`) and the *config* bind read-only.

3. **AC3 — upstream image, config-driven.** `image: litestream/litestream`
   (pinned via `LITESTREAM_VERSION`); `command: ["replicate", "-config",
   "/etc/litestream/litestream.yml"]`; config bind-mounted read-only from
   `${OMB_LITESTREAM_CONFIG_PATH:-./litestream.yml}`. **VERIFIED** in rendered
   config.

4. **AC4 — env wiring.** `OMB_LITESTREAM_CONFIG_PATH` + `LITESTREAM_VERSION`
   documented in `.env.example` (Epic-13 section) with the "credentials live in
   litestream.yml, not here" + "off by default" notes. (Full litestream.yml
   template + credential-placement runbook = Story 13.2.)

5. **AC5 — ADR-0007 accepted.** `docs/adr/0007-litestream-wal-replication.md`
   authored, status `accepted`, with the explicit "replication ≠ HA" framing
   (Epic-13 gate item).

6. **AC6 — count semantics.** Without the profile the stack is unchanged (6 core
   + metrics = 7 services). With `--profile litestream` the sidecar is added (8
   services); it carries no compose healthcheck (by design — see Design choices),
   so it does not change the *healthy* count of the core services. Full live
   bring-up validation (the "N/N healthy" boot) is deferred to the nightly /
   operator, consistent with the Epic-11.3 AC8 precedent (this story's
   verification is `docker compose config`, which validates structure without a
   live stack).

## Constraints

- **NO `mcp_clients.py` / no Python service code touched** — compose + env + ADR only.
- **FR26 single-writer preserved** at the application layer — registry-state is
  the sole row-author; litestream needs RW (meta dir + checkpoints) but only
  relocates committed frames, never authors data (ADR-0007 §1/§3).
- **Replication ≠ HA** — see ADR-0007; no failover, no second live writer.
- **Sharp edge (documented):** enabling the profile without first creating the
  `litestream.yml` the bind mount points at will fail/auto-create a dir — the
  operator must complete Story 13.2's setup before enabling. The `.env` note +
  the off-by-default profile guard against accidental activation.

## Dev Agent Record

### Agent Model Used
claude-opus-4-8[1m] (create-story + dev-story, 2026-06-01).

### Completion Notes List
- ADR-0007 authored (accepted) — the Epic-13 prerequisite.
- litestream service added to docker-compose.yml (profile-gated, READ-WRITE data mount,
  pinned upstream image, replicate command).
- `.env.example` Epic-13 section (OMB_LITESTREAM_CONFIG_PATH + LITESTREAM_VERSION).
- Verified via `docker compose config`: 7 services default / 8 with profile;
  data volume read-write (config bind :ro); image + command correct. CORRECTED the initial :ro data mount → rw after verifying litestream needs DB-dir write (meta dir + checkpoint) via docs/source.
- Deferred to later Epic-13 stories: litestream.yml.example + credential runbook
  (13.2), restore recipe (13.3), lag-check + replication.lagging (13.4).

### File List
- docs/adr/0007-litestream-wal-replication.md (NEW — ADR, accepted)
- docker-compose.yml (M — litestream sidecar service)
- .env.example (M — Epic-13 env section)

## Definition of Done
- litestream sidecar in compose, profile-gated OFF by default, read-WRITE data mount (litestream requirement).
- ADR-0007 authored + accepted with "replication ≠ HA" framing.
- `OMB_LITESTREAM_CONFIG_PATH` + `LITESTREAM_VERSION` in `.env.example`.
- `docker compose config` validates default (no sidecar) and `--profile litestream` (sidecar present).
- Code review discharged; `sprint-status.yaml` flips `13-1-litestream-sidecar-compose` to done.

## Frontmatter

```yaml
---
story_id: 13.1
story_key: 13-1-litestream-sidecar-compose
parent_epic: 13
phase: 2
fr_refs: [FR69]
nfr_refs: [NFR-R7]
arch_refs:
  - "ADR-0007 (authored here) — litestream WAL replication, replication ≠ HA"
  - "epics.md Epic 13 — orthogonal DR sidecar; Story 13.1 scope + AC"
  - "Story 11.3.12 — state.sqlite3 0o660 / WAL-sidecar mode the litestream sidecar (shared omb group) reads+writes"
  - "docker-compose migrator profiles:[migrate] — the optional-service pattern mirrored"
estimated_complexity: SMALL (compose + env + ADR; no service code)
priority: MEDIUM (FR69; Epic-13 opener, orthogonal)
blocks: []
unblocks:
  - Story 13.2 (litestream.yml template + credential docs)
  - Story 13.3 (restore-from-litestream recipe)
  - Story 13.4 (lag-check + replication.lagging event)
---
```
