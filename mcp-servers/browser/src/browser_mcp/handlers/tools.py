"""Browser tool registrations + TIER_MAP (Epic 20 / FR78).

Story 20.1 scaffold: no tools registered. TIER_MAP is empty.
Tools land in Stories 21.1-21.5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from capabilities import Tier
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from events.envelope import ActorKind

    from browser_mcp.adapters.clawhip_client import EmitterHolder

# Story 20.1 scaffold — empty until browser tools land in 21.1-21.5.
# Re-exported from server.py so the canonical TIER_MAP lives in one place.
TIER_MAP: dict[str, Tier] = {}


def register_tools(
    mcp: FastMCP,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None,
) -> None:
    """Register browser tools on the FastMCP instance.

    Story 20.1 scaffold: no tools registered. Browser tools land in 21.1-21.5.
    """
