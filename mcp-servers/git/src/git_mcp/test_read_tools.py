"""Green unit tests for the Tier-1 git read tools + run_git sandbox (Story 15.3).

These exercise the actual ``git`` subprocess (via ``GitExecutor.run_git``) over a
real temp repo, plus the structured parsers and the security invariants
(path-escape refusal, env allowlist, timeout kill+reap). ``check_no_subprocess``
exempts co-located ``test_*.py`` files, so the test setup may shell out to ``git``
to build a fixture repo.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

from git_mcp.handlers.tools import _parse_numstat
from git_mcp.server import GitExecutor, GitOutputTooLarge, GitTimeout, _build_git_env

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"

# Hermetic author/committer identity so commits don't depend on operator config.
_GIT_FIXTURE_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.test",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.test",
}


def _git(repo: Path, *args: str) -> None:
    """Run a ``git`` command in *repo* with hermetic identity (fixture setup only)."""
    env = {**os.environ, **_GIT_FIXTURE_ENV}
    subprocess.run(  # noqa: S603 — test fixture builder, not the request path
        ["git", "-C", str(repo), *args],
        check=True,
        env=env,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file and one dirty + one untracked file."""
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "tracked.txt").write_text("line1\nline2\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "initial commit")
    # Dirty the worktree: modify the tracked file + add an untracked file.
    (root / "tracked.txt").write_text("line1\nline2\nline3\n")
    (root / "untracked.txt").write_text("new\n")
    return root


async def _tool(repo: Path, name: str) -> Callable[..., Awaitable[dict[str, object]]]:
    """Build the audit-off server over *repo* and return the named tool's fn."""
    from events import FROZEN_EPOCH, FrozenClock

    from git_mcp.server import build_server

    mcp = build_server(
        worktree_root=repo,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="worker",
        actor_id="test-worker",
    )
    # ``list_tools()`` returns schema objects (no ``.fn``); the callable lives on
    # the FastMCP tool-manager keyed by the dotted name.
    await mcp.list_tools()
    return mcp._tool_manager._tools[name].fn


# ---------------------------------------------------------------------------
# Structured tool results over a real repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_dirty_worktree(repo: Path) -> None:
    fn = await _tool(repo, "git.status")
    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert result["ok"] is True
    assert result["branch"] == "main"
    assert result["clean"] is False
    entries = cast("list[dict[str, str]]", result["entries"])
    paths = {e["path"] for e in entries}
    assert "tracked.txt" in paths
    assert "untracked.txt" in paths


@pytest.mark.asyncio
async def test_log_returns_commits_in_order(repo: Path) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second commit")
    fn = await _tool(repo, "git.log")
    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert result["ok"] is True
    commits = cast("list[dict[str, str]]", result["commits"])
    subjects = [c["subject"] for c in commits]
    assert subjects == ["second commit", "initial commit"]
    assert commits[0]["author"] == "Test Author"


@pytest.mark.asyncio
async def test_branch_reports_current(repo: Path) -> None:
    fn = await _tool(repo, "git.branch")
    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert result["ok"] is True
    assert result["current"] == "main"
    branches = cast("list[str]", result["branches"])
    assert "main" in branches


@pytest.mark.asyncio
async def test_diff_numstat_counts(repo: Path) -> None:
    fn = await _tool(repo, "git.diff")
    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert result["ok"] is True
    files = cast("list[dict[str, object]]", result["files"])
    by_path = {cast("str", f["path"]): f for f in files}
    # tracked.txt gained one line vs HEAD (untracked.txt is not in `git diff`).
    assert by_path["tracked.txt"]["added"] == 1
    assert by_path["tracked.txt"]["deleted"] == 0


# ---------------------------------------------------------------------------
# _parse_numstat rename handling (deferred-work P1 — `git.diff` rename detection)
# ---------------------------------------------------------------------------
#
# Under ``git diff --numstat -z`` a rename/copy emits the numstat record with an
# EMPTY path field, followed by the origin and destination names as two separate
# NUL records. The parser must consume the origin and surface the destination —
# otherwise the renamed file is recorded with ``path=""`` and its real name lost.


def test_parse_numstat_modify_only() -> None:
    """Baseline: a plain modify record is unaffected by rename handling."""
    out = "1\t0\ttracked.txt\x00"
    result = cast("list[dict[str, object]]", _parse_numstat(out)["files"])
    assert result == [{"added": 1, "deleted": 0, "path": "tracked.txt"}]


def test_parse_numstat_text_rename_surfaces_destination() -> None:
    """A text rename surfaces the destination path, not ``path=''``."""
    # added=2, deleted=1, empty path, then origin + destination NUL records.
    out = "2\t1\t\x00old/name.py\x00new/name.py\x00"
    files = cast("list[dict[str, object]]", _parse_numstat(out)["files"])
    assert files == [{"added": 2, "deleted": 1, "path": "new/name.py"}]
    assert all(f["path"] != "" for f in files)


def test_parse_numstat_binary_rename_surfaces_destination() -> None:
    """A binary rename surfaces the destination with ``None`` counts."""
    out = "-\t-\t\x00bin/old.png\x00bin/new.png\x00"
    files = cast("list[dict[str, object]]", _parse_numstat(out)["files"])
    assert files == [{"added": None, "deleted": None, "path": "bin/new.png"}]


def test_parse_numstat_mixed_modify_and_rename() -> None:
    """A modify and a rename in the same diff both keep their real paths."""
    out = "1\t0\tkept.txt\x00" "3\t2\t\x00src/old.py\x00src/new.py\x00"
    files = cast("list[dict[str, object]]", _parse_numstat(out)["files"])
    by_path = {cast("str", f["path"]): f for f in files}
    assert set(by_path) == {"kept.txt", "src/new.py"}
    assert by_path["src/new.py"] == {"added": 3, "deleted": 2, "path": "src/new.py"}
    assert "" not in by_path


# ---------------------------------------------------------------------------
# Sandbox / security invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_path_escape_raises(repo: Path) -> None:
    fn = await _tool(repo, "git.diff")
    escaping = str(repo / ".." / "evil")
    with pytest.raises(ValueError, match="escapes worktree root"):
        await fn(caller_trace_id=_VALID_TRACE_ID, path=escaping)


@pytest.mark.asyncio
async def test_run_git_timeout_kills_and_reaps(repo: Path) -> None:
    """A wedged git invocation raises GitTimeout and leaves the sandbox usable.

    A near-zero ``timeout`` forces ``asyncio.wait_for`` to trip before
    ``communicate()`` returns, driving ``run_git``'s kill+reap path. A
    subsequent call must still succeed (no leaked/zombie process wedges later
    invocations).
    """
    ex = GitExecutor(repo)
    with pytest.raises(GitTimeout):
        await ex.run_git(["log", "--format=%H", "-n", "1"], timeout=1e-9)
    ok = await ex.run_git(["rev-parse", "--is-inside-work-tree"])
    assert ok.returncode == 0


@pytest.mark.asyncio
async def test_run_git_output_cap_kills_and_reaps(repo: Path) -> None:
    """Output past the cap raises GitOutputTooLarge and leaves the sandbox usable.

    A tiny ``output_cap`` forces the incremental reader to trip before the
    subprocess finishes, driving ``run_git``'s kill+reap path (the
    memory-pressure sibling of the timeout path). A subsequent call must still
    succeed (no leaked/zombie process wedges later invocations).
    """
    ex = GitExecutor(repo)
    with pytest.raises(GitOutputTooLarge):
        # `git log` emits a commit hash + metadata — far more than 4 bytes.
        await ex.run_git(["log", "--format=%H"], output_cap=4)
    ok = await ex.run_git(["rev-parse", "--is-inside-work-tree"])
    assert ok.returncode == 0


@pytest.mark.asyncio
async def test_run_git_output_under_cap_succeeds(repo: Path) -> None:
    """Output within the cap returns normally (boundary: cap not tripped)."""
    ex = GitExecutor(repo)
    result = await ex.run_git(["rev-parse", "--is-inside-work-tree"], output_cap=4096)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


@pytest.mark.asyncio
async def test_run_git_output_cap_boundary_is_exclusive(repo: Path) -> None:
    """Output of exactly ``output_cap`` bytes succeeds (cap check is ``>``, not ``>=``)."""
    ex = GitExecutor(repo)
    # `rev-parse --is-inside-work-tree` emits exactly "true\n" = 5 bytes.
    result = await ex.run_git(["rev-parse", "--is-inside-work-tree"], output_cap=5)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_build_git_env_drops_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _build_git_env()
    assert "GITHUB_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "HOME" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_SYSTEM"] == "/dev/null"


