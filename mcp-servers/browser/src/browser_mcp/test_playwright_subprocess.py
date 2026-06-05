"""Tests for Playwright subprocess lifecycle management (Story 20.2 / FR78).

Unit tests for ``playwright_subprocess.py`` — the core lifecycle manager.
Tests mock ``asyncio.create_subprocess_exec`` to avoid needing Docker.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from browser_mcp.adapters.playwright_subprocess import (
    PlaywrightSubprocessManager,
    _build_docker_command,
)

# Short alias for the very long patch target used throughout.
_EXEC = "browser_mcp.adapters.playwright_subprocess.asyncio.create_subprocess_exec"
_UUID = "events.ids.new_uuid7"


# ---------------------------------------------------------------------------
# _build_docker_command — command construction
# ---------------------------------------------------------------------------


class TestBuildDockerCommand:
    """Verify the ``docker run`` argv is constructed correctly."""

    def test_minimal_command(self) -> None:
        """Core command includes all required flags."""
        cmd = _build_docker_command("pw@sha256:abc")
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "-i" in cmd
        assert "--rm" in cmd
        assert "--init" in cmd
        assert "--headless" in cmd
        assert "--isolated" in cmd
        assert "--no-sandbox" not in cmd
        assert "--network" not in cmd
        assert "host" not in cmd

    def test_image_pinned(self) -> None:
        """Image is passed verbatim — callers validate digest pinning."""
        cmd = _build_docker_command("mcr.microsoft.com/playwright/mcp@sha256:deadbeef")
        assert "mcr.microsoft.com/playwright/mcp@sha256:deadbeef" in cmd

    def test_default_caps(self) -> None:
        """Default caps are core,config."""
        cmd = _build_docker_command("pw@sha256:abc")
        caps_flag = [a for a in cmd if a.startswith("--caps=")]
        assert len(caps_flag) == 1
        assert caps_flag[0] == "--caps=core,config"

    def test_extra_caps_appended(self) -> None:
        """Extra caps are appended after core,config."""
        cmd = _build_docker_command("pw@sha256:abc", extra_caps=["testing"])
        caps_flag = [a for a in cmd if a.startswith("--caps=")]
        assert caps_flag[0] == "--caps=core,config,testing"

    def test_memory_limit(self) -> None:
        """Memory limit flag is set."""
        cmd = _build_docker_command("pw@sha256:abc", memory_limit="1g")
        mem_flags = [a for a in cmd if a.startswith("--memory=")]
        assert mem_flags == ["--memory=1g"]

    def test_cpu_limit(self) -> None:
        """CPU limit flag is set."""
        cmd = _build_docker_command("pw@sha256:abc", cpu_limit=2.0)
        cpu_flags = [a for a in cmd if a.startswith("--cpus=")]
        assert cpu_flags == ["--cpus=2.0"]

    def test_allowed_origins(self) -> None:
        """Allowed origins flag is added when provided."""
        cmd = _build_docker_command(
            "pw@sha256:abc",
            allowed_origins=["localhost", "example.com"],
        )
        origin_flags = [a for a in cmd if a.startswith("--allowed-origins=")]
        assert origin_flags == ["--allowed-origins=localhost,example.com"]

    def test_no_allowed_origins_when_none(self) -> None:
        """No allowed-origins flag when not provided."""
        cmd = _build_docker_command("pw@sha256:abc")
        origin_flags = [a for a in cmd if a.startswith("--allowed-origins")]
        assert origin_flags == []

    def test_no_sandbox_never_present(self) -> None:
        """--no-sandbox is NEVER in the command (P4-I3)."""
        for extra_caps in [None, [], ["testing"]]:
            cmd = _build_docker_command("pw@sha256:abc", extra_caps=extra_caps)
            assert "--no-sandbox" not in cmd

    def test_no_host_network(self) -> None:
        """--network host is NEVER in the command (P4-I3)."""
        cmd = _build_docker_command("pw@sha256:abc")
        assert "--network" not in cmd
        assert "host" not in cmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(pid: int = 12345, returncode: int | None = None) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = pid
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=0)
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    return proc


@pytest.fixture
def manager() -> PlaywrightSubprocessManager:
    """Create a manager for testing."""
    return PlaywrightSubprocessManager(image="pw@sha256:test")


# ---------------------------------------------------------------------------
# PlaywrightSubprocessManager — lifecycle tests
# ---------------------------------------------------------------------------


class TestSpawn:
    @pytest.mark.asyncio
    async def test_spawn_creates_session(self, manager: PlaywrightSubprocessManager) -> None:
        """Spawn creates a session entry."""
        proc = _mock_proc()
        with (
            patch(_EXEC, AsyncMock(return_value=proc)),
            patch(_UUID, return_value="sid-001"),
        ):
            session = await manager.spawn("task-1")

        assert session.task_id == "task-1"
        assert session.session_id == "sid-001"
        assert session.proc is proc
        assert manager.has_session("task-1")

    @pytest.mark.asyncio
    async def test_spawn_rejects_duplicate(self, manager: PlaywrightSubprocessManager) -> None:
        """Spawn raises if task already has an active session."""
        proc = _mock_proc()
        with patch(_EXEC, AsyncMock(return_value=proc)):
            await manager.spawn("task-1")

        with pytest.raises(RuntimeError, match="already has an active"):
            await manager.spawn("task-1")

    @pytest.mark.asyncio
    async def test_spawn_uses_correct_command(self, manager: PlaywrightSubprocessManager) -> None:
        """Spawn passes the correct argv to create_subprocess_exec."""
        proc = _mock_proc()
        with (
            patch(_EXEC, AsyncMock(return_value=proc)) as mock_exec,
            patch(_UUID, return_value="sid"),
        ):
            await manager.spawn("task-1")

        args = mock_exec.call_args[0]
        assert args[0] == "docker"
        assert args[1] == "run"


class TestGetOrSpawn:
    @pytest.mark.asyncio
    async def test_returns_existing(self, manager: PlaywrightSubprocessManager) -> None:
        """get_or_spawn returns existing session if alive."""
        proc = _mock_proc(returncode=None)
        with patch(_EXEC, AsyncMock(return_value=proc)):
            session1 = await manager.spawn("task-1")

        result = await manager.get_or_spawn("task-1")
        assert result is session1

    @pytest.mark.asyncio
    async def test_respawns_if_dead(self, manager: PlaywrightSubprocessManager) -> None:
        """get_or_spawn respawns if existing session's proc is dead."""
        proc_dead = _mock_proc(returncode=0)
        with patch(_EXEC, AsyncMock(return_value=proc_dead)):
            await manager.spawn("task-1")

        proc_alive = _mock_proc(returncode=None)
        with (
            patch(_EXEC, AsyncMock(return_value=proc_alive)),
            patch(_UUID, return_value="sid-2"),
        ):
            session = await manager.get_or_spawn("task-1")

        assert session.session_id == "sid-2"

    @pytest.mark.asyncio
    async def test_spawns_new_if_missing(self, manager: PlaywrightSubprocessManager) -> None:
        """get_or_spawn spawns when no session exists."""
        proc = _mock_proc()
        with patch(_EXEC, AsyncMock(return_value=proc)):
            session = await manager.get_or_spawn("task-new")

        assert session.task_id == "task-new"
        assert manager.has_session("task-new")


