"""Fixture: a tool references a TIER_MAP key with no dict-literal entry — VIOLATION (TIER002).

``git_commit`` is registered and DOES reference ``TIER_MAP["git.commit"]``, but
``TIER_MAP`` has no ``"git.commit"`` entry (typo / missing declaration). The
scanner MUST surface TIER002 — the tier is "declared" in name only.
"""

from __future__ import annotations


class Tier:
    ONE = 1
    TWO = 2


def check_tier(action: str, ctx: object, tier: Tier) -> None: ...


TIER_MAP: dict[str, Tier] = {
    "git.status": Tier.ONE,
    # NOTE: "git.commit" intentionally MISSING.
}


def register_tools(mcp: object) -> None:
    @mcp.tool()
    async def git_commit(*, caller_trace_id: str) -> dict[str, object]:
        check_tier("git.commit", object(), TIER_MAP["git.commit"])
        return {"ok": True}
