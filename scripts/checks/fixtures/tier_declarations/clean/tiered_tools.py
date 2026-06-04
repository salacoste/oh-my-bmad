"""Fixture: every @mcp.tool() handler declares a tier via TIER_MAP["<key>"] — CLEAN.

Mirrors the real task-registry / session-registry shape: a module-level
``TIER_MAP`` dict literal plus tool handlers whose bodies pass
``TIER_MAP["<action>"]`` to ``check_tier`` / ``check_tier_with_approval``.
The scanner must report ZERO violations.
"""

from __future__ import annotations


class Tier:
    ONE = 1
    TWO = 2
    THREE = 3


TIER_MAP: dict[str, Tier] = {
    "demo.read": Tier.ONE,
    "demo.write": Tier.TWO,
    "demo.push": Tier.THREE,
}


def check_tier(action: str, ctx: object, tier: Tier) -> None: ...


async def check_tier_with_approval(action: str, ctx: object, tier: Tier, **_: object) -> None: ...


def register_tools(mcp: object) -> None:
    @mcp.tool()
    async def demo_read(*, caller_trace_id: str) -> dict[str, object]:
        # Tier declared directly as a check_tier(...) argument.
        check_tier("demo.read", object(), TIER_MAP["demo.read"])
        return {"ok": True}

    @mcp.tool(name="demo.write")
    async def demo_write(*, caller_trace_id: str) -> dict[str, object]:
        # Tier declared via the parametrised-decorator form.
        check_tier("demo.write", object(), TIER_MAP["demo.write"])
        return {"ok": True}

    @mcp.tool()
    async def demo_push(*, caller_trace_id: str) -> dict[str, object]:
        # Tier declared inside a check_tier_with_approval(...) call.
        await check_tier_with_approval("demo.push", object(), TIER_MAP["demo.push"])
        return {"ok": True}
