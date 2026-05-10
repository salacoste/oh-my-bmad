"""Task lifecycle state machine — canonical action/state rules.

Single source of truth for which actions are valid in which task states.
Route modules derive their lookup tables from the ``ACTION_VALID_STATES``
map so the two views cannot drift.
"""

from __future__ import annotations

# Canonical mapping: action → set of valid states.
# This is the single source of truth — derive other lookups from it.
ACTION_VALID_STATES: dict[str, set[str]] = {
    "approve": {"plan_ready", "awaiting_approval"},
    "reject": {"plan_ready", "awaiting_approval"},
    "stop": {"pending", "planning", "plan_ready", "awaiting_approval", "executing", "blocked"},
    "retry": {"blocked", "failed"},
}


def _derive_state_actions() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for action, states in ACTION_VALID_STATES.items():
        for state in states:
            result.setdefault(state, []).append(action)
    # Terminal states with no valid actions.
    for terminal in ("completed", "stopped"):
        result.setdefault(terminal, [])
    # Sort for deterministic ordering.
    return {k: sorted(v) for k, v in sorted(result.items())}


# Inverse lookup: state → list of valid actions.
STATE_NEXT_COMMANDS: dict[str, list[str]] = _derive_state_actions()
