# Story 13.2 — litestream config template + S3-compatible target docs (FR70)

Status: review

<!-- Epic 13, story 2. Authoring-heavy (config template + credential docs +
operator runbook) + the small deferred compose items from Story 13.1's review.
Conventional choices — implemented directly. -->

## Story

**As** the platform operator,
**I want** a ready-to-copy `litestream.yml.example` (one block per S3/B2/R2/MinIO
target), clear credential-placement guidance, and an operator-runbook section,
**so that** I can copy → fill credentials → enable the sidecar and have it
replicate within ~1 minute, without leaking secrets into the repo.

## Acceptance Criteria

1. **AC1 — `litestream.yml.example` shipped.** A committed template at repo root
   with a replica block for each supported target (AWS S3, Backblaze B2,
   Cloudflare R2, MinIO), pointing at the in-container DB path
   `/var/lib/oh-my-bmad/registry/state.sqlite3` (registry-state's single-writer
   DB; the idempotency cache is deliberately NOT replicated). One block active,
   the others commented; `force-path-style: true` noted for MinIO.

2. **AC2 — credentials never in the repo.** The template carries NO secrets:
   access keys come from the standard `LITESTREAM_ACCESS_KEY_ID` /
   `LITESTREAM_SECRET_ACCESS_KEY` env vars (litestream reads them automatically),
   passed to the sidecar via `env_file: .env`. The filled-in `litestream.yml` is
   added to `.gitignore`. **VERIFIED:** `git check-ignore litestream.yml` → ignored.

3. **AC3 — `.env.example` credential-placement comment.** The Epic-13 section now
   documents the copy→fill flow + `LITESTREAM_ACCESS_KEY_ID` /
   `LITESTREAM_SECRET_ACCESS_KEY` (with per-target guidance: AWS IAM key, B2
   keyId/applicationKey, R2 token, MinIO key) + the "never commit creds" warning.
   (`OMB_LITESTREAM_CONFIG_PATH` + `LITESTREAM_VERSION` already added in 13.1.)

4. **AC4 — operator-runbook section.** `docs/operator-runbook.md` gains a
   "litestream WAL replication" section: enable steps (bucket → cp template →
   set `.env` creds → `--profile litestream up`), the ~1-minute first-snapshot
   expectation + how to confirm (`docker logs omb-litestream`, bucket objects),
   sharp edges (creds-never-in-repo, RW mount rationale, group/permissions,
   missing-config dir trap, checkpoint coexistence), and disable steps.

5. **AC5 — deferred Story-13.1 compose items applied (the safe ones).**
   `depends_on: { registry-state: { condition: service_healthy } }` added so the
   sidecar waits for the DB to exist (avoids cold-start restart-loop).
   `group_add: ["10000"]` was already added in 13.1. The explicit non-root
   `user:` stays deferred to first-live-enable (ADR-0007 verification item — the
   upstream image runs as root and forcing a uid is unvalidated). **VERIFIED:**
   `docker compose --profile litestream config` renders depends_on + group_add.

6. **AC6 — replicate-within-1-minute (live AC).** The "operator copies → fills →
   replicates within 1 minute" behaviour is a LIVE acceptance that needs a real
   bucket + running stack; deferred to operator/nightly validation (consistent
   with the Epic-11.3 AC8 precedent). Static verification done here: template
   parses as YAML, compose renders, gitignore protects creds.

7. **AC7 — gates + code review.** ruff/format n/a (no Python); discipline gates
   green; `docker compose config` valid; secret-hygiene clean (no creds in repo);
   code review discharged.

## Constraints

- **NO secrets committed** — `litestream.yml` gitignored; template + `.env.example`
  carry placeholders only. Secret-hygiene gate must stay clean.
- **Only registry-state's DB replicated** — NOT the rebuildable idempotency cache.
- **Replication ≠ HA** — runbook reiterates ADR-0007.
- **NO Python / service code touched** — template + docs + compose only.

## Dev Agent Record

### Agent Model Used
claude-opus-4-8[1m] (create-story + dev-story, 2026-06-02).

### Completion Notes List
- `litestream.yml.example` (NEW): S3 active + B2/R2/MinIO commented; creds via env.
- `.gitignore`: `litestream.yml` ignored (creds protection).
- `.env.example`: expanded credential-placement + `LITESTREAM_ACCESS_KEY_ID/SECRET`.
- `docs/operator-runbook.md`: full litestream enable/operate/disable section.
- `docker-compose.yml`: `depends_on: registry-state (service_healthy)` (deferred
  13.1 item); non-root `user:` still deferred to first-live-enable.
- Verified: compose renders (7 default / 8 profile, depends_on+group_add+RW),
  `git check-ignore litestream.yml` ignored, template valid YAML.
- Deferred: live "replicate within 1 min" (needs real bucket) → operator/nightly;
  restore drill = 13.3; lag-check + replication.lagging = 13.4.

### File List
- litestream.yml.example (NEW — config template, 4 targets)
- .gitignore (M — ignore litestream.yml)
- .env.example (M — credential env vars + placement docs)
- docs/operator-runbook.md (M — litestream section)
- docker-compose.yml (M — depends_on registry-state)

## Definition of Done
- `litestream.yml.example` with S3/B2/R2/MinIO blocks; `litestream.yml` gitignored.
- `.env.example` credential-placement comment + `LITESTREAM_*` vars.
- operator-runbook litestream section (enable/operate/disable).
- compose `depends_on` added; secrets never in repo (secret-hygiene clean).
- code review discharged; `sprint-status.yaml` flips `13-2-litestream-config-template-docs` to done.

## Frontmatter

```yaml
---
story_id: 13.2
story_key: 13-2-litestream-config-template-docs
parent_epic: 13
phase: 2
fr_refs: [FR70]
nfr_refs: []
arch_refs:
  - "ADR-0007 — litestream WAL replication, replication ≠ HA"
  - "Story 13.1 — the sidecar this story configures; carries 13.1's deferred depends_on/permissions items"
  - "epics.md Story 13.2 — config template + credential docs + runbook"
estimated_complexity: SMALL (template + docs + 1 compose directive; no service code)
priority: MEDIUM (FR70)
blocks: []
unblocks:
  - operators can actually enable + credential the sidecar (13.1 made it exist; 13.2 makes it usable)
  - Story 13.3 (restore recipe) + 13.4 (lag-check)
---
```
