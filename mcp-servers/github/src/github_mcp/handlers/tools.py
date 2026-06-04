"""github-mcp MCP tool handlers (Epic 16 / Story 16.2 — scaffold).

Story 16.2 ships the SCAFFOLD: ``TIER_MAP`` is empty and ``register_tools``
registers NO tools yet. The bounded read tools (``github.issues.list`` /
``github.issues.get`` / ``github.prs.list`` / ``github.prs.get`` /
``github.reviews.list`` / ``github.reviews.get``, all Tier-1) land in Story 16.3;
the write tools (``github.issues.create`` / ``github.issues.update`` /
``github.prs.create`` / ``github.prs.update`` / ``github.reviews.request`` /
``github.comment.create``, all Tier-3 approval-gated) plus their ``github.*``
event emission land in Story 16.4.

Each future handler will (mirroring git-mcp):
  1. validate ``caller_trace_id`` FIRST (FR58 / Story 9.1 shape contract);
  2. gate on its tier via ``check_tier`` / ``check_tier_with_approval`` with
     ``TIER_MAP["github.<op>"]`` passed as a DIRECT argument (so
     ``scripts/check_tier_declarations.py`` recognises the tool as tiered);
  3. route the REST call through the github adapter (16.3) and return a
     deterministic structured result.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, and git-mcp (mcp-servers cannot
share code per Story 5.8's import-graph constraint); the same drift guard extends
to github-mcp once it registers tools (Story 16.5 contract test).
"""

from __future__ import annotations

import asyncio  # noqa: F401 — reserved for the Story 16.4 write-tool event-emission path
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier, check_tier_with_approval
from capabilities.emit import emit_capability_denied_on_deny
from events import current_day_path, read_log_lines  # noqa: IMP001 — packages/
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from github_mcp.adapters.clawhip_client import EmitterHolder
from github_mcp.adapters.github_rest import GitHubReadClient, ReadResult

if TYPE_CHECKING:
    from pathlib import Path as _Path

    from events.clock import Clock  # noqa: IMP001 — packages/
    from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

# Story 16.2 scaffold — EMPTY until the github tools land in 16.3 / 16.4. Keys are
# the canonical dotted ``github.<noun>.<verb>`` MCP tool ids (read tools ==
# Tier.ONE in 16.3; write tools == Tier.THREE in 16.4). Re-exported from
# ``server.py`` so the canonical TIER_MAP lives in one place (mirrors the git-mcp
# handlers/tools.py shape). Tools register against it later.
#
# Story 16.3: the six Tier-1 read tools. Write tools (all Tier-3 — every GitHub
# state change is a remote, externally-visible mutation; GitHub has NO Tier-2
# local-only analogue) land in Story 16.4.
TIER_MAP: dict[str, Tier] = {
    "github.issues.list": Tier.ONE,
    "github.issues.get": Tier.ONE,
    "github.prs.list": Tier.ONE,
    "github.prs.get": Tier.ONE,
    "github.reviews.list": Tier.ONE,
    "github.reviews.get": Tier.ONE,
}


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

    Story 16.4: the Tier-3 github write tools are gated by
    ``check_tier_with_approval(..., approval_lookup=...)`` — the lookup returns
    True only when a matching ``approval.granted`` event exists for the caller's
    *task_id*. Scans TODAY's JSONL event log for ``approval.granted`` events whose
    payload ``task_id`` matches; approvals are currently task-scoped only (the
    *action* parameter is accepted but unused — reserved for future wildcard
    matching).

    COPIED (not imported) from git-mcp's / clawhip-bridge's approval lookup — the
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


def _owner_repo_error(owner: str, repo: str) -> str | None:
    """Reject an owner/repo segment that is empty or contains a path separator.

    A ``/`` in an owner/repo would split the REST path into an unintended route
    (option/path injection on the GitHub API). Mirrors the worker adapter's
    ``_validate_owner_repo``; returns a human-readable reason or None.
    """
    if not owner or "/" in owner:
        return f"Invalid owner: {owner!r}"
    if not repo or "/" in repo:
        return f"Invalid repo: {repo!r}"
    return None


