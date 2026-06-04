"""Green unit tests for the Tier-2/3 git mutating tools + their RCE shield (Story 15.4).

These exercise the actual ``git`` subprocess (via ``GitExecutor.run_git``) over a
real temp repo and lock the P0 security shield that Story 15.4 extends:

  * ``diff.external`` — a repo-local content-diff external program must NOT execute
    on the commit (diff) path (closed by the BASE ``_GIT_HARDENING`` ``-c diff.external=``).
  * ``core.sshCommand`` — a repo-local ssh-command must NOT execute on the push
    path (closed by the push-only ``_GIT_HARDENING_PUSH`` ``-c core.sshCommand=``).
  * a repo-local hook must NOT execute on commit (base ``core.hooksPath=/dev/null``).
  * ``GIT_SSH`` / ``GIT_SSH_COMMAND`` are NOT in ``_GIT_ENV_ALLOWLIST`` (env vector).

Each RCE regression follows the 15.3 gold-standard pattern: PROVE the vector is
live unshielded (precondition), then assert the shielded ``run_git`` path blocks
it. ``check_no_subprocess`` exempts co-located ``test_*.py`` files, so the fixture
builders may shell out to ``git``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git_mcp.server import (
    _GIT_ENV_ALLOWLIST,
    _GIT_HARDENING_PUSH,
    GitExecutor,
    RepoConfigUnsafe,
    _build_git_env,
)

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
        ["git", "-C", str(repo), *args], check=True, env=env, capture_output=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file + a pending staged change."""
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "tracked.txt").write_text("line1\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


# ---------------------------------------------------------------------------
# Unshielded-precondition probe: build the SAME hermetic child-env the shield
# uses but WITHOUT the per-invocation ``-c`` overrides, so we can prove each
# vector is live before asserting the shield blocks it. ``GIT_CONFIG_GLOBAL/
# SYSTEM=/dev/null`` is kept (the repo-local config is the attack surface).
# ---------------------------------------------------------------------------


def _run_git_unshielded(repo: Path, *subcmd: str) -> int:
    env = {**_GIT_FIXTURE_ENV, "PATH": os.environ.get("PATH", "")}
    proc = subprocess.run(  # noqa: S603 — precondition prover, not the request path
        ["git", "-C", str(repo), *subcmd],
        env=env,
        capture_output=True,
    )
    return proc.returncode


@pytest.mark.asyncio
async def test_run_git_blocks_repo_local_diff_external_rce_on_commit_path(
    repo: Path, tmp_path: Path
) -> None:
    """P0 (Story 15.4): a repo-local ``diff.external`` must NOT execute on the diff path.

    ``git commit`` (and content-diff machinery) invokes ``diff.external`` when set.
    A malicious repo-local ``.git/config`` ``diff.external = <prog>`` runs an
    arbitrary command when git computes a content diff — RCE as the server. The
    BASE ``_GIT_HARDENING`` ``-c diff.external=`` neutralizes it.
    """
    evil = repo / "evil.sh"

    # -- precondition: the vector is LIVE unshielded --
    pre_sentinel = tmp_path / "diff_external_pre"
    evil.write_text(f"#!/bin/sh\ntouch '{pre_sentinel}'\nexit 0\n")
    evil.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[diff]\n\texternal = {evil}\n")
    (repo / "tracked.txt").write_text("line1\nline2\n")
    # ``git diff`` with a worktree change + diff.external set runs the program.
    _run_git_unshielded(repo, "diff")
    assert pre_sentinel.exists(), (
        "precondition failed: diff.external did NOT execute unshielded — test is not "
        "proving a live vector"
    )

    # -- shielded: the override neutralizes diff.external --
    sentinel = tmp_path / "diff_external_pwned"
    evil.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 0\n")
    evil.chmod(0o755)
    result = await GitExecutor(repo).run_git(["diff", "--numstat", "-z"])
    assert not sentinel.exists(), (
        "run_git executed repo-local diff.external → P0 diff.external RCE not blocked"
    )
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_run_git_blocks_repo_local_ssh_command_rce_on_push_path(
    repo: Path, tmp_path: Path
) -> None:
    """P0 (Story 15.4): a repo-local ``core.sshCommand`` must NOT execute on push.

    A ``core.sshCommand = <prog>`` runs an arbitrary command instead of ``ssh`` for
    an ``ssh://`` remote. The push-only ``_GIT_HARDENING_PUSH`` overlay
    (``-c core.sshCommand=``) neutralizes it. We point ``origin`` at an ``ssh://``
    URL so git would invoke the ssh transport (and thus sshCommand) if unshielded.
    """
    evil = repo / "evil_ssh.sh"

    # -- precondition: the vector is LIVE unshielded --
    pre_sentinel = tmp_path / "ssh_command_pre"
    evil.write_text(f"#!/bin/sh\ntouch '{pre_sentinel}'\nexit 1\n")
    evil.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[core]\n\tsshCommand = {evil}\n")
    _git(repo, "remote", "add", "origin", "ssh://git@localhost/nonexistent.git")
    # Unshielded push attempts the ssh transport → runs sshCommand (then fails).
    _run_git_unshielded(repo, "push", "origin", "main")
    assert pre_sentinel.exists(), (
        "precondition failed: core.sshCommand did NOT execute unshielded — test is "
        "not proving a live vector"
    )

    # -- shielded: the push-overlay neutralizes core.sshCommand --
    sentinel = tmp_path / "ssh_command_pwned"
    evil.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 1\n")
    evil.chmod(0o755)
    # The push will fail (no real remote), but the sentinel must NOT appear:
    # the sshCommand override means git never invokes the malicious program.
    await GitExecutor(repo).run_git(
        ["push", "--no-verify", "origin", "main"], extra_hardening=_GIT_HARDENING_PUSH
    )
    assert not sentinel.exists(), (
        "run_git executed repo-local core.sshCommand → P0 sshCommand RCE not blocked"
    )


@pytest.mark.asyncio
async def test_run_git_blocks_repo_local_hook_on_commit(repo: Path, tmp_path: Path) -> None:
    """P0 (Story 15.4): a repo-local ``core.hooksPath`` pre-commit hook must NOT run on commit.

    The base ``_GIT_HARDENING`` ``core.hooksPath=/dev/null`` neutralizes hooks; the
    commit handler ALSO passes ``--no-verify`` (belt-and-suspenders). Either alone
    blocks the hook — this asserts the shielded path keeps the sentinel uncreated.
    """
    sentinel = tmp_path / "precommit_pwned"
    hooks = repo / "evilhooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 0\n")
    hook.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[core]\n\thooksPath = {hooks}\n")

    (repo / "tracked.txt").write_text("line1\nline2\n")
    await GitExecutor(repo).run_git(["add", "--", "tracked.txt"])
    # Hermetic identity via -c (mirrors the git.commit handler), --no-verify.
    await GitExecutor(repo).run_git(
        [
            "-c",
            "user.name=git-mcp",
            "-c",
            "user.email=git-mcp+test@oh-my-bmad.local",
            "commit",
            "--no-verify",
            "-m",
            "shielded commit",
        ]
    )
    assert not sentinel.exists(), (
        "run_git ran a repo-local pre-commit hook → P0 repo-local-config RCE not blocked"
    )


