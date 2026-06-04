"""Fixture: tier reference lives only in an uncalled nested helper — VIOLATION (TIER001).

P1b bypass (Story 15.2a review): ``git_danger`` is registered as a tool and its
body defines an inner ``def _gate()`` that calls
``check_tier(..., TIER_MAP["git.danger"])`` — but ``_gate`` is never invoked, so
the real op runs ungated. An ``ast.walk``-based rule descended into the nested
``def`` and counted the subscript; the hardened rule prunes nested
function/lambda scopes, so the handler's OWN body has no tier declaration and the
gate MUST now surface TIER001.

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
        def _gate() -> None:
            # The only TIER_MAP reference — but _gate is never called.
            check_tier("git.danger", object(), TIER_MAP["git.danger"])

        # _gate() intentionally NOT invoked — handler body never gates.
        return {"ok": True}
