"""git-mcp MCP tool handlers (Epic 15 / Story 15.3 — Tier-1 read tools).

Story 15.3 lands the four bounded read tools — ``git.status``, ``git.diff``,
``git.log``, ``git.branch`` — all at ``Tier.ONE``. Each is registered with an
EXPLICIT dotted MCP name (``@mcp.tool(name="git.status")``) so ``list_tools()``
surfaces the canonical ``git.<op>`` id the 15.1 ATDD contracts assert (FastMCP
would otherwise default the id to the Python function name ``git_status`` —
which is ≠ ``git.status``). Every handler:

  1. validates ``caller_trace_id`` FIRST (FR58 / Story 9.1 shape contract);
  2. gates on its tier via ``check_tier("git.<op>", CallerContext(...),
     TIER_MAP["git.<op>"])`` — the ``TIER_MAP[...]`` subscript is passed as a
     DIRECT argument to ``check_tier`` so ``scripts/check_tier_declarations.py``
     recognises the tool as tiered;
  3. containment-checks any path arg against the worktree root (escaping paths
     raise ``ValueError``); and
  4. routes the actual ``git`` invocation through ``GitExecutor.run_git`` (the
     single sandboxed spawn-site) and returns a deterministic, injection-safe
     structured result.

Reads emit NO events (no ``clock`` is threaded into this story); the mutating
tools' ``git.*`` event emission lands in Story 15.4.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, and session-registry (mcp-servers cannot share
code per Story 5.8's import-graph constraint); the same drift guard extends to
git-mcp now that it registers tools.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier, check_tier_with_approval
from capabilities.emit import emit_capability_denied_on_deny
from events import current_day_path, read_log_lines  # noqa: IMP001 — packages/
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from git_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from pathlib import Path as _Path

    from events.clock import Clock  # noqa: IMP001 — packages/
    from mcp.server.fastmcp import FastMCP

    from git_mcp.server import GitExecutor

log = logging.getLogger(__name__)

# Story 15.3: the four Tier-1 read tools. Mutating tools (git.add/commit ==
# Tier.TWO, git.push/rebase == Tier.THREE) land in Story 15.4. Keys are the
# canonical dotted ``git.<op>`` MCP tool ids (matched to the ``@mcp.tool(name=…)``
# registration below).
TIER_MAP: dict[str, Tier] = {
    "git.status": Tier.ONE,
    "git.diff": Tier.ONE,
    "git.log": Tier.ONE,
    "git.branch": Tier.ONE,
    # Story 15.4 — mutating tools. add/commit are Tier-2 (repo mutation); push
    # and history-rewrite (rebase) are Tier-3 (approval-gated via
    # check_tier_with_approval + approval_lookup).
    "git.add": Tier.TWO,
    "git.commit": Tier.TWO,
    "git.push": Tier.THREE,
    "git.rebase": Tier.THREE,
}

# D5: default + hard cap on ``git.log`` history depth so an unbounded request
# cannot make git stream the entire repo history through the request path.
_LOG_DEFAULT_LIMIT: int = 50
_LOG_MAX_LIMIT: int = 1000


def validate_caller_trace_id(caller_trace_id: str) -> None:
    """Reject invalid ``caller_trace_id`` per Story 9.1 contract.

    Public helper used by every ``@mcp.tool()`` handler in this server to
    validate the operator-originating correlation ID supplied as an explicit
    Pydantic-validated input (Story 9.5 / FR58 MCP). Validation uses
    :func:`events.envelope.is_valid_trace_id` so the shape contract (UUIDv7
    bare form OR ``tg:<update_id>``) stays in one place — Story 9.4 pass-2 S1
    lesson (shape-validation, not just type-check, avoids whitespace/CRLF
    injection).

    Public name (no leading underscore) per Story 9.5 pass-1 review T4:
    these helpers are part of the public tool-validation contract documented
    in the Story 9.5 spec and exercised by ``tests/contract/`` — the contract
    test for byte-identical body sync (T2) requires a public symbol.

    NOTE: Duplicated byte-identically in ``clawhip-bridge`` and
    ``session-registry``. mcp-servers cannot share code per Story 5.8's
    import-graph constraint; the helper body MUST stay in sync across all
    three servers. Drift is guarded by
    ``tests/contract/test_mcp_tool_schemas.py::test_validate_caller_trace_id_byte_identical_across_servers``
    (Story 9.5 pass-1 T2).

    Raises:
        ValueError: if ``caller_trace_id`` doesn't match the Story 9.1
            contract (UUIDv7 bare form OR ``tg:<digits>``).
    """
    if not is_valid_trace_id(caller_trace_id):
        raise ValueError(
            f"caller_trace_id must match Story 9.1 contract "
            f"(UUIDv7 or tg:<update_id>); got {caller_trace_id!r}"
        )


def make_approval_lookup(
    base_dir: _Path,
    clock: Clock,
) -> Callable[[str, str], Awaitable[bool]]:
    """Return an async ``(task_id, action) -> bool`` approval lookup for Tier-3 gating.

    Story 15.4: the Tier-3 git tools (``git.push`` / ``git.rebase``) are gated by
    ``check_tier_with_approval(..., approval_lookup=...)`` — the lookup returns
    True only when a matching ``approval.granted`` event exists for the caller's
    *task_id*. Scans TODAY's JSONL event log for ``approval.granted`` events whose
    payload ``task_id`` matches; approvals are currently task-scoped only (the
    *action* parameter is accepted but unused — reserved for future wildcard
    matching).

    COPIED (not imported) from clawhip-bridge's ``_make_approval_lookup`` — the
    Story 5.8 import-graph constraint forbids cross-importing between mcp-servers,
    so the lookup body is duplicated here (the same discipline that duplicates
    ``validate_caller_trace_id``). The worker / clawhip-bridge precedents carry
    the same Phase-1 limitation:

    Phase-1 limitation: only scans today's JSONL log file. Cross-day approvals
    (granted yesterday, used today) are not covered. Acceptable for the current
    scale; addressed when the materialized Event table becomes the primary
    approval source.
    """

    async def _lookup(task_id: str, action: str) -> bool:  # noqa: ARG001 — action reserved for future wildcard matching
        # NOTE: O(n) linear scan of today's JSONL per check — acceptable for
        # current scale, but cache or index if event volume grows.
        path = current_day_path(base_dir, clock.now())
        try:
            for envelope in read_log_lines(path):
                payload = envelope.payload
                if (
                    envelope.type == "approval.granted"
                    and isinstance(payload, dict)
                    and payload.get("task_id") == task_id
                ):
                    return True
        except FileNotFoundError:
            pass
        return False

    return _lookup


def _make_actor_id_extractor(actor_id: str) -> Callable[..., str]:
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``.

    The configured ``actor_id`` is the calling actor's identity for the
    duration of this server process (set at startup). Tool kwargs do not carry
    actor identity — it comes from the server's launch config — so the
    extractor returns the closed-over value irrespective of args/kwargs.
    """

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


