"""Task state machine — sole authority for task lifecycle transitions (P6-I3).

Phase 6 Epic 31 (FR102). The FSM enforces that only valid state transitions
occur. Every ``Task.status`` mutation must go through ``transition()`` — no
service may set status directly on the ORM model or via raw SQL UPDATE.

The states and transitions are derived from the actual production handlers in
``handlers.py`` — not from a theoretical design. This FSM guards what the
codebase *already does*, making implicit transitions explicit and raising
``InvalidStateTransition`` for anything else.

States (8):
  ``pending``, ``planning``, ``plan_ready``, ``executing``, ``blocked``,
  ``completed``, ``stopped``, ``failed``

Terminal states (no outgoing transitions):
  ``completed``, ``stopped``

Note: ``failed`` allows ``→ pending`` (retry from failed) so it is NOT terminal.

Design decisions:
- Pure function: no database, no I/O, no side effects. The caller is
  responsible for persisting the new state and emitting events.
- ``transition()`` returns the target state string on success.
- ``transition()`` raises ``InvalidStateTransition`` on failure.
- The FSM does NOT know about events — events are mapped to transitions
  by the materializer/handler layer (Story 31.4).
"""

from __future__ import annotations

from registry_state.domain.errors import InvalidStateTransition


class TaskStateMachine:
    """Finite state machine for task lifecycle (P6-I3).

    Sole authority for task state transitions. No service may mutate
    ``task.status`` directly — all transitions go through this class.

    Usage::

        fsm = TaskStateMachine()
        new_state = fsm.transition("pending", "planning")
        # new_state == "planning"
        # Caller persists new_state and emits event.
    """

    STATES: frozenset[str] = frozenset({
        "pending",
        "planning",
        "plan_ready",
        "executing",
        "blocked",
        "completed",
        "stopped",
        "failed",
    })

    TRANSITIONS: dict[str, frozenset[str]] = {
        # pending: initial state after task.created
        "pending": frozenset({"planning", "stopped"}),
        # planning: task.planning.started
        "planning": frozenset({"plan_ready", "stopped"}),
        # plan_ready: task.plan.ready
        "plan_ready": frozenset({"executing", "stopped"}),
        # executing: task.execution.started
        "executing": frozenset({"completed", "blocked", "stopped", "failed"}),
        # blocked: task.blocker_raised or task.budget_exceeded
        "blocked": frozenset({"executing", "pending", "stopped", "failed"}),
        # completed: task.completed (terminal)
        "completed": frozenset(),
        # stopped: task.stop_requested (terminal)
        "stopped": frozenset(),
        # failed: runtime failure (re-tryable via pending)
        "failed": frozenset({"pending"}),
    }

    def transition(self, current: str, target: str) -> str:
        """Validate and return the target state for a transition.

        Args:
            current: The current task status.
            target:  The desired target status.

        Returns:
            The ``target`` string (for caller to persist).

        Raises:
            InvalidStateTransition: If the transition is not permitted.
        """
        if current not in self.STATES:
            raise InvalidStateTransition(
                current, target, f"Unknown current state: {current!r}"
            )
        if target not in self.STATES:
            raise InvalidStateTransition(
                current, target, f"Unknown target state: {target!r}"
            )
        permitted = self.TRANSITIONS.get(current, frozenset())
        if target not in permitted:
            raise InvalidStateTransition(
                current, target,
                f"Permitted transitions from {current!r}: {sorted(permitted)}"
            )
        return target


__all__ = ["TaskStateMachine"]
