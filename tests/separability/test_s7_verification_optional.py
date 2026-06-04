"""S-7 separability test — verification-mcp is an OPTIONAL stdio member (Story 17.5).

Epic 17 / FR74 / NFR-M8 / P3-I3. Like git-mcp (S-5) and github-mcp (S-6),
verification-mcp has **no container** — it is a 6th stdio subprocess spawned by
``MCPClientGroup`` inside worker-wrapper, gated on a non-blank
``WorkerSettings.verification_command`` (default ``""`` → OFF). The blank-command
toggle IS the separability seam, so S-7 mirrors the in-process
MCP-client-composition style (real ``MCPClientGroup`` boot spawning real stdio
subprocesses, NO Docker) — the same shape as S-5/S-6.

Two states are proven:

1. :func:`test_verification_spawned_when_command_set` — ``verification_command``
   set + the VERIFICATION_MCP_* env present (a real worktree root) →
   ``MCPClientGroup`` spawns verification as the 6th member, ``clients.verification``
   is live, both Tier-2 tools appear in ``list_tools()``, and a tool is callable
   end-to-end through the stdio boundary.

2. :func:`test_verification_absent_when_command_blank` — ``verification_command``
   blank → the three core MCP servers still initialize
   (``clients.verification is None``) and a scripted task completes, proving the
   member is optional (NFR-M8).

The SPAWNED-state callable assertion invokes ``verification.run_build`` with only
``caller_trace_id``: the default recipe (``just build``) is spawned in the tmp
worktree, which has no ``justfile`` → ``run_recipe`` surfaces a structured failure
(``ok``/``pass`` False, no raise) — so the tool is callable end-to-end (validates
trace_id, enforces the Tier-2 gate, runs the sandboxed executor, returns a
structured dict + the ``verification.completed`` event descriptor) WITHOUT
depending on a real build toolchain. No secret crosses into the recipe env
(``VerificationExecutor`` forwards only its own ``_ENV_ALLOWLIST``).

Both boot REAL stdio subprocesses (heavy) → ``@pytest.mark.slow`` so they are
excluded from the PR-gate ``just test`` and run on merge / nightly (same as
S-5/S-6). Audit emission is disabled (``OMB_MCP_AUDIT_EMISSION_ENABLED=0``) so the
registry/verification servers do NOT spawn nested clawhip-bridge subprocesses. No
Docker is required.
"""

from __future__ import annotations

