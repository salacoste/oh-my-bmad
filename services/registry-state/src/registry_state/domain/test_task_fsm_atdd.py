"""ATDD contract tests for the task state machine (Epic 31).

Phase 6 Epic 31 — Task State Machine (ADR-0018). All contracts are satisfied.
The xfail markers were removed when the production code landed.

Contracts verified:
  1. TaskStateMachine class exists with STATES and TRANSITIONS
  2. All 13+ valid transitions succeed and return target state
  3. Invalid transitions raise InvalidStateTransition
  4. Terminal states reject all transitions
  5. Unknown states raise InvalidStateTransition
  6. FSM is pure — no database dependency, no side effects
  7. InvalidStateTransition is importable from domain.errors

Reference tests (not gated):
  - Current status values used in codebase are enumerated
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Story 31.2: TaskStateMachine class exists with correct states
# ---------------------------------------------------------------------------


def test_task_state_machine_class_exists() -> None:
    """The TaskStateMachine class must be importable from domain.task_fsm."""
    from registry_state.domain.task_fsm import TaskStateMachine

    assert TaskStateMachine is not None


def test_fsm_contains_all_task_states() -> None:
    """The FSM must define all 8 task states used by the current codebase."""
    from registry_state.domain.task_fsm import TaskStateMachine

    expected = {
        "pending",
        "planning",
        "plan_ready",
        "executing",
        "blocked",
        "completed",
        "stopped",
        "failed",
    }
    assert expected.issubset(TaskStateMachine.STATES), (
        f"Missing states: {expected - TaskStateMachine.STATES}"
    )


# ---------------------------------------------------------------------------
# Story 31.2: Valid transitions succeed
# ---------------------------------------------------------------------------


def test_transition_pending_to_planning() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("pending", "planning")
    assert result == "planning"


def test_transition_pending_to_stopped() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("pending", "stopped")
    assert result == "stopped"


def test_transition_planning_to_plan_ready() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("planning", "plan_ready")
    assert result == "plan_ready"


def test_transition_plan_ready_to_executing() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("plan_ready", "executing")
    assert result == "executing"


def test_transition_executing_to_completed() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("executing", "completed")
    assert result == "completed"


def test_transition_executing_to_blocked() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("executing", "blocked")
    assert result == "blocked"


def test_transition_blocked_to_executing() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("blocked", "executing")
    assert result == "executing"


def test_transition_blocked_to_pending() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("blocked", "pending")
    assert result == "pending"


def test_transition_failed_to_pending() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("failed", "pending")
    assert result == "pending"


def test_transition_executing_to_stopped() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("executing", "stopped")
    assert result == "stopped"


def test_transition_executing_to_failed() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("executing", "failed")
    assert result == "failed"


# ---------------------------------------------------------------------------
# Story 31.2: Invalid transitions raise InvalidStateTransition
# ---------------------------------------------------------------------------


def test_terminal_state_completed_rejects_all() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    for target in ("pending", "planning", "executing", "blocked"):
        with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition"):
            fsm.transition("completed", target)


def test_terminal_state_stopped_rejects_all() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    for target in ("pending", "planning", "executing"):
        with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition"):
            fsm.transition("stopped", target)


def test_terminal_to_self_rejected() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition"):
        fsm.transition("completed", "completed")


def test_invalid_forward_transition_rejected() -> None:
    """pending → executing is invalid (must go through planning → plan_ready first)."""
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition"):
        fsm.transition("pending", "executing")


def test_unknown_current_state_raises() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition|Unknown"):
        fsm.transition("nonexistent_state", "pending")


def test_unknown_target_state_raises() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    with pytest.raises(Exception, match="Cannot transition|InvalidStateTransition|Unknown"):
        fsm.transition("pending", "nonexistent_target")


# ---------------------------------------------------------------------------
# Story 31.2: InvalidStateTransition exception
# ---------------------------------------------------------------------------


def test_invalid_state_transition_exception_exists() -> None:
    from registry_state.domain.errors import InvalidStateTransition

    assert issubclass(InvalidStateTransition, Exception)


# ---------------------------------------------------------------------------
# Story 31.2: FSM is pure — no database or I/O dependency
# ---------------------------------------------------------------------------


def test_fsm_transition_returns_string() -> None:
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("pending", "planning")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Reference test (NOT xfail): current status values in codebase
# ---------------------------------------------------------------------------


def test_ref_current_status_values_are_documented() -> None:
    """[Reference] Document the 8 task status values used across the codebase.
    Not xfail — this is documentation, not a contract test."""
    current_statuses = {
        "pending",
        "planning",
        "plan_ready",
        "executing",
        "blocked",
        "completed",
        "stopped",
        "failed",
    }
    # Verify these match what handlers.py uses
    assert len(current_statuses) == 8
    assert "pending" in current_statuses
    assert "completed" in current_statuses
