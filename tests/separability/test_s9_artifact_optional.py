"""S-9 separability test — artifact-mcp is an OPTIONAL stdio member (Story 19.5).

Epic 19 / FR76 / NFR-M8 / P3-I2 / P3-I3. The LAST of the Phase-3 fleet: like git
(S-5), github (S-6), verification (S-7), and memory (S-8), artifact-mcp has **no
container** — it is the 8th stdio subprocess spawned by ``MCPClientGroup`` inside
worker-wrapper, gated on a non-blank ``WorkerSettings.artifact_command`` (default
``""`` → OFF). The blank-command toggle IS the separability seam.

Two states are proven:

1. :func:`test_artifact_spawned_when_command_set` — ``artifact_command`` set + the
   ARTIFACT_MCP_* env present (a dedicated content-store root) → ``MCPClientGroup``
   spawns artifact as the 8th member, ``clients.artifact`` is live, the four tools
   appear in ``list_tools()``, and an ``artifact.put`` → ``artifact.get`` round-trip
   works end-to-end through the stdio boundary (binary content via base64). Asserts
   P3-I2: the put lands in artifact-mcp's OWN store root and the sibling registry DB
   path is NEVER created.

2. :func:`test_artifact_absent_when_command_blank` — ``artifact_command`` blank → the
   three core MCP servers still initialize (``clients.artifact is None``) and a
   scripted task completes, proving the member is optional (NFR-M8).

Both boot REAL stdio subprocesses (heavy) → ``@pytest.mark.slow``. Audit emission OFF
(no nested clawhip-bridge). No Docker required.
"""

from __future__ import annotations

import base64
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

# Arbitrary BINARY payload (NOT valid UTF-8) — proves the artifact store handles
# binary build/run output, base64-encoded over the wire.
_BINARY_CONTENT: bytes = bytes(range(256))


def _spawn_command() -> str:
    """Return the venv interpreter used to spawn every ``python -m <module>`` member."""
    return sys.executable


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to the spawned MCP members.

    Carries every REQUIRED var for the 3 registry servers, with audit emission
    OFF so no nested clawhip-bridge is spawned. Mirror of S-5..S-8's ``_base_env``.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TASK_REGISTRY_DB_PATH": str(tmp_path / "task.db"),
        "TASK_REGISTRY_ACTOR_KIND": "worker",
        "TASK_REGISTRY_ACTOR_ID": "s9-worker",
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s9-worker",
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s9-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


def _seed_task_row(db_path: str, task_id: str) -> None:
    """Create the registry schema at ``db_path`` and seed one ``Task`` row.

    Identical to S-5..S-8's helper — the task-registry MCP server opens its OWN
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
                actor_id="s9-worker",
                title="s9-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, artifact_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python.

    git/github/verification/memory stay blank so S-9 isolates the artifact member:
    only the 3 core servers + (optionally) artifact are spawned.
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
        memory_command="",
        artifact_command=artifact_command,
        artifact_args=["-m", "artifact_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_artifact_spawned_when_command_set(tmp_path: Path) -> None:
    """SPAWNED state: artifact_command set + ARTIFACT_MCP_* env → live 8th member.

    Asserts the four tools are listed AND a put→get round-trip works end-to-end
    (binary content via base64). P3-I2: the put lands in artifact-mcp's OWN store
    root; the registry DB path is NEVER created by an artifact write.
    """
    store_root = tmp_path / "artifact-store"
    registry_db = Path(_base_env(tmp_path)["REGISTRY_DB_PATH"])
    env = _base_env(tmp_path)
    env["ARTIFACT_MCP_STORE_PATH"] = str(store_root)
    env["ARTIFACT_MCP_ACTOR_KIND"] = "worker"
    env["ARTIFACT_MCP_ACTOR_ID"] = "s9-worker"

    settings = _settings(artifact_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 8th member is live; the other optional members stay absent.
        assert clients.artifact is not None, (
            "artifact-mcp should be spawned when artifact_command is set"
        )
        assert clients.git is None and clients.github is None
        assert clients.verification is None and clients.memory is None

        # Connectivity over the three core members.
        results = await verify_connectivity(clients)
        assert results["task-registry"] is True
        assert results["session-registry"] is True
        assert results["clawhip-bridge"] is True

        # All four artifact tools are listed.
        tools = await clients.artifact.list_tools()
        names = {t.name for t in tools.tools}
        assert {"artifact.get", "artifact.list", "artifact.put", "artifact.delete"} <= names, (
            f"artifact tools missing from list_tools: {sorted(names)}"
        )

        # put → get round-trip end-to-end (binary content via base64).
        put_result = await clients.artifact.call_tool(
            "artifact.put",
            {
                "caller_trace_id": _TRACE_ID,
                "content": base64.b64encode(_BINARY_CONTENT).decode("ascii"),
                "name": "s9-artifact",
            },
        )
        assert put_result.isError is False, f"artifact.put raised: {put_result.content!r}"
        assert put_result.structuredContent is not None
        assert put_result.structuredContent.get("ok") is True
        content_hash = put_result.structuredContent["hash"]

        get_result = await clients.artifact.call_tool(
            "artifact.get", {"caller_trace_id": _TRACE_ID, "hash": content_hash}
        )
        assert get_result.isError is False, f"artifact.get raised: {get_result.content!r}"
        assert get_result.structuredContent is not None
        assert get_result.structuredContent.get("found") is True
        # The bytes survive the round-trip exactly (binary-safe base64).
        got = base64.b64decode(get_result.structuredContent["content"])
        assert got == _BINARY_CONTENT, "artifact put→get binary round-trip corrupted the content"

    # P3-I2: the put landed in artifact-mcp's OWN store root; the registry DB was
    # never created by an artifact operation (this test never seeds it).
    assert store_root.exists(), "artifact put did not create its own store root"
    assert not registry_db.exists(), (
        "P3-I2 violation: an artifact write created/touched the registry DB path"
    )
    # Cleanly nulled after exit (separability teardown).
    assert clients.artifact is None


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_artifact_absent_when_command_blank(tmp_path: Path) -> None:
    """ABSENT state: artifact_command blank → the 3 core init and a scripted task completes.

    Proves artifact-mcp is OPTIONAL (NFR-M8): with no ARTIFACT_MCP_* env and a blank
    artifact_command the worker still boots its three core MCP members and runs a
    scripted write-tool round-trip to completion.
    """
    env = _base_env(tmp_path)
    # NOTE: deliberately NO ARTIFACT_MCP_* env — the absent state must not depend on it.

    task_id = f"t-{uuid4().hex[:8]}-0001-7000-8000-000000000001"
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    settings = _settings(artifact_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        # artifact is absent (and all other optional members too).
        assert clients.artifact is None, (
            "artifact-mcp must NOT be spawned when artifact_command is blank"
        )
        assert clients.git is None and clients.github is None
        assert clients.verification is None and clients.memory is None

        # Exactly the three core members initialized.
        results = await verify_connectivity(clients)
        assert results == {
            "task-registry": True,
            "session-registry": True,
            "clawhip-bridge": True,
        }, f"core members did not all initialize: {results}"

        # Scripted task: a task-registry write-tool round-trip completes without artifact.
        assert clients.task_registry is not None
        note = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s9 scripted task completed without artifact-mcp",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert note.isError is False, f"task_add_note raised an MCP error: {note.content!r}"
        assert note.structuredContent == {"ok": True}, (
            f"scripted task did not complete cleanly: {note.structuredContent!r}"
        )
