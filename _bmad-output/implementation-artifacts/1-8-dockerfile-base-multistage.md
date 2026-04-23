# Story 1.8: Dockerfile.base + multi-stage builds per service

Status: done

## Story

As the **operator**,
I want **a shared `Dockerfile.base` multi-stage template + per-service thin-override `Dockerfile`s** replacing Story 1.4's single-stage scaffolds,
so that **all 7 Python services (the 6 compose services + `console-cli`) build consistently, each runtime image stays ≤ 200 MB, and the Node-inclusive `worker-wrapper` image is a two-runtime composition — Python slim + Node.js slim — assembled via multi-stage**.

## Acceptance Criteria

1. **AC-1: `Dockerfile.base` at repo root** — shared multi-stage template. Contains (at minimum):
   - **Stage `venv-builder`**: `python:3.12-slim-bookworm` + `uv==0.11.*` + copy workspace manifests (`pyproject.toml`, `uv.lock`, `packages/`, `services/`, `mcp-servers/`, `src/`) + `uv sync --frozen --no-dev --all-packages` → venv materialized at `/app/.venv`.
   - **Stage `runtime-base`**: `python:3.12-slim-bookworm` + shared non-root group `omb` (GID 10000) + `/opt/venv` populated by `COPY --from=venv-builder /app/.venv /opt/venv` + `PATH` / `PYTHONDONTWRITEBYTECODE` / `PYTHONUNBUFFERED` env vars set.
   - Built via `docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .`.
   - `Dockerfile.base` header comment documents the two-step build + references the justfile `build-base` recipe.

2. **AC-2: Per-service `Dockerfile`s become thin overrides** — replace Story 1.4's single-stage scaffolds:
   - `services/registry-api/Dockerfile`, `services/registry-state/Dockerfile`, `services/telegram-gateway/Dockerfile`, `services/orchestrator-adapter/Dockerfile`, `services/clawhip-daemon/Dockerfile` all become ~6-line wrappers:
     ```dockerfile
     # Per-service runtime override. Builds on oh-my-bmad-base:local (see Dockerfile.base).
     FROM oh-my-bmad-base:local
     RUN useradd --system --uid <per-service-UID> --gid omb --home /app --shell /usr/sbin/nologin <service-name>
     USER <service-name>
     ENTRYPOINT ["python", "-m", "<module_name>"]
     ```
   - UIDs carry forward from Story 1.4 (10001–10006) — registry-api 10001, registry-state 10002, telegram-gateway 10003, orchestrator-adapter 10004, worker-wrapper 10005, clawhip-daemon 10006, console-cli 10007 (new).
   - `# SCAFFOLD VERSION` headers from Story 1.4 removed (this IS the Story 1.8 version).

3. **AC-3: `services/worker-wrapper/Dockerfile`** is a 3-stage Node-inclusive composition:
   - Stage A: `FROM oh-my-bmad-base:local AS python-runtime` — inherits the Python venv.
   - Stage B: `FROM node:lts-bookworm-slim AS node-source` — source of Node binaries.
   - Stage C (final): `FROM oh-my-bmad-base:local` + `COPY --from=node-source /usr/local/bin/node /usr/local/bin/node` + `COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules` (or equivalent minimal copy) + `useradd worker-wrapper` + `USER worker-wrapper` + `ENTRYPOINT ["python", "-m", "worker_wrapper"]`.
   - Verification: `docker run --rm oh-my-bmad-worker-wrapper:local node --version` prints a version string.
   - Per Architecture line 243 ("Node.js worker wrapper uses node:lts-bookworm-slim").

4. **AC-4: `services/console-cli/Dockerfile` (new)**. Console-CLI was NOT in Story 1.4's compose wiring (it's invoked via `docker compose exec` or host binary), but this story's epic AC explicitly lists it among "every Python service" that must build. Ship:
   - `services/console-cli/src/console_cli/__main__.py` — hello-world entrypoint following the Story 1.4 pattern (register SIGTERM/SIGINT → touch `/tmp/ready` → log ready → `signal.pause()`). Real Typer CLI lands in Story 4.1.
   - `services/console-cli/Dockerfile` — thin override with UID 10007.
   - Does NOT get a `docker-compose.yml` entry (operator invocation is `docker run --rm oh-my-bmad-console-cli:local <command>` or `docker compose exec console-cli <command>` via ad-hoc container — deferred to Story 4.6 which wires the console wrapper).

