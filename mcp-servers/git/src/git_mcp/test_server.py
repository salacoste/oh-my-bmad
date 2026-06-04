"""Tests for git-mcp server (Epic 15 / Story 15.2 scaffold).

No conftest — fixtures inlined per project convention. Async tests use
pytest-asyncio strict mode.

Classes:
  TestServerConstruction  — build_server returns a FastMCP with no tools yet
  TestGitExecutor         — worktree-containment logic (in-root OK, escape refused)
  TestEntryPoint          — env-var validation (subprocess; exit 2 on missing/invalid)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from capabilities import Tier
from events import FROZEN_EPOCH, FrozenClock
from mcp.server.fastmcp import FastMCP

from git_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from git_mcp.server import GitExecutor, build_server

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_VALID_TG_TRACE_ID = "tg:42"


def _build(worktree_root: Path) -> FastMCP:
    return build_server(
        worktree_root=worktree_root,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="worker",
        actor_id="test-worker",
    )


# ---------------------------------------------------------------------------
# TestServerConstruction
# ---------------------------------------------------------------------------


class TestServerConstruction:
    """Story 15.2 scaffold — build_server returns a FastMCP with no tools."""

    def test_build_server_returns_fastmcp(self, tmp_path: Path) -> None:
        mcp = _build(tmp_path)
        assert isinstance(mcp, FastMCP)

    @pytest.mark.asyncio
    async def test_build_server_registers_read_tools(self, tmp_path: Path) -> None:
        """Story 15.3/15.4: the four read tools + four mutating tools are registered."""
        mcp = _build(tmp_path)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "git.status",
            "git.diff",
            "git.log",
            "git.branch",
            "git.add",
            "git.commit",
            "git.push",
            "git.rebase",
        }
        assert TIER_MAP == {
            "git.status": Tier.ONE,
            "git.diff": Tier.ONE,
            "git.log": Tier.ONE,
            "git.branch": Tier.ONE,
            "git.add": Tier.TWO,
            "git.commit": Tier.TWO,
            "git.push": Tier.THREE,
            "git.rebase": Tier.THREE,
        }

    def test_build_server_with_clawhip_disabled_returns_cleanly(self, tmp_path: Path) -> None:
        """With clawhip args None, no lifespan/spawn is wired (audit-off path)."""
        mcp = build_server(
            worktree_root=tmp_path,
            clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
            actor_kind="worker",
            actor_id="test-worker",
            clawhip_bridge_command=None,
            clawhip_bridge_args=None,
        )
        assert isinstance(mcp, FastMCP)


# ---------------------------------------------------------------------------
# TestGitExecutor
# ---------------------------------------------------------------------------


class TestGitExecutor:
    """Worktree-containment logic (Story 15.2 — NO subprocess yet)."""

    def test_root_realpath_resolved_at_construction(self, tmp_path: Path) -> None:
        ex = GitExecutor(tmp_path)
        assert ex.worktree_root == Path(os.path.realpath(tmp_path))

    def test_contains_root_itself(self, tmp_path: Path) -> None:
        ex = GitExecutor(tmp_path)
        assert ex._contains(tmp_path) is True

    def test_contains_in_root_path(self, tmp_path: Path) -> None:
        ex = GitExecutor(tmp_path)
        inside = tmp_path / "subdir" / "file.txt"
        assert ex._contains(inside) is True

    def test_refuses_escaping_path(self, tmp_path: Path) -> None:
        ex = GitExecutor(tmp_path)
        escaping = tmp_path / ".." / "evil.txt"
        assert ex._contains(escaping) is False

    def test_refuses_absolute_path_outside_root(self, tmp_path: Path) -> None:
        ex = GitExecutor(tmp_path)
        assert ex._contains(Path("/etc/passwd")) is False

    def test_refuses_symlink_escaping_root(self, tmp_path: Path) -> None:
        """A symlink inside the root pointing OUT must not be considered contained."""
        ex = GitExecutor(tmp_path)
        outside = tmp_path.parent / "outside_target"
        outside.mkdir()
        link = tmp_path / "escape_link"
        link.symlink_to(outside)
        assert ex._contains(link) is False


# ---------------------------------------------------------------------------
# TestCallerTraceId helper (shipped ahead of tools — Story 15.2)
# ---------------------------------------------------------------------------


class TestCallerTraceIdHelper:
    """The FR58 helper ships in the scaffold so 15.3's first tool inherits it."""

    def test_accepts_uuidv7(self) -> None:
        validate_caller_trace_id(_VALID_TRACE_ID)

    def test_accepts_telegram_form(self) -> None:
        validate_caller_trace_id(_VALID_TG_TRACE_ID)

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "bad-format",
            "tg:",
            "tg:0",
            "01917e5c-a7d1-7000-8abc-0123456789ab\n",
            " 01917e5c-a7d1-7000-8abc-0123456789ab",
        ],
    )
    def test_rejects_invalid_shapes(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Story 9.1 contract"):
            validate_caller_trace_id(bad)


# ---------------------------------------------------------------------------
# TestEntryPoint — env-var validation (subprocess)
# ---------------------------------------------------------------------------


class TestEntryPoint:
    """``python -m git_mcp`` exits 2 on missing/invalid required env vars."""

    def test_main_exits_2_on_missing_worktree_root(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GIT_MCP_WORKTREE_ROOT"}
        env["GIT_MCP_ACTOR_KIND"] = "worker"
        env["GIT_MCP_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "git_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "GIT_MCP_WORKTREE_ROOT" in result.stderr

    def test_main_exits_2_on_missing_actor_kind(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GIT_MCP_ACTOR_KIND"}
        env["GIT_MCP_WORKTREE_ROOT"] = "/tmp"
        env["GIT_MCP_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "git_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "GIT_MCP_ACTOR_KIND" in result.stderr

    def test_main_exits_2_on_missing_actor_id(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GIT_MCP_ACTOR_ID"}
        env["GIT_MCP_WORKTREE_ROOT"] = "/tmp"
        env["GIT_MCP_ACTOR_KIND"] = "worker"
        result = subprocess.run(
            [sys.executable, "-m", "git_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "GIT_MCP_ACTOR_ID" in result.stderr

    def test_main_exits_2_on_invalid_actor_kind(self) -> None:
        env = dict(os.environ)
        env["GIT_MCP_WORKTREE_ROOT"] = "/tmp"
        env["GIT_MCP_ACTOR_KIND"] = "invalid_role"
        env["GIT_MCP_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "git_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "invalid" in result.stderr.lower()
