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
    uv sync --frozen
    uv run python -c "from events import __version__; print('events', __version__)"
    uv run python -c "from registry_api import __version__, hello; print('registry_api', __version__, '|', hello())"
    uv run python -c "from registry_state import __version__; print('registry_state', __version__)"
    uv run python -c "from telegram_gateway import __version__; print('telegram_gateway', __version__)"
    uv run python -c "from console_cli import __version__; print('console_cli', __version__)"
    uv run python -c "from orchestrator_adapter import __version__; print('orchestrator_adapter', __version__)"
    uv run python -c "from worker_wrapper import __version__; print('worker_wrapper', __version__)"
    uv run python -c "from clawhip_daemon import __version__; print('clawhip_daemon', __version__)"
    uv run python -c "from task_registry_mcp import __version__; print('task_registry_mcp', __version__)"
    uv run python -c "from session_registry_mcp import __version__; print('session_registry_mcp', __version__)"
    uv run python -c "from clawhip_bridge_mcp import __version__; print('clawhip_bridge_mcp', __version__)"
    uv run python -c "from secret_hygiene import __version__; print('secret_hygiene', __version__)"
    uv run python -c "from idempotency import __version__; print('idempotency', __version__)"
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
    docker run --rm -v "$(pwd)/.tmp/migrator-test:/data" -e EVENT_LOG_PATH=/data/sample.jsonl oh-my-bmad-migrator:test v1.0.0-to-v1.0.1
    @echo "→ asserting output…"
    uv run python scripts/migrator/tests/assert_migrated.py .tmp/migrator-test/sample.v1.0.1.jsonl
    @test -f .tmp/migrator-test/sample.v1.0.0.archive || (echo "FAIL: archive missing" && exit 1)
    @echo "→ cleanup"
    rm -rf .tmp/migrator-test
