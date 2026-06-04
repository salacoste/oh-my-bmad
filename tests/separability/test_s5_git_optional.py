"""S-5 separability test — git-mcp is an OPTIONAL stdio member (Story 15.5).

Epic 15 / FR72 / NFR-M8 / P3-I3. Unlike S-1…S-4 (which toggle a Docker compose
*service*), git-mcp has **no container** — it is a 4th stdio subprocess spawned
by ``MCPClientGroup`` inside worker-wrapper, gated on a non-blank
``WorkerSettings.git_command`` (default ``""`` → OFF). The blank-command toggle
IS the separability seam, so S-5 mirrors the in-process MCP-client-composition
style (real ``MCPClientGroup`` boot spawning real stdio subprocesses, NO Docker)
rather than the compose-toggle style of S-1/S-4.

Two states are proven:

1. :func:`test_git_spawned_when_command_set` — ``git_command`` set + the GIT_MCP_*
   env present → ``MCPClientGroup`` spawns git as the 4th member, ``clients.git``
   is live, ``git.status`` appears in its ``list_tools()``, and ``git.status`` is
   callable end-to-end.

2. :func:`test_git_absent_when_command_blank` — ``git_command`` blank → the other
   three MCP servers still initialize (``clients.git is None``) and a scripted
   task (here: a ``task_*`` write-tool round-trip via ``task-registry``) completes,
   proving the member is optional (NFR-M8).

Both boot REAL stdio subprocesses (heavy) → marked ``@pytest.mark.slow`` so they
are excluded from the PR-gate ``just test`` and run on merge / nightly (same as
S-1's slow harness). Audit emission is disabled
(``OMB_MCP_AUDIT_EMISSION_ENABLED=0``) so the registry/git servers do NOT spawn
nested clawhip-bridge subprocesses — keeping the in-process boot lightweight and
deterministic. No Docker is required (these tests do NOT request
``skip_if_no_docker``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from events.ids import new_uuid7
from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.app.config import WorkerSettings

# A valid UUIDv7 caller_trace_id (FR58 contract) for every tool round-trip.
_TRACE_ID: str = new_uuid7()


def _spawn_command() -> str:
    """Return the interpreter used to spawn every ``python -m <module>`` member.

    ``sys.executable`` is the active venv python so ``git_mcp`` /
    ``task_registry_mcp`` / ``session_registry_mcp`` / ``clawhip_bridge_mcp``
    all resolve without manual ``PYTHONPATH`` wiring (they are editable-installed
    workspace members).
    """
    return sys.executable


@pytest.fixture
def git_worktree(tmp_path: Path) -> Iterator[Path]:
    """Create a real git worktree for git-mcp's ``GIT_MCP_WORKTREE_ROOT``.

    git-mcp's ``GitExecutor`` realpath-resolves this root and every git tool is
    confined to it. ``git.status`` needs an initialized repo to return cleanly.
    """
    root = tmp_path / "worktree"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    # A committed-config so git.status / git.* don't trip on missing identity.
    subprocess.run(["git", "config", "user.email", "s5@example.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "s5-test"], cwd=root, check=True)
    yield root


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to the spawned MCP members.

    Carries every REQUIRED var for the 3 registry servers (+ git when present),
    with audit emission OFF so no nested clawhip-bridge is spawned. Mirrors the
    allowlist shape the production ``_default_env_allowlist`` would forward — but
    constructed explicitly here so the test never leaks host secrets.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(exist_ok=True)
    return {
        # Process basics needed for a python subprocess to start.
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        # task-registry REQUIRED.
        "TASK_REGISTRY_DB_PATH": str(tmp_path / "task.db"),
        "TASK_REGISTRY_ACTOR_KIND": "worker",
        "TASK_REGISTRY_ACTOR_ID": "s5-worker",
        # session-registry REQUIRED.
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s5-worker",
        # clawhip-bridge REQUIRED (only consulted if audit emission re-enabled).
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s5-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        # Shared spine paths.
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        # Audit OFF → no nested clawhip-bridge spawn (keeps the boot light).
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


def _seed_task_row(db_path: str, task_id: str) -> None:
    """Create the registry schema at ``db_path`` and seed one ``Task`` row.

    The task-registry MCP server opens its OWN engine on ``TASK_REGISTRY_DB_PATH``
    in READ-ONLY URI mode (``mode=ro``) and does NOT create the schema — it is the
    event-sourced read replica (the materializer is the sole writer). Against an
    empty DB its ``task_*`` tools return ``{"ok": False, "error": "task ... not
    found"}``. Seeding a row here (via a sync engine at the same path) lets the
    absent-state scripted task complete with ``ok: True`` — a concrete "the worker
    still completes a scripted task without git-mcp" assertion.

    The DB is created in WAL journal mode so the ``-wal``/``-shm`` sidecars exist:
    the read-only server connection runs ``PRAGMA journal_mode=WAL`` at connect
    (registry_state.adapters.sqlite_store), which on an already-WAL DB is a no-op
    read — without WAL sidecars that pragma is a WRITE and fails ``mode=ro`` with
    "attempt to write a readonly database".
    """
    from registry_state.schema import Base, Task
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SyncSession

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with SyncSession(engine) as session:
        session.add(
            Task(
                id=task_id,
                status="running",
                created_at=now,
                updated_at=now,
                actor_kind="worker",
                actor_id="s5-worker",
                title="s5-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, git_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python."""
    cmd = _spawn_command()
    return WorkerSettings(
        task_registry_command=cmd,
        task_registry_args=["-m", "task_registry_mcp"],
        session_registry_command=cmd,
        session_registry_args=["-m", "session_registry_mcp"],
        clawhip_bridge_command=cmd,
        clawhip_bridge_args=["-m", "clawhip_bridge_mcp"],
        git_command=git_command,
        git_args=["-m", "git_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_git_spawned_when_command_set(tmp_path: Path, git_worktree: Path) -> None:
    """SPAWNED state: git_command set + GIT_MCP_* env → git is the live 4th member.

    Asserts git's tools appear in its ``list_tools()`` (``git.status``) AND a git
    tool is callable end-to-end — proving the optional member, when opted in,
    fully participates.
    """
    env = _base_env(tmp_path)
    env["GIT_MCP_WORKTREE_ROOT"] = str(git_worktree)
    env["GIT_MCP_ACTOR_KIND"] = "worker"
    env["GIT_MCP_ACTOR_ID"] = "s5-worker"

    settings = _settings(git_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 4th member is live.
        assert clients.git is not None, "git-mcp should be spawned when git_command is set"

        # Connectivity over all four members.
        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is True
        assert results["clawhip-bridge"] is True

        # git's tools are listed.
        tools = await clients.git.list_tools()
        names = {t.name for t in tools.tools}
        assert "git.status" in names, f"git.status missing from git tools: {sorted(names)}"

        # git tool callable end-to-end (Tier-1 read, no approval needed).
        call_result = await clients.git.call_tool("git.status", {"caller_trace_id": _TRACE_ID})
        assert call_result.isError is False, f"git.status raised: {call_result.content!r}"

    # Cleanly nulled after exit (separability teardown).
    assert clients.git is None


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_git_absent_when_command_blank(tmp_path: Path) -> None:
    """ABSENT state: git_command blank → the other 3 init and a scripted task completes.

    Proves git-mcp is OPTIONAL (NFR-M8): with no GIT_MCP_* env and a blank
    git_command the worker still boots its three core MCP members and runs a
    scripted write-tool round-trip (``task_add_note`` via task-registry) to
    completion.
    """
    env = _base_env(tmp_path)
    # NOTE: deliberately NO GIT_MCP_* env — the absent state must not depend on it.

    # Seed a task row so the scripted write-tool round-trip can complete (ok=True).
    task_id = f"t-{uuid4().hex[:8]}-0001-7000-8000-000000000001"
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    settings = _settings(git_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        # git is absent.
        assert clients.git is None, "git-mcp must NOT be spawned when git_command is blank"

        # Exactly the three core members initialized.
        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }, f"core members did not all initialize: {results}"

        # Scripted task: a task-registry write-tool round-trip completes without git.
        assert clients.task_registry is not None
        note = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s5 scripted task completed without git-mcp",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert note.isError is False, f"task_add_note raised an MCP error: {note.content!r}"
        # The stub returns {"ok": True} on success — assert the scripted task
        # actually completed (not the "task not found" empty-DB path).
        assert note.structuredContent == {"ok": True}, (
            f"scripted task did not complete cleanly: {note.structuredContent!r}"
        )
