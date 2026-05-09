"""Capability-tier classification and enforcement helpers (FR37, FR38).

Tier levels (from PRD lines 361-369):

- Tier 0 — Read-only core: workspace read, search, task/session registry read, event read.
- Tier 1 — Bounded write: write/edit within assigned worktree, run local tests, artifact write.
- Tier 2 — Repo mutation: git commit, branch create, PR draft. Subject to approval policy.
- Tier 3 — High-risk: requires explicit approval event (Phase 1: git push only).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from events.envelope import ActorKind
from events.errors import CapabilityDenied


class Tier(IntEnum):
    """Capability tier levels, ordered by risk."""

    ZERO = 0  # read-only
    ONE = 1  # bounded write
    TWO = 2  # repo mutation
    THREE = 3  # high-risk (requires approval)


@dataclass(frozen=True)
class CallerContext:
    """Identifies who is making a capability-tiered call."""

    actor_kind: ActorKind
    actor_id: str
    task_id: str | None = None


@dataclass(frozen=True)
class CapabilityOk:
    """Successful result from check_tier."""

    action: str
    caller: CallerContext
    tier: Tier


_MAX_TIER_BY_ACTOR: dict[ActorKind, Tier] = {
    "operator": Tier.THREE,
    "system": Tier.THREE,
    "clawhip": Tier.TWO,
    "orchestrator": Tier.TWO,
    "worker": Tier.TWO,
}


def check_tier(
    action: str,
    caller: CallerContext,
    required_tier: Tier,
) -> CapabilityOk:
    """Check whether *caller* is authorized for *required_tier* on *action*.

    Returns CapabilityOk on success; raises CapabilityDenied otherwise.
    """
    max_tier = _MAX_TIER_BY_ACTOR.get(caller.actor_kind)
    if max_tier is None:
        raise CapabilityDenied(
            action=action,
            actor_kind=caller.actor_kind,
            required_tier=int(required_tier),
            reason=f"unknown actor_kind {caller.actor_kind!r}",
        )
    if required_tier > max_tier:
        raise CapabilityDenied(
            action=action,
            actor_kind=caller.actor_kind,
            required_tier=int(required_tier),
            reason=(
                f"actor_kind {caller.actor_kind!r} allows Tier.{max_tier.value} "
                f"at most; action requires Tier.{required_tier.value}"
            ),
        )
    return CapabilityOk(action=action, caller=caller, tier=required_tier)