import os
import sys
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
    """Return the venv interpreter used to spawn every ``python -m <module>`` member."""
    return sys.executable


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to the spawned MCP members.

    Carries every REQUIRED var for the 3 registry servers, with audit emission
    OFF so no nested clawhip-bridge is spawned. Constructed explicitly so the
    test never leaks host secrets (mirror of S-5/S-6's ``_base_env``).
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
        "TASK_REGISTRY_ACTOR_ID": "s7-worker",
        # session-registry REQUIRED.
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s7-worker",
        # clawhip-bridge REQUIRED (only consulted if audit emission re-enabled).
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s7-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        # Shared spine paths.
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        # Audit OFF → no nested clawhip-bridge spawn (keeps the boot light).
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


def _seed_task_row(db_path: str, task_id: str) -> None:
    """Create the registry schema at ``db_path`` and seed one ``Task`` row.

    The task-registry MCP server opens its OWN engine in READ-ONLY URI mode and
    does NOT create the schema. Seeding a row here lets the absent-state scripted
    task complete with ``ok: True``. The DB is created in WAL journal mode so the
    ``-wal``/``-shm`` sidecars exist (the read-only ``PRAGMA journal_mode=WAL`` at
    connect is then a no-op read, not a write). Identical to S-5/S-6's helper.
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
                actor_id="s7-worker",
                title="s7-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, verification_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python.

    ``git_command`` / ``github_command`` stay blank (git/github absent) so S-7
    isolates the verification member: only the 3 core servers + (optionally)
    verification are spawned.
    """
    cmd = _spawn_command()
    return WorkerSettings(
        task_registry_command=cmd,
        task_registry_args=["-m", "task_registry_mcp"],
        session_registry_command=cmd,
        session_registry_args=["-m", "session_registry_mcp"],
        clawhip_bridge_command=cmd,
        clawhip_bridge_args=["-m", "clawhip_bridge_mcp"],
        git_command="",
        github_command="",
        verification_command=verification_command,
        verification_args=["-m", "verification_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_verification_spawned_when_command_set(tmp_path: Path) -> None:
    """SPAWNED state: verification_command set + VERIFICATION_MCP_* env → live 6th member.

    Asserts both Tier-2 tools appear in ``list_tools()`` AND a tool is callable
    end-to-end — proving the optional member, when opted in, fully participates.
    The callable path runs the default recipe in a justfile-less worktree → a
    structured failure (no raise), so no real build toolchain is needed.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env = _base_env(tmp_path)
    env["VERIFICATION_MCP_WORKTREE_ROOT"] = str(worktree)
    env["VERIFICATION_MCP_ACTOR_KIND"] = "worker"
    env["VERIFICATION_MCP_ACTOR_ID"] = "s7-worker"

    settings = _settings(verification_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 6th member is live; git/github (4th/5th) stay absent.
        assert clients.verification is not None, (
            "verification-mcp should be spawned when verification_command is set"
        )
        assert clients.git is None and clients.github is None

        # Connectivity over the three core members.
        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is True
        assert results["clawhip-bridge"] is True

        # Both Tier-2 tools are listed.
        tools = await clients.verification.list_tools()
        names = {t.name for t in tools.tools}
        assert "verification.run_build" in names, (
            f"verification.run_build missing from tools: {sorted(names)}"
        )
        assert "verification.run_tests" in names, (
            f"verification.run_tests missing from tools: {sorted(names)}"
        )

        # Tool callable end-to-end (Tier-2, no approval). Default recipe in a
        # justfile-less worktree → structured failure, NOT an MCP error.
        call_result = await clients.verification.call_tool(
            "verification.run_build", {"caller_trace_id": _TRACE_ID}
        )
        assert call_result.isError is False, (
            f"verification.run_build raised an MCP error: {call_result.content!r}"
        )
        assert call_result.structuredContent is not None
        # The handler ran the sandboxed executor and surfaced a structured result
        # + the verification.completed event descriptor (NO secret, NO raise).
        assert call_result.structuredContent.get("pass") is False
        assert call_result.structuredContent.get("event", {}).get("type") == (
            "verification.completed"
        )

    # Cleanly nulled after exit (separability teardown).
    assert clients.verification is None


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_verification_absent_when_command_blank(tmp_path: Path) -> None:
    """ABSENT state: verification_command blank → 3 core init and a scripted task completes.

    Proves verification-mcp is OPTIONAL (NFR-M8): with no VERIFICATION_MCP_* env
    and a blank verification_command the worker still boots its three core MCP
    members and runs a scripted write-tool round-trip to completion.
    """
    env = _base_env(tmp_path)
    # NOTE: deliberately NO VERIFICATION_MCP_* env — the absent state must not depend on it.

    task_id = f"t-{uuid4().hex[:8]}-0001-7000-8000-000000000001"
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    settings = _settings(verification_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        # verification is absent (and git/github too).
        assert clients.verification is None, (
            "verification-mcp must NOT be spawned when verification_command is blank"
        )
        assert clients.git is None and clients.github is None

        # Exactly the three core members initialized.
        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }, f"core members did not all initialize: {results}"

        # Scripted task: a task-registry write-tool round-trip completes without verification.
        assert clients.task_registry is not None
        note = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s7 scripted task completed without verification-mcp",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert note.isError is False, f"task_add_note raised an MCP error: {note.content!r}"
        assert note.structuredContent == {"ok": True}, (
            f"scripted task did not complete cleanly: {note.structuredContent!r}"
        )
