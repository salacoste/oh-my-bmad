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
TIER_MAP: dict[str, Tier] = {}


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


def register_tools(
    mcp: FastMCP,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
) -> None:
    """Register the github read (Tier-1) + write (Tier-3) tools on *mcp*.

    Story 16.2 SCAFFOLD: registers NO tools yet (``TIER_MAP`` is empty). Story
    16.3 ships the six Tier-1 read tools; Story 16.4 adds the six Tier-3 write
    tools (approval-gated) plus their ``github.*`` event emission.

    Mirrors git-mcp's ``register_tools``: when *emitter_holder* is wired, each
    tier-gated handler will be wrapped with ``emit_capability_denied_on_deny`` so
    a ``CapabilityDenied`` from ``check_tier`` / ``check_tier_with_approval`` emits
    a ``capability.denied`` audit envelope via clawhip-bridge before re-raising.
    The closures below (``_maybe_wrap`` / ``_caller``) are wired now so 16.3 / 16.4
    add tools by registration only — no plumbing churn.

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

    # Story 16.3 / 16.4 register the github tools here, each gating on its tier via
    # ``check_tier("github.<op>", _caller(), TIER_MAP["github.<op>"])`` (read) or
    # ``check_tier_with_approval(..., approval_lookup=approval_lookup)`` (write).
    # Reference the scaffolded helpers + gates so linters see them as used until
    # the tools land (FastMCP holds the real references via decorator side-effects).
    _ = (_maybe_wrap, _caller, check_tier, check_tier_with_approval)