def _contained_relpath(executor: GitExecutor, path: str) -> str:
    """Resolve *path* against the worktree root, refuse escapes, return a relpath.

    Containment (P0/security): an absolute path is taken as-is; a relative path
    is joined to the root. The candidate is validated with the shipped
    ``GitExecutor._contains`` (realpath containment — collapses ``..`` and
    symlinks). An escaping path raises ``ValueError`` BEFORE any git spawn. The
    returned value is a path RELATIVE to the root, which the caller places after
    a ``--`` separator in the git argv (option-injection defense).
    """
    root = executor.worktree_root
    candidate = Path(path) if os.path.isabs(path) else root / path
    if not executor._contains(candidate):
        raise ValueError(f"path {path!r} escapes worktree root")
    resolved = Path(os.path.realpath(candidate))
    return os.path.relpath(resolved, root)


def _parse_status(stdout: str) -> dict[str, object]:
    """Parse ``git status --porcelain=v1 -z --branch`` into a structured payload.

    The ``-z`` records are NUL-separated; the first record is the ``## <branch>``
    header. Each entry is two status chars (``X``/``Y``) + a space + the path.
    Rename entries (``R``) carry a second NUL-separated path (origin) which we
    consume but do not surface (path is the destination).
    """
    records = [r for r in stdout.split("\x00") if r != ""]
    branch = ""
    entries: list[dict[str, str]] = []
    it = iter(records)
    for rec in it:
        if rec.startswith("## "):
            # ``## main...origin/main [ahead 1]`` → take the local branch name.
            header = rec[3:]
            branch = header.split("...", 1)[0].split(" ", 1)[0]
            continue
        if len(rec) < 3:
            # Defensive: a well-formed porcelain=v1 entry record is >=3 chars
            # (XY + space + path). Skip a truncated/garbled record rather than
            # IndexError on rec[0]/rec[1] (code-review P2).
            continue
        x = rec[0]
        y = rec[1]
        path = rec[3:]
        # Rename/copy entries emit the origin path as the next NUL record.
        if x in ("R", "C"):
            next(it, None)
        entries.append({"x": x, "y": y, "path": path})
    return {
        "ok": True,
        "branch": branch,
        "entries": entries,
        "clean": len(entries) == 0,
    }


