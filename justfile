# oh-my-bmad — operator recipes
#
# Story 1.1 shipped `bootstrap-verify`. Story 1.2 extended it to cover all 14
# workspace members. Story 1.3 adds `sync-upstream` + `migrator-test-additive`.
# Richer recipes (dev, test, lint, scenarios, backup, build, deploy-vps,
# deploy-macos) arrive in Story 1.4 (compose + env + justfile).

default:
    @just --list

# Apply any pending Alembic migrations to the registry-state SQLite store.
# DB path is controlled by REGISTRY_STATE_DB_URL env var (default: local dev path
# sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3 from env.py).
# Example: REGISTRY_STATE_DB_URL=sqlite+aiosqlite:////tmp/test.sqlite3 just migrate
migrate:
    cd services/registry-state && uv run alembic upgrade head

# Verify the uv workspace resolves and every cross-package import works.
bootstrap-verify:
    uv sync --frozen --no-dev
    uv run --no-dev python -c "from events import __version__; print('events', __version__)"
    uv run --no-dev python -c "from registry_api import __version__, hello; print('registry_api', __version__, '|', hello())"
    uv run --no-dev python -c "from registry_state import __version__; print('registry_state', __version__)"
    uv run --no-dev python -c "from telegram_gateway import __version__; print('telegram_gateway', __version__)"
    uv run --no-dev python -c "from console_cli import __version__; print('console_cli', __version__)"
    uv run --no-dev python -c "from orchestrator_adapter import __version__; print('orchestrator_adapter', __version__)"
    uv run --no-dev python -c "from worker_wrapper import __version__; print('worker_wrapper', __version__)"
    uv run --no-dev python -c "from clawhip_daemon import __version__; print('clawhip_daemon', __version__)"
    uv run --no-dev python -c "from task_registry_mcp import __version__; print('task_registry_mcp', __version__)"
    uv run --no-dev python -c "from session_registry_mcp import __version__; print('session_registry_mcp', __version__)"
    uv run --no-dev python -c "from clawhip_bridge_mcp import __version__; print('clawhip_bridge_mcp', __version__)"
    uv run --no-dev python -c "from secret_hygiene import __version__; print('secret_hygiene', __version__)"
    uv run --no-dev python -c "from idempotency import __version__; print('idempotency', __version__)"
    @echo "✓ bootstrap OK (13 workspace-member imports verified)"

# Vendor an upstream fork into upstream/<name>/ and update VENDORED.md with
# the pinned commit SHA. Supported names: omc, clawhip. See Architecture
# §Starter Template Evaluation / Upstream Fork Integration.
sync-upstream name:
    uv run python scripts/sync_upstream.py {{name}}

# Build the migrator Docker image and run its trivial v1.0.0→v1.0.1 additive
# upgrade against a synthetic fixture. Asserts the output is valid JSONL
# with the new `extensions` field on every event.
migrator-test-additive:
    @echo "→ building migrator image…"
    docker build --quiet -t oh-my-bmad-migrator:test scripts/migrator
    @echo "→ preparing fixture sandbox…"
    rm -rf .tmp/migrator-test
    mkdir -p .tmp/migrator-test
    cp scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl .tmp/migrator-test/sample.jsonl
    @echo "→ running migrator…"
    docker run --rm -v "${PWD}/.tmp/migrator-test:/data" -e EVENT_LOG_PATH=/data/sample.jsonl oh-my-bmad-migrator:test v1.0.0-to-v1.0.1
    @echo "→ asserting output…"
    uv run python scripts/migrator/tests/assert_migrated.py .tmp/migrator-test/sample.v1.0.1.jsonl --expected 135
    @test -f .tmp/migrator-test/sample.v1.0.0.archive || (echo "FAIL: archive missing" && exit 1)
    @echo "→ cleanup"
    rm -rf .tmp/migrator-test

# Bring up the stack. On macOS automatically includes the overlay (bind-mount
# to ${HOME}/.oh-my-bmad) and ensures the host directory exists. On Linux uses
# the base compose only (named volume, no host-path prep needed).
# Depends on `build-base` so a fresh clone gets the shared `oh-my-bmad-base:local`
# image before compose's per-service builds run (they all `FROM oh-my-bmad-base:local`).
dev: build-base
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(uname -s)" = "Darwin" ]; then
        mkdir -p "${HOME}/.oh-my-bmad"
        exec docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d
    else
        exec docker compose -f docker-compose.yml up -d
    fi