def test_build_git_env_excludes_ssh_vectors() -> None:
    """``GIT_SSH`` / ``GIT_SSH_COMMAND`` are NOT forwarded to the child git env.

    These env vars name a program git runs for the ssh transport — an inherited
    operator/attacker value would be an RCE on the push path. They are excluded
    from ``_GIT_ENV_ALLOWLIST`` (env-level defense), so even if set in the parent
    they never reach the child env.
    """
    assert "GIT_SSH" not in _GIT_ENV_ALLOWLIST
    assert "GIT_SSH_COMMAND" not in _GIT_ENV_ALLOWLIST


def test_build_git_env_drops_ssh_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when set in the parent env, GIT_SSH/GIT_SSH_COMMAND are dropped."""
    monkeypatch.setenv("GIT_SSH", "/tmp/evil")
    monkeypatch.setenv("GIT_SSH_COMMAND", "/tmp/evil --opt")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _build_git_env()
    assert "GIT_SSH" not in env
    assert "GIT_SSH_COMMAND" not in env


# ---------------------------------------------------------------------------
# P0 (security review) — exec-capable repo-local drivers the ``-c`` shields
# CANNOT neutralize (gitattributes-chosen driver names): the pre-op config-scrub
# (``assert_safe_repo_config``) refuses the mutation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_safe_repo_config_refuses_filter_clean_rce_on_add(
    repo: Path, tmp_path: Path
) -> None:
    """P0-1: a repo-local ``filter.<name>.clean`` runs on ``git add`` (driver name
    is attacker-chosen → no ``-c`` override possible). The scrub must refuse."""
    evil = repo / "evil_filter.sh"
    # -- precondition: the filter.clean vector is LIVE on `git add` unshielded --
    pre = tmp_path / "filter_pre"
    evil.write_text(f"#!/bin/sh\ntouch '{pre}'\ncat\n")
    evil.chmod(0o755)
    (repo / ".gitattributes").write_text("* filter=evil\n")
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f'\n[filter "evil"]\n\tclean = {evil}\n')
    (repo / "newfile.txt").write_text("data\n")
    _run_git_unshielded(repo, "add", "newfile.txt")
    assert pre.exists(), "precondition failed: filter.clean did NOT execute unshielded on git add"
    # -- the pre-op scrub detects filter.evil.clean and refuses the mutation --
    with pytest.raises(RepoConfigUnsafe, match="filter.evil.clean"):
        await GitExecutor(repo).assert_safe_repo_config()


@pytest.mark.asyncio
async def test_assert_safe_repo_config_refuses_merge_driver_on_rebase(repo: Path) -> None:
    """P0-2: a repo-local ``merge.<name>.driver`` executes during ``git rebase``'s
    3-way merge (security review confirmed live). The scrub must refuse."""
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write('\n[merge "evil"]\n\tdriver = touch /tmp/should_not_run %O %A %B\n')
    with pytest.raises(RepoConfigUnsafe, match="merge.evil.driver"):
        await GitExecutor(repo).assert_safe_repo_config()


@pytest.mark.asyncio
async def test_assert_safe_repo_config_allows_clean_repo(repo: Path) -> None:
    """The scrub must NOT false-positive on a normal repo (no exec-driver config)."""
    await GitExecutor(repo).assert_safe_repo_config()  # no raise


@pytest.mark.asyncio
async def test_push_denies_network_url_insteadof_rewrite(repo: Path, tmp_path: Path) -> None:
    """P0-3: a repo-local ``url.<https>.insteadOf`` rewriting the local remote to a
    NETWORK URL must fail closed (``protocol.https.allow=never``) — no SSRF/exfil."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)  # noqa: S603,S607 — test fixture
    _git(repo, "remote", "add", "origin", str(bare))
    with (repo / ".git" / "config").open("a", encoding="utf-8") as fh:
        fh.write(f'\n[url "https://attacker.invalid/x.git"]\n\tinsteadOf = {bare}\n')
    result = await GitExecutor(repo).run_git(
        ["push", "--no-verify", "origin", "main"], extra_hardening=_GIT_HARDENING_PUSH
    )
    assert result.returncode != 0
    assert "not allowed" in result.stderr.lower() or "protocol" in result.stderr.lower(), (
        f"https rewrite was not blocked by protocol.allow=never; stderr={result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_push_to_local_bare_remote_succeeds_under_hardening(
    repo: Path, tmp_path: Path
) -> None:
    """The push protocol-denial overlay must NOT break a legit local/file push."""
    bare = tmp_path / "bare2.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)  # noqa: S603,S607 — test fixture
    _git(repo, "remote", "add", "origin", str(bare))
    result = await GitExecutor(repo).run_git(
        ["push", "--no-verify", "origin", "main"], extra_hardening=_GIT_HARDENING_PUSH
    )
    assert result.returncode == 0, f"local bare push failed under hardening: {result.stderr!r}"
