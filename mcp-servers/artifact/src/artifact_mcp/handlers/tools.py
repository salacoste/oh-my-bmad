"""artifact-mcp MCP tool handlers (Epic 19 / Stories 19.3 + 19.4).

Stories 19.1 / 19.2 ship the SCAFFOLD: ``TIER_MAP`` is empty and
``register_tools`` registers NO tools yet. The four artifact tools land later:

  * Story 19.3 — ``artifact.get`` (Tier-1 bounded read), ``artifact.list``
    (Tier-1 bounded read), ``artifact.put`` (Tier-2 store mutation — gated by
    ``check_tier``, NO approval gate), and ``artifact.delete`` (Tier-3 —
    approval-gated via ``check_tier_with_approval`` + the *approval_lookup*
    threaded into ``register_tools`` here).
  * Story 19.4 — the ``artifact.*`` event emission (``artifact.put`` →
    ``artifact.stored``; the retention sweep → ``artifact.deleted`` per evicted
    object).

Each tool, once it lands, will register with an EXPLICIT dotted MCP name
(``@mcp.tool(name="artifact.get")``) so ``list_tools()`` surfaces the canonical
``artifact.<op>`` id the 19.1 ATDD contracts assert (FastMCP would otherwise
default the id to the Python function name ``artifact_get`` — which is ≠
``artifact.get``). Every handler will validate ``caller_trace_id`` FIRST (FR58 /
Story 9.1 shape contract), gate on its tier via ``check_tier`` / (for delete)
``check_tier_with_approval``, and route through the injected
:class:`~artifact_mcp.store.ArtifactStore`.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, git-mcp, and memory-mcp
(mcp-servers cannot share code per Story 5.8's import-graph constraint); the same
drift guard extends to artifact-mcp now that it ships the helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from artifact_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server.fastmcp import FastMCP

    from artifact_mcp.store import ArtifactStore

# Stories 19.1-19.2 scaffold — empty until the artifact tools land in 19.3 / 19.4.
# Keys will be the canonical dotted ``artifact.<op>`` MCP tool ids:
#   artifact.get  == Tier.ONE   (bounded read; Story 19.3)
#   artifact.list == Tier.ONE   (bounded read; Story 19.3)
#   artifact.put  == Tier.TWO   (store mutation; ``check_tier`` — NO approval gate; 19.3)
#   artifact.delete == Tier.THREE (approval-gated via ``check_tier_with_approval``; 19.3)
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


def register_tools(
    mcp: FastMCP,
    store: ArtifactStore,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
) -> None:
    """Register the artifact tools on *mcp* (no tools yet — 19.1/19.2 scaffold).

    Stories 19.1 / 19.2 register ZERO tools (``TIER_MAP`` is empty). The signature
    already threads everything the 19.3 / 19.4 tools need so the factory wiring is
    stable across the epic:

      * *store* — the live :class:`~artifact_mcp.store.ArtifactStore` the
        ``artifact.get`` / ``list`` / ``put`` / ``delete`` handlers will close over.
      * *emitter_holder* — the clawhip-bridge audit-emission holder; when wired,
        each tier-gated handler will be wrapped with
        ``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` from the tier
        gate emits a ``capability.denied`` audit envelope before re-raising.
      * *approval_lookup* — the async ``(task_id, action) -> bool`` callable the
        Tier-3 ``artifact.delete`` handler (Story 19.3) will pass to
        ``check_tier_with_approval``; an absent ``approval.granted`` for the task
        denies the deletion.

    Args:
        mcp: The FastMCP server to register tools on.
        store: The live artifact store the tool handlers will operate against.
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        emitter_holder: Optional clawhip-bridge audit-emission holder; when None,
            audit emission is disabled (test mode).
        approval_lookup: Optional async approval lookup for the Tier-3
            ``artifact.delete`` tool (Story 19.3). When None, no Tier-3 gating
            source is wired (test mode / scaffold).
    """
    # Stories 19.1-19.2 scaffold: no tools registered yet (``TIER_MAP`` empty).
    # The store / emitter_holder / approval_lookup are threaded so the artifact
    # tools (19.3 / 19.4) close over a live store + the FR26 single-writer audit
    # surface + the Tier-3 approval source without re-plumbing the factory.
    # Reference the params so linters see them as intentionally retained.
    _ = (mcp, store, actor_kind, actor_id, emitter_holder, approval_lookup)


__all__ = ["TIER_MAP", "register_tools", "validate_caller_trace_id"]
