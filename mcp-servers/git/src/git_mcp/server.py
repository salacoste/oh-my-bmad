"""git-mcp MCP server — bounded git operations over a sandboxed worktree (Epic 15 / FR72).

Exports:
    ``build_server(*, worktree_root, clock, actor_kind, actor_id, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Story 15.2 ships
        the SCAFFOLD: no git tools are registered yet (``TIER_MAP`` is empty);
        the git tools land in Stories 15.3 / 15.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      task-registry does: when ``clawhip_bridge_command`` is configured, a stdio
      MCP client to clawhip-bridge is spawned inside the FastMCP lifespan and
      held by an ``EmitterHolder`` (consumed by the tool decorators once tools
      land in 15.3). Startup is bounded by ``asyncio.wait_for(initialize(),
      _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - ``GitExecutor`` realpath-resolves the worktree root and exposes a
      ``_contains`` containment helper. Story 15.2 ships ONLY the containment
      logic (import-clean + unit-tested); the actual ``git`` subprocess spawn
      lands in Story 15.3.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from git_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of task-registry / clawhip_client ``_INIT_TIMEOUT``.
# Startup spawn of the clawhip-bridge subprocess must complete within this bound
# or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging forever.
_INIT_TIMEOUT: float = 30.0

# Wall-clock bound for any single ``git`` invocation (Story 15.3). A wedged git
# process (e.g. waiting on a lock, a credential prompt, an fsmonitor hook) must
# not hang the request path — ``run_git`` kills+reaps on timeout and raises.
_GIT_TIMEOUT: float = 30.0

# Byte bound for a single ``git`` invocation's combined-per-stream output
# (deferred-work P1). The timeout bounds WALL-CLOCK but not MEMORY: a
# pathological repo (or a future content-exposing tool) could stream gigabytes
# faster than the timeout trips. ``run_git`` reads each stream incrementally and,
# once a stream crosses this cap, kills+reaps the subprocess and raises
# ``GitOutputTooLarge`` — the memory-pressure sibling of ``GitTimeout``. 16 MiB
# comfortably exceeds the cardinality-scaled metadata the read tools emit.
_GIT_OUTPUT_CAP: int = 16 * 1024 * 1024
# Per-read chunk size for the incremental bounded reader.
_GIT_READ_CHUNK: int = 65536

# Env allowlist for the spawned ``git`` subprocess (Story 15.3, P0/security).
# Only these parent-env vars are forwarded to git; everything else (operator /
# platform secrets) is dropped. ``HOME`` is INTENTIONALLY EXCLUDED so git cannot
# read a malicious ``~/.gitconfig``; hermetic config (``GIT_CONFIG_GLOBAL`` /
# ``GIT_CONFIG_SYSTEM`` → ``/dev/null`` + ``GIT_CONFIG_NOSYSTEM=1``) is overlaid
# in ``_build_git_env``. NEVER add ``GITHUB_TOKEN`` / ``ANTHROPIC_API_KEY`` / any
# ``*_TOKEN`` / ``*_KEY`` / ``*_SECRET`` — reads need no credentials and the
# worktree is local.
#
# NOTE: env-level hermeticity only disables GLOBAL+SYSTEM config. The repo-local
# ``.git/config`` is STILL read by git on every read op and is attacker-writable
# (the worktree is the worker's sandbox), so the per-invocation ``_GIT_HARDENING``
# shield below — NOT this env — is what closes the repo-local config RCE.
_GIT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)

# P0 (Story 15.3 security review) — per-invocation repo-local-config shield.
# ``GIT_CONFIG_*=/dev/null`` disables GLOBAL+SYSTEM config, but ``git status`` /
# ``diff`` / ``log`` in a worktree STILL read the repo-local ``.git/config``,
# which a malicious worker (or attacker-supplied repo content / merged branch)
# can write. A repo-local ``core.fsmonitor`` or ``core.hooksPath`` executes an
# arbitrary command on a mere ``git status`` (empirically confirmed RCE). These
# ``-c`` overrides are injected into every invocation's argv (after ``-C`` and
# before the subcommand) to neutralize that; ``--no-optional-locks`` avoids
# lock-file side effects on a read. Story 15.4 (content diff + push) MUST extend
# this for the ``diff.external`` / ``*.textconv`` and ``core.sshCommand`` /
# ``GIT_SSH`` vectors those tools open.
#
# Story 15.4 (content diff + push) extends this base with ``-c diff.external=``:
# the mutating tools (``git.commit`` invokes diff machinery; an attacker-supplied
# repo-local ``diff.external`` runs an arbitrary command when git computes a
# content diff — empirically confirmed RCE, same class as ``core.fsmonitor``).
# Neutralizing it here (in the BASE) closes the vector on every path, not just
# commit. The push-only ``core.sshCommand`` vector is closed by the separate
# ``_GIT_HARDENING_PUSH`` overlay (threaded via ``run_git(extra_hardening=...)``)
# so non-push paths don't carry a push-specific override.
_GIT_HARDENING: tuple[str, ...] = (
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
    "--no-optional-locks",
)

# Story 15.4 P0 — push-path-only hardening overlay. ``git.push`` to a configured
# remote can be hijacked by a repo-local ``core.sshCommand`` (runs an arbitrary
# command instead of ``ssh`` for an ``ssh://`` / ``scp``-style remote). Even
# though DECISION-1(A) restricts the push target to a LOCAL bare remote (no ssh
# transport reached in practice), the override is injected belt-and-suspenders so
# a repo-local ``core.sshCommand`` can never execute on the push path. Passed via
# ``run_git(..., extra_hardening=_GIT_HARDENING_PUSH)`` so read/commit paths do
# not carry it. (``GIT_SSH`` / ``GIT_SSH_COMMAND`` are env vectors — closed by
# their exclusion from ``_GIT_ENV_ALLOWLIST``; asserted in the regression suite.)
_GIT_HARDENING_PUSH: tuple[str, ...] = (
    "-c",
    "core.sshCommand=",
    # Story 15.4 P0-3 (security review) — DECISION-1(A) restricts push to a LOCAL
    # bare remote, but a repo-local ``url.<base>.insteadOf`` can rewrite that local
    # target to a NETWORK URL (https/ssh/git/ftp), turning push into an SSRF /
    # repo-exfiltration primitive (the ``core.sshCommand=`` shield covers ssh but
    # NOT https). Deny every network transport so any rewrite to a remote URL fails
    # closed BEFORE connecting; ``file``/local transport keeps its default (allowed)
    # so the legitimate bare-remote push still works.
    "-c",
    "protocol.https.allow=never",
    "-c",
    "protocol.http.allow=never",
    "-c",
    "protocol.ssh.allow=never",
    "-c",
    "protocol.ftp.allow=never",
    "-c",
    "protocol.ftps.allow=never",
    "-c",
    "protocol.git.allow=never",
)


def _build_git_env() -> dict[str, str]:
    """Return the explicit, hermetic child-env for the ``git`` subprocess.

    P0/security (Story 15.3): start from the parent env filtered to
    ``_GIT_ENV_ALLOWLIST`` (drops every secret / ``HOME``), then overlay
    ``GIT_CONFIG_GLOBAL`` / ``GIT_CONFIG_SYSTEM`` = ``/dev/null`` (D1, REQUIRED)
    so no global/system git config — and therefore no attacker-controlled
    ``core.pager`` / alias / fsmonitor hook — can execute on the read path.
    """
    env = {k: v for k, v in os.environ.items() if k in _GIT_ENV_ALLOWLIST}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"  # belt-and-suspenders with GIT_CONFIG_SYSTEM=/dev/null
    return env


class GitTimeout(RuntimeError):  # noqa: N818 — domain-named (no Error suffix); mirrors events.errors CapabilityDenied / WorktreeLockHeld precedent
    """Raised when a single ``git`` invocation exceeds ``_GIT_TIMEOUT`` (Story 15.3).

    The wedged subprocess is killed and reaped before this propagates so no
    zombie/leaked process survives the request.
    """


class GitOutputTooLarge(RuntimeError):  # noqa: N818 — domain-named (no Error suffix); mirrors GitTimeout precedent
    """Raised when a ``git`` invocation's output exceeds ``_GIT_OUTPUT_CAP``.

    The memory-pressure sibling of ``GitTimeout`` (deferred-work P1): the
    subprocess is killed and reaped before this propagates, so a pathological
    repo cannot exhaust request-path memory by streaming unbounded output that
    completes within the wall-clock timeout.
    """


class RepoConfigUnsafe(RuntimeError):  # noqa: N818 — domain-named (no Error suffix); mirrors GitTimeout / events.errors precedent
    """Raised when a mutating op finds an exec-capable driver in the repo-local config.

    Story 15.4 P0 (security review): ``filter.<name>.clean/smudge/process`` and
    ``merge.<name>.driver`` (and named ``diff.<name>.command/textconv``) run an
    arbitrary command via a gitattributes-chosen driver NAME. Because the name is
    attacker-controlled, no name-agnostic ``-c <key>=`` override can neutralize
    them (unlike ``core.fsmonitor`` / ``diff.external`` which are fixed keys). A
    mutating git op (add / commit / push / rebase) therefore REFUSES to run on a
    worktree whose repo-local ``.git/config`` defines any such driver.
    """


# Repo-local config keys that execute an arbitrary command via an attacker-chosen
# (gitattributes-driven) driver name. Reading config does NOT execute a driver, so
# the pre-op scrub (``GitExecutor.assert_safe_repo_config``) can safely read these
# and refuse. Fixed-key exec vectors (``core.fsmonitor`` / ``core.hooksPath`` /
# ``diff.external`` / ``core.sshCommand``) are handled by the ``-c`` shields and are
# NOT listed here.
_DANGEROUS_CONFIG_RE: re.Pattern[str] = re.compile(
    r"^(?:filter\..+\.(?:clean|smudge|process)"
    r"|merge\..+\.driver"
    r"|diff\..+\.(?:command|textconv))$"
)


@dataclass(frozen=True)
class GitResult:
    """Structured outcome of a single ``git`` invocation (Story 15.3).

    A non-zero ``returncode`` is NOT an exception — handlers surface it as a
    structured ``{"ok": false, ...}`` payload. Only a timeout (``GitTimeout``)
    or a spawn failure propagates as an exception.
    """

    returncode: int
    stdout: str
    stderr: str


# Story 15.2 scaffold — empty until the git tools land in 15.3 / 15.4. Re-exported
# from ``handlers.tools`` so the canonical TIER_MAP lives in one place (mirrors
# the task-registry handlers/tools.py shape). Tools register against it later.
TIER_MAP: dict[str, Tier] = {}


class GitExecutor:
    """Sandbox guard for git operations confined to a single worktree.

    Story 15.2 ships ONLY the containment logic. The ``worktree_root`` is
    realpath-resolved at construction so symlink components in the configured
    path cannot later be used to escape the sandbox. ``_contains`` answers
    whether a candidate path resolves to a location inside the root — the
    load-bearing check the git tools (15.3 / 15.4) call before invoking any
    ``git`` subprocess. NO subprocess is spawned in this story.
    """

    def __init__(self, worktree_root: Path) -> None:
        # ``realpath`` (not ``resolve``) so a non-existent root still resolves
        # its existing symlink components deterministically — the git tools in
        # 15.3 validate existence separately. ``strict=False`` semantics match
        # ``os.path.realpath`` for not-yet-created subpaths.
        self.worktree_root: Path = Path(os.path.realpath(worktree_root))

    def _contains(self, path: Path) -> bool:
        """Return True iff *path* resolves to a location inside the worktree root.

        Realpath-resolves *path* (collapsing ``..`` and symlinks) then checks
        containment via ``Path.is_relative_to``. The root itself counts as
        contained. An escaping path (``..`` traversal, absolute path outside
        the root, or a symlink pointing out) returns False.
        """
        resolved = Path(os.path.realpath(path))
        return resolved == self.worktree_root or resolved.is_relative_to(self.worktree_root)

    async def run_git(
        self,
        subcmd: list[str],
        *,
        timeout: float = _GIT_TIMEOUT,
        output_cap: int = _GIT_OUTPUT_CAP,
        extra_hardening: tuple[str, ...] = (),
    ) -> GitResult:
        """Run ``git -C <root> <subcmd...>`` in the sandbox and return its result.

        The ONLY git spawn-site (Story 15.3). Security (every item load-bearing):

        - ``create_subprocess_exec`` (the ``_exec`` variant), NEVER ``_shell``:
          *subcmd* is passed as discrete argv elements, so a user-supplied path
          filter is a single list element and can never be re-parsed by a shell.
          (Callers place ``--`` before any path filter so a path starting with
          ``-`` cannot be parsed as a flag — option-injection defense.)
        - Location is pinned twice: ``-C <root>`` AND ``cwd=<root>``.
        - ``env`` is the hermetic allowlist from ``_build_git_env`` (no secrets,
          no ``HOME``, ``GIT_CONFIG_*=/dev/null``).
        - ``_GIT_HARDENING`` ``-c`` overrides are injected before *subcmd* to
          neutralize the repo-local ``.git/config`` RCE surface (``core.fsmonitor``
          / ``core.hooksPath`` / ``diff.external`` would otherwise run on a mere
          ``git status`` / diff). *extra_hardening* (Story 15.4) appends
          path-specific overrides AFTER the base — e.g. the push path passes
          ``_GIT_HARDENING_PUSH`` (``core.sshCommand=``) so the ssh-transport
          vector is closed without burdening read/commit invocations.
        - A wedged git is bounded by ``timeout``; on expiry it is killed+reaped
          and ``GitTimeout`` is raised (no leaked process).
        - Output is bounded by ``output_cap``: each stream is read incrementally
          and, once a stream crosses the cap, the subprocess is killed+reaped and
          ``GitOutputTooLarge`` is raised (memory-pressure sibling of the
          timeout). ``communicate()`` is NOT used — it would buffer unbounded
          output in memory before returning. Both streams are drained
          concurrently so a full stderr pipe cannot deadlock a stdout read.

        A non-zero exit is NOT raised — it is returned in ``GitResult.returncode``
        so handlers can surface a structured error. Output is decoded with
        ``errors="replace"`` so binary/garbled bytes never crash the request.
        """
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.worktree_root),
            *_GIT_HARDENING,
            *extra_hardening,
            *subcmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.worktree_root),
            env=_build_git_env(),
        )

        async def _read_capped(stream: asyncio.StreamReader) -> bytes:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await stream.read(_GIT_READ_CHUNK)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > output_cap:
                    raise GitOutputTooLarge(f"git {subcmd!r} exceeded {output_cap}-byte output cap")
                chunks.append(chunk)

        # ``proc.stdout``/``proc.stderr`` are non-None because both were PIPE'd.
        assert proc.stdout is not None and proc.stderr is not None
        out_task = asyncio.ensure_future(_read_capped(proc.stdout))
        err_task = asyncio.ensure_future(_read_capped(proc.stderr))

        async def _cleanup_child() -> None:
            # Cancel both readers, kill+reap the child (if still live), then
            # await-suppress the tasks so none is left pending and no "exception
            # never retrieved" / "task destroyed pending" warning fires.
            # gather() does NOT cancel the sibling on first error, and wait_for
            # cancels gather (and its children) on timeout — the explicit
            # cancels are idempotent across both.
            out_task.cancel()
            err_task.cancel()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            await asyncio.gather(out_task, err_task, return_exceptions=True)

        try:
            out, err = await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout)
        except (TimeoutError, GitOutputTooLarge) as exc:
            await _cleanup_child()
            if isinstance(exc, GitOutputTooLarge):
                raise
            raise GitTimeout(f"git {subcmd!r} exceeded {timeout}s timeout") from exc
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit (or any unexpected
            # error) must not leak the child or the reader tasks — mirror the
            # claude_code_runner BaseException-cleanup discipline (Epic-11 L7).
            await _cleanup_child()
            raise
        await proc.wait()
        return GitResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )

    async def assert_safe_repo_config(self) -> None:
        """Refuse a mutating op if the repo-local config defines an exec-capable driver.

        Story 15.4 P0 (security review): ``filter.<name>.clean/smudge/process`` runs
        on ``git add`` and ``merge.<name>.driver`` runs during ``git rebase``'s 3-way
        merge — each executes an arbitrary command chosen by a gitattributes driver
        NAME the attacker controls, so (unlike the fixed-key ``core.fsmonitor`` /
        ``diff.external`` vectors) no ``-c`` override can name-agnostically disable
        them. The mutating handlers call this BEFORE the mutating ``run_git`` and it
        raises :class:`RepoConfigUnsafe` when any such driver is present.

        Reading config (``git config --local --list``) does NOT execute any driver,
        so this probe is itself safe under the base ``_GIT_HARDENING`` shield. A
        repo with no local config (returncode != 0) is treated as clean — the real
        git error surfaces from the subsequent op.
        """
        result = await self.run_git(["config", "--local", "--list", "-z"])
        if result.returncode != 0:
            return
        for record in result.stdout.split("\x00"):
            if not record:
                continue
            key = record.split("\n", 1)[0]
            if _DANGEROUS_CONFIG_RE.match(key):
                raise RepoConfigUnsafe(
                    f"repo-local git config defines an exec-capable driver {key!r}; "
                    "mutating git operations are refused on this worktree (Story 15.4 P0)"
                )


def build_server(
    *,
    worktree_root: Path,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Story 15.2 SCAFFOLD: no git tools are registered (``TIER_MAP`` is empty).
    The clawhip-bridge audit-emission lifespan is wired so the first git tool
    added in 15.3 inherits the FR26 single-writer audit path for free.

    Args:
        worktree_root: Sandbox root for all git operations. Realpath-resolved
            inside ``GitExecutor``; the git tools (15.3) refuse any path that
            does not satisfy ``GitExecutor._contains``.
        clock: Injected clock for deterministic testing (reserved for the git
            tools' event emission in 15.3 / 15.4).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log, scanned by the
            Tier-3 ``approval_lookup`` (Story 15.4) for an ``approval.granted``
            matching the caller's ``task_id``. When None, the Tier-3 git tools
            (``git.push`` / ``git.rebase``) have NO approval source and every
            Tier-3 call is denied (test/no-approval default).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Constructed now so a malformed ``worktree_root`` fails fast at build time;
    # the git tools in 15.3 will close over this instance.
    _executor = GitExecutor(worktree_root)

    emitter_holder: EmitterHolder | None
    lifespan_fn = None
    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4).

            Mirror of task-registry's lifespan. Startup failure (clawhip-bridge
            subprocess refuses to launch) propagates here so the operator sees a
            hard error instead of silently degrading to no-emission mode. The
            bounded ``asyncio.wait_for(initialize(), _INIT_TIMEOUT)`` lives
            inside ``ClawhipBridgeClient.__aenter__`` (G-FN-3).
            """
            # Explicit RuntimeError, not assert — ``python -O`` strips asserts.
            if clawhip_bridge_command is None:
                raise RuntimeError("clawhip_bridge_command must be set when lifespan_fn is wired")
            if clawhip_bridge_args is None:
                raise RuntimeError("clawhip_bridge_args must be set when lifespan_fn is wired")
            if emitter_holder is None:
                raise RuntimeError("emitter_holder must be initialized when lifespan_fn is wired")
            client = ClawhipBridgeClient(
                command=clawhip_bridge_command,
                args=clawhip_bridge_args,
                caller_actor_kind=actor_kind,
                caller_actor_id=actor_id,
            )
            await client.__aenter__()
            emitter_holder.client = client
            log.info(
                "git_mcp_clawhip_client_ready",
                extra={"command": clawhip_bridge_command, "args": clawhip_bridge_args},
            )
            try:
                yield
            finally:
                # Null the holder ONLY AFTER ``__aexit__`` completes so any
                # in-flight handler sees a closed-pipe error (PD-1 fail-soft),
                # not a "lifespan not wired" RuntimeError. Nested try/finally so
                # the reference is ALWAYS dropped even if teardown raises.
                try:
                    await client.__aexit__(None, None, None)
                finally:
                    emitter_holder.client = None

        lifespan_fn = _lifespan
    else:
        emitter_holder = None

    mcp = FastMCP("git", lifespan=lifespan_fn) if lifespan_fn else FastMCP("git")

    # Story 15.3/15.4: register the read (Tier-1) + mutating (Tier-2/3) tools.
    # ``clock`` is threaded so the Tier-3 ``approval_lookup`` scans TODAY's JSONL
    # event log (``current_day_path(events_dir, clock.now())``). ``emitter_holder``
    # is forwarded so the audit ``capability.denied`` decorator wraps each handler
    # AND so the mutating tools emit ``git.committed`` / ``git.pushed`` through the
    # FR26 single-writer surface. ``registry_events_dir`` is the approval-source
    # base dir; when None, every Tier-3 call is denied (no approval source).
    from git_mcp.handlers.tools import make_approval_lookup, register_tools

    approval_lookup = (
        make_approval_lookup(registry_events_dir, clock)
        if registry_events_dir is not None
        else None
    )

    register_tools(
        mcp,
        _executor,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
        approval_lookup=approval_lookup,
    )

    return mcp


__all__ = ["GitExecutor", "build_server"]
