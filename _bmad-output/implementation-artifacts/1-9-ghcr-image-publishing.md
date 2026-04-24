# Story 1.9: GHCR image publishing on git tag

Status: review

## Story

As the **operator**,
I want **`.github/workflows/release.yml` to build + publish multi-arch (linux/amd64 + linux/arm64) Docker images for every platform-owned service to GHCR on git tag push**,
so that **deploying a new version is a `docker compose pull && up -d` away — no manual image building on the VPS, and the hand-off gap Story 1.8 flagged (per-service Dockerfiles reference the local `oh-my-bmad-base:local` which doesn't exist on GHCR's buildx runner) is explicitly resolved**.

## Acceptance Criteria

1. **AC-1: `.github/workflows/release.yml` triggers on git tag push matching `v*`.** The workflow:
   - Triggers on `push.tags: ['v*']` (e.g. `v0.1.0`, `v1.2.3-rc1`, `v0.2.0`).
   - Has `permissions: contents: read, packages: write` (minimum for pushing to GHCR).
   - Uses `concurrency: {group: release-${{ github.ref }}, cancel-in-progress: false}` — releases do NOT cancel each other.
   - `runs-on: ubuntu-24.04`.
   - `timeout-minutes: 45` (multi-arch buildx + 7 services + cross-compilation is the budget).

2. **AC-2: Base-image hand-off resolved.** Story 1.8 flagged this: per-service Dockerfiles start `FROM oh-my-bmad-base:local` which is a local-only tag the GHCR runner doesn't have. The release workflow publishes the base image to GHCR as a first-class artifact + retags per-service FROMs at build time. Concrete approach: buildx `--build-context base=docker-image://ghcr.io/<owner>/oh-my-bmad-base:<version>` injects the base. Per-service Dockerfiles keep `FROM oh-my-bmad-base:local` for local `just build-base` convenience; the release workflow builds the base FIRST (step 1) and uses `--build-context base=...` to override the FROM reference during per-service builds. This avoids editing per-service Dockerfiles at release time.

   Alternatively (simpler — chosen approach): the workflow includes a short `sed` / template step that writes a temporary `Dockerfile.<service>.release` replacing `FROM oh-my-bmad-base:local` with `FROM ghcr.io/<owner>/oh-my-bmad-base:<version>` and builds from that. Trade-off: slightly less elegant than `--build-context` but more legible.

   **Decision: go with `--build-context base=...` for cleanliness.** Document this in the workflow's header comment so future contributors understand the indirection.

3. **AC-3: Images built + pushed for 7 services.** Each image gets two tags: the version (`0.1.0` extracted from `v0.1.0` tag — strip the `v` prefix) AND `latest`. Services: registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon, console-cli.
   - Image name pattern: `ghcr.io/<owner>/oh-my-bmad-<service>:<version>` + `ghcr.io/<owner>/oh-my-bmad-<service>:latest`.
   - `<owner>` is derived from `${{ github.repository_owner }}` (so the workflow works for any fork without config changes).
   - Multi-arch: `linux/amd64,linux/arm64` via `docker buildx build --platform linux/amd64,linux/arm64 --push`.

4. **AC-4: Base image also published** as `ghcr.io/<owner>/oh-my-bmad-base:<version>` + `:latest`. This is technically a new public artifact (Story 1.8 treated it as local-only); publishing it is the prerequisite that AC-2 depends on. Documented in the workflow + `.env.example` so operators can override `OMB_IMAGE_REGISTRY` / `OMB_VERSION` correctly post-release.

5. **AC-5: GHCR login via `GITHUB_TOKEN`.** The workflow uses `docker/login-action@v3` with `username: ${{ github.actor }}` + `password: ${{ secrets.GITHUB_TOKEN }}`. No additional secrets required — GHCR accepts the workflow's identity token when `packages: write` permission is granted.