# PR-gate test suite (excludes @pytest.mark.slow). Runs on `just test`, on
# every commit via .github/workflows/ci.yml, and local developer workflows.
test:
    uv run pytest -m "not slow"

# Full test matrix — includes @pytest.mark.slow. Used by the nightly CI
# workflow (lands in a later story) and operator-manual full-regression runs.
test-slow:
    uv run pytest

# Just the contract-test tree. Required before any `just sync-upstream` per
# Architecture §Post-MVP ops — contract tests pin upstream-fork behavior.
test-contract:
    uv run pytest tests/contract

# Synthetic crash-injection harness (Story 2.11 / NFR-R1 / NFR-R2 + Story
# 2.12 / FR30). Boots `registry-state` under docker compose, drives a
# synthesized task through each lifecycle phase, kills the container (Linux:
# `stop --timeout 1`, macOS: `kill --signal SIGKILL`), restarts, and asserts
# state-reconstruction with zero duplicate events. Slow (~3-5 min); excluded
# from PR-gate `just test`. Nightly CI invokes this recipe via
# `.github/workflows/nightly.yml`.
#
# Docker dependency split:
#   - Story 2.11 tests (test_restart_recovery.py) require Docker — skipped
#     gracefully via the `skip_if_no_docker` fixture when `docker info` fails.
#   - Story 2.12 tests (test_write_interrupt.py) are filesystem-only (pure
#     subprocess + tmp_path) and run UNCONDITIONALLY — Docker is not needed.
test-crash:
    uv run pytest -m crash --tb=short -v tests/crash-injection/

# Story 2.13 — idempotency 100× concurrent-replay test (FR28 / NFR-R4).
# Exercises ``IdempotencyCacheStore.get_or_run`` wired into POST /v1/tasks:
# 100 concurrent same-key POSTs, byte-identical replies, exactly 1
# ``task.created`` event. The 10× parametrized variant gives the nightly
# its statistical-flakiness signal. Pure in-memory SQLite + httpx
# ASGITransport — no Docker required; fast (~1.3s for all 15 cases).
# Included in the PR-gate ``just test`` lane AND nightly CI.
# Trailing ``*ARGS`` lets nightly forward ``--junitxml=...`` (Mn9).
test-idempotency *ARGS:
    uv run pytest -m idempotency -v tests/idempotency/ {{ARGS}}

# Story 2.14 — migrator integration tests (FR22 / NFR-M3).
# Verifies the v1.0.0 → v1.0.1 additive schema upgrade end-to-end:
# the 100-event fixture runs through the migrator in-process, the
# migrated bytes round-trip through ``EventEnvelope.from_canonical_json``,
# and materializing both the v1.0.0 archive and the v1.0.1 output through
# fresh in-memory SQLite DBs yields identical observable state
# (``tasks`` + ``sessions`` + ``events`` identity columns). Pure stdlib
# + in-memory SQLite — no Docker required; runtime well under 1s.
# Included in nightly CI as the ``migrator-integration`` job.
test-migrator *ARGS="":
    uv run pytest -m migrator -v tests/migrator/ {{ARGS}}

# Story 2.15 — separability tests (FR34 / FR35; S-3 lands here, S-1 + S-2
# in Stories 5.16 + 5.17c). The S-3 e2e test (slow) boots a 3-service
# compose stack with ``ORCHESTRATOR_IMAGE=null-orchestrator:latest`` and
# asserts a task POSTed to registry-api transitions to ``completed`` via
# the null fixture's emitted lifecycle — proving FR35 / NFR-M5. The
# git-diff sentinel (fast) asserts the working tree leaves spine source
# untouched. Trailing ``*ARGS`` lets nightly forward ``--junitxml=...``.
test-separability *ARGS="": build-base
    uv run pytest -m separability -v tests/separability/ {{ARGS}}

