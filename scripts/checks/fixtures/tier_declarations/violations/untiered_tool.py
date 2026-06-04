"""Fixture: a deliberately-untiered @mcp.tool() handler — VIOLATION (TIER001).

This is the AC scenario "a deliberately-untiered tool added to git-mcp fails the
gate": ``git_danger`` is registered as a tool but its body never references
``TIER_MAP["<key>"]``, so it declares no capability tier. The scanner MUST
surface TIER001.

A correctly-tiered sibling (``git_status``) is included so the file proves the
gate flags ONLY the untiered handler, not every tool.
"""

from __future__ import annotations


class Tier:
    ONE = 1


def check_tier(action: str, ctx: object, tier: Tier) -> None: ...


TIER_MAP: dict[str, Tier] = {
    "git.status": Tier.ONE,
}


def register_tools(mcp: object) -> None:
    @mcp.tool()
    async def git_status(*, caller_trace_id: str) -> dict[str, object]:
        check_tier("git.status", object(), TIER_MAP["git.status"])
        return {"ok": True}

    @mcp.tool()
    async def git_danger(*, caller_trace_id: str) -> dict[str, object]:
        # No TIER_MAP[...] reference anywhere — untiered tool (P3-I1 failure).
        return {"ok": True}