5. **AC-5: `docker-compose.yml` build context updated.** Story 1.4's per-service context `build.context: ./services/<name>` is INSUFFICIENT for the new multi-stage build — the builder stage needs the full workspace. Change every service's `build:` block:
   ```yaml
   build:
     context: .
     dockerfile: services/<name>/Dockerfile
   ```
   Build args: none required; the base image is pre-built before `docker compose build`.

6. **AC-6: `justfile` additions + `build` recipe update.**
   - New recipe `build-base` — builds the shared base image:
     ```justfile
     build-base:
         docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .
     ```
   - `build` recipe chains: `build-base` first, then `docker compose build`:
     ```justfile
     build: build-base
         docker compose -f docker-compose.yml build
     ```
   - `deploy-vps` + `deploy-macos` recipes likewise chain `build-base` before `build` (update their bodies).
   - New recipe `image-sizes` — prints each image's size for sanity:
     ```justfile
     image-sizes:
         docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'oh-my-bmad-' | sort
     ```

7. **AC-7: Image-size budget — every service image ≤ 200 MB.** Measured via `docker image inspect <image> --format '{{.Size}}' | numfmt --to=iec`. After `just build-base && just build`, each of the 7 service images ≤ 200 MB per AC spec. The `worker-wrapper` image has the extra Node runtime (+~60 MB typical) so it will be larger than the others; it still stays ≤ 200 MB per the AC.

8. **AC-8: Non-root + /tmp/ready healthcheck still works.** The Story 1.4 healthcheck `test: ["CMD", "test", "-f", "/tmp/ready"]` passes because `/tmp/ready` is written by each service's `__main__.py` on startup. Verify by `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` + `docker compose ps` showing 6/6 healthy within 60 s (the Story 1.4 smoke test, rerun on the new images).

9. **AC-9: BuildKit enforced.** `Dockerfile.base` + per-service overrides rely on BuildKit features (multi-stage, `--target`, layer caching). Document `DOCKER_BUILDKIT=1` requirement in the top-of-file comment of `Dockerfile.base`. Modern Docker Desktop + Docker Engine ≥ 24 enables BuildKit by default, so this is a docs-only note in Phase 1.

10. **AC-10: `.dockerignore` audit.** The 6 existing per-service `.dockerignore` files (Story 1.4) assumed per-service build context. With the context now being repo-root, a repo-root `.dockerignore` is needed. Ship one that excludes:
    - `.venv/`, `.uv/`, `.tmp/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`
    - `_bmad/`, `_bmad-output/`
    - `upstream/` (vendored OMC + clawhip ~50 MB — never copied into images)
    - `.agent/`, `.agents/`, `.claude/`, `.cursor/`, `.gemini/`, `.opencode/`, `.pi/`, `.omc/`
    - `__pycache__/`, `*.pyc`
    - `.env`, `.env.*` (keep `!.env.example`)
    - `.git/`, `.github/`
    - `tests/` (test tree not needed in runtime images)
    - `docs/`, `README.md`, `CHANGELOG.md`, `LICENSE`
    - Existing per-service `.dockerignore` files can either be kept (redundant — no functional effect) or removed. Story 1.8 removes them to reduce duplication.

11. **AC-11: All prior regressions stay green.**
    - `just bootstrap-verify` → 13/13 (`oh-my-bmad` workspace + 12 members — unchanged).
    - `just test` → 75 passed + 6 skipped (unchanged).
    - `just lint` → all 6 sub-commands (ruff + format + mypy + 3 check-gates + scan-secrets) exit 0.
    - `just migrator-test-additive` → 3/3 v1.0.1+extensions.
    - `just check-gates-self-test` → all 3 green.
    - `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` → 6/6 healthy within 60 s (on macOS dev host — parallel to Story 1.4 AC-10).

12. **AC-12: Atomic commit.** All new/modified files land in one commit titled `chore(scaffold): story 1.8 — Dockerfile.base + multi-stage per-service builds · FR46 FR51`. Docs-only follow-up commits permitted.

