"""ATDD red-phase contract tests for recovery loops (Epic 38, Story 38.1).

Phase 7 Epic 38 — Recovery Loops.  These tests assert contracts that
are NOT YET IMPLEMENTED.  Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate).  When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a HARD
FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts tested (all xfail):
  1. RecoveryPolicy class exists with per-state action mapping
  2. RecoveryPolicy decides auto_retry for critical-stale failed tasks
  3. RecoveryPolicy decides auto_stop for critical-stale blocked tasks after max retries
  4. RecoveryPolicy decides no_op for warning-severity stale tasks
  5. RecoveryExecutor class exists and is callable
  6. RecoveryExecutor auto-retry emits task.auto_retry event
  7. RecoveryExecutor auto-stop emits task.auto_stop event
  8. task.auto_retry event type registered with schema
  9. task.auto_stop event type registered with schema
 10. RecoveryPolicy has max_retries_per_task configuration

Reference tests (NOT xfail):
  - StaleTaskDetector has overdue_tasks_and_mark method
  - task.stale_critical event type is registered
  - Task FSM has failed→pending transition (retry path)
  - Task FSM has *→stopped transition (auto-stop path)
"""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone

from events import FROZEN_EPOCH, FrozenClock


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail) — existing infrastructure recovery builds on
# ---------------------------------------------------------------------------


def test_stale_detector_has_overdue_tasks_and_mark() -> None:
    """StaleTaskDetector must have the ``overdue_tasks_and_mark`` method."""
    from registry_state.domain.failure_detection import StaleTaskDetector

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    detector = StaleTaskDetector(clock=clock)
    assert hasattr(detector, "overdue_tasks_and_mark")
    assert callable(detector.overdue_tasks_and_mark)


def test_stale_critical_event_registered() -> None:
    """The ``task.stale_critical`` event type must be registered."""
    from events.schema_registry import REGISTRY

    assert ("task.stale_critical", "1.1.0") in REGISTRY


def test_fsm_failed_to_pending_transition_exists() -> None:
    """Task FSM must allow ``failed`` → ``pending`` for auto-retry."""
    from registry_state.domain.task_fsm import TaskStateMachine

    machine = TaskStateMachine()
    assert "pending" in machine.TRANSITIONS.get("failed", set())


def test_fsm_has_stopped_as_valid_target() -> None:
    """Task FSM must allow transitions to ``stopped`` for auto-stop."""
    from registry_state.domain.task_fsm import TaskStateMachine

    # Multiple states can transition to stopped
    can_stop = {
        state
        for state, targets in TaskStateMachine.TRANSITIONS.items()
        if "stopped" in targets
    }
    assert len(can_stop) >= 3, "At least 3 states must transition to stopped"


# ---------------------------------------------------------------------------
# xfail contract tests — RecoveryPolicy (Story 38.2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 38.2 — RecoveryPolicy not yet implemented")
def test_recovery_policy_class_exists() -> None:
    """RecoveryPolicy class must exist in failure_detection module."""
    from registry_state.domain.failure_detection import RecoveryPolicy

    policy = RecoveryPolicy()
    assert policy is not None


@pytest.mark.xfail(strict=True, reason="Story 38.2 — RecoveryPolicy not yet implemented")
def test_recovery_policy_auto_retry_for_critical_failed() -> None:
    """RecoveryPolicy must decide auto_retry for critical-severity stale failed tasks.

    A task in 'failed' state that hits critical staleness should be
    automatically retried (requeued to pending) up to max_retries.
    """
    from registry_state.domain.failure_detection import RecoveryPolicy

    policy = RecoveryPolicy()
    decision = policy.decide(
        status="failed",
        severity="critical",
        retry_count=0,
    )
    assert decision == "auto_retry"