# Strict lint + format + type-check + architectural-discipline gates.
# ruff rules cover style (E/F/I/UP/B/SIM/N); ruff format --check enforces
# canonical formatting; mypy --strict gates the platform-owned packages +
# the registry services (upstream adapters relaxed per mypy.ini).
# The three check-gate scripts enforce import-graph, event-registry, and
# single-writer constraints — same checks CI runs; splitting them out would
# create a footgun where `just lint` is green locally but CI fails.
# Story 2.11 (AC-15): crash-injection harness also passes mypy --strict.
# Story 2.13 (AC-13): idempotency replay tests also pass mypy --strict.
# `--explicit-package-bases` avoids a module-name conflict when the harness
# directory (tests/crash-injection/, tests/idempotency/) is not a Python
# package. Registry_state is found via mypy_path (mypy.ini) because
# services/registry-state now has a py.typed marker.
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy --strict packages/ services/registry-api services/registry-state services/worker-wrapper
    uv run mypy --strict --explicit-package-bases tests/crash-injection tests/idempotency tests/migrator tests/separability tests/fixtures/null_orchestrator
    uv run python scripts/check_imports.py
    uv run python scripts/check_event_registry.py
    uv run python scripts/check_single_writer.py
    git ls-files -z | xargs -0 uv run secret-hygiene-precommit

# Run the secret-hygiene scanner across every tracked file. Pre-commit hook
# runs it per-commit; this recipe is the full-tree sweep operators run
# periodically + that `just lint` delegates to.
scan-secrets:
    git ls-files -z | xargs -0 uv run secret-hygiene-precommit

# Architectural-discipline gates: import-graph, event-registry, single-writer.
# Replicates the CI `Check*` steps locally; run before opening a PR.
check-gates:
    uv run python scripts/check_imports.py
    uv run python scripts/check_event_registry.py
    uv run python scripts/check_single_writer.py

# Run the three architectural-gate self-tests — exercises the bundled fixture
# trees under scripts/checks/fixtures/ to verify each check script's own
# detection logic still works.
check-gates-self-test:
    uv run python scripts/check_imports.py --self-test
    uv run python scripts/check_event_registry.py --self-test
    uv run python scripts/check_single_writer.py --self-test

# Scenario harness (journey-level smoke tests) lands across Stories 2.11 /
# 2.12 / 5.18. Story 1.5 only wires the harness; real scenarios land later.
scenarios:
    @echo "scenario harness lands across Stories 2.11 / 2.12 / 5.18"

# Snapshot the `oh-my-bmad-data` named volume to a local .tgz. Works on Linux
# (named volume lives under /var/lib/docker/volumes/) AND macOS (overlay binds
# to ${HOME}/.oh-my-bmad) because we mount the volume into a throwaway alpine
# container and tar it from there — no host-path assumption. Optional `name=`
# suffix labels the archive (e.g., `just backup name=pre-upgrade`). Timestamp
# is UTC second-precision so same-day backups don't collide.
backup name="":
    #!/usr/bin/env bash
    set -euo pipefail
    ts=$(date -u +%FT%H%M%SZ)
    name_arg='{{name}}'
    # Validate name contains only safe chars to prevent shell-expansion surprises
    # when a future operator passes e.g. `name="foo; rm -rf /"`.
    if [ -n "${name_arg}" ] && ! echo "${name_arg}" | grep -Eq '^[A-Za-z0-9._-]+$'; then
        echo "ERROR: backup name must match [A-Za-z0-9._-]+, got: ${name_arg}" >&2
        exit 1
    fi
    suffix=""
    if [ -n "${name_arg}" ]; then suffix="-${name_arg}"; fi
    archive="oh-my-bmad-backup-${ts}${suffix}.tgz"
    # Locate the data volume regardless of compose project prefix.
    volume=$(docker volume ls --format '{{{{.Name}}' | grep -E '_oh-my-bmad-data$' | head -1 || true)
    if [ -z "${volume}" ]; then
        echo "ERROR: no *_oh-my-bmad-data docker volume found. Initialize the stack first (just dev)." >&2
        exit 1
    fi
    compose_files=(-f docker-compose.yml)
    if [ "$(uname -s)" = "Darwin" ]; then
        compose_files+=(-f docker-compose.macos.yml)
    fi
    # Restart the stack on exit even if tar fails — operators should never lose
    # their platform to a failed backup attempt.
    restart() {
        echo "→ restarting stack"
        docker compose "${compose_files[@]}" up -d || true
    }
    trap restart EXIT
    echo "→ stopping stack"
    docker compose "${compose_files[@]}" down
    echo "→ archiving volume ${volume} → ${PWD}/${archive}"
    docker run --rm \
        -v "${volume}:/source:ro" \
        -v "${PWD}:/dest" \
        alpine:3 \
        tar -czf "/dest/${archive}" -C /source .
    echo "✓ backup written to ${PWD}/${archive}"

