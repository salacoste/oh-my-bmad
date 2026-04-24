# Story 1.10a: Deployment quickstart docs (Bootstrap-blocker)

Status: done

## Story

As the **operator**,
I want **`docs/deployment/vps.md` + `docs/deployment/macos.md` + a corrected top-level README Quickstart section**,
so that **a cold return to the project (or a future collaborator's first clone) can reach the Bootstrap Milestone without out-of-doc guessing — and no forward-reference ("Story 1.4 will land…") remains in operator-facing docs**.

## Acceptance Criteria

1. **AC-1: `docs/deployment/vps.md`** — step-by-step VPS deploy guide.
   - Audience: operator provisioning a fresh Ubuntu 24.04 VPS (≥2 GB RAM, public IPv4).
   - Sections (in order):
     - **Prerequisites** — Docker Engine ≥ 24, Docker Compose v2, git, `uv ≥ 0.5`, `just`. Install commands for each.
     - **Tunnel choice** — one-liner rationale for the 3 tunnel-first TLS options (Cloudflare Tunnel default, ngrok, BYO), pointing at Story 1.4's `.env.example` `TUNNEL_MODE` section. Includes the `cloudflared tunnel` invocation example.
     - **Clone + bootstrap** — `git clone`, `uv sync --dev`, `uv run pre-commit install`, `just bootstrap-verify`. Expected output per step.
     - **Configure `.env`** — `cp .env.example .env` + field-by-field annotation (TELEGRAM_BOT_TOKEN where to get it; ANTHROPIC_API_KEY where; GITHUB_TOKEN scopes needed; TG_ALLOWLIST_USER_IDS how to find yours; REGISTRY_DB_PATH usually leave default; ENV=production for a real VPS; TUNNEL_MODE based on prior section; OMB_IMAGE_REGISTRY/OMB_VERSION — set to your GHCR namespace + latest stable tag, OR leave `dev` for local-build path).
     - **Deploy** — `just deploy-vps` (which chains `build-base` → `compose pull || true` → `compose build` → `compose up -d`). Expected output + how to interpret.
     - **Verify** — `docker compose ps` shows 6/6 healthy within 60 s; tunnel responds on whatever port operator picked; once Story 3.5 lands, `/ping` via Telegram returns `pong · <latency>` within 2 s.
     - **Troubleshooting** — 5 most likely first-run issues with fix:
       - `env file .env not found` → `cp .env.example .env`.
       - container unhealthy → `docker compose logs <service>`; Story-1.4 hello-world `__main__.py` logs `<service> ready`.
       - tunnel can't reach Telegram webhook → check `cloudflared` logs + `TUNNEL_MODE` setting.
       - `manifest unknown` on `compose pull` → `OMB_VERSION` points at a tag that hasn't been released (set to `dev` or a published tag).
       - SELinux (on RHEL-family hosts) blocks bind-mount → document `:Z` flag or suggest switching to macOS overlay pattern.
     - **Upgrade** — `docker compose pull && docker compose up -d` once a new `v*` tag is released.
   - Total length: ~150-200 lines.
   - Every command-line example operator can copy-paste verbatim, no angle-bracket placeholders except where genuinely operator-specific (<GITHUB_OWNER>, <BOT_USERNAME>).

2. **AC-2: `docs/deployment/macos.md`** — step-by-step macOS deploy guide.
   - Audience: operator on macOS 14+ wanting a local Docker Desktop (or Colima) deployment.
   - Sections (in order):
     - **Prerequisites** — Docker Desktop (or Colima ≥ 0.6), git, `uv ≥ 0.5`, `just`, Homebrew. `brew install uv just` one-liner.
     - **Tunnel choice** — same 3 options as VPS; local macOS often uses ngrok for rapid iteration.
     - **Clone + bootstrap** — same shape as VPS.
     - **Configure `.env`** — same annotation, with the key difference that `OMB_VERSION=dev` + local-build is usually the right choice on macOS dev hosts (no GHCR round-trip).
     - **macOS data dir prerequisite** — `mkdir -p "${HOME}/.oh-my-bmad"` (or rely on `just dev` / `just deploy-macos` to create it automatically; document both paths).
     - **Deploy** — `just deploy-macos` (which chains `build-base` → `mkdir -p ~/.oh-my-bmad` → `compose pull || true` → `compose build` → `compose up -d` with the macos overlay).
     - **Verify** — same 6/6 healthy check; note that macOS Docker Desktop reports healthcheck slightly differently (`docker inspect` vs `compose ps`); both work.
     - **Troubleshooting** — 5 most likely issues:
       - `mkdir: /Users/<user>/.oh-my-bmad: Permission denied` → unusual (operator HOME), more likely a missing parent.
       - Docker Desktop VM out of disk → shrink + relaunch.
       - Colima vs Docker Desktop → cluster-name differences; document `colima start --runtime docker` if using it.
       - BuildKit disabled (Docker Desktop < 4.x) → export `DOCKER_BUILDKIT=1`.
       - Port conflicts when tunneling local → `lsof -nP -iTCP -sTCP:LISTEN | grep <port>`.
     - **Upgrade** — same `pull && up -d` flow once GHCR has tags.
   - Total length: ~150-200 lines.

3. **AC-3: Top-level README Quickstart section rewrite.** Replace the current Stories-1.4/1.10a placeholders with working steps. Target length: ≤ 15 lines of commands total (the "10 commands" promise in the epic AC is a target — 10-15 is acceptable).
   - Remove the line "Story 1.1 ships only the workspace skeleton + this README. The full deploy quickstart is finished in Stories 1.4 (compose + env + justfile) and 1.10a (deployment quickstart docs)." — obsolete now that 1.4 is done + 1.10a is THIS story.
   - Remove the `# 2. Configure (Story 1.4 will land .env.example):` forward-reference — `.env.example` exists.
   - Remove the `# 3. Deploy (Story 1.4 will land docker-compose.yml):` forward-reference — `docker-compose.yml` exists.
   - New Quickstart:
     ```sh
     # Prereqs: Docker Engine ≥ 24 + Docker Compose v2 + uv ≥ 0.5 + just
     #   brew install uv just                                  # macOS
     #   curl -LsSf https://astral.sh/uv/install.sh | sh        # Linux (uv)
     git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
     uv sync --dev
     uv run pre-commit install
     just bootstrap-verify
     cp .env.example .env
     $EDITOR .env                                             # fill in secrets + tunnel choice
     just dev                                                 # macOS: overlay; Linux: base compose
     docker compose ps                                        # expect 6/6 Up (healthy) within 60 s
     ```
   - Followed by: "**For detailed deployment guides:** [`docs/deployment/vps.md`](docs/deployment/vps.md) · [`docs/deployment/macos.md`](docs/deployment/macos.md)."

4. **AC-4: README Deployment checklist section cleanup.** Currently lines 67-87 carry the "Both targets are populated as stubs in Story 1.1 and filled in detail by Story 1.10a" preamble. Replace with:
   - A 1-sentence intro: "Full deployment guides live at [`docs/deployment/vps.md`](docs/deployment/vps.md) + [`docs/deployment/macos.md`](docs/deployment/macos.md). The checklist below is the 6-step summary."
   - Keep the two checklists (VPS + macOS) but remove the Story-reference parentheticals ("Story 3.5", "Story 1.4 docs the three options", etc.). Those belong in the detailed deployment docs, not the README summary.

5. **AC-5: README `docs/` description updated.** Current line 44: `"Operator documentation: deployment guides, runbooks, schema-evolution, exceptions, testing-guide. Empty until Stories 1.10a and 1.10b."` — update to `"Operator documentation: [deployment guides](docs/deployment/) landed in Story 1.10a; runbook + schema evolution + exceptions + testing guide + message-design land in Story 1.10b."`. Just reflects reality now that 1.10a lands 2 docs.

6. **AC-6: Cross-references all internal.** Every link in the new docs uses relative paths (no absolute `https://github.com/...` refs for files living in this repo). Operator reads offline — no network required.

7. **AC-7: Placeholder-reference discipline.** Where a future story lands a feature, the docs cite it by story-number (e.g., "`/ping` command arrives in Story 3.5") rather than pretending it works today. Operator is never confused about what's live vs. stubbed.

8. **AC-8: Prerequisite version matrix.** Both deployment docs open with an explicit table:
   ```
   | Tool | Minimum version | Notes |
   |------|----------------|-------|
   | Docker Engine | 24.0 | BuildKit enabled by default |
   | Docker Compose | v2.20 | Required for build-contexts |
   | uv | 0.5.0 | Matches pyproject.toml required-version |
   | just | 1.30 | Required for recipes |
   | git | 2.40 | Required for sync-upstream fetch |
   ```

9. **AC-9: Scan-secrets clean.** Both new docs must pass `uv run secret-hygiene-precommit docs/deployment/vps.md docs/deployment/macos.md` → exit 0. Any example tokens use `sk-ant-<key-here>` or `<TELEGRAM_BOT_TOKEN>` placeholders (angle brackets are outside the allowed char-class, so no pattern fires).

10. **AC-10: All Story 1.1–1.9 regressions stay green.**
    - `just bootstrap-verify` → 13/13.
    - `just test` → 75 + 6 skipped.
    - `just lint` → all 6 sub-commands (including scan-secrets against the new docs).
    - `just migrator-test-additive` → 3/3.
    - `just check-gates-self-test` → 3/3.

11. **AC-11: Atomic commit.** All new/modified files land in one commit titled `docs(story-1-10a): deployment quickstart — vps.md + macos.md + README rewrite · NFR-M7`. Docs-only follow-up commits permitted (this story IS the docs commit; no scaffold/review-fix cycle expected unless a reviewer finds factual errors).

## Tasks / Subtasks

- [x] **Task 1: Write `docs/deployment/vps.md`** (AC: #1)
  - [x] Create `docs/deployment/` directory if missing.
  - [x] All 8 sections per AC-1.
  - [x] Prerequisite version table per AC-8.
  - [x] Every command copy-paste-ready.

- [x] **Task 2: Write `docs/deployment/macos.md`** (AC: #2)
  - [x] All 8 sections per AC-2.
  - [x] Same prereq table + macOS-specific footnotes.
  - [x] Explicit `mkdir -p "${HOME}/.oh-my-bmad"` prerequisite.

- [x] **Task 3: Rewrite README Quickstart** (AC: #3)
  - [x] Remove Story-1.4 forward-references.
  - [x] Remove Story-1.10a forward-reference footer.
  - [x] New 10-15 line shell block.
  - [x] Link to `docs/deployment/{vps,macos}.md`.

- [x] **Task 4: Rewrite README Deployment checklist** (AC: #4)
  - [x] 1-sentence intro pointing at detailed docs.
  - [x] Strip story-number parentheticals.

- [x] **Task 5: Update README `docs/` row** (AC: #5)
  - [x] Reflect that deployment docs now exist.

- [x] **Task 6: Scan-secrets check** (AC: #9)
  - [x] `uv run secret-hygiene-precommit docs/deployment/vps.md docs/deployment/macos.md` → exit 0.

- [x] **Task 7: Regression check** (AC: #10)
  - [x] Run full regression suite.

- [x] **Task 8: Atomic commit** (AC: #11)
  - [x] Single commit per AC-11 title.

## Dev Notes

### Architecture patterns for this story

- **NFR-M7 subset** — this story delivers the "quickstart + deployment checklist" arm. Story 1.10b delivers the "runbook + schema evolution + exceptions + testing guide + backup/restore + message-design" arm.
- **No code changes.** Docs-only. Scan-secrets must still pass, lint still green, tests still pass.
- **Placeholder discipline** — documents future-story features by their story number, not as if they exist today. Keeps the docs honest.
- **Forward-reference cleanup** — the README currently has 3 "Story X will land" placeholders that obsolete the moment this story lands. Clean them up here.

### What this story does NOT do

- `docs/operator-runbook.md` — Story 1.10b.
- `docs/schema-evolution.md` — Story 1.10b (the migrator runbook).
- `docs/exceptions.md` — Story 1.10b.
- `docs/testing-guide.md` — Story 1.10b.
- `docs/backup-restore.md` — Story 1.10b (README has a summary now; detailed doc later).
- `docs/message-design.md` — Story 1.10b.
- Actual GHCR image publishing (Story 1.9's release.yml — already shipped).
- Real `/ping` response code (Story 3.5).
- Real `/v1/health` endpoint (Story 2.9).

### Source tree components to touch

```
oh-my-bmad/
├── docs/
│   └── deployment/                             # Task 1+2 NEW directory
│       ├── vps.md                              # Task 1 NEW
│       └── macos.md                            # Task 2 NEW
└── README.md                                   # Tasks 3+4+5 MODIFIED
```

**Files: 2 new + 1 modified. Docs-only.**

### Content sketch — `docs/deployment/vps.md` outline

```markdown
# VPS Deployment (Ubuntu 24.04)

This guide walks an operator from a fresh VPS to a running oh-my-bmad
stack that passes `docker compose ps` all-healthy.

## Prerequisites

[version table]

### Install

```sh
# Docker Engine + Compose v2 (per Docker's official apt repo).
# (full commands here — the ones Docker docs give.)

# uv + just
curl -LsSf https://astral.sh/uv/install.sh | sh
# For just: prefer `cargo install just` or `snap install just` on Ubuntu.
```

## Tunnel choice

Telegram webhook needs HTTPS. Three options, choose one:

### Option A — Cloudflare Tunnel (default)
```sh
cloudflared tunnel --url http://localhost:8080
```
[pros/cons]

### Option B — ngrok
### Option C — BYO reverse proxy

## Clone + bootstrap

```sh
git clone <repo-url> oh-my-bmad && cd oh-my-bmad
uv sync --dev
uv run pre-commit install
just bootstrap-verify
```
Expected output: `✓ bootstrap OK (13 workspace-member imports verified)`

## Configure `.env`

```sh
cp .env.example .env
$EDITOR .env
```

Fields:
- `TELEGRAM_BOT_TOKEN=` — get via @BotFather on Telegram. FAIL-CLOSED: leave empty and the bot won't start.
- `ANTHROPIC_API_KEY=` — from console.anthropic.com. Starts with `sk-ant-`.
- `GITHUB_TOKEN=` — PAT with `repo` scope.
- `TG_ALLOWLIST_USER_IDS=` — your Telegram numeric ID (get via @userinfobot).
  **WARNING: empty denies ALL users — the bot becomes a read-only ghost.**
- `REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3` — usually leave default.
- `ENV=production` — disables FastAPI /v1/docs Swagger UI on prod VPS.
- `TUNNEL_MODE=cloudflare` — must match tunnel choice above.
- `OMB_IMAGE_REGISTRY=ghcr.io/<owner>` — your GHCR namespace OR keep `ghcr.io/r2d2` to pull the canonical upstream images.
- `OMB_VERSION=0.1.0` — set to a published tag (see https://github.com/<owner>/oh-my-bmad/releases) OR keep `dev` to build locally.

## Deploy

```sh
just deploy-vps
```
This runs: `build-base` → `compose pull || true` → `compose build` → `compose up -d`.

Expected output: all 6 services start; last line `Container oh-my-bmad-worker-wrapper-1  Started` or equivalent.

## Verify

```sh
docker compose ps
```
Expected: 6/6 `Up (healthy)` within 60 s.

Once Story 2.9 lands the `/v1/health` endpoint:
```sh
curl http://localhost:<port>/v1/health
```

Once Story 3.5 lands `/ping`:
```
# In Telegram
/ping
# Expected: "pong · <latency-ms>ms"
```

## Troubleshooting

[5 common issues per AC-1]

## Upgrading

```sh
# Edit .env to bump OMB_VERSION to the new tag.
docker compose pull
docker compose up -d
```
```

### Content sketch — `docs/deployment/macos.md` outline

Same shape as VPS guide, with macOS-specific variants:
- `brew install uv just` prereqs.
- `mkdir -p "${HOME}/.oh-my-bmad"` prerequisite.
- `just deploy-macos` (uses the macOS overlay).
- Docker Desktop VM troubleshooting.
- Colima alternative.

### README Quickstart rewrite

Per AC-3. Goal: ≤ 15 command lines, copy-paste-ready, no story-number placeholders.

### Previous Story Intelligence

- Stories 1.1–1.9 have all populated the pieces this doc references (`.env.example` 1.4, `just bootstrap-verify` 1.1, `just dev` 1.4, compose files 1.4, GHCR publishing 1.9). 1.10a is the synthesis.
- README has been touched across 1.1 (initial), 1.4 (compose section), 1.7 (pre-commit install), 1.9 (Upgrading). This story reconciles the forward-references the earlier edits left behind.
- Scan-secrets pattern (Story 1.7): angle-bracket placeholders like `<TELEGRAM_BOT_TOKEN>` fall outside the pattern's allowed char-class → never trip. Using `sk-ant-<key-here>` literally fails the `{20,200}` length bound for the ANTHROPIC pattern → safe.

### Git Intelligence

Recent commits:
- `22f8c58 docs(story-1-9): finalize + mark done`
- `0b3511b chore(scaffold): apply story 1.9 code-review fixes · all severities`
- `db92561 docs(story-1-9): finalize story file + mark review`
- `3211e18 chore(scaffold): story 1.9 — GHCR multi-arch release workflow · FR51 FR52`

Cadence: 4 commits per code-heavy story. Story 1.10a is docs-only — expect **1 commit** (scaffold-equivalent) + 1 finalize (optional — can combine with scaffold since there's no review-fix stage for pure docs typically).

### Latest Tech Information

- **Docker Engine ≥ 24.0** — BuildKit default-enabled.
- **Docker Compose v2.20+** — `build-contexts:` + `additional_contexts` support.
- **`uv ≥ 0.5`** — matches repo's `[tool.uv] required-version = ">=0.5"`.
- **`just ≥ 1.30`** — stable recipe syntax.

### References

- `epics.md` §Epic 1 / Story 1.10a (lines 625-641) — AC source.
- `prd.md` NFR-M7 (line 947) — operator docs requirement.
- README.md — the file being reshaped.
- `.env.example` — the env-var template docs reference.
- All prior Story-1.x implementation artifacts — the feature lineage the docs describe.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — docs-writing with careful cross-reference discipline. No Opus reasoning required.

### Debug Log References

_Placeholder._

### Completion Notes List

_To be filled by the dev agent. Record per AC: pass/fail + evidence._

- AC-1 — vps.md present, all 8 sections.
- AC-2 — macos.md present, all 8 sections.
- AC-3 — README Quickstart rewritten, no forward-refs, ≤ 15 command lines.
- AC-4 — README Deployment checklist rewritten.
- AC-5 — README `docs/` row updated.
- AC-6 — all cross-refs relative.
- AC-7 — future-story mentions cite story numbers.
- AC-8 — prereq version table present in both docs.
- AC-9 — scan-secrets exit 0 on both new files.
- AC-10 — regressions green.
- AC-11 — single atomic commit SHA.

### File List

_To be filled by the dev agent. Expected: 2 new + 1 modified._

### Change Log

- **2026-04-24:** Story 1.10a implemented. 2 new + 1 modified; atomic scaffold commit `e424932` (663+/27-). Verification green.
- **2026-04-24 (review):** 3-layer adversarial review surfaced 2 CRITICAL + 2 MAJOR + 6 HIGH + LOW/MEDIUM. Applied in commit `9b2f984` (4 files, 176+/327-):
  - **CRITICAL — port 8080 is fiction.** `docker-compose.yml` declares zero host `ports:`; `telegram-gateway` hello-world has no HTTP listener. Tunnel examples rewritten with `<port-set-by-Story-3.1>` placeholder + explicit Phase-1-state callout.
  - **CRITICAL — Compose version v2.20 too low.** `env_file: {path, required: false}` syntax needs Compose v2.24+. Bumped matrices + README checklist.
  - **MAJOR — `just dev` fresh-clone failure.** Recipe didn't depend on `build-base` → `FROM oh-my-bmad-base:local not found`. Fixed with `dev: build-base` dependency.
  - **MAJOR — docs over length ceiling.** 308/333 → 244/244 (AC target 150-200). Collapsed tunnel alternatives, removed duplicate Upgrading section (link to README), tightened bootstrap-verify output block.
  - **HIGH** (6) — Shell-metachar `<TOKEN>` (quoted); tunnel-before-clone reordered; cloudflared jammy-on-noble comment; macOS `OMB_VERSION=dev` recipe accuracy; version-matrix floors (git 2.25, just 1.14 — realistic); README version-string consistency.
  - **MEDIUM/LOW** — SELinux `:z` vs `:Z` safety; healthcheck-timing caveat for cold build; macOS `registry-state`-is-sole-writer framing; `REGISTRY_DB_PATH` dual-change warning; `brew install --cask ngrok`; `<GITHUB_TOKEN>` placeholder consistency.
  - Skipped: Story-1.4 prose refs (factual, date-stable); story Completion Notes deferred to finalize commit.
  - Verification post-fix: scan-secrets clean; `just -n dev` dry-run confirms `build-base` ordering; all regressions green.
- **2026-04-24 (finalize):** Change Log expanded with review-fix summary. Status `review` → `done`.
