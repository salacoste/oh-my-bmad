"""S-10 separability test — browser-mcp is an OPTIONAL stdio member (Story 20.6).

Epic 20 / FR88 / NFR-M9 / P4-I3. Like artifact (S-9), browser-mcp has **no
container** — it is the 9th stdio subprocess spawned by ``MCPClientGroup`` inside
worker-wrapper, gated on a non-blank ``WorkerSettings.browser_command`` (default
``""`` → OFF). The blank-command toggle IS the separability seam.

Two states are proven:

1. :func:`test_browser_spawned_when_command_set` — ``browser_command`` set + the
   BROWSER_MCP_* env present → ``MCPClientGroup`` spawns browser as the 9th
   member, ``clients.browser`` is live, and the server responds to a
   ``list_tools()`` call (empty TIER_MAP in scaffold — proves the stdio
   boundary works).

2. :func:`test_browser_absent_when_command_blank` — ``browser_command`` blank
   → the 3 core MCP servers still initialize (``clients.browser is None``) and
   a scripted task completes, proving the member is optional (NFR-M9).

Both boot REAL stdio subprocesses (heavy) → ``@pytest.mark.slow``. Audit
emission OFF (no nested clawhip-bridge). No Docker required.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from events.ids import new_uuid7
from worker_wrapper.adapters.mcp_clients import MCPClientGroup, verify_connectivity
from worker_wrapper.app.config import WorkerSettings

# A valid UUIDv7 caller_trace_id (FR58 contract) for tool round-trips.
_TRACE_ID: str = new_uuid7()


def _spawn_command() -> str:
    """Return the venv interpreter used to spawn every ``python -m <module>`` member."""
    return sys.executable


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build the explicit allowlisted env forwarded to the spawned MCP members.

    Carries every REQUIRED var for the 3 registry servers, with audit emission
    OFF so no nested clawhip-bridge is spawned. Mirror of S-5..S-9's ``_base_env``.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TASK_REGISTRY_DB_PATH": str(tmp_path / "task.db"),
        "TASK_REGISTRY_ACTOR_KIND": "worker",
        "TASK_REGISTRY_ACTOR_ID": "s10-worker",
        "SESSION_REGISTRY_DB_PATH": str(tmp_path / "session.db"),
        "SESSION_REGISTRY_ACTOR_KIND": "worker",
        "SESSION_REGISTRY_ACTOR_ID": "s10-worker",
        "CLAWHIP_BRIDGE_ACTOR_KIND": "worker",
        "CLAWHIP_BRIDGE_ACTOR_ID": "s10-worker",
        "CLAWHIP_BRIDGE_LOG_DIR": str(events_dir),
        "REGISTRY_EVENTS_DIR": str(events_dir),
        "REGISTRY_DB_PATH": str(tmp_path / "registry.db"),
        "OMB_MCP_AUDIT_EMISSION_ENABLED": "0",
    }


def _seed_task_row(db_path: str, task_id: str) -> None:
    """Create the registry schema at ``db_path`` and seed one ``Task`` row.

    Identical to S-5..S-9's helper — seeding a row lets the absent-state
    scripted task complete with ``ok: True``.
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
                actor_id="s10-worker",
                title="s10-scripted-task",
            )
        )
        session.commit()
    engine.dispose()


def _settings(*, browser_command: str) -> WorkerSettings:
    """Build WorkerSettings whose spawn commands all point at the venv python.

    git/github/verification/memory/artifact stay blank so S-10 isolates the
    browser member: only the 3 core servers + (optionally) browser are spawned.
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
        artifact_command="",
        browser_command=browser_command,
        browser_args=["-m", "browser_mcp"],
    )


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_browser_spawned_when_command_set(
    tmp_path: Path,
) -> None:
    """SPAWNED state: browser_command set + BROWSER_MCP_* env → live 9th member.

    Asserts the server boots and responds to list_tools() through the stdio
    boundary. Epic 21 ships 15 browser tools; the round-trip proves both
    the subprocess lifecycle and tool registration work end-to-end.
    """
    env = _base_env(tmp_path)
    env["BROWSER_MCP_ACTOR_KIND"] = "worker"
    env["BROWSER_MCP_ACTOR_ID"] = "s10-worker"
    env["BROWSER_MCP_PLAYWRIGHT_IMAGE"] = "mcr.microsoft.com/playwright/mcp@sha256:deadbeef"

    settings = _settings(browser_command=_spawn_command())
    async with MCPClientGroup(settings, env=env) as clients:
        # The 9th member is live; the other optional members stay absent.
        assert clients.browser is not None, (
            "browser-mcp should be spawned when browser_command is set"
        )
        # Verify the stdio boundary works — list_tools() round-trip.
        tools_result = await clients.browser.list_tools()
        tool_names = [t.name for t in tools_result.tools]
        # Epic 21 ships 15 browser tools; verify tool registration is live.
        assert isinstance(tool_names, list)
        assert len(tool_names) > 0, f"Expected browser tools registered; got {tool_names}"

        # Audit emission is OFF → verify_connectivity should still work
        # for the 3 core members.
        ok = await verify_connectivity(clients)
        assert ok, "verify_connectivity should pass for the 3 core servers"


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.asyncio
async def test_browser_absent_when_command_blank(
    tmp_path: Path,
) -> None:
    """ABSENT state: browser_command blank → browser stays None (NFR-M9).

    Proves the 3 core MCP servers still initialize and a scripted task
    round-trip completes — browser-mcp is fully optional.
    """
    env = _base_env(tmp_path)
    task_id = new_uuid7()
    _seed_task_row(env["TASK_REGISTRY_DB_PATH"], task_id)

    # browser_command is blank by default → browser should NOT spawn.
    settings = _settings(browser_command="")
    async with MCPClientGroup(settings, env=env) as clients:
        assert clients.browser is None, "browser-mcp should be absent when browser_command is blank"
        assert clients.task_registry is not None, "task-registry must be present"
        # A scripted task_add_note round-trip proves the 3 core members work.
        result = await clients.task_registry.call_tool(
            "task_add_note",
            {
                "task_id": task_id,
                "note": "s10 browser-absent check",
                "caller_trace_id": _TRACE_ID,
            },
        )
        assert result.isError is not True, f"task_add_note should succeed: {result}"
