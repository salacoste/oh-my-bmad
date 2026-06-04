"""S-6 separability test — github-mcp is an OPTIONAL stdio member (Story 16.6).

Epic 16 / FR73 / NFR-M8 / P3-I3. Like git-mcp (S-5), github-mcp has **no
container** — it is a 5th stdio subprocess spawned by ``MCPClientGroup`` inside
worker-wrapper, gated on a non-blank ``WorkerSettings.github_command`` (default
``""`` → OFF). The blank-command toggle IS the separability seam, so S-6 mirrors
the in-process MCP-client-composition style (real ``MCPClientGroup`` boot
spawning real stdio subprocesses, NO Docker) — the same shape as S-5.

Two states are proven:

1. :func:`test_github_spawned_when_command_set` — ``github_command`` set + the
   GITHUB_MCP_* env (incl. the scoped token) present → ``MCPClientGroup`` spawns
   github as the 5th member, ``clients.github`` is live, its tools appear in
   ``list_tools()`` (a Tier-1 read AND a Tier-3 write), and a read tool is callable
   end-to-end through the stdio boundary.

2. :func:`test_github_absent_when_command_blank` — ``github_command`` blank → the
   three core MCP servers still initialize (``clients.github is None``) and a
   scripted task (a ``task_*`` write-tool round-trip via ``task-registry``)
   completes, proving the member is optional (NFR-M8).

The SPAWNED-state callable assertion deliberately invokes ``github.issues.list``
with ONLY ``caller_trace_id`` (no ``owner``/``repo``): the handler validates the
trace_id, enforces the Tier-1 gate, and returns a structured
``{"ok": False, "error": "owner and repo are required"}`` WITHOUT any GitHub HTTP
call — so the test is hermetic (no live api.github.com dependency, no scoped-token
disclosure) while still proving the tool fully participates through the stdio
boundary. (A real owner/repo call would hit GitHub; the read client's no-raise
contract means even an auth/network failure returns a structured error, but that
would add a network dependency this separability test does not need.)

Both boot REAL stdio subprocesses (heavy) → marked ``@pytest.mark.slow`` so they
are excluded from the PR-gate ``just test`` and run on merge / nightly (same as
S-5). Audit emission is disabled (``OMB_MCP_AUDIT_EMISSION_ENABLED=0``) so the
registry/github servers do NOT spawn nested clawhip-bridge subprocesses. No
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

# A deliberately bogus, NARROWLY-named scoped token. github-mcp's __main__.py
# requires GITHUB_MCP_SCOPED_TOKEN to be non-empty (exits 2 otherwise); the
# SPAWNED-state assertions never trigger a real GitHub call, so the value only
# needs to be present, not valid. The broad GITHUB_TOKEN is NEVER set here.
_BOGUS_SCOPED_TOKEN = "ghp_s6_bogus_scoped_token_not_a_real_credential"


def _spawn_command() -> str:
    """Return the venv interpreter used to spawn every ``python -m <module>`` member."""
    return sys.executable


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to the spawned MCP members.

    Carries every REQUIRED var for the 3 registry servers, with audit emission
    OFF so no nested clawhip-bridge is spawned. Constructed explicitly so the
    test never leaks host secrets (mirror of S-5's ``_base_env``).
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
        "TASK_REGISTRY_ACTOR_ID": "s6-worker",
        # session-registry REQUIRED.
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s6-worker",
        # clawhip-bridge REQUIRED (only consulted if audit emission re-enabled).
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s6-worker",
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
    connect is then a no-op read, not a write). Identical to S-5's helper.
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
                actor_id="s6-worker",
                title="s6-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, github_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python.

    ``git_command`` stays blank (git-mcp absent) so S-6 isolates the github
    member: only the 3 core servers + (optionally) github are spawned.
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
        github_command=github_command,
        github_args=["-m", "github_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_github_spawned_when_command_set(tmp_path: Path) -> None:
    """SPAWNED state: github_command set + GITHUB_MCP_* env → github is the live 5th member.

    Asserts github's tools appear in its ``list_tools()`` (a Tier-1 read AND a
    Tier-3 write) AND a read tool is callable end-to-end — proving the optional
    member, when opted in, fully participates. The callable path uses the
    owner/repo-omitted structured-error branch so NO live GitHub call is made.
    """
    env = _base_env(tmp_path)
    env["GITHUB_MCP_ACTOR_KIND"] = "worker"
    env["GITHUB_MCP_ACTOR_ID"] = "s6-worker"
    env["GITHUB_MCP_SCOPED_TOKEN"] = _BOGUS_SCOPED_TOKEN

    settings = _settings(github_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 5th member is live; git (4th) stays absent (git_command="").
        assert clients.github is not None, "github-mcp should be spawned when github_command is set"
        assert clients.git is None, "git-mcp must stay absent (git_command blank)"

        # Connectivity over the three core members.
        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is True
        assert results["clawhip-bridge"] is True

        # github's tools are listed — a Tier-1 read and a Tier-3 write.
        tools = await clients.github.list_tools()
        names = {t.name for t in tools.tools}
        assert "github.issues.list" in names, (
            f"github.issues.list missing from github tools: {sorted(names)}"
        )
        assert "github.prs.create" in names, (
            f"github.prs.create missing from github tools: {sorted(names)}"
        )

        # Read tool callable end-to-end (Tier-1, no approval, no live GitHub call:
        # owner/repo omitted → structured "owner and repo are required" result).
        call_result = await clients.github.call_tool(
            "github.issues.list", {"caller_trace_id": _TRACE_ID}
        )
        assert call_result.isError is False, (
            f"github.issues.list raised an MCP error: {call_result.content!r}"
        )
        # The handler ran its logic and returned a structured result (NOT a token).
        assert call_result.structuredContent is not None
        assert call_result.structuredContent.get("ok") is False
        assert "_auth_token" not in call_result.structuredContent, (
            "github read tool must NEVER disclose the scoped token in its result"
        )

    # Cleanly nulled after exit (separability teardown).
    assert clients.github is None


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_github_absent_when_command_blank(tmp_path: Path) -> None:
    """ABSENT state: github_command blank → the 3 core init and a scripted task completes.

    Proves github-mcp is OPTIONAL (NFR-M8): with no GITHUB_MCP_* env and a blank
    github_command the worker still boots its three core MCP members and runs a
    scripted write-tool round-trip (``task_add_note`` via task-registry) to
    completion.
    """
    env = _base_env(tmp_path)
    # NOTE: deliberately NO GITHUB_MCP_* env — the absent state must not depend on it.

    # Seed a task row so the scripted write-tool round-trip can complete (ok=True).
    task_id = f"t-{uuid4().hex[:8]}-0001-7000-8000-000000000001"
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    settings = _settings(github_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        # github is absent (and git too).
        assert clients.github is None, "github-mcp must NOT be spawned when github_command is blank"
        assert clients.git is None

        # Exactly the three core members initialized.
        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }, f"core members did not all initialize: {results}"

        # Scripted task: a task-registry write-tool round-trip completes without github.
        assert clients.task_registry is not None
        note = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s6 scripted task completed without github-mcp",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert note.isError is False, f"task_add_note raised an MCP error: {note.content!r}"
        assert note.structuredContent == {"ok": True}, (
            f"scripted task did not complete cleanly: {note.structuredContent!r}"
        )
