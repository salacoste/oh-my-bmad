"""Fleet smoke test for Codex runtime + MCP server fleet (Epic 29 / FR96).

Verifies that the Codex runtime adapter and the MCP server fleet can be
initialized together.  Four test cases exercise:

1. ``CodexRunner.health_check`` — binary presence probe.
2. ``get_runtime_adapter`` — factory resolves to ``CodexRunner``.
3. MCP fleet connectivity — ``MCPClientGroup`` spawns git-mcp +
   verification-mcp alongside the core trio; all are connectable.
4. End-to-end tool call — ``git.status`` callable through git-mcp.

All tests skip gracefully when ``codex`` or ``OPENAI_API_KEY`` are
unavailable.  They are marked ``@pytest.mark.slow`` so they run on
merge / nightly, not the PR-gate.  No Docker is required.

Mirrors the separability test style (S-5/S-7) — real ``MCPClientGroup``
boot spawning real stdio subprocesses, explicit allowlisted env, audit
emission OFF so no nested clawhip-bridge is spawned.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from events.ids import new_uuid7
from worker_wrapper.adapters.codex_runner import CodexRunner
from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.adapters.runtime_factory import get_runtime_adapter
from worker_wrapper.app.config import WorkerSettings

# ---------------------------------------------------------------------------
# Skip conditions — codex binary + OPENAI_API_KEY must both be present.
# ---------------------------------------------------------------------------

_codex_available: bool = bool(shutil.which("codex"))
_openai_key_set: bool = bool(os.environ.get("OPENAI_API_KEY"))
_skip_reason: str = "Codex binary or OPENAI_API_KEY not available"

pytestmark = pytest.mark.skipif(
    not (_codex_available and _openai_key_set),
    reason=_skip_reason,
)

# A valid UUIDv7 caller_trace_id (FR58 contract) for tool round-trips.
_TRACE_ID: str = new_uuid7()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spawn_command() -> str:
    """Return the venv interpreter used to spawn ``python -m <module>`` members."""
    return sys.executable


def _settings(tmp_path: Path) -> WorkerSettings:
    """Build WorkerSettings with Codex runtime + all MCP server commands."""
    cmd = _spawn_command()
    return WorkerSettings(
        task_registry_command=cmd,
        task_registry_args=["-m", "task_registry_mcp"],
        session_registry_command=cmd,
        session_registry_args=["-m", "session_registry_mcp"],
        clawhip_bridge_command=cmd,
        clawhip_bridge_args=["-m", "clawhip_bridge_mcp"],
        git_command=cmd,
        git_args=["-m", "git_mcp"],
        verification_command=cmd,
        verification_args=["-m", "verification_mcp"],
        # Codex runtime settings
        runtime="codex",
        codex_command="codex",
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
    )


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to spawned MCP members.

    Carries every REQUIRED var for the 3 core servers + git + verification,
    with audit emission OFF so no nested clawhip-bridge is spawned.
    Constructed explicitly so the test never leaks host secrets.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(exist_ok=True)
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir(exist_ok=True)
    return {
        # Process basics.
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        # task-registry REQUIRED.
        "TASK_REGISTRY_DB_PATH": str(tmp_path / "task.db"),
        "TASK_REGISTRY_ACTOR_KIND": "worker",
        "TASK_REGISTRY_ACTOR_ID": "fleet-smoke-worker",
        # session-registry REQUIRED.
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "fleet-smoke-worker",
        # clawhip-bridge REQUIRED.
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "fleet-smoke-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        # git-mcp REQUIRED.
        "GIT_MCP_WORKTREE_ROOT": str(worktree_dir),
        "GIT_MCP_ACTOR_KIND": "worker",
        "GIT_MCP_ACTOR_ID": "fleet-smoke-worker",
        # verification-mcp REQUIRED.
        "VERIFICATION_MCP_WORKTREE_ROOT": str(worktree_dir),
        "VERIFICATION_MCP_ACTOR_KIND": "worker",
        "VERIFICATION_MCP_ACTOR_ID": "fleet-smoke-worker",
        # Shared spine paths.
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        # Audit OFF → no nested clawhip-bridge spawn.
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


@pytest.fixture
def git_worktree(tmp_path: Path) -> Path:
    """Create a real git worktree for git-mcp's ``GIT_MCP_WORKTREE_ROOT``."""
    root = tmp_path / "worktree"
    root.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fleet-smoke@example.test"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "fleet-smoke-test"],
        cwd=root,
        check=True,
    )
    return root


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_codex_runtime_health_check(tmp_path: Path) -> None:
    """CodexRunner health_check reports the binary as installed."""
    settings = _settings(tmp_path)
    runner = CodexRunner(settings)
    result = await runner.health_check()
    assert result.installed is True, (
        "codex binary should be installed (skip guard should have skipped this test)"
    )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_codex_runtime_dispatch_via_factory(tmp_path: Path) -> None:
    """get_runtime_adapter resolves runtime='codex' to a CodexRunner instance."""
    settings = _settings(tmp_path)
    adapter = get_runtime_adapter(settings, runtime="codex")
    assert isinstance(adapter, CodexRunner), f"Expected CodexRunner, got {type(adapter).__name__}"
    assert adapter.runtime_name == "codex"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_mcp_servers_connect_with_codex_settings(
    tmp_path: Path,
    git_worktree: Path,
) -> None:
    """MCP fleet boots with Codex runtime settings: all members connectable."""
    env = _base_env(tmp_path)
    # Override the worktree root to point at the initialized git repo.
    env["GIT_MCP_WORKTREE_ROOT"] = str(git_worktree)

    settings = _settings(tmp_path)
    async with MCPClientGroup(settings, env=env) as clients:
        # Core trio + git + verification should all be present.
        assert clients.git is not None, "git-mcp should be spawned"
        assert clients.verification is not None, "verification-mcp should be spawned"

        results = await verify_connectivity(clients)
        assert results["task-registry"] is True, "task-registry not connectable"
        assert results["session-registry"] is True, "session-registry not connectable"
        assert results["clawhip-bridge"] is True, "clawhip-bridge not connectable"

    # Cleanly nulled after exit.
    assert clients.git is None
    assert clients.verification is None


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_codex_fleet_git_tool_callable(
    tmp_path: Path,
    git_worktree: Path,
) -> None:
    """Full workflow: spawn MCP fleet, call git.status, assert success."""
    env = _base_env(tmp_path)
    env["GIT_MCP_WORKTREE_ROOT"] = str(git_worktree)

    settings = _settings(tmp_path)
    async with MCPClientGroup(settings, env=env) as clients:
        assert clients.git is not None

        # Verify git.status appears in listed tools.
        tools = await clients.git.list_tools()
        names = {t.name for t in tools.tools}
        assert "git.status" in names, f"git.status missing from tools: {sorted(names)}"

        # Call git.status end-to-end.
        call_result = await clients.git.call_tool(
            "git.status",
            {"caller_trace_id": _TRACE_ID},
        )
        assert call_result.isError is False, f"git.status raised: {call_result.content!r}"