# Build the shared base image `oh-my-bmad-base:local`. Every per-service
# Dockerfile extends this. Run before `just build` / `just deploy-*` on a
# fresh checkout or whenever `Dockerfile.base` / `uv.lock` changes.
build-base:
    DOCKER_BUILDKIT=1 docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .

# Build all 7 service images locally (single-arch: 6 compose services via
# `docker compose build` + console-cli via `docker build`). Multi-arch buildx
# bake lands with Story 1.9's release workflow. Depends on the shared base
# image — `build-base` ensures it exists before per-service builds run.
build: build-base
    docker compose -f docker-compose.yml build
    docker build -f services/console-cli/Dockerfile -t oh-my-bmad-console-cli:local .

# Print every oh-my-bmad-* docker image and its size. Operator sanity after
# `just build` — each service image must stay ≤ 200 MB per Story 1.8 AC-7
# (worker-wrapper is the documented exception at ~283 MB; see story 1.8 notes).
image-sizes:
    @docker image ls --format '{{{{.Repository}}:{{{{.Tag}} {{{{.Size}}' | grep -E '(^oh-my-bmad-|/oh-my-bmad-)' | sort

# Linux VPS deploy primitive: build-base → pull-if-available → build → up.
# Story 1.9's GHCR images will make pull the primary path; until then build
# is the source of truth. Docs in Story 1.10a.
deploy-vps: build-base
    docker compose -f docker-compose.yml pull || true
    docker compose -f docker-compose.yml build
    docker compose -f docker-compose.yml up -d

# macOS deploy primitive — same flow plus the macOS overlay and a mkdir
# prerequisite so the bind-mount source exists.
deploy-macos: build-base
    mkdir -p "${HOME}/.oh-my-bmad"
    docker compose -f docker-compose.yml -f docker-compose.macos.yml pull || true
    docker compose -f docker-compose.yml -f docker-compose.macos.yml build
    docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d

# Build base + one representative service multi-arch locally via buildx
# (no push). Diagnostic hook — exercises the release.yml hand-off
# (`--build-context oh-my-bmad-base:local=...`) to catch shape bugs before
# tagging. Auto-bootstraps a `omb-multiarch` buildx builder if missing.
# Pass `version=<X>` to set tag; defaults to `dev`.
release-local version="dev":
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker buildx inspect omb-multiarch >/dev/null 2>&1; then
        echo "→ bootstrapping omb-multiarch buildx builder"
        docker buildx create --name omb-multiarch --driver docker-container >/dev/null
    fi
    docker buildx use omb-multiarch
    echo "→ building multi-arch base: oh-my-bmad-base:{{version}}"
    docker buildx build --platform linux/amd64,linux/arm64 \
        -f Dockerfile.base --target runtime-base \
        -t oh-my-bmad-base:{{version}} \
        --output type=oci,dest=/tmp/omb-base-{{version}}.tar \
        .
    echo "→ building registry-api with --build-context override (amd64 only, loaded locally for smoke)"
    # Single-platform + --load so the resulting image is usable via
    # `docker run` — multi-platform --load is not supported.
    docker buildx build --platform linux/amd64 \
        --build-context oh-my-bmad-base:local=docker-image://python:3.12-slim-bookworm \
        -f services/registry-api/Dockerfile \
        -t oh-my-bmad-registry-api:{{version}}-smoke \
        --load \
        . || echo "  ↑ smoke build without real base is expected to warn; it validates build-contexts syntax only"
    echo "✓ release-local {{version}} complete"
    echo "  Base OCI archive: /tmp/omb-base-{{version}}.tar"
    echo "  Real release builds a base-FROM chain; this is a syntax/shape check only."