def _parse_numstat(stdout: str) -> dict[str, object]:
    """Parse ``git diff --numstat -z`` into a structured payload.

    Each record is ``<added>\\t<deleted>\\t<path>``. ``added``/``deleted`` are
    ``-`` for binary files (surfaced as ``None``). With ``-z`` the records are
    NUL-separated and the path is NOT tab-terminated.
    """
    files: list[dict[str, object]] = []
    for rec in stdout.split("\x00"):
        if rec == "":
            continue
        parts = rec.split("\t", 2)
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        added = None if added_s == "-" else int(added_s)
        deleted = None if deleted_s == "-" else int(deleted_s)
        files.append({"added": added, "deleted": deleted, "path": path})
    return {"ok": True, "files": files}


def _parse_log(stdout: str) -> dict[str, object]:
    """Parse the custom ``git log`` format into a structured commit list.

    Records are ``\\x1e``-separated; fields within a record are ``\\x1f``-
    separated as ``hash``, ``author``, ``date`` (ISO-8601 ``%aI``), ``subject``.
    """
    commits: list[dict[str, str]] = []
    for rec in stdout.split("\x1e"):
        rec = rec.strip("\n")
        if rec == "":
            continue
        fields = rec.split("\x1f")
        if len(fields) != 4:
            continue
        commits.append(
            {
                "hash": fields[0],
                "author": fields[1],
                "date": fields[2],
                "subject": fields[3],
            }
        )
    return {"ok": True, "commits": commits}


def _parse_branch(stdout: str) -> dict[str, object]:
    """Parse ``git branch --format=%(refname:short)%00%(HEAD)`` into a payload.

    Each line is ``<name>\\x00<head-marker>`` where ``<head-marker>`` is ``*``
    for the current branch (else empty).
    """
    branches: list[str] = []
    current: str | None = None
    for line in stdout.splitlines():
        if line == "":
            continue
        name, _, marker = line.partition("\x00")
        branches.append(name)
        if marker == "*":
            current = name
    return {"ok": True, "branches": branches, "current": current}


def _git_error(result_stderr: str, returncode: int) -> dict[str, object]:
    """Structured error for a non-zero git exit (first stderr line only).

    Only the first line of stderr is surfaced and it is never logged here
    without structured ``extra=`` fields (CRLF-injection defense).
    """
    first_line = result_stderr.splitlines()[0] if result_stderr else ""
    return {"ok": False, "error": first_line, "returncode": returncode}


async def _commit_metadata(executor: GitExecutor) -> tuple[str, str, int]:
    """Return ``(head_sha, branch, files_changed)`` for the current HEAD.

    ``head_sha`` is the full 40-char HEAD sha (``rev-parse HEAD``); ``branch`` is
    the current branch short name (``rev-parse --abbrev-ref HEAD``); ``files_changed``
    is the number of files touched by the HEAD commit (``diff-tree`` numstat, or
    ``-z``-NUL-separated lines for the root commit). Each lookup is a bounded read
    through the single sandboxed ``run_git`` spawn-site. A failed lookup yields a
    safe default (empty string / 0) so a metadata hiccup never masks the already-
    successful mutation; the typed payload models still reject a malformed sha at
    emit time (caught by the best-effort emit guard).
    """
    sha_res = await executor.run_git(["rev-parse", "HEAD"])
    sha = sha_res.stdout.strip() if sha_res.returncode == 0 else ""
    branch_res = await executor.run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
    # ``diff-tree --no-commit-id --numstat -r -z HEAD`` lists one NUL-terminated
    # numstat record per changed file; for the root commit (no parent) it lists
    # all files added. Count non-empty records.
    files_res = await executor.run_git(
        ["diff-tree", "--no-commit-id", "--numstat", "-r", "-z", "HEAD"]
    )
    files_changed = 0
    if files_res.returncode == 0:
        files_changed = len([r for r in files_res.stdout.split("\x00") if r != ""])
    return sha, branch, files_changed