6. **AC-6: buildx + QEMU setup.**
   - `docker/setup-qemu-action@v3` — enables `linux/arm64` cross-compilation on an amd64 runner.
   - `docker/setup-buildx-action@v3` — enables BuildKit's multi-platform + cache features.
   - Optional: `docker/build-push-action@v6` for per-service builds (cleaner than raw `docker buildx build` calls).

7. **AC-7: Build-time caching across workflow runs.** Use GitHub Actions cache (`type=gha`) to persist BuildKit layers:
   ```yaml
   cache-from: type=gha,scope=<service>
   cache-to: type=gha,mode=max,scope=<service>
   ```
   Base image uses `scope=base`; each service uses `scope=<service-name>`. Different scopes prevent cache cross-contamination.

8. **AC-8: Version extraction.** The workflow extracts the semver from the git tag `v0.1.0` → `0.1.0`:
   ```yaml
   - name: Extract version
     id: ver
     run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"
   ```
   Downstream steps reference `${{ steps.ver.outputs.version }}`.

9. **AC-9: Job matrix for per-service builds** (DRY — 7 services × near-identical build steps). Single matrix job:
   ```yaml
   jobs:
     build-and-push-base:
       # Builds & pushes the base FIRST, since services depend on it.

     build-and-push-services:
       needs: build-and-push-base
       strategy:
         matrix:
           service:
             - registry-api
             - registry-state
             - telegram-gateway
             - orchestrator-adapter
             - worker-wrapper
             - clawhip-daemon
             - console-cli
         fail-fast: false
   ```
   `fail-fast: false` so a failure in one service doesn't cancel the others — publishes get as much done as possible.

10. **AC-10: README update — install-from-registry path.** README gains a short paragraph explaining that released images are available on GHCR + how to pull them:
    - `OMB_IMAGE_REGISTRY=ghcr.io/<owner>` in `.env` (already present per Story 1.4 — verify).
    - `OMB_VERSION=0.1.0` (or whatever tag) instead of the default `dev`.
    - `docker compose pull && docker compose up -d` upgrades in-place.
    - Placement: after the existing Quickstart (Story 1.1) + Backup/restore (Story 1.4-ish) + before the Schema Evolution (Story 1.3) section. Could go under a new "Upgrading" subsection — story leaves the exact placement to the dev agent but requires the content.

11. **AC-11: `.env.example` TLD/hint update.** The existing `OMB_IMAGE_REGISTRY=ghcr.io/r2d2` default from Story 1.4 stays, but the comment gets a tweak clarifying that Story 1.9 now populates this registry: `# Story 1.9 release.yml publishes to this registry; Story 1.4 seeded the var.`. Minor wording.

12. **AC-12: `justfile` — optional `release-local` recipe.** Operators can sanity-check the multi-arch build locally before pushing a tag:
    ```justfile
    # Build multi-arch images locally via buildx (no push). Useful for
    # validating the release workflow's image shape before tagging. Requires
    # `docker buildx create --name <name> --use` + QEMU emulation.
    release-local version="dev":
        docker buildx build --platform linux/amd64,linux/arm64 -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:{{version}} .
        # Per-service builds would follow the same pattern.
    ```
    Minimal version — keeps the recipe as a diagnostic hook, not a full release mirror. Add or omit per operator preference; flagged in the story but not a hard requirement.

13. **AC-13: Regression.** All Story 1.1–1.8 verifications stay green:
    - `just bootstrap-verify` 13/13 + 0 dev-dep leak.
    - `just test` 75+6.
    - `just lint` all 6 sub-commands.
    - `just migrator-test-additive` 3/3.
    - `just check-gates-self-test` 3/3.
    - `just build` 7 images still build locally.
    - `docker compose up -d` 6/6 healthy.

14. **AC-14: Atomic commit.** All new/modified files land in one commit titled `chore(scaffold): story 1.9 — GHCR multi-arch release workflow · FR51 FR52`. Docs-only follow-ups permitted.

## Tasks / Subtasks