def register_tools(
    mcp: FastMCP,
    read_client_factory: Callable[[], GitHubReadClient],
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
) -> None:
    """Register the github read (Tier-1) + write (Tier-3) tools on *mcp*.

    Story 16.3 ships the six Tier-1 read tools (``github.issues.list`` /
    ``github.issues.get`` / ``github.prs.list`` / ``github.prs.get`` /
    ``github.reviews.list`` / ``github.reviews.get``); Story 16.4 adds the six
    Tier-3 write tools (approval-gated) plus their ``github.*`` event emission.

    *read_client_factory* returns a fresh ``GitHubReadClient`` (used as a per-call
    ``async with`` session) authenticated with the SCOPED token (closed over by the
    factory in ``build_server``); the broad ``GITHUB_TOKEN`` is NEVER used. The
    scoped token is the client's bearer only — it is never returned in a tool result
    (that would disclose the credential through the tool boundary).

    Mirrors git-mcp's ``register_tools``: when *emitter_holder* is wired, each
    tier-gated handler is wrapped with ``emit_capability_denied_on_deny`` so a
    ``CapabilityDenied`` from ``check_tier`` / ``check_tier_with_approval`` emits a
    ``capability.denied`` audit envelope via clawhip-bridge before re-raising.

    *approval_lookup* is the async ``(task_id, action) -> bool`` callable threaded
    into ``check_tier_with_approval`` for the Tier-3 tools; when None the Tier-3
    tools deny every call (no approval source — test/no-approval default).
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

    def _result(read: ReadResult) -> dict[str, object]:
        """Map a ``ReadResult`` into the structured tool payload."""
        if read.ok:
            return {"ok": True, "status": read.status, "data": read.data}
        return {"ok": False, "status": read.status, "error": read.error}

    # ------------------------------------------------------------------
    # Tool: github.issues.list
    # ------------------------------------------------------------------

    @mcp.tool(name="github.issues.list")
    @_maybe_wrap("github.issues.list")
    async def github_issues_list(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        state: str = "open",
    ) -> dict[str, object]:
        """List issues in a repo (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            state: Issue state filter (``open``/``closed``/``all``).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.issues.list", _caller(), TIER_MAP["github.issues.list"])
        if owner is None or repo is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.list_issues(owner, repo, state=state))

    # ------------------------------------------------------------------
    # Tool: github.issues.get
    # ------------------------------------------------------------------

    @mcp.tool(name="github.issues.get")
    @_maybe_wrap("github.issues.get")
    async def github_issues_get(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        number: int | None = None,
    ) -> dict[str, object]:
        """Get a single issue (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            number: Issue number.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.issues.get", _caller(), TIER_MAP["github.issues.get"])
        if owner is None or repo is None or number is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.get_issue(owner, repo, number))

    # ------------------------------------------------------------------
    # Tool: github.prs.list
    # ------------------------------------------------------------------

    @mcp.tool(name="github.prs.list")
    @_maybe_wrap("github.prs.list")
    async def github_prs_list(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        state: str = "open",
    ) -> dict[str, object]:
        """List pull requests in a repo (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            state: PR state filter (``open``/``closed``/``all``).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.prs.list", _caller(), TIER_MAP["github.prs.list"])
        if owner is None or repo is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.list_pull_requests(owner, repo, state=state))

    # ------------------------------------------------------------------
    # Tool: github.prs.get
    # ------------------------------------------------------------------

    @mcp.tool(name="github.prs.get")
    @_maybe_wrap("github.prs.get")
    async def github_prs_get(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        number: int | None = None,
    ) -> dict[str, object]:
        """Get a single pull request (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            number: PR number.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.prs.get", _caller(), TIER_MAP["github.prs.get"])
        if owner is None or repo is None or number is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.get_pull_request(owner, repo, number))

    # ------------------------------------------------------------------
    # Tool: github.reviews.list
    # ------------------------------------------------------------------

    @mcp.tool(name="github.reviews.list")
    @_maybe_wrap("github.reviews.list")
    async def github_reviews_list(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        pull_number: int | None = None,
    ) -> dict[str, object]:
        """List reviews on a pull request (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            pull_number: PR number whose reviews are listed.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.reviews.list", _caller(), TIER_MAP["github.reviews.list"])
        if owner is None or repo is None or pull_number is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.list_reviews(owner, repo, pull_number))

    # ------------------------------------------------------------------
    # Tool: github.reviews.get
    # ------------------------------------------------------------------

    @mcp.tool(name="github.reviews.get")
    @_maybe_wrap("github.reviews.get")
    async def github_reviews_get(
        *,
        caller_trace_id: str,
        owner: str | None = None,
        repo: str | None = None,
        pull_number: int | None = None,
        review_id: int | None = None,
    ) -> dict[str, object]:
        """Get a single review on a pull request (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            owner: Repository owner. Omitted → auth-probe result (no REST call).
            repo: Repository name. Omitted → auth-probe result (no REST call).
            pull_number: PR number the review belongs to.
            review_id: Review id to fetch.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("github.reviews.get", _caller(), TIER_MAP["github.reviews.get"])
        if owner is None or repo is None or pull_number is None or review_id is None:
            return {"ok": False, "status": 0, "error": "owner and repo are required"}
        err = _owner_repo_error(owner, repo)
        if err is not None:
            return {"ok": False, "status": 0, "error": err}
        async with read_client_factory() as gh:
            return _result(await gh.get_review(owner, repo, pull_number, review_id))

    # Reference the handlers so linters see them as used (FastMCP holds the real
    # references via the decorator registration side-effect). ``check_tier_with_approval``
    # is referenced too — it is wired for the Story 16.4 Tier-3 write tools.
    _ = (
        github_issues_list,
        github_issues_get,
        github_prs_list,
        github_prs_get,
        github_reviews_list,
        github_reviews_get,
        check_tier_with_approval,
    )