def register_tools(
    mcp: FastMCP,
    executor: GitExecutor,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
) -> None:
    """Register the git read (Tier-1) + mutating (Tier-2/3) tools on *mcp*.

    Story 15.3 ships the four Tier-1 read tools; Story 15.4 adds the mutating
    tools — ``git.add`` / ``git.commit`` (Tier-2) and ``git.push`` /
    ``git.rebase`` (Tier-3, approval-gated).

    Mirrors task-registry's ``register_tools``: when *emitter_holder* is wired,
    each tier-gated handler is wrapped with ``emit_capability_denied_on_deny``
    so a ``CapabilityDenied`` from ``check_tier`` / ``check_tier_with_approval``
    emits a ``capability.denied`` audit envelope via clawhip-bridge before
    re-raising (the Tier-3 negative-test path). On success the mutating handlers
    also emit a typed ``git.committed`` / ``git.pushed`` event through the same
    single-writer surface (best-effort, guarded on *emitter_holder*).

    *approval_lookup* is the async ``(task_id, action) -> bool`` callable
    threaded into ``check_tier_with_approval`` for the Tier-3 tools; when None
    the Tier-3 tools deny every call (no approval source — test/no-approval
    default).
    """
    get_actor_id = _make_actor_id_extractor(actor_id)

    def _maybe_wrap(
        tool_name: str,
    ) -> Callable[
        [Callable[..., Awaitable[dict[str, object]]]],
        Callable[..., Awaitable[dict[str, object]]],
    ]:
        """Apply the audit-emission decorator iff an emitter holder is wired."""
        if emitter_holder is None:
            return lambda fn: fn
        return emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter_holder.emit_event,
            attempted_action=tool_name,
            get_actor_id=get_actor_id,
        )

    def _caller(task_id: str | None = None) -> CallerContext:
        return CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=task_id)

    async def _emit_git_event(
        event_type: str,
        payload: dict[str, object],
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Best-effort emit a ``git.*`` event; return the result-surfaced descriptor.

        FR26 single-writer: the actual emission routes through *emitter_holder*
        (clawhip-bridge MCP RPC), NEVER a direct event-log write. The emit is
        BEST-EFFORT — guarded on *emitter_holder* and wrapped so an emission
        failure (audit-off, closed pipe) is logged and swallowed, never failing
        the already-successful git mutation.

        The returned ``{"type": ..., "trace_id": caller_trace_id}`` descriptor is
        built UNCONDITIONALLY (independent of whether the emit fired) and surfaced
        by the caller as ``result["event"]`` so the ATDD contract can read the
        git.* event type + inbound trace_id even when audit emission is disabled.
        """
        descriptor: dict[str, object] = {"type": event_type, "trace_id": caller_trace_id}
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    event_type, payload, caller_trace_id=caller_trace_id
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):  # noqa: TRY302 — cancellation MUST propagate (PP1 contract); re-raise control-flow
                raise
            except BaseException:  # noqa: BLE001 — best-effort audit; never fail the git op
                log.exception(
                    "git_event_emission_failed",
                    extra={"event_type": event_type},
                )
        return descriptor

    # ------------------------------------------------------------------
    # Tool: git.status
    # ------------------------------------------------------------------

    @mcp.tool(name="git.status")
    @_maybe_wrap("git.status")
    async def git_status(
        *,
        caller_trace_id: str,
        path: str | None = None,
    ) -> dict[str, object]:
        """Return the worktree status (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            path: Optional path filter (D2); refused if it escapes the worktree
                root.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.status", _caller(), TIER_MAP["git.status"])
        argv = ["status", "--porcelain=v1", "-z", "--branch"]
        if path is not None:
            argv += ["--", _contained_relpath(executor, path)]
        result = await executor.run_git(argv)
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return _parse_status(result.stdout)

    # ------------------------------------------------------------------
    # Tool: git.diff
    # ------------------------------------------------------------------

    @mcp.tool(name="git.diff")
    @_maybe_wrap("git.diff")
    async def git_diff(
        *,
        caller_trace_id: str,
        path: str | None = None,
    ) -> dict[str, object]:
        """Return the worktree diff as numstat (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            path: Optional path filter (D2); refused if it escapes the worktree
                root.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.diff", _caller(), TIER_MAP["git.diff"])
        argv = ["diff", "--numstat", "-z"]
        if path is not None:
            argv += ["--", _contained_relpath(executor, path)]
        result = await executor.run_git(argv)
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return _parse_numstat(result.stdout)

    # ------------------------------------------------------------------
    # Tool: git.log
    # ------------------------------------------------------------------

    @mcp.tool(name="git.log")
    @_maybe_wrap("git.log")
    async def git_log(
        *,
        caller_trace_id: str,
        path: str | None = None,
        limit: int = _LOG_DEFAULT_LIMIT,
    ) -> dict[str, object]:
        """Return recent commits (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            path: Optional path filter (D2); refused if it escapes the worktree
                root.
            limit: Max commits to return (D5 — defaults to 50, capped at 1000).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.log", _caller(), TIER_MAP["git.log"])
        capped = max(1, min(limit, _LOG_MAX_LIMIT))
        argv = ["log", "--format=%H%x1f%an%x1f%aI%x1f%s%x1e", "-n", str(capped)]
        if path is not None:
            argv += ["--", _contained_relpath(executor, path)]
        result = await executor.run_git(argv)
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return _parse_log(result.stdout)

    # ------------------------------------------------------------------
    # Tool: git.branch
    # ------------------------------------------------------------------

    @mcp.tool(name="git.branch")
    @_maybe_wrap("git.branch")
    async def git_branch(
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """List branches and the current HEAD (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.branch", _caller(), TIER_MAP["git.branch"])
        argv = ["branch", "--format=%(refname:short)%00%(HEAD)"]
        result = await executor.run_git(argv)
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return _parse_branch(result.stdout)

    # ------------------------------------------------------------------
    # Tool: git.add (Tier-2 repo mutation)
    # ------------------------------------------------------------------

    @mcp.tool(name="git.add")
    @_maybe_wrap("git.add")
    async def git_add(
        *,
        caller_trace_id: str,
        path: str,
    ) -> dict[str, object]:
        """Stage *path* for the next commit (Tier-2 repo mutation).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            path: Path to stage; refused if it escapes the worktree root. Placed
                after a ``--`` separator (option-injection defense).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.add", _caller(), TIER_MAP["git.add"])
        await executor.assert_safe_repo_config()  # P0: refuse filter.*.clean RCE on add
        relpath = _contained_relpath(executor, path)
        result = await executor.run_git(["add", "--", relpath])
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return {"ok": True, "staged": relpath}

    # ------------------------------------------------------------------
    # Tool: git.commit (Tier-2 repo mutation; emits git.committed)
    # ------------------------------------------------------------------

    @mcp.tool(name="git.commit")
    @_maybe_wrap("git.commit")
    async def git_commit(
        *,
        caller_trace_id: str,
        message: str,
    ) -> dict[str, object]:
        """Commit the staged index (Tier-2 repo mutation; emits ``git.committed``).

        DECISION-2: the committer/author identity is injected per-invocation via
        ``-c user.name`` / ``-c user.email`` (no env, no ``~/.gitconfig``) so the
        bot identity is hermetic and deterministic. The identity ``-c`` flags MUST
        precede the ``commit`` subcommand. ``--no-verify`` is belt-and-suspenders
        on top of the base ``core.hooksPath=/dev/null`` shield.

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            message: The commit message (``-m`` body).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("git.commit", _caller(), TIER_MAP["git.commit"])
        await executor.assert_safe_repo_config()  # P0: refuse exec-driver config before mutation
        bot_name = "git-mcp"
        bot_email = f"git-mcp+{actor_id}@oh-my-bmad.local"
        # Identity ``-c`` flags precede the ``commit`` subcommand (git requires
        # config overrides before the subcommand). ``--no-verify`` skips hooks.
        result = await executor.run_git(
            [
                "-c",
                f"user.name={bot_name}",
                "-c",
                f"user.email={bot_email}",
                "commit",
                "--no-verify",
                "-m",
                message,
            ]
        )
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        sha, branch, files_changed = await _commit_metadata(executor)
        payload: dict[str, object] = {
            "sha": sha,
            "branch": branch,
            "message_summary": message.splitlines()[0] if message else message,
            "files_changed": files_changed,
        }
        event = await _emit_git_event("git.committed", payload, caller_trace_id=caller_trace_id)
        return {"ok": True, "sha": sha, "branch": branch, "event": event}

    # ------------------------------------------------------------------
    # Tool: git.push (Tier-3 — approval-gated; emits git.pushed)
    # ------------------------------------------------------------------

    @mcp.tool(name="git.push")
    @_maybe_wrap("git.push")
    async def git_push(
        *,
        caller_trace_id: str,
        task_id: str,
        remote: str = "origin",
    ) -> dict[str, object]:
        """Push the current branch to *remote* (Tier-3; approval-gated).

        DECISION-1(A): the push target is a LOCAL bare remote (no network, no
        credentials). The Tier-3 gate ``check_tier_with_approval`` denies the call
        unless a matching ``approval.granted`` exists for *task_id*; on denial the
        ``@_maybe_wrap`` decorator emits ``capability.denied`` before re-raising.
        The push path carries ``_GIT_HARDENING_PUSH`` (``core.sshCommand=``) so a
        repo-local ssh-command vector cannot execute.

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            task_id: Task whose ``approval.granted`` authorizes this Tier-3 push.
            remote: Configured remote name (defaults to ``origin``).
        """
        from git_mcp.server import _GIT_HARDENING_PUSH

        validate_caller_trace_id(caller_trace_id)
        await check_tier_with_approval(
            "git.push",
            _caller(task_id=task_id),
            TIER_MAP["git.push"],
            approval_lookup=approval_lookup,
        )
        await executor.assert_safe_repo_config()  # P0: defense-in-depth before Tier-3 mutation
        sha, branch, _ = await _commit_metadata(executor)
        result = await executor.run_git(
            ["push", "--no-verify", remote, branch],
            extra_hardening=_GIT_HARDENING_PUSH,
        )
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        payload: dict[str, object] = {"remote": remote, "refspec": branch, "sha": sha}
        event = await _emit_git_event("git.pushed", payload, caller_trace_id=caller_trace_id)
        return {"ok": True, "remote": remote, "refspec": branch, "sha": sha, "event": event}

    # ------------------------------------------------------------------
    # Tool: git.rebase (Tier-3 — history-rewrite; approval-gated)
    # ------------------------------------------------------------------

    @mcp.tool(name="git.rebase")
    @_maybe_wrap("git.rebase")
    async def git_rebase(
        *,
        caller_trace_id: str,
        task_id: str,
        upstream: str,
    ) -> dict[str, object]:
        """Rebase the current branch onto *upstream* (Tier-3 history-rewrite).

        Approval-gated identically to ``git.push`` — a history-rewrite is a
        high-risk Tier-3 action and is denied without a matching
        ``approval.granted`` for *task_id*. ``--no-verify`` skips pre-rebase
        hooks (belt-and-suspenders on the base ``core.hooksPath`` shield).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            task_id: Task whose ``approval.granted`` authorizes this Tier-3 rebase.
            upstream: The upstream ref to rebase onto (e.g. ``main``). Validated
                as a contained ref name (no path-escape / option injection).
        """
        validate_caller_trace_id(caller_trace_id)
        await check_tier_with_approval(
            "git.rebase",
            _caller(task_id=task_id),
            TIER_MAP["git.rebase"],
            approval_lookup=approval_lookup,
        )
        await executor.assert_safe_repo_config()  # P0: refuse merge.*.driver RCE on rebase
        # ``--`` terminates option parsing so an ``upstream`` starting with ``-``
        # cannot be reparsed as a flag (option-injection defense).
        result = await executor.run_git(["rebase", "--no-verify", "--", upstream])
        if result.returncode != 0:
            return _git_error(result.stderr, result.returncode)
        return {"ok": True, "upstream": upstream}

    # Reference the handlers so linters see them as used (FastMCP holds the real
    # references via the decorator registration side-effect).
    _ = (
        git_status,
        git_diff,
        git_log,
        git_branch,
        git_add,
        git_commit,
        git_push,
        git_rebase,
    )
