"""memory-mcp MCP tool handlers (Epic 18 / Story 18.3 — Tier-1 read tools).

Story 18.3 lands the two bounded read tools — ``memory.read`` and
``memory.search`` — both at ``Tier.ONE``. Each is registered with an EXPLICIT
dotted MCP name (``@mcp.tool(name="memory.read")``) so ``list_tools()`` surfaces
the canonical ``memory.<op>`` id the 18.1 ATDD contracts assert (FastMCP would
otherwise default the id to the Python function name ``memory_read`` — which is
≠ ``memory.read``). Every handler:

  1. validates ``caller_trace_id`` FIRST (FR58 / Story 9.1 shape contract);
  2. gates on its tier via ``check_tier("memory.<op>", CallerContext(...),
     TIER_MAP["memory.<op>"])`` — the ``TIER_MAP[...]`` subscript is passed as a
     DIRECT argument to ``check_tier`` so ``scripts/check_tier_declarations.py``
     recognises the tool as tiered; and
  3. routes the actual read through the injected
     :class:`~memory_mcp.store.MemoryStore` (``store.read`` / ``store.search``)
     and returns a deterministic structured result.

Reads emit NO events (no ``clock`` is threaded into this story); the mutating
``memory.write`` tool's ``memory.*`` event emission lands in Story 18.4.

The :class:`~memory_mcp.store.MemoryStore` is threaded into ``register_tools``
since 18.2 so the read tools close over the live store without re-plumbing the
factory.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, and git-mcp (mcp-servers cannot
share code per Story 5.8's import-graph constraint); the same drift guard extends
to memory-mcp now that it registers tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier
from capabilities.emit import emit_capability_denied_on_deny
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from memory_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server.fastmcp import FastMCP

    from memory_mcp.store import MemoryStore

log = logging.getLogger(__name__)

# Story 18.3: the two Tier-1 read tools. The mutating ``memory.write`` tool
# (Tier-2 store mutation) lands in Story 18.4. Keys are the canonical dotted
# ``memory.<op>`` MCP tool ids (matched to the ``@mcp.tool(name=…)``
# registration below).
#   memory.write == Tier.TWO  (store mutation; Story 18.4) — added when the
#   write tool is registered, so its ``test_write_tool_is_tier_two`` ATDD
#   contract stays red-phase xfail until then.
TIER_MAP: dict[str, Tier] = {
    "memory.read": Tier.ONE,
    "memory.search": Tier.ONE,
}

# D5: default + hard cap on ``memory.search`` result count so an unbounded
# request cannot make the store stream an arbitrarily large ranked result set
# through the request path. Mirrors git-mcp's ``git.log`` depth bound.
_SEARCH_DEFAULT_LIMIT: int = 10
_SEARCH_MAX_LIMIT: int = 100


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


def _make_actor_id_extractor(actor_id: str) -> Callable[..., str]:
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``.

    The configured ``actor_id`` is the calling actor's identity for the duration
    of this server process (set at startup). Tool kwargs do not carry actor
    identity — it comes from the server's launch config — so the extractor
    returns the closed-over value irrespective of args/kwargs.
    """

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


def register_tools(
    mcp: FastMCP,
    store: MemoryStore,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
) -> None:
    """Register the memory read (Tier-1) tools on *mcp*.

    Story 18.3 ships the two Tier-1 read tools (``memory.read`` /
    ``memory.search``); Story 18.4 adds the mutating ``memory.write`` (Tier-2)
    plus its ``memory.*`` event emission.

    The signature mirrors git-mcp's ``register_tools``: *store* is the live
    :class:`~memory_mcp.store.MemoryStore` the handlers close over, and when
    *emitter_holder* is wired each tier-gated handler is wrapped with
    ``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` from
    ``check_tier`` emits a ``capability.denied`` audit envelope via
    clawhip-bridge before re-raising.

    Args:
        mcp: The FastMCP server to register tools on.
        store: The live memory store the tool handlers operate against.
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        emitter_holder: Optional clawhip-bridge audit-emission holder; when None,
            audit emission is disabled (test mode).
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

    def _caller() -> CallerContext:
        return CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=None)

    # ------------------------------------------------------------------
    # Tool: memory.read (Tier-1 bounded read)
    # ------------------------------------------------------------------

    @mcp.tool(name="memory.read")
    @_maybe_wrap("memory.read")
    async def memory_read(
        *,
        caller_trace_id: str,
        key: str = "",
    ) -> dict[str, object]:
        """Return the document keyed by *key* (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            key: Stable string id of the document to fetch. A missing key yields
                ``found=False`` with ``document=None`` (no error).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("memory.read", _caller(), TIER_MAP["memory.read"])
        document = store.read(key)
        return {"ok": True, "found": document is not None, "document": document}

    # ------------------------------------------------------------------
    # Tool: memory.search (Tier-1 bounded read)
    # ------------------------------------------------------------------

    @mcp.tool(name="memory.search")
    @_maybe_wrap("memory.search")
    async def memory_search(
        *,
        caller_trace_id: str,
        query: str = "",
        limit: int = _SEARCH_DEFAULT_LIMIT,
    ) -> dict[str, object]:
        """Return documents matching *query*, ranked best-first (Tier-1 bounded read).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            query: An FTS5 query string (a bare term or ``term1 term2``). An empty
                query yields an empty result set (no store call).
            limit: Max results to return (D5 — defaults to 10, capped at 100).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("memory.search", _caller(), TIER_MAP["memory.search"])
        if query == "":
            return {"ok": True, "results": []}
        capped = max(1, min(limit, _SEARCH_MAX_LIMIT))
        results = store.search(query, capped)
        return {"ok": True, "results": results}

    # Reference the handlers so linters see them as used (FastMCP holds the real
    # references via the decorator registration side-effect).
    _ = (memory_read, memory_search)


__all__ = ["TIER_MAP", "register_tools", "validate_caller_trace_id"]
