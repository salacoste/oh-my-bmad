"""Resume-after-approval state machine (Story 5.17a — FR36, HIGH-RISK).

Deterministic FSM with states ``RUNNING`` → ``AWAITING_APPROVAL`` →
``PAUSED`` → ``RESUMED`` → ``COMPLETED`` / ``FAILED``.  Transitions are
driven exclusively by input events; the module has **zero IO** imports.

The state machine is the isolated core that Story 5.17b will couple to
cross-restart recovery, idempotency, and MCP event emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# States & input events
# ---------------------------------------------------------------------------


class WorkerState(Enum):
    """Lifecycle states for the worker FSM."""

    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    FAILED = "failed"


class LifecycleEvent(Enum):
    """Input events that drive FSM transitions."""

    TASK_AWAITING_APPROVAL = "task.awaiting_approval"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


# ---------------------------------------------------------------------------
# Transition table  — (current_state, event) → new_state
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[tuple[WorkerState, LifecycleEvent], WorkerState] = {
    # RUNNING →
    (WorkerState.RUNNING, LifecycleEvent.TASK_AWAITING_APPROVAL): WorkerState.AWAITING_APPROVAL,
    (WorkerState.RUNNING, LifecycleEvent.TASK_PAUSED): WorkerState.PAUSED,
    (WorkerState.RUNNING, LifecycleEvent.TASK_COMPLETED): WorkerState.COMPLETED,
    (WorkerState.RUNNING, LifecycleEvent.TASK_FAILED): WorkerState.FAILED,
    # AWAITING_APPROVAL →
    (WorkerState.AWAITING_APPROVAL, LifecycleEvent.APPROVAL_GRANTED): WorkerState.RESUMED,
    (WorkerState.AWAITING_APPROVAL, LifecycleEvent.APPROVAL_REJECTED): WorkerState.FAILED,
    (WorkerState.AWAITING_APPROVAL, LifecycleEvent.TASK_FAILED): WorkerState.FAILED,
    # PAUSED →
    (WorkerState.PAUSED, LifecycleEvent.TASK_RESUMED): WorkerState.RESUMED,
    (WorkerState.PAUSED, LifecycleEvent.TASK_FAILED): WorkerState.FAILED,
    # RESUMED →
    (WorkerState.RESUMED, LifecycleEvent.TASK_AWAITING_APPROVAL): WorkerState.AWAITING_APPROVAL,
    (WorkerState.RESUMED, LifecycleEvent.TASK_PAUSED): WorkerState.PAUSED,
    (WorkerState.RESUMED, LifecycleEvent.TASK_COMPLETED): WorkerState.COMPLETED,
    (WorkerState.RESUMED, LifecycleEvent.TASK_FAILED): WorkerState.FAILED,
    # COMPLETED and FAILED are terminal — no outgoing transitions.
}

# Terminal states (no valid outgoing transitions).
_TERMINAL_STATES: frozenset[WorkerState] = frozenset({WorkerState.COMPLETED, WorkerState.FAILED})


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when an event cannot be applied in the current state.

    Attributes:
        current_state: The FSM state when the invalid event was received.
        event: The event that was rejected.
    """

    def __init__(self, current_state: WorkerState, event: LifecycleEvent) -> None:
        self.current_state = current_state
        self.event = event
        super().__init__(
            f"invalid transition: event={event.value!r} from state={current_state.value!r}"
        )


# ---------------------------------------------------------------------------
# Transition log entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionLogEntry:
    """Immutable record of a single state transition."""

    from_state: WorkerState
    event: LifecycleEvent
    to_state: WorkerState


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class LifecycleFSM:
    """Deterministic lifecycle state machine for the worker.

    Usage::

        fsm = LifecycleFSM()
        fsm.transition(LifecycleEvent.TASK_AWAITING_APPROVAL)
        fsm.transition(LifecycleEvent.APPROVAL_GRANTED)
        fsm.transition(LifecycleEvent.TASK_COMPLETED)
    """

    def __init__(self, initial_state: WorkerState = WorkerState.RUNNING) -> None:
        self._state = initial_state
        self._log: list[TransitionLogEntry] = []

    @property
    def current_state(self) -> WorkerState:
        return self._state

    @property
    def transition_log(self) -> tuple[TransitionLogEntry, ...]:
        return tuple(self._log)

    def transition(self, event: LifecycleEvent) -> WorkerState:
        """Apply *event* and return the new state.

        Raises:
            InvalidTransitionError: if *event* is not valid in the current state.
        """
        key = (self._state, event)
        new_state = _TRANSITIONS.get(key)
        if new_state is None:
            raise InvalidTransitionError(self._state, event)
        entry = TransitionLogEntry(from_state=self._state, event=event, to_state=new_state)
        self._log.append(entry)
        self._state = new_state
        return new_state

    def is_terminal(self) -> bool:
        return self._state in _TERMINAL_STATES

    @classmethod
    def all_transitions(cls) -> dict[tuple[WorkerState, LifecycleEvent], WorkerState]:
        """Return the full transition table (for test coverage checks)."""
        return dict(_TRANSITIONS)