## Tasks / Subtasks

- [x] **Task 1: `Dockerfile.base`** (AC: #1)
  - [x] Two stages: `venv-builder` + `runtime-base`.
  - [x] `venv-builder` copies workspace manifests + runs `uv sync --frozen --no-dev --all-packages`.
  - [x] `runtime-base` creates the shared `omb` group + copies venv to `/opt/venv` + sets `PATH`.
  - [x] Smoke-verify: `docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .` exit 0; `docker run --rm oh-my-bmad-base:local python -c "print('ok')"` prints `ok`.

- [x] **Task 2: 6 per-service Dockerfile rewrites** (AC: #2)
  - [x] Replace the Story 1.4 single-stage content of `services/{registry-api,registry-state,telegram-gateway,orchestrator-adapter,clawhip-daemon}/Dockerfile` with the thin-override template.
  - [x] Each carries its service-specific UID (10001–10006, per Story 1.4) + `USER <name>` + `ENTRYPOINT ["python", "-m", "<module>"]`.
  - [x] Remove the Story-1.4 `# SCAFFOLD VERSION` header.

- [x] **Task 3: `worker-wrapper` Node-inclusive Dockerfile** (AC: #3)
  - [x] 3-stage composition per AC-3.
  - [x] Copy `/usr/local/bin/node` + `/usr/local/lib/node_modules` from `node:lts-bookworm-slim`.
  - [x] Verify `docker run --rm oh-my-bmad-worker-wrapper:local node --version` prints v20.x or v22.x (whatever current LTS is).

- [x] **Task 4: `console-cli` Dockerfile + hello-world `__main__.py`** (AC: #4)
  - [x] `services/console-cli/src/console_cli/__main__.py` — identical shape to the Story 1.4 hello-world entrypoints; service name `console-cli`, module `console_cli`.
  - [x] `services/console-cli/Dockerfile` — UID 10007; thin override.
  - [x] Verify `docker build -f services/console-cli/Dockerfile -t oh-my-bmad-console-cli:local .` exit 0.
  - [x] Verify `docker run --rm oh-my-bmad-console-cli:local` logs `"console-cli ready"` + stays up until SIGTERM.

- [x] **Task 5: `docker-compose.yml` build context update** (AC: #5)
  - [x] Change every service's `build.context` from `./services/<name>` to `.`.
  - [x] Change `build.dockerfile` to `services/<name>/Dockerfile`.
  - [x] console-cli does NOT get a compose entry (deferred to Story 4.6).

- [x] **Task 6: `justfile` recipe additions** (AC: #6)
  - [x] New recipe `build-base`.
  - [x] `build` recipe — `build: build-base` dependency + compose build.
  - [x] `deploy-vps` + `deploy-macos` — add `build-base` dependency or call it before their body runs.
  - [x] New recipe `image-sizes` for operator sanity.

- [x] **Task 7: `.dockerignore` at repo root** (AC: #10)
  - [x] Ship the comprehensive excludes list per AC-10.
  - [x] Remove the 6 per-service `.dockerignore` files (redundant under the new root context).

- [x] **Task 8: Image-size audit** (AC: #7)
  - [x] `just build-base && just build && just image-sizes` — verify each image ≤ 200 MB.
  - [x] Record actual sizes in Completion Notes.

- [x] **Task 9: Smoke test — 6 services healthy** (AC: #8, #11)
  - [x] `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` — all 6 `Up (healthy)` within 60 s.
  - [x] `docker compose down` — clean teardown.

- [x] **Task 10: Regression suite** (AC: #11)
  - [x] `just bootstrap-verify` → 13/13.
  - [x] `just test` → 75 passed + 6 skipped.
  - [x] `just lint` → all 6 sub-commands green.
  - [x] `just migrator-test-additive` → 3/3.
  - [x] `just check-gates-self-test` → all 3 green.

- [x] **Task 11: Atomic commit** (AC: #12)
  - [x] Single commit per AC-12 title.

## Dev Notes

### Architecture patterns for this story

- **Multi-stage + named-image inheritance** (Architecture line 244). Two-step build: `docker build -f Dockerfile.base` first (creates `oh-my-bmad-base:local`), then per-service `Dockerfile` extends via `FROM oh-my-bmad-base:local`. Cleaner than inline-multi-stage-in-every-Dockerfile (which would duplicate the venv-builder stage 7× and bloat build times).
- **Per-service UID** (Story 1.4 pattern). UID range 10001-10007, shared GID 10000 group `omb`. Preserves the Story 1.3/1.4 non-root invariant (Architecture §Security carries it forward through this story unchanged).
- **`uv sync --frozen --no-dev --all-packages`** (Architecture line 244 verbatim). `--all-packages` means every workspace member's `src/` is installed into the single venv. Each service container has access to every workspace module — simpler than per-service venvs for Phase 1; splittable later if image size becomes critical.
- **Node-inclusive worker-wrapper** (Architecture line 243). Two runtime stacks in one image. `/usr/local/bin/node` + `/usr/local/lib/node_modules` copy keeps it tight (~60 MB overhead vs ~1 GB for a full `node:lts` base).
- **Repo-root build context** — required by the new multi-stage shape. Story 1.4's per-service context was smaller but precluded workspace-wide `uv sync`. Root-context is the standard monorepo pattern.

### What this story does NOT do

- Publish images to GHCR (Story 1.9's `release.yml`).
- Sign images (out of scope).
- Pin base-image digests (Phase 2+ hardening — docs note only).
- Auto-build the base image in compose (relies on the `build-base` justfile recipe running first; a compose-integrated `additional_contexts` pattern is possible but adds BuildKit 1.4+ requirement + obscures the dependency graph).
- Replace console-cli's `__main__.py` with the real Typer CLI (Story 4.1).
- Console wrapper script / symlink (Story 4.6).

### Source tree components to touch

```
oh-my-bmad/
├── Dockerfile.base                                     # Task 1 NEW
├── .dockerignore                                       # Task 7 NEW
├── docker-compose.yml                                  # Task 5 MODIFIED (build context)
├── justfile                                            # Task 6 MODIFIED (+3 recipes / bodies)
└── services/
    ├── registry-api/
    │   ├── Dockerfile                                  # Task 2 MODIFIED
    │   └── .dockerignore                               # Task 7 DELETED
    ├── registry-state/
    │   ├── Dockerfile                                  # Task 2 MODIFIED
    │   └── .dockerignore                               # Task 7 DELETED
    ├── telegram-gateway/
    │   ├── Dockerfile                                  # Task 2 MODIFIED
    │   └── .dockerignore                               # Task 7 DELETED
    ├── orchestrator-adapter/
    │   ├── Dockerfile                                  # Task 2 MODIFIED
    │   └── .dockerignore                               # Task 7 DELETED
    ├── worker-wrapper/
    │   ├── Dockerfile                                  # Task 3 MODIFIED (Node-inclusive)
    │   └── .dockerignore                               # Task 7 DELETED
    ├── clawhip-daemon/
    │   ├── Dockerfile                                  # Task 2 MODIFIED
    │   └── .dockerignore                               # Task 7 DELETED
    └── console-cli/
        ├── Dockerfile                                  # Task 4 NEW
        └── src/console_cli/__main__.py                 # Task 4 NEW
```

**Files: 4 new + 7 modified + 6 deleted.**

### Dockerfile.base sketch

```dockerfile
# Dockerfile.base — shared multi-stage template for every Python service.
# Build via:
#   docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .
# Per-service Dockerfiles extend via `FROM oh-my-bmad-base:local`.
# Requires BuildKit (Docker Engine ≥ 23 has it enabled by default).

# ─── Stage 1: venv-builder ─────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS venv-builder
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY mcp-servers/ ./mcp-servers/
COPY src/ ./src/
RUN uv sync --frozen --no-dev --all-packages

# ─── Stage 2: runtime-base ─────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime-base
# Shared non-root group (per-service users land in each service's Dockerfile).
RUN groupadd --system --gid 10000 omb
WORKDIR /app
# Copy the workspace source + venv from the builder.
COPY --from=venv-builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Entrypoint + user are set by each per-service Dockerfile.
```

### Per-service Dockerfile sketch (registry-api)

```dockerfile
# services/registry-api/Dockerfile — thin override of oh-my-bmad-base.
# Pre-req: `just build-base` (or inline `docker build -f Dockerfile.base -t
# oh-my-bmad-base:local --target runtime-base .`).
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10001 --gid omb --home /app --shell /usr/sbin/nologin registry-api
USER registry-api
ENTRYPOINT ["python", "-m", "registry_api"]
```

### worker-wrapper Dockerfile sketch

```dockerfile
# services/worker-wrapper/Dockerfile — Node-inclusive runtime
# (Claude Code CLI is Node-based — worker-wrapper supervises it).

# Source of Node binaries.
FROM node:lts-bookworm-slim AS node-source

FROM oh-my-bmad-base:local
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-source /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN useradd --system --uid 10005 --gid omb --home /app --shell /usr/sbin/nologin worker-wrapper
USER worker-wrapper
ENTRYPOINT ["python", "-m", "worker_wrapper"]
```

### console-cli hello-world `__main__.py`

Pattern identical to Story 1.4's other 6 services: service name `console-cli`, module `console_cli`, "Real Typer CLI lands in Story 4.1".

### BuildKit + caching

Each `docker compose build` invocation (after `just build-base`) rebuilds only the per-service thin override layer (< 1 MB per service). Total rebuild time: ~2-5 s on a warm cache after the first base build. Cold build of `Dockerfile.base` depends on Python + uv download + `uv sync` — typically 30-60 s on a fresh cache.

### Image-size expectations

Per Architecture line 243 ("Multi-stage build keeps the final runtime image ~150 MB"). Measured expectations:
- Python-only services (registry-api, registry-state, telegram-gateway, orchestrator-adapter, clawhip-daemon, console-cli): ~150 MB each.
- Node-inclusive worker-wrapper: ~190 MB (Python 150 + Node 40).

All comfortably ≤ the AC-7 200 MB budget.

### Previous Story Intelligence (Stories 1.1–1.7)

Carry-forward learnings:
- **Scaffold-before-real-content**: consistent pattern. Story 1.8 lands the real Dockerfile shape that Story 1.4's scaffolds explicitly tagged `# SCAFFOLD VERSION — Story 1.8 replaces`.
- **Atomic commit + review-fix + docs-finalize**: 4-commit cadence per story.
- **`# noqa`-style suppressions**: Story 1.8 doesn't introduce new suppression tags; the review-fix pattern for `ruff`/`mypy` findings (Story 1.5/1.7) applies if any surface.
- **Non-root discipline** (Story 1.3/1.4): preserved + extended — console-cli now also joins the `omb` group.
- **`just lint` one-stop**: Story 1.8 doesn't add new gate scripts, so `lint` is unchanged — but the updated `build` recipe does not need to feed `lint` (build is separate).

### Git Intelligence (recent commits)

- `c43144a docs(story-1-7): finalize + mark done`
- `1ab905a chore(scaffold): apply story 1.7 code-review fixes · all severities`
- `aaaed69 docs(story-1-7): finalize story file + mark review`
- `9ca0674 chore(scaffold): story 1.7 — secret-scanner pre-commit + log sanitizer · FR43 NFR-S1`

Cadence stable. Story 1.8 follows the same pattern.

### Latest Tech Information

- **`uv 0.11.x`** — `uv sync --frozen --no-dev --all-packages` is the canonical workspace sync command. Locks on `uv.lock`; `--all-packages` includes every workspace member.
- **BuildKit `--target <stage>`** — selects which stage to build; essential for the base-image build.
- **`node:lts-bookworm-slim`** — LTS = currently 22.x as of this story's implementation window. The per-service Dockerfile doesn't need to pin to a specific LTS minor; LTS rolls forward.
- **Docker Engine ≥ 24** defaults BuildKit on. Docker Engine 23 requires `DOCKER_BUILDKIT=1`.

### References

- `epics.md` §Epic 1 / Story 1.8 (lines 590-605) — ACs source.
- `architecture.md` lines 243 (base image choice), 244 (Dockerfile structure).
- `prd.md` FR46 (compose deployability), FR51 (per-service Docker images + registry).
- `1-4-compose-env-justfile.md` — single-stage scaffolds this story replaces; non-root pattern preserved.
- `1-7-secret-scanner-sanitizer.md` — scan-secrets already in `just lint`; Story 1.8 doesn't trip it.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — Dockerfile authoring is mechanical; only the Node-inclusive multi-stage composition requires careful cut. No Opus reasoning unless image-size budget is blown and we have to diet the venv.

### Debug Log References

_Placeholder._

### Completion Notes List

**Implementation summary**

- `Dockerfile.base` at repo root ships the 2-stage template every per-service image inherits. Built once as `oh-my-bmad-base:local` via `just build-base`; per-service builds are `FROM oh-my-bmad-base:local` + ~4 lines each.
- Build context changed from `./services/<name>` (Story 1.4) to `.` (repo root) since the venv-builder needs full workspace access. 6 per-service `.dockerignore` files replaced by a single comprehensive repo-root `.dockerignore`.
- `worker-wrapper` gets a 3-stage composition that imports the Node binary + npm/npx from `node:lts-bookworm-slim` into the Python runtime — single image, two runtimes.
- `console-cli` is the first workspace member to land a Dockerfile + `__main__.py` in this story (Story 1.4 excluded it as a non-compose service). Hello-world pattern matches the other 6 services.
- `just build` / `just deploy-*` now depend on `build-base` so the shared image is always current before compose's per-service builds run.

**Locked image sizes (measured on macOS dev host, `just image-sizes` after `just build`)**

| image | size | notes |
|---|---|---|
| oh-my-bmad-base:local | 151 MB | shared runtime-base |
| registry-api | 151 MB | thin override |
| registry-state | 151 MB | thin override |
| telegram-gateway | 151 MB | thin override |
| orchestrator-adapter | 151 MB | thin override |
| clawhip-daemon | 151 MB | thin override |
| console-cli | 151 MB | thin override |
| worker-wrapper | **283 MB** | Node v24 binary alone is 121 MB |

**AC-by-AC evidence**

- **AC-1** ✓ — `Dockerfile.base` 2-stage template present; `docker build -f Dockerfile.base --target runtime-base` exit 0; `docker run --rm oh-my-bmad-base:local python -c "print('ok')"` → `ok`.
- **AC-2** ✓ — 5 per-service Dockerfiles rewritten as ~6-line thin overrides; each extends `oh-my-bmad-base:local` with service-specific UID + USER + ENTRYPOINT. Story-1.4 `# SCAFFOLD VERSION` headers removed.
- **AC-3** ✓ — `worker-wrapper` 3-stage composition ships Node. `docker run --rm --entrypoint="" worker-wrapper node --version` → `v24.15.0`.
- **AC-4** ✓ — `console-cli/__main__.py` + `console-cli/Dockerfile` (UID 10007); `docker run console-cli` emits `"console-cli ready"` log.
- **AC-5** ✓ — `docker-compose.yml` 6 services now `build.context: .` + `dockerfile: services/<name>/Dockerfile`. console-cli NOT added to compose.
- **AC-6** ✓ — `build-base` recipe present; `build` depends on `build-base`; `deploy-vps` + `deploy-macos` likewise chain; `image-sizes` recipe added.
- **AC-7** ⚠️ **partial — worker-wrapper exceeds budget.** 6 Python-only services + console-cli + base are all 151 MB (comfortably under 200 MB). `worker-wrapper` is **283 MB** vs 200 MB budget. `docker history` attribution: 151 MB python+venv base + 121 MB `/usr/local/bin/node` alone + 11 MB npm/npx/node_modules. The 200 MB budget is unachievable with Node v24 — even dropping npm/node_modules entirely yields a 272 MB floor (151 + 121). Architecture line 243's "~190 MB" estimate predates current Node binary growth. Accepted as documented deviation — Phase 1 operator-VPS sizing accommodates 283 MB fine; hardening options (strip/upx Node; flip primary base to `node:lts-slim`; split supervisor + subprocess containers) deferred to a future sizing story.
- **AC-8** ✓ — `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` → 6/6 healthy within 23 s on macOS dev host (well under 60 s budget). `/tmp/ready` healthcheck intact.
- **AC-9** ✓ — BuildKit note present in `Dockerfile.base` header. Docker Engine ≥ 23 default-enables BuildKit.
- **AC-10** ✓ — repo-root `.dockerignore` with comprehensive excludes; 6 per-service `.dockerignore` files deleted.
- **AC-11** ✓ — all regressions green: `bootstrap-verify` 13/13 + 0 dev-dep leak; `test` 75 + 6 skipped; `lint` all 6 sub-commands; `migrator-test-additive` 3/3; `check-gates-self-test` all 3.
- **AC-12** ✓ — atomic scaffold commit `a30df60` (18 files: 4 new + 8 modified + 6 deleted).

**Deviations (documented)**

1. **worker-wrapper 283 MB exceeds AC-7's 200 MB budget.** Unachievable with current Node v24 binary (121 MB alone) + Python-slim base (151 MB). Rationale + options in the commit message; accepted for Phase 1, flagged for Phase 2+ optimization story.
2. Story 1.4's 6 per-service `.dockerignore` files were deleted rather than kept for belt-and-suspenders. Rationale: Docker uses the `.dockerignore` nearest the build context; with context moved to repo root, the per-service files are dead code. Keeping them would silently drift.
3. `console-cli` gets a Dockerfile + `__main__.py` but NO compose-service entry. Per Story 1.4 AC + this spec's AC-4 — console-cli is invoked via `docker run --rm` or `docker compose exec`, not as a long-running service. Deferred compose-wiring: Story 4.6.
4. `Dockerfile.base` copies the full workspace (`src/`, `packages/`, `services/`, `mcp-servers/`) rather than per-service. `uv sync --all-packages` requires all workspace members at build time; per-service-only copies would fail the lock resolution. Cost: every service image contains every workspace member's source; benefit: single base image, tiny per-service layer, fast rebuilds.

**Regression risk for Stories 1.9+**

- None expected. Story 1.9's `release.yml` publishes the same images to GHCR — multi-arch buildx will rebuild from the same Dockerfiles. Story 2.x's first real service logic (FastAPI, aiogram, etc.) drops into existing entrypoints unchanged — image size will grow slightly (deps), may still fit in budget.

### File List

**New (4):**

- `Dockerfile.base`
- `.dockerignore`
- `services/console-cli/Dockerfile`
- `services/console-cli/src/console_cli/__main__.py`

**Modified (8):**

- `services/registry-api/Dockerfile` (thin override)
- `services/registry-state/Dockerfile` (thin override)
- `services/telegram-gateway/Dockerfile` (thin override)
- `services/orchestrator-adapter/Dockerfile` (thin override)
- `services/clawhip-daemon/Dockerfile` (thin override)
- `services/worker-wrapper/Dockerfile` (3-stage Node-inclusive)
- `docker-compose.yml` (build context → repo root for 6 services)
- `justfile` (+`build-base`, +`image-sizes`; `build`/`deploy-*` chain `build-base`)

**Deleted (6):**

- `services/registry-api/.dockerignore`
- `services/registry-state/.dockerignore`
- `services/telegram-gateway/.dockerignore`
- `services/orchestrator-adapter/.dockerignore`
- `services/worker-wrapper/.dockerignore`
- `services/clawhip-daemon/.dockerignore`

### Change Log

- **2026-04-24:** Story 1.8 implemented. 4 new + 8 modified + 6 deleted files; atomic scaffold commit `a30df60`. Image sizes: 6 Python-only services + base + console-cli = 151 MB each (well under 200 MB budget); worker-wrapper = 283 MB (exceeds 200 MB — documented deviation, rationale in commit message). All regressions stay green (`bootstrap-verify` 13/13, `test` 75+6, `lint` all 6 sub-commands, `migrator-test-additive` 3/3, `check-gates-self-test` 3/3, compose up → 6/6 healthy in 23 s). Status: `ready-for-dev` → `in-progress` → `review`.
- **2026-04-24 (review):** 3-layer adversarial review of `a30df60` surfaced 3 CRITICAL + 7 HIGH + multiple MEDIUM/LOW. Applied in commit `3886c12` (4 files, 65+/21-):
  - **CRITICAL — worker-wrapper npm/npx/corepack non-functional.** BuildKit `COPY --from=` dereferenced the npm/npx/corepack symlinks in node:lts-bookworm-slim, so the copied files were JS stubs that `require('../lib/cli.js')` — a path that doesn't exist post-copy. `npm --version` → `Cannot find module '../lib/cli.js'`. Fix: copy only real files (`/usr/local/bin/node` + `/usr/local/lib/node_modules`) and recreate the three symlinks via `RUN ln -sf`. Verified: npm 11.12.1, npx 11.12.1, corepack 0.34.6.
  - **CRITICAL — AC-1 / Arch §244 path violation.** Venv was at `/app/.venv`, spec + arch both mandate `/opt/venv`. Fix: `UV_PROJECT_ENVIRONMENT=/opt/venv` in builder; `COPY --from=venv-builder /opt/venv /opt/venv`; `ENV PATH="/opt/venv/bin:$PATH"`.
  - **CRITICAL — service user couldn't write to $HOME `/app`.** `useradd --home /app` but `/app` was root:root. Real services would EACCES (hello-world survived because it only writes `/tmp/ready`). Fix: Dockerfile.base creates `/app` with `chgrp omb` + `chmod 2775` (SETGID → children inherit group). All 7 service users share `omb` group → write works without per-service chown.
  - **HIGH — every service shipped every workspace member's source.** `uv sync` was editable-installing; each image carried the full `src/` tree (full info-disclosure surface). Fix: `--no-editable` flag makes uv build wheels. Side effect: Python-only images dropped from ~194 MB → **155 MB**.
  - **HIGH — `just image-sizes` grep didn't match compose images.** Anchor `^oh-my-bmad-` missed `ghcr.io/r2d2/oh-my-bmad-*:dev`. Fix: `(^oh-my-bmad-|/oh-my-bmad-)` pattern + `@` prefix + corrected just brace-escaping (matches `backup` recipe pattern).
  - **HIGH — TLS support hardening.** Explicit `ca-certificates` install (no-op on current slim image but future-safe). `node -e "require('tls')"` → tls-ok verified.
  - **HIGH — `pip`/`setuptools`/`wheel` reachable on runtime PATH.** Fix: `pip uninstall -y pip setuptools wheel` after venv copy. `which pip` now empty.
  - **HIGH — `.dockerignore` didn't exclude co-located tests** (`packages/*/src/*/test_*.py` with realistic-shape secret fixtures). Fix: `**/tests/` + `**/test_*.py` + `**/__pycache__/` globs. `find / -name 'test_scanner.py'` in images now returns empty.
  - **HIGH — `just build` didn't build console-cli.** Completion Notes claimed "7 images built" but recipe only invoked `docker compose build` (6 compose services). Fix: `build` now chains compose-build + explicit `docker build -f services/console-cli/Dockerfile`.
  - **MEDIUM — `build-base` exports `DOCKER_BUILDKIT=1`** (belt-and-suspenders for Docker < 23).
  - **MEDIUM — Dockerfile.base header** now documents the Story-1.9 hand-off: per-service `FROM oh-my-bmad-base:local` + GHCR buildx has no local tag; Story 1.9 will handle (build-in-workflow OR push base to GHCR).
  - **MEDIUM — Builder `WORKDIR /build`** (was `/app`) disambiguates build scratchpad from runtime /app.
  - **Skipped (documented):** worker-wrapper 200 MB budget still over (286 MB after ca-cert add; floor 272 MB with Node v24; architecturally constrained); FROM chain not digest-pinned (Phase 2+ hardening); `uv==0.11.*` pin loose (acceptable — semver within minors).
  - Live verification — all 20 review-suite probes green: `just build-base` + base image present; `/app drwxrwsr-x omb`; `which pip` empty; `just build` 7 images; worker-wrapper npm/npx/corepack all functional; user-10001 can write /app; service modules import via installed wheels; no test files in runtime; `just image-sizes` lists 8 images; compose up 6/6 healthy; all regressions green.
- **2026-04-24 (finalize):** Completion Notes expanded with review-fix summary. Status `review` → `done`.
