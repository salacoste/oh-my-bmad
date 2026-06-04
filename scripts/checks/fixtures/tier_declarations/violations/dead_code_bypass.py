"""Fixture: tier reference lives only in unreachable dead code — VIOLATION (TIER001).

P1a bypass (Story 15.2a review): ``git_danger`` is registered as a tool and its
body *textually* contains ``TIER_MAP["git.danger"]``, but only as a bare subscript
inside an ``if False:`` branch that never executes — the real op below runs
ungated. An ``ast.walk``-based "ANY TIER_MAP subscript anywhere" rule was
satisfied by this; the hardened rule (a tier reference counts only as a direct
``check_tier*`` argument in the handler's own body) MUST now surface TIER001.

A correctly-tiered sibling (``git_status``) proves the gate flags ONLY the
bypassing handler, not every tool.
"""

from __future__ import annotations


class Tier:
    ONE = 1


def check_tier(action: str, ctx: object, tier: Tier) -> None: ...


TIER_MAP: dict[str, Tier] = {
    "git.status": Tier.ONE,
    "git.danger": Tier.ONE,
}


def register_tools(mcp: object) -> None:
    @mcp.tool()
    async def git_status(*, caller_trace_id: str) -> dict[str, object]:
        check_tier("git.status", object(), TIER_MAP["git.status"])
        return {"ok": True}

    @mcp.tool()
    async def git_danger(*, caller_trace_id: str) -> dict[str, object]:
        if False:  # unreachable — the tier "declaration" never runs.
            _ = TIER_MAP["git.danger"]
        # Real op runs with NO tier check — untiered tool (P3-I1 failure).
        return {"ok": True}
