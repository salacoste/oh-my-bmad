"""ATDD red-phase contract tests for stale-task alerting (Epic 37).

Phase 7 Epic 37 — Stale Task Alerting.  These tests assert contracts that
are NOT YET IMPLEMENTED.  Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate).  When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a
HARD FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts tested (all xfail):
  1. TaskStaleWarningPayload validates correctly with required fields
  2. TaskStaleCriticalPayload validates correctly with required fields
  3. task.stale_warning event type registered at v1.1.0
  4. task.stale_critical event type registered at v1.1.0
  5. StaleTaskDetector.overdue_tasks_and_mark() method exists
  6. emit_task_stale_warning function exists and is callable
  7. emit_task_stale_critical function exists and is callable
  8. Stale task emission has production caller path in subscriber loop

Reference tests (NOT xfail):
  - Task FSM non-terminal states are well-defined
  - tasks table has updated_at column (staleness clock)
  - ix_tasks_status_updated_at index exists for efficient stale queries
"""

from __future__ import annotations

import pytest

from events import FROZEN_EPOCH, FrozenClock


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail) — existing infrastructure that Epic 37 builds on
# ---------------------------------------------------------------------------


def test_task_fsm_has_non_terminal_states() -> None:
    """The task FSM must define non-terminal states suitable for stale detection.

    Non-terminal states are those with at least one outgoing transition
    (i.e. the task can still move forward).  Terminal states (completed,
    stopped) have empty transition sets.
    """
    from registry_state.domain.task_fsm import TaskStateMachine

    non_terminal = {
        state
        for state, targets in TaskStateMachine.TRANSITIONS.items()
        if targets  # has outgoing transitions → non-terminal
    }
    assert len(non_terminal) >= 5
    assert "pending" in non_terminal
    assert "executing" in non_terminal


def test_tasks_table_has_updated_at_column() -> None:
    """The tasks table must have an ``updated_at`` column for staleness tracking."""
    from registry_state.schema import Task

    assert hasattr(Task, "updated_at")


def test_stale_query_index_exists() -> None:
    """The ``ix_tasks_status_updated_at`` composite index must exist for stale queries."""
    from sqlalchemy import inspect as sa_inspect

    from registry_state.schema import Task

    # Check that the index exists on the Task table.
    indexes = [idx.name for idx in Task.__table__.indexes]
    assert "ix_tasks_status_updated_at" in indexes


# ---------------------------------------------------------------------------
# xfail contract tests — payload models (Story 37.1)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
def test_task_stale_warning_payload_validates_correctly() -> None:
    """TaskStaleWarningPayload must validate with task_id, status,
    stale_since, stale_duration_s, severity, and threshold_s.
    """
    from datetime import UTC, datetime

    from events.payloads import TaskStaleWarningPayload

    p = TaskStaleWarningPayload(
        task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
        status="executing",
        stale_since=datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC),
        stale_duration_s=1800.0,
        severity="warning",
        threshold_s=600.0,
    )
    assert p.task_id == "t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c"
    assert p.status == "executing"
    assert p.severity == "warning"
    assert p.threshold_s == 600.0


@pytest.mark.xfail(strict=True)
def test_task_stale_critical_payload_validates_correctly() -> None:
    """TaskStaleCriticalPayload must validate with the same fields as warning
    but with severity='critical'.
    """
    from datetime import UTC, datetime

    from events.payloads import TaskStaleCriticalPayload

    p = TaskStaleCriticalPayload(
        task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
        status="pending",
        stale_since=datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC),
        stale_duration_s=3600.0,
        severity="critical",
        threshold_s=900.0,
    )
    assert p.severity == "critical"


@pytest.mark.xfail(strict=True)
def test_task_stale_warning_payload_rejects_naive_datetime() -> None:
    """stale_since must be AwareDatetime — naive timestamps rejected."""
    from datetime import datetime

    from events.payloads import TaskStaleWarningPayload

    with pytest.raises(Exception):  # ValidationError
        TaskStaleWarningPayload(
            task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
            status="executing",
            stale_since=datetime(2026, 6, 8, 12, 0, 0),  # naive!
            stale_duration_s=1800.0,
            severity="warning",
            threshold_s=600.0,
        )


# ---------------------------------------------------------------------------
# xfail contract tests — event registration (Story 37.1)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
def test_task_stale_warning_event_registered() -> None:
    """The ``task.stale_warning`` event type must be registered at v1.1.0."""
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("task.stale_warning", "1.1.0") in REGISTRY


@pytest.mark.xfail(strict=True)
def test_task_stale_critical_event_registered() -> None:
    """The ``task.stale_critical`` event type must be registered at v1.1.0."""
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("task.stale_critical", "1.1.0") in REGISTRY


# ---------------------------------------------------------------------------
# xfail contract tests — detector class (Story 37.2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
def test_stale_task_detector_overdue_tasks_and_mark_exists() -> None:
    """StaleTaskDetector must have an overdue_tasks_and_mark method."""
    from registry_state.domain.failure_detection import StaleTaskDetector

    detector = StaleTaskDetector(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    assert hasattr(detector, "overdue_tasks_and_mark")
    assert callable(detector.overdue_tasks_and_mark)


# ---------------------------------------------------------------------------
# xfail contract tests — emission functions (Story 37.2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
def test_emit_task_stale_warning_exists() -> None:
    """emit_task_stale_warning must be a callable async function."""
    from registry_state.domain.failure_detection import emit_task_stale_warning

    assert callable(emit_task_stale_warning)


@pytest.mark.xfail(strict=True)
def test_emit_task_stale_critical_exists() -> None:
    """emit_task_stale_critical must be a callable async function."""
    from registry_state.domain.failure_detection import emit_task_stale_critical

    assert callable(emit_task_stale_critical)


# ---------------------------------------------------------------------------
# xfail contract tests — production wiring (Story 37.3)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_stale_detection_has_production_caller() -> None:
    """Stale-task emission must have at least one production caller
    in the subscriber loop (not just test callers).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent
    callers = []
    for pyfile in src.rglob("*.py"):
        if "test_" in pyfile.name or "failure_detection.py" in pyfile.name:
            continue
        if "__init__.py" in pyfile.name or "__pycache__" in str(pyfile):
            continue
        if "stale_task_atdd" in pyfile.name:
            continue
        try:
            tree = ast.parse(pyfile.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in (
                "StaleTaskDetector",
                "emit_task_stale_warning",
                "emit_task_stale_critical",
            ):
                callers.append((str(pyfile.name), node.id))
    assert len(callers) >= 1, (
        f"Stale-task detection must be imported by ≥1 production module "
        f"(found {len(callers)} callers: {callers})"
    )
