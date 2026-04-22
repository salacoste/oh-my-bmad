# oh-my-bmad — operator recipes
#
# Story 1.1 shipped `bootstrap-verify`. Story 1.2 extended it with
# representative imports from each workspace group. Richer recipes (dev,
# test, lint, scenarios, sync-upstream, backup, build, deploy-vps,
# deploy-macos) arrive in Story 1.4 (compose + env + justfile).

default:
    @just --list

# Verify the uv workspace resolves and cross-package imports work.
# Story 1.1 + 1.2 acceptance gate. Uses `--frozen` so the committed
# lockfile is the source of truth; mismatch means the operator must run
# `uv sync` (without --frozen) to reconcile, then re-commit `uv.lock`.
bootstrap-verify:
    uv sync --frozen
    uv run python -c "from events import __version__; print('events', __version__)"
    uv run python -c "from registry_api import __version__, hello; print('registry_api', __version__, '|', hello())"
    uv run python -c "from registry_state import __version__; print('registry_state', __version__)"
    uv run python -c "from task_registry_mcp import __version__; print('task_registry_mcp', __version__)"
    uv run python -c "from secret_hygiene import __version__; print('secret_hygiene', __version__)"
    @echo "✓ bootstrap OK"