@pytest.mark.xfail(strict=True, reason="Story 38.2 — RecoveryPolicy not yet implemented")
def test_recovery_policy_auto_stop_after_max_retries() -> None:
    """RecoveryPolicy must decide auto_stop when retry_count >= max_retries.

    After exhausting retries, the task should be auto-stopped rather than
    retrying indefinitely.
    """
    from registry_state.domain.failure_detection import RecoveryPolicy

    policy = RecoveryPolicy(max_retries=3)
    decision = policy.decide(
        status="failed",
        severity="critical",
        retry_count=3,
    )
    assert decision == "auto_stop"


@pytest.mark.xfail(strict=True, reason="Story 38.2 — RecoveryPolicy not yet implemented")
def test_recovery_policy_no_op_for_warning() -> None:
    """RecoveryPolicy must decide no_op for warning-severity stale tasks.

    Warning-level staleness only generates alerts; no automatic action taken.
    The operator still has time to intervene.
    """
    from registry_state.domain.failure_detection import RecoveryPolicy

    policy = RecoveryPolicy()
    decision = policy.decide(
        status="failed",
        severity="warning",
        retry_count=0,
    )
    assert decision == "no_op"


@pytest.mark.xfail(strict=True, reason="Story 38.2 — RecoveryPolicy not yet implemented")
def test_recovery_policy_has_max_retries_config() -> None:
    """RecoveryPolicy must expose max_retries_per_task configuration."""
    from registry_state.domain.failure_detection import RecoveryPolicy

    default_policy = RecoveryPolicy()
    assert hasattr(default_policy, "max_retries")
    assert default_policy.max_retries > 0

    custom_policy = RecoveryPolicy(max_retries=5)
    assert custom_policy.max_retries == 5


# ---------------------------------------------------------------------------
# xfail contract tests — RecoveryExecutor (Story 38.3)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 38.3 — RecoveryExecutor not yet implemented")
def test_recovery_executor_class_exists() -> None:
    """RecoveryExecutor class must exist in failure_detection module."""
    from registry_state.domain.failure_detection import RecoveryExecutor

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    executor = RecoveryExecutor(clock=clock)
    assert executor is not None


@pytest.mark.xfail(strict=True, reason="Story 38.3 — RecoveryExecutor not yet implemented")
def test_recovery_executor_auto_retry_emits_event() -> None:
    """RecoveryExecutor auto_retry action must emit a task.auto_retry event."""
    from registry_state.domain.failure_detection import RecoveryExecutor
    from registry_state.adapters.event_log import InMemoryEventLogWriter

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    executor = RecoveryExecutor(clock=clock)
    writer = InMemoryEventLogWriter()

    envelope = executor.execute_auto_retry(
        writer=writer,
        task_id="t-01923abc7000",
        from_status="failed",
        retry_count=1,
    )

    assert envelope is not None
    assert envelope.type == "task.auto_retry"


@pytest.mark.xfail(strict=True, reason="Story 38.3 — RecoveryExecutor not yet implemented")
def test_recovery_executor_auto_stop_emits_event() -> None:
    """RecoveryExecutor auto_stop action must emit a task.auto_stop event."""
    from registry_state.domain.failure_detection import RecoveryExecutor
    from registry_state.adapters.event_log import InMemoryEventLogWriter

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    executor = RecoveryExecutor(clock=clock)
    writer = InMemoryEventLogWriter()

    envelope = executor.execute_auto_stop(
        writer=writer,
        task_id="t-01923abc7000",
        from_status="failed",
        reason="max_retries_exceeded",
    )

    assert envelope is not None
    assert envelope.type == "task.auto_stop"


# ---------------------------------------------------------------------------
# xfail contract tests — event type registration (Story 38.2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 38.2 — task.auto_retry event not registered")
def test_task_auto_retry_event_registered() -> None:
    """The ``task.auto_retry`` event type must be registered in schema registry."""
    from events.schema_registry import REGISTRY

    assert ("task.auto_retry", "1.1.0") in REGISTRY


@pytest.mark.xfail(strict=True, reason="Story 38.2 — task.auto_stop event not registered")
def test_task_auto_stop_event_registered() -> None:
    """The ``task.auto_stop`` event type must be registered in schema registry."""
    from events.schema_registry import REGISTRY

    assert ("task.auto_stop", "1.1.0") in REGISTRY
