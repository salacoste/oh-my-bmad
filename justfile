# oh-my-bmad — operator recipes
#
# Story 1.1 ships only `bootstrap-verify`. Richer recipes (dev, test, lint,
# scenarios, sync-upstream, backup, build, deploy-vps, deploy-macos) arrive in
# Story 1.4 (compose + env + justfile).

default:
    @just --list

# Verify the uv workspace resolves and cross-package imports work.
# This is the Story 1.1 acceptance gate.
bootstrap-verify:
    uv sync
    uv run python -c "from events import __version__; print(__version__)"
    @echo "✓ bootstrap OK"