class TestKillSession:
    @pytest.mark.asyncio
    async def test_kill_removes_session(self, manager: PlaywrightSubprocessManager) -> None:
        """kill_session removes the entry and terminates the proc."""
        proc = _mock_proc()
        with patch(_EXEC, AsyncMock(return_value=proc)):
            await manager.spawn("task-1")

        result = await manager.kill_session("task-1", reason="test_done")

        assert result is not None
        assert not manager.has_session("task-1")
        proc.send_signal.assert_called_with(signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_kill_returns_none_if_absent(self, manager: PlaywrightSubprocessManager) -> None:
        """kill_session returns None if task has no session."""
        result = await manager.kill_session("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_kill_sends_sigkill_on_timeout(
        self, manager: PlaywrightSubprocessManager
    ) -> None:
        """kill_session escalates to SIGKILL if SIGTERM doesn't work."""
        proc = _mock_proc()
        proc.wait = AsyncMock(side_effect=[TimeoutError, None])

        with patch(_EXEC, AsyncMock(return_value=proc)):
            await manager.spawn("task-1")

        with (
            patch(
                "browser_mcp.adapters.playwright_subprocess._GRACEFUL_TIMEOUT",
                0.01,
            ),
            patch(
                "browser_mcp.adapters.playwright_subprocess._HARD_KILL_TIMEOUT",
                0.02,
            ),
        ):
            await manager.kill_session("task-1")

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_handles_process_lookup(self, manager: PlaywrightSubprocessManager) -> None:
        """kill_session handles ProcessLookupError gracefully."""
        proc = _mock_proc()
        proc.send_signal.side_effect = ProcessLookupError("gone")

        with patch(_EXEC, AsyncMock(return_value=proc)):
            await manager.spawn("task-1")

        await manager.kill_session("task-1")
        assert not manager.has_session("task-1")


class TestKillAll:
    @pytest.mark.asyncio
    async def test_kill_all_cleans_up(self, manager: PlaywrightSubprocessManager) -> None:
        """kill_all removes all sessions."""
        procs = [_mock_proc(), _mock_proc()]
        with patch(_EXEC, AsyncMock(side_effect=procs)):
            await manager.spawn("task-1")
            await manager.spawn("task-2")

        assert len(manager.sessions) == 2
        await manager.kill_all()
        assert len(manager.sessions) == 0

    @pytest.mark.asyncio
    async def test_kill_all_empty(self, manager: PlaywrightSubprocessManager) -> None:
        """kill_all on empty manager is a no-op."""
        await manager.kill_all()
        assert len(manager.sessions) == 0


class TestSessionsProperty:
    @pytest.mark.asyncio
    async def test_sessions_is_read_only_copy(self, manager: PlaywrightSubprocessManager) -> None:
        """sessions property returns a copy, not the internal dict."""
        proc = _mock_proc()
        with patch(_EXEC, AsyncMock(return_value=proc)):
            await manager.spawn("task-1")

        s = manager.sessions
        assert "task-1" in s
        s["task-fake"] = None  # type: ignore[assignment]
        assert not manager.has_session("task-fake")
