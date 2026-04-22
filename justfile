# oh-my-bmad — operator recipes
#
# Story 1.1 ships only `bootstrap-verify`. Richer recipes (dev, test, lint,
# scenarios, sync-upstream, backup, build, deploy-vps, deploy-macos) arrive in
# Story 1.4 (compose + env + justfile).

default:
    @just --list

# Verify the uv workspace resolves and cross-package imports work.
# Story 1.1 acceptance gate. Uses `--frozen` so the committed lockfile is the
# source of truth; mismatch means the operator must run `uv sync` (without
# --frozen) to reconcile, then re-commit `uv.lock`.
bootstrap-verify:
    uv sync --frozen
    uv run python -c "from events import __version__; print('events', __version__)"
    uv run python -c "from registry_api import __version__, hello; print('registry_api', __version__, '|', hello())"
    @echo "✓ bootstrap OK"