- [x] **Task 1: Write `.github/workflows/release.yml`** (AC: #1–#9)
  - [ ] `name: release` + on-tag-push trigger (`v*`).
  - [ ] `permissions: contents: read, packages: write`.
  - [ ] `concurrency: release-<ref>` + cancel-in-progress false.
  - [ ] Job 1: `build-and-push-base` — extracts version, logs into GHCR, buildx setup, builds + pushes `ghcr.io/<owner>/oh-my-bmad-base:<version>` + `:latest`.
  - [ ] Job 2: `build-and-push-services` with matrix for 7 service names — needs: base; uses `--build-context base=docker-image://ghcr.io/<owner>/oh-my-bmad-base:<version>` to override the `FROM oh-my-bmad-base:local` reference.
  - [ ] Tag both `<version>` and `latest` on every service + base image.
  - [ ] Cache scoped per service via `type=gha`.
  - [ ] YAML-valid.

- [x] **Task 2: README update** (AC: #10)
  - [ ] Add an "Upgrading" subsection explaining the pull-from-GHCR flow.
  - [ ] Reference the `OMB_IMAGE_REGISTRY` + `OMB_VERSION` env vars (Story 1.4's `.env.example`).
  - [ ] One-sentence note that Phase 1 uses tag-based releases; digest-pinning lands in Phase 2 hardening.

- [x] **Task 3: `.env.example` comment tweak** (AC: #11)
  - [ ] Update the `OMB_IMAGE_REGISTRY` / `OMB_VERSION` comments to mention Story 1.9 as the producer.

- [x] **Task 4: Optional `release-local` recipe in `justfile`** (AC: #12)
  - [ ] Shipped per spec — diagnostic hook only.
  - [ ] Fully optional; if operator time is tight, skip with note.

- [x] **Task 5: Regression check** (AC: #13)
  - [ ] Run full regression suite.

- [x] **Task 6: Workflow dry-run** (AC: #1)
  - [ ] `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` via `uv run --with pyyaml` → exit 0.
  - [ ] `act` dry-run optional; document as future enhancement since `act` doesn't fully support buildx+QEMU.
  - [ ] Real verification defers to the first `v*` tag push — document this in Completion Notes.

- [x] **Task 7: Atomic commit** (AC: #14)
  - [ ] Single commit per AC-14 title.

## Dev Notes

### Architecture patterns for this story

- **Multi-arch via buildx + QEMU** (Arch line 245: "Separate `.github/workflows/release.yml` on git tag: build + publish multi-arch Docker images to GHCR"). Single x86 runner uses QEMU to cross-compile arm64. Slower than a native arm64 runner but no GitHub-provided arm64 runner is available on the free tier for personal projects.
- **GHCR as the registry** (Arch line 246). `ghcr.io/<owner>/oh-my-bmad-<service>` naming; tag-per-version + `latest`. Free for personal projects, GitHub-native auth (no API key in operator secrets).
- **Base-image hand-off** — resolved via buildx `--build-context base=docker-image://...`. The per-service Dockerfiles stay unchanged (`FROM oh-my-bmad-base:local` works locally after `just build-base`); release workflow overrides the `base` image source at build time.
- **Cache-scoped per service** — one cache per service prevents cross-contamination. Story 1.8's `Dockerfile.base` is the venv-builder stage; caching it separately (scope=base) means service builds skip the expensive `uv sync` step on every rebuild.
- **`fail-fast: false`** — operator can't tell which service broke unless all 7 run to completion. Failure isolation > failure speed.

### What this story does NOT do

- Digest-pin base images (Phase 2+ supply-chain hardening).
- Sign images (cosign / sigstore — Phase 2+).
- Vulnerability scan (trivy / grype — Phase 2+).
- SBOM generation (syft — Phase 2+).
- Non-GHCR mirrors (Docker Hub, AWS ECR — deliberate Phase 1 simplification).
- Rolling-release channel (the `latest` tag is the implicit stable channel; no nightly/edge).
- Release notes generation (separate workflow / release-please / etc. — out of scope).
- Real GitHub remote configuration (operator question from Story 1.1 — the workflow won't run until a remote is wired + a tag pushed). Documented in Completion Notes.

### Source tree components to touch

```
oh-my-bmad/
├── .github/workflows/release.yml       # Task 1 NEW
├── .env.example                        # Task 3 MODIFIED (comment tweak)
├── README.md                           # Task 2 MODIFIED (+Upgrading section)
└── justfile                            # Task 4 MODIFIED (optional release-local recipe)
```

**Files: 1 new + 3 modified. Small story.**

### `release.yml` sketch

```yaml
name: release

# Tag-triggered multi-arch build + push of all platform-owned service images
# to GHCR. See Story 1.9 spec for design rationale.
#
# Hand-off from Story 1.8: per-service Dockerfiles `FROM oh-my-bmad-base:local`
# which doesn't exist on a fresh runner. We build + push the base FIRST, then
# override the base reference via buildx `--build-context base=...` during
# per-service builds.

on:
  push:
    tags: ['v*']

permissions:
  contents: read
  packages: write

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false

env:
  REGISTRY: ghcr.io
  OWNER: ${{ github.repository_owner }}

jobs:
  build-and-push-base:
    name: Build + push base image
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    outputs:
      version: ${{ steps.ver.outputs.version }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Extract version from tag
        id: ver
        run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build + push base (multi-arch)
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.base
          target: runtime-base
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.OWNER }}/oh-my-bmad-base:${{ steps.ver.outputs.version }}
            ${{ env.REGISTRY }}/${{ env.OWNER }}/oh-my-bmad-base:latest
          cache-from: type=gha,scope=base
          cache-to: type=gha,mode=max,scope=base

  build-and-push-services:
    name: Build + push ${{ matrix.service }}
    needs: build-and-push-base
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    strategy:
      fail-fast: false
      matrix:
        service:
          - registry-api
          - registry-state
          - telegram-gateway
          - orchestrator-adapter
          - worker-wrapper
          - clawhip-daemon
          - console-cli
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build + push ${{ matrix.service }} (multi-arch)
        uses: docker/build-push-action@v6
        with:
          context: .
          file: services/${{ matrix.service }}/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.OWNER }}/oh-my-bmad-${{ matrix.service }}:${{ needs.build-and-push-base.outputs.version }}
            ${{ env.REGISTRY }}/${{ env.OWNER }}/oh-my-bmad-${{ matrix.service }}:latest
          # Story 1.8 hand-off: replace `FROM oh-my-bmad-base:local` with the
          # just-pushed GHCR image. `--build-context base=...` tells buildx to
          # resolve the named context to the given image.
          build-contexts: |
            oh-my-bmad-base:local=docker-image://${{ env.REGISTRY }}/${{ env.OWNER }}/oh-my-bmad-base:${{ needs.build-and-push-base.outputs.version }}
          cache-from: type=gha,scope=${{ matrix.service }}
          cache-to: type=gha,mode=max,scope=${{ matrix.service }}
```

### README "Upgrading" section sketch

```markdown
## Upgrading

Released images live on GHCR (`ghcr.io/<owner>/oh-my-bmad-<service>`).
To upgrade a running deployment:

1. Edit `.env`:

       OMB_IMAGE_REGISTRY=ghcr.io/<owner>
       OMB_VERSION=0.2.0                  # or whichever tag you want

2. Pull + restart:

       docker compose pull
       docker compose up -d

Compose stops each service, pulls the new tag, and starts it with preserved
volumes. Persistent data (registry DB, event log, artifacts) survives the
upgrade.

Phase 1 uses tag-based versioning; digest-pinning + signed-image verification
land in a Phase 2 hardening story.
```

### Why `build-contexts:` rather than the `sed` alternative?

The `sed`-rewrite approach (temporarily modify per-service Dockerfiles, build, revert) is uglier but debuggable — you can inspect the rewritten Dockerfile. `--build-context` is BuildKit-native and doesn't mutate files, so PRs / diffs stay clean. Both approaches work; we pick the BuildKit-native for operator clarity. Document in the workflow header comment so future contributors understand.

### Previous Story Intelligence (Stories 1.1–1.8)

Carry-forward learnings:
- **Scaffold-before-real-content**: Story 1.9's release.yml will run for real only once a git remote is wired + a tag pushed. Like Story 1.5's ci.yml, it's aspirational infrastructure until the operator takes the "git remote + first push" step (Story 1.1 open question).
- **Atomic commit + review-fix + docs-finalize** cadence preserved.
- **BuildKit everywhere**: Story 1.8 confirmed Docker Engine ≥ 23 has BuildKit on by default; the release workflow's `docker/setup-buildx-action@v3` is explicit about it.
- **Non-digest-pinned FROM chain**: Story 1.8 accepted this as Phase 2 hardening; Story 1.9 inherits the decision — release images will likewise be tag-pinned, not digest-pinned.
- **worker-wrapper 286 MB**: will be the largest published image. GHCR's free tier accommodates easily.

### Git Intelligence (recent commits)

- `db582b6 docs(story-1-8): finalize + mark done`
- `3886c12 chore(scaffold): apply story 1.8 code-review fixes · all severities`
- `fef9dfd docs(story-1-8): finalize story file + mark review`
- `a30df60 chore(scaffold): story 1.8 — Dockerfile.base + multi-stage per-service builds · FR46 FR51`

### Latest Tech Information

- **`docker/build-push-action@v6`** — current major; supports `build-contexts:` input for named-context injection.
- **`docker/setup-qemu-action@v3`** — QEMU emulation for arm64 builds on amd64 runners.
- **`docker/setup-buildx-action@v3`** — BuildKit multi-platform.
- **`docker/login-action@v3`** — GHCR login via `GITHUB_TOKEN`.
- **`type=gha` cache** — GitHub Actions-native BuildKit cache; no external storage needed.
- **`${{ github.repository_owner }}`** — derives GHCR path without hard-coding a specific owner; works for any fork.
- **`strategy.fail-fast: false`** — matrix job independence.

### References

- `epics.md` §Epic 1 / Story 1.9 (lines 607–623) — ACs source.
- `architecture.md` lines 245 (GitHub Actions + release.yml), 246 (GHCR as image registry).
- `prd.md` FR51 (line 884 — package + publish images), FR52 (line 885 — upgrade via compose tag).
- `1-5-test-tree-ci-skeleton.md` — sibling `ci.yml` (PR-gate) established permissions / concurrency / action-version conventions; `release.yml` follows the same patterns.
- `1-8-dockerfile-base-multistage.md` — the hand-off spec mandated at AC-2 was documented in Dockerfile.base's header comment during Story 1.8's review fixes.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — GHA YAML authoring is mechanical + well-established; only the `build-contexts:` hand-off needs careful cut. No Opus reasoning unless buildx behavior surprises.

### Debug Log References

_Placeholder._

### Completion Notes List

_To be filled by the dev agent. Record per AC: pass/fail + evidence._

- AC-1 — release.yml on v* tag + permissions + concurrency + timeout.
- AC-2 — `build-contexts:` hand-off wires ghcr.io/<owner>/oh-my-bmad-base as the `oh-my-bmad-base:local` reference.
- AC-3 — 7 per-service images published with version + latest tags.
- AC-4 — base image published as oh-my-bmad-base:<version> + :latest.
- AC-5 — GHCR login via GITHUB_TOKEN + packages: write.
- AC-6 — buildx + QEMU setup.
- AC-7 — `type=gha` cache with per-service scopes.
- AC-8 — version extraction via `${GITHUB_REF_NAME#v}`.
- AC-9 — matrix job + fail-fast: false.
- AC-10 — README Upgrading section added.
- AC-11 — .env.example comment tweak.
- AC-12 — optional `release-local` recipe.
- AC-13 — regressions green.
- AC-14 — atomic commit SHA.

Record: real CI trigger status (can only be verified once remote + tag exist; document as deferred).

### File List

_To be filled by the dev agent. Expected: 1 new + 3 modified._

### Change Log

_To be filled by the dev agent._
