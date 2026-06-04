"""S-8 separability test — memory-mcp is an OPTIONAL stdio member (Story 18.5).

Epic 18 / FR75 / NFR-M8 / P3-I2 / P3-I3. Like git (S-5), github (S-6), and
verification (S-7), memory-mcp has **no container** — it is a 7th stdio subprocess
spawned by ``MCPClientGroup`` inside worker-wrapper, gated on a non-blank
``WorkerSettings.memory_command`` (default ``""`` → OFF). The blank-command toggle
IS the separability seam.

Two states are proven:

1. :func:`test_memory_spawned_when_command_set` — ``memory_command`` set + the
   MEMORY_MCP_* env present (a dedicated store path) → ``MCPClientGroup`` spawns
   memory as the 7th member, ``clients.memory`` is live, the three tools appear in
   ``list_tools()``, and a ``memory.write`` → ``memory.read`` round-trip works
   end-to-end through the stdio boundary. Also asserts P3-I2: the write lands in
   memory-mcp's OWN store file and a sibling registry DB path is NEVER created.

2. :func:`test_memory_absent_when_command_blank` — ``memory_command`` blank → the
   three core MCP servers still initialize (``clients.memory is None``) and a
   scripted task completes, proving the member is optional (NFR-M8).

Both boot REAL stdio subprocesses (heavy) → ``@pytest.mark.slow`` (excluded from the
PR-gate, run on merge / nightly). Audit emission OFF (no nested clawhip-bridge). No
Docker required.
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
    test never leaks host secrets (mirror of S-5/S-6/S-7's ``_base_env``).
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TASK_REGISTRY_DB_PATH": str(tmp_path / "task.db"),
        "TASK_REGISTRY_ACTOR_KIND": "worker",
        "TASK_REGISTRY_ACTOR_ID": "s8-worker",
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s8-worker",
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s8-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


def _seed_task_row(db_path: str, task_id: str) -> None:
    """Create the registry schema at ``db_path`` and seed one ``Task`` row.

    Identical to S-5/S-6/S-7's helper — the task-registry MCP server opens its OWN
    engine read-only and does not create the schema; seeding a row lets the
    absent-state scripted task complete with ``ok: True``. WAL mode so the
    ``-wal``/``-shm`` sidecars exist (the read-only ``PRAGMA journal_mode=WAL`` at
    connect is then a no-op read, not a write).
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
                actor_id="s8-worker",
                title="s8-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, memory_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python.

    git/github/verification stay blank so S-8 isolates the memory member: only the
    3 core servers + (optionally) memory are spawned.
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
        verification_command="",
        memory_command=memory_command,
        memory_args=["-m", "memory_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_memory_spawned_when_command_set(tmp_path: Path) -> None:
    """SPAWNED state: memory_command set + MEMORY_MCP_* env → live 7th member.

    Asserts the three tools are listed AND a write→read round-trip works end-to-end
    through the stdio boundary. P3-I2: the write lands in memory-mcp's OWN store
    file; the registry DB path is NEVER created by a memory write.
    """
    store_path = tmp_path / "memory-store" / "store.db"
    registry_db = Path(_base_env(tmp_path)["REGISTRY_DB_PATH"])
    env = _base_env(tmp_path)
    env["MEMORY_MCP_STORE_PATH"] = str(store_path)
    env["MEMORY_MCP_ACTOR_KIND"] = "worker"
    env["MEMORY_MCP_ACTOR_ID"] = "s8-worker"

    settings = _settings(memory_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 7th member is live; git/github/verification stay absent.
        assert clients.memory is not None, "memory-mcp should be spawned when memory_command is set"
        assert clients.git is None and clients.github is None
        assert clients.verification is None

        # Connectivity over the three core members.
        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is True
        assert results["clawhip-bridge"] is True

        # All three memory tools are listed.
        tools = await clients.memory.list_tools()
        names = {t.name for t in tools.tools}
        assert {"memory.read", "memory.search", "memory.write"} <= names, (
            f"memory tools missing from list_tools: {sorted(names)}"
        )

        # write → read round-trip end-to-end through the stdio boundary.
        key = "s8-doc"
        write_result = await clients.memory.call_tool(
            "memory.write",
            {
                "caller_trace_id": _TRACE_ID,
                "key": key,
                "title": "S-8 doc",
                "body": "the quick brown fox jumps over the lazy dog",
            },
        )
        assert write_result.isError is False, f"memory.write raised: {write_result.content!r}"
        assert write_result.structuredContent is not None
        assert write_result.structuredContent.get("ok") is True

        read_result = await clients.memory.call_tool(
            "memory.read", {"caller_trace_id": _TRACE_ID, "key": key}
        )
        assert read_result.isError is False, f"memory.read raised: {read_result.content!r}"
        assert read_result.structuredContent is not None
        assert read_result.structuredContent.get("found") is True, (
            f"written doc not found on read-back: {read_result.structuredContent!r}"
        )

    # P3-I2: the write landed in memory-mcp's OWN store; the registry DB was never
    # created by a memory operation (this test never seeds it).
    assert store_path.exists(), "memory write did not create its own store file"
    assert not registry_db.exists(), (
        "P3-I2 violation: a memory write created/touched the registry DB path"
    )
    # Cleanly nulled after exit (separability teardown).
    assert clients.memory is None


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_memory_absent_when_command_blank(tmp_path: Path) -> None:
    """ABSENT state: memory_command blank → the 3 core init and a scripted task completes.

    Proves memory-mcp is OPTIONAL (NFR-M8): with no MEMORY_MCP_* env and a blank
    memory_command the worker still boots its three core MCP members and runs a
    scripted write-tool round-trip to completion.
    """
    env = _base_env(tmp_path)
    # NOTE: deliberately NO MEMORY_MCP_* env — the absent state must not depend on it.

    task_id = f"t-{uuid4().hex[:8]}-0001-7000-8000-000000000001"
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    settings = _settings(memory_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        # memory is absent (and git/github/verification too).
        assert clients.memory is None, "memory-mcp must NOT be spawned when memory_command is blank"
        assert clients.git is None and clients.github is None
        assert clients.verification is None

        # Exactly the three core members initialized.
        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }, f"core members did not all initialize: {results}"

        # Scripted task: a task-registry write-tool round-trip completes without memory.
        assert clients.task_registry is not None
        note = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s8 scripted task completed without memory-mcp",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert note.isError is False, f"task_add_note raised an MCP error: {note.content!r}"
        assert note.structuredContent == {"ok": True}, (
            f"scripted task did not complete cleanly: {note.structuredContent!r}"
        )
