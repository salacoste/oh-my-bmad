"""verification-mcp MCP tool handlers (Epic 17 / Story 17.2 — scaffold).

Story 17.2 ships the SCAFFOLD only: an empty ``TIER_MAP`` and the shared
``validate_caller_trace_id`` helper. ``register_tools`` registers NO tools yet —
the two Tier-2 verification tools (``verification.run_build`` /
``verification.run_tests``) land in Stories 17.3 / 17.4, each registered with an
EXPLICIT dotted MCP name (``@mcp.tool(name="verification.run_build")``) so
``list_tools()`` surfaces the canonical ``verification.<op>`` id the 17.1 ATDD
contracts assert (FastMCP would otherwise default the id to the Python function
name). Both tools are ``Tier.TWO`` (they run project code but perform no external
mutation) and gate via ``check_tier``.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, and git-mcp (mcp-servers cannot
share code per Story 5.8's import-graph constraint); the same drift guard extends
to verification-mcp now that it ships the scaffold.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from verification_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from verification_mcp.server import VerificationExecutor

log = logging.getLogger(__name__)

# Story 17.2 scaffold — EMPTY until the verification tools land in 17.3 / 17.4.
# Keys will be the canonical dotted ``verification.<op>`` MCP tool ids
# (``verification.run_build`` / ``verification.run_tests``), both ``Tier.TWO``
# (run project code, no external mutation). check_tier is imported ready.
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
    executor: VerificationExecutor,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
) -> None:
    """Register the verification tools on *mcp* (Story 17.2 — NO tools yet).

    Story 17.2 is the SCAFFOLD: ``TIER_MAP`` is empty and this function
    registers ZERO tools. Stories 17.3 / 17.4 add the two Tier-2 tools —
    ``verification.run_build`` / ``verification.run_tests`` — each gated via
    ``check_tier`` and (17.4) emitting a typed ``verification.*`` event.

    Mirrors git-mcp's / task-registry's ``register_tools``: when *emitter_holder*
    is wired, each tier-gated handler is wrapped with
    ``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` from
    ``check_tier`` emits a ``capability.denied`` audit envelope via clawhip-bridge
    before re-raising. The parameters are accepted now so the 17.3 handlers can
    close over them without changing the factory wiring.
    """
    # Story 17.2 scaffold — no tools registered yet. The parameters are bound to
    # ``_`` so linters / mypy --strict do not flag them as unused while the
    # handler bodies (17.3) are still pending. ``CallerContext`` / ``check_tier``
    # are imported ready for the 17.3 handlers' tier gate.
    _ = (executor, actor_kind, actor_id, emitter_holder, CallerContext, check_tier)


__all__ = ["TIER_MAP", "register_tools", "validate_caller_trace_id"]