@pytest.mark.asyncio
async def test_nonzero_returncode_surfaced_as_structured_error(tmp_path: Path) -> None:
    """A git failure (not a repo) returns ok=False, not an exception."""
    root = tmp_path / "norepo"
    root.mkdir()
    ex = GitExecutor(root)
    result = await ex.run_git(["status", "--porcelain"])
    assert result.returncode != 0
    assert "not a git repository" in result.stderr.lower()


@pytest.mark.asyncio
async def test_run_git_blocks_repo_local_config_fsmonitor_rce(repo: Path, tmp_path: Path) -> None:
    """P0 regression (Story 15.3 security review): repo-local ``.git/config``
    ``core.fsmonitor`` must NOT execute on a read.

    ``GIT_CONFIG_GLOBAL/SYSTEM=/dev/null`` only disables global/system config; the
    repo-local ``.git/config`` is still read by ``git status`` and is attacker-
    writable (the worktree is the worker's sandbox). Without the ``_GIT_HARDENING``
    ``-c core.fsmonitor=`` override, ``git status`` runs the configured program →
    arbitrary command execution as the server. This locks the shield.
    """
    sentinel = tmp_path / "fsmonitor_pwned"
    evil = repo / "evil.sh"
    evil.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    evil.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[core]\n\tfsmonitor = {evil}\n")

    result = await GitExecutor(repo).run_git(["status", "--porcelain=v1", "-z", "--branch"])

    assert not sentinel.exists(), (
        "run_git executed repo-local core.fsmonitor → P0 repo-local-config RCE not blocked"
    )
    # The shield disables fsmonitor but the read itself still succeeds.
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_run_git_blocks_repo_local_config_hookspath_rce(repo: Path, tmp_path: Path) -> None:
    """P0 regression: a repo-local ``core.hooksPath`` post-index-change hook must NOT
    execute on ``git status`` (closed by ``_GIT_HARDENING`` ``core.hooksPath=/dev/null``)."""
    sentinel = tmp_path / "hook_pwned"
    hooks = repo / "evilhooks"
    hooks.mkdir()
    hook = hooks / "post-index-change"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    hook.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[core]\n\thooksPath = {hooks}\n")

    await GitExecutor(repo).run_git(["status", "--porcelain=v1", "-z", "--branch"])

    assert not sentinel.exists(), (
        "run_git ran a repo-local-configured hook → P0 repo-local-config RCE not blocked"
    )


def test_build_git_env_sets_nosystem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth: GIT_CONFIG_NOSYSTEM=1 alongside GIT_CONFIG_SYSTEM=/dev/null."""
    env = _build_git_env()
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
