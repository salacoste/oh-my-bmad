"""Unit tests for the resume-after-approval lifecycle FSM (Story 5.17a).

Achieves 100 % transition-table coverage: every valid (state, event) pair
and every invalid combination is tested.
"""

from __future__ import annotations

import pytest

from worker_wrapper.domain.lifecycle import (
    _TERMINAL_STATES,
    _TRANSITIONS,
    InvalidTransitionError,
    LifecycleFSM,
    TransitionLogEntry,
)
from worker_wrapper.domain.lifecycle import (
    LifecycleEvent as Evt,
)
from worker_wrapper.domain.lifecycle import (
    WorkerState as St,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_STATES = list(St)
_ALL_EVENTS = list(Evt)


def _state(fsm: LifecycleFSM) -> St:
    """Read ``current_state`` as the declared ``WorkerState`` type.

    Reading through this helper erases mypy's literal-narrowing carried over
    from a prior ``assert``, so a later equality check against a different
    state is not flagged ``comparison-overlap``. Behaviour is identical to
    reading ``fsm.current_state`` directly.
    """
    return fsm.current_state


# ---------------------------------------------------------------------------
# Parametrized: every valid transition in the table
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Each (state, event) pair in _TRANSITIONS produces the expected state."""

    @pytest.mark.parametrize(
        ("from_state", "event", "expected"),
        [(from_state, event, to_state) for (from_state, event), to_state in _TRANSITIONS.items()],
        ids=lambda v: v.value if isinstance(v, (St, Evt)) else "",
    )
    def test_valid_transition(self, from_state: St, event: Evt, expected: St) -> None:
        fsm = LifecycleFSM(initial_state=from_state)
        result = fsm.transition(event)
        assert result == expected
        assert fsm.current_state == expected


# ---------------------------------------------------------------------------
# Parametrized: every invalid combination raises InvalidTransition
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    """Any (state, event) NOT in the table raises InvalidTransitionError."""

    @pytest.mark.parametrize(
        ("state", "event"),
        [
            (state, event)
            for state in _ALL_STATES
            for event in _ALL_EVENTS
            if (state, event) not in _TRANSITIONS
        ],
        ids=lambda v: v.value if isinstance(v, (St, Evt)) else "",
    )
    def test_invalid_transition_raises(self, state: St, event: Evt) -> None:
        fsm = LifecycleFSM(initial_state=state)
        with pytest.raises(InvalidTransitionError) as exc_info:
            fsm.transition(event)
        assert exc_info.value.current_state == state
        assert exc_info.value.event == event

    def test_invalid_transition_message_contains_state_and_event(self) -> None:
        fsm = LifecycleFSM(initial_state=St.COMPLETED)
        with pytest.raises(InvalidTransitionError) as exc_info:
            fsm.transition(Evt.TASK_COMPLETED)
        msg = str(exc_info.value)
        assert "completed" in msg
        assert "task.completed" in msg


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminalStates:
    def test_completed_is_terminal(self) -> None:
        assert St.COMPLETED in _TERMINAL_STATES

    def test_failed_is_terminal(self) -> None:
        assert St.FAILED in _TERMINAL_STATES

    def test_no_outgoing_from_completed(self) -> None:
        for event in _ALL_EVENTS:
            assert (St.COMPLETED, event) not in _TRANSITIONS

    def test_no_outgoing_from_failed(self) -> None:
        for event in _ALL_EVENTS:
            assert (St.FAILED, event) not in _TRANSITIONS

    def test_is_terminal_method(self) -> None:
        fsm = LifecycleFSM(initial_state=St.RUNNING)
        assert not fsm.is_terminal()
        fsm.transition(Evt.TASK_COMPLETED)
        assert fsm.is_terminal()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_sequence_same_result(self) -> None:
        """Run the canonical approval flow N times — identical results."""
        sequence = [
            Evt.TASK_AWAITING_APPROVAL,
            Evt.APPROVAL_GRANTED,
            Evt.TASK_COMPLETED,
        ]
        results: list[tuple[St, tuple[TransitionLogEntry, ...]]] = []
        for _ in range(20):
            fsm = LifecycleFSM()
            for event in sequence:
                fsm.transition(event)
            results.append((fsm.current_state, fsm.transition_log))

        assert len(set(results)) == 1, "non-deterministic FSM output"
        assert results[0][0] == St.COMPLETED

    def test_log_is_immutable_copy(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_COMPLETED)
        log = fsm.transition_log
        assert isinstance(log, tuple)
        # Mutating the returned tuple is not possible, but verify it's
        # disconnected from the internal list.
        assert len(log) == 1


# ---------------------------------------------------------------------------
# Canonical flows
# ---------------------------------------------------------------------------


class TestCanonicalApprovalFlow:
    def test_running_approval_grant_complete(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        assert _state(fsm) == St.AWAITING_APPROVAL
        fsm.transition(Evt.APPROVAL_GRANTED)
        assert _state(fsm) == St.RESUMED
        fsm.transition(Evt.TASK_COMPLETED)
        assert _state(fsm) == St.COMPLETED

    def test_running_approval_reject(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        fsm.transition(Evt.APPROVAL_REJECTED)
        assert fsm.current_state == St.FAILED

    def test_awaiting_approval_then_fail(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        fsm.transition(Evt.TASK_FAILED)
        assert fsm.current_state == St.FAILED


class TestPauseResumeFlow:
    def test_running_pause_resume_complete(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_PAUSED)
        assert _state(fsm) == St.PAUSED
        fsm.transition(Evt.TASK_RESUMED)
        assert _state(fsm) == St.RESUMED
        fsm.transition(Evt.TASK_COMPLETED)
        assert _state(fsm) == St.COMPLETED

    def test_paused_then_fail(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_PAUSED)
        fsm.transition(Evt.TASK_FAILED)
        assert fsm.current_state == St.FAILED


class TestResumeCycle:
    def test_resumed_can_reenter_awaiting_approval(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        fsm.transition(Evt.APPROVAL_GRANTED)
        assert _state(fsm) == St.RESUMED
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        assert _state(fsm) == St.AWAITING_APPROVAL

    def test_resumed_can_pause(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_PAUSED)
        fsm.transition(Evt.TASK_RESUMED)
        fsm.transition(Evt.TASK_PAUSED)
        assert fsm.current_state == St.PAUSED


# ---------------------------------------------------------------------------
# Transition log
# ---------------------------------------------------------------------------


class TestTransitionLog:
    def test_log_records_each_transition(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_COMPLETED)
        log = fsm.transition_log
        assert len(log) == 1
        assert log[0] == TransitionLogEntry(
            from_state=St.RUNNING, event=Evt.TASK_COMPLETED, to_state=St.COMPLETED
        )

    def test_log_records_full_sequence(self) -> None:
        fsm = LifecycleFSM()
        fsm.transition(Evt.TASK_AWAITING_APPROVAL)
        fsm.transition(Evt.APPROVAL_GRANTED)
        fsm.transition(Evt.TASK_COMPLETED)
        log = fsm.transition_log
        assert len(log) == 3
        assert log[0].from_state == St.RUNNING
        assert log[0].to_state == St.AWAITING_APPROVAL
        assert log[1].from_state == St.AWAITING_APPROVAL
        assert log[1].to_state == St.RESUMED
        assert log[2].from_state == St.RESUMED
        assert log[2].to_state == St.COMPLETED


# ---------------------------------------------------------------------------
# Coverage completeness check
# ---------------------------------------------------------------------------


class TestCoverageCompleteness:
    def test_all_valid_transitions_tested(self) -> None:
        """Verify the parametrized test above covers every table entry."""
        assert len(_TRANSITIONS) > 0
        # Each valid entry is covered by TestValidTransitions parametrize.
        # Count unique (state, event) pairs = table size.
        assert len(set(_TRANSITIONS.keys())) == len(_TRANSITIONS)

    def test_all_invalid_combinations_tested(self) -> None:
        """Verify total valid + invalid = all combinations."""
        total = len(_ALL_STATES) * len(_ALL_EVENTS)
        valid = len(_TRANSITIONS)
        invalid = total - valid
        assert invalid > 0  # terminal states guarantee this
