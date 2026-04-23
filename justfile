# oh-my-bmad — operator recipes
#
# Story 1.1 shipped `bootstrap-verify`. Story 1.2 extended it to cover all 14
# workspace members. Story 1.3 adds `sync-upstream` + `migrator-test-additive`.
# Richer recipes (dev, test, lint, scenarios, backup, build, deploy-vps,
# deploy-macos) arrive in Story 1.4 (compose + env + justfile).

default:
    @just --list

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
    uv run python scripts/migrator/tests/assert_migrated.py .tmp/migrator-test/sample.v1.0.1.jsonl
    @test -f .tmp/migrator-test/sample.v1.0.0.archive || (echo "FAIL: archive missing" && exit 1)
    @echo "→ cleanup"
    rm -rf .tmp/migrator-test

# Bring up the stack. On macOS automatically includes the overlay (bind-mount
# to ${HOME}/.oh-my-bmad) and ensures the host directory exists. On Linux uses
# the base compose only (named volume, no host-path prep needed).
dev:
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

# Strict lint + format + type-check + architectural-discipline gates.
# ruff rules cover style (E/F/I/UP/B/SIM/N); ruff format --check enforces
# canonical formatting; mypy --strict gates the platform-owned packages +
# the registry services (upstream adapters relaxed per mypy.ini).
# The three check-gate scripts enforce import-graph, event-registry, and
# single-writer constraints — same checks CI runs; splitting them out would
# create a footgun where `just lint` is green locally but CI fails.
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy --strict packages/ services/registry-api services/registry-state
    uv run python scripts/check_imports.py
    uv run python scripts/check_event_registry.py
    uv run python scripts/check_single_writer.py
    uv run secret-hygiene-precommit $(git ls-files)

# Run the secret-hygiene scanner across every tracked file. Pre-commit hook
# runs it per-commit; this recipe is the full-tree sweep operators run
# periodically + that `just lint` delegates to.
scan-secrets:
    uv run secret-hygiene-precommit $(git ls-files)

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

# Build all 6 service images locally (single-arch). Multi-arch buildx bake
# lands with Story 1.9's release workflow.
build:
    docker compose -f docker-compose.yml build

# Linux VPS deploy primitive. Pulls pre-built images if OMB_IMAGE_REGISTRY
# points at a real registry (Story 1.9 publishes to GHCR); builds locally
# otherwise. `|| true` on `pull` so the recipe doesn't error when images are
# only local.
deploy-vps:
    docker compose -f docker-compose.yml pull || true
    docker compose -f docker-compose.yml build
    docker compose -f docker-compose.yml up -d

# macOS deploy primitive — same flow plus the macOS overlay and a mkdir
# prerequisite so the bind-mount source exists.
deploy-macos:
    mkdir -p "${HOME}/.oh-my-bmad"
    docker compose -f docker-compose.yml -f docker-compose.macos.yml pull || true
    docker compose -f docker-compose.yml -f docker-compose.macos.yml build
    docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d
