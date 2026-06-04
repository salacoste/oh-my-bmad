"""memory-mcp MCP tool handlers (Epic 18 / Stories 18.1-18.2 scaffold).

Stories 18.1 / 18.2 are the SCAFFOLD only — ``TIER_MAP`` is empty and NO
``@mcp.tool()`` handlers are registered. The memory tools (``memory.read`` /
``memory.search`` at Tier-1, ``memory.write`` at Tier-2) land in Stories
18.3 / 18.4, where each entry in ``TIER_MAP`` gates a bounded operation routed
through the :class:`~memory_mcp.store.MemoryStore`.

The :class:`~memory_mcp.store.MemoryStore` is threaded into ``register_tools``
now (18.2) so the first tool added in 18.3 closes over the live store without
re-plumbing the factory.

The ``validate_caller_trace_id`` helper is shipped now so the first tool added in
18.3 inherits the FR58 caller-trace-id contract without re-deriving it. Its body
is duplicated byte-identically across clawhip-bridge, task-registry,
session-registry, and git-mcp (mcp-servers cannot share code per Story 5.8's
import-graph constraint); the same drift guard extends to memory-mcp once it
registers tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from memory_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from memory_mcp.store import MemoryStore

log = logging.getLogger(__name__)

# Stories 18.1-18.2 scaffold — empty until the memory tools land in 18.3 / 18.4.
# Each future operation registers its required capability tier here (mirroring
# the git-mcp / task-registry ``TIER_MAP`` shape):
#   memory.read   == Tier.ONE  (bounded read; Story 18.3)
#   memory.search == Tier.ONE  (bounded read; Story 18.3)
#   memory.write  == Tier.TWO  (store mutation; Story 18.4)
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
    store: MemoryStore,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
) -> None:
    """Register the memory read (Tier-1) + write (Tier-2) tools on *mcp*.

    Stories 18.1 / 18.2 SCAFFOLD: ``TIER_MAP`` is empty and NO ``@mcp.tool()``
    handlers are registered. Story 18.3 lands the Tier-1 read tools
    (``memory.read`` / ``memory.search``); Story 18.4 adds ``memory.write``
    (Tier-2) plus its ``memory.*`` event emission.

    The signature mirrors git-mcp's ``register_tools``: *store* is the live
    :class:`~memory_mcp.store.MemoryStore` the future handlers close over, and
    when *emitter_holder* is wired each tier-gated handler will be wrapped with
    ``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` emits a
    ``capability.denied`` audit envelope via clawhip-bridge before re-raising.

    Args:
        mcp: The FastMCP server to register tools on.
        store: The live memory store the future tool handlers operate against.
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        emitter_holder: Optional clawhip-bridge audit-emission holder; when None,
            audit emission is disabled (test mode).
    """
    # Scaffold: no tools registered yet. The memory tools (18.3 / 18.4) will use
    # ``store``, ``actor_kind`` / ``actor_id`` (capability gating), and
    # ``emitter_holder`` (audit emission). Referenced here so the scaffold is
    # import-clean under ``ruff`` / ``mypy --strict`` without unused-arg noise.
    _ = (TIER_MAP, store, actor_kind, actor_id, emitter_holder)


__all__ = ["TIER_MAP", "register_tools", "validate_caller_trace_id"]
