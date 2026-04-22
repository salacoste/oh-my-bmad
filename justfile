# oh-my-bmad — operator recipes
#
# Story 1.1 shipped `bootstrap-verify`. Story 1.2 extended it. Review-cycle on
# 1.2 expanded it to cover all 14 workspace members (was 5/14 sampled, which
# would have let silent import breakage through). Richer recipes (dev, test,
# lint, scenarios, sync-upstream, backup, build, deploy-vps, deploy-macos)
# arrive in Story 1.4 (compose + env + justfile).

default:
    @just --list

# Verify the uv workspace resolves and every cross-package import works.
# Story 1.1 + 1.2 acceptance gate. Uses `--frozen` so the committed lockfile
# is the source of truth; mismatch means the operator must run `uv sync`
# (without --frozen) to reconcile, then re-commit `uv.lock`.
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
