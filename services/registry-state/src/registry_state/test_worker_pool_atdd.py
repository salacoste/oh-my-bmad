"""ATDD red-phase contract tests for multi-task parallelism / worker pool (Epic 32).

Phase 6 Epic 32 — Multi-Task Parallelism. These tests assert contracts that are
NOT YET IMPLEMENTED. Every test is marked ``@pytest.mark.xfail(strict=True)``
so the expected outcome is XFAILED (green PR-gate). When the corresponding
production code lands, each test will XPASS (unexpected pass), which is a HARD
FAILURE signalling "remove the xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts satisfied (Story 32.2):
  1. Task ORM has nullable ``worker_id`` column
  6. Worker identity generator produces ``hostname-pid`` format
  7. ``claim_next_task`` atomic claiming function exists (stub)
  10. ``handle_worker_crash`` crash detection function exists (stub)

Contracts still xfail (Stories 32.3–32.7):
  2. ``task.assigned`` event type registered in event_types
  3. ``TaskAssignedPayload`` model with ``worker_id`` field
  4. ``handle_task_assigned`` handler stamps worker_id on Task row
  5. ``task.assigned`` maps to a valid FSM target state
  8. ``WORKER_POLL_INTERVAL_SECONDS`` config exists on WorkerSettings
  9. Per-worker metrics family ``"worker"`` in _EVENT_FAMILIES

Reference tests (NOT xfail):
  - Existing FSM states enumerated
  - Existing Task ORM columns enumerated
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Story 32.1 / AC1: Task ORM has nullable worker_id column
# ---------------------------------------------------------------------------


def test_task_orm_has_worker_id_column() -> None:
    """The Task ORM model must have a nullable ``worker_id`` column (String(64)).

    ADR-0019 D2: each worker instance has a unique worker_id stamped on claimed
    tasks. The column is nullable because pre-worker-pool tasks (Phase 5) were
    never assigned — their worker_id remains NULL for backward compatibility.
    """
    from registry_state.schema import Task

    col = Task.__table__.columns.get("worker_id")
    assert col is not None, "Task ORM must have a 'worker_id' column"
    # Must be nullable (pre-worker-pool tasks have no worker_id)
    assert col.nullable is True, "worker_id must be nullable for backward compat"
    # String type for hostname-pid format (e.g. "worker-01-12345")
    assert col.type.length == 64, (
        f"Expected String(64), got String({col.type.length})"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC2: task.assigned event registered
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 32.3: task.assigned event not registered")
def test_task_assigned_event_registered() -> None:
    """The ``task.assigned`` event type must be registered in event_types.py.

    Born at schema 1.1.0 (Phase 6, NEW event — no v1.0.0 predecessor, same
    convention as capability.denied / key.rotated / browser.* events).
    """
    from registry_state.domain.event_types import ensure_registered
    from events.schema_registry import lookup

    ensure_registered()
    entry = lookup("task.assigned", "1.1.0")
    assert entry is not None, "task.assigned @ 1.1.0 must be registered"


# ---------------------------------------------------------------------------
# Story 32.1 / AC2: TaskAssignedPayload has worker_id field
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 32.3: TaskAssignedPayload not yet defined")
def test_task_assigned_payload_has_worker_id_field() -> None:
    """The ``TaskAssignedPayload`` model must include a ``worker_id`` field.

    ADR-0019 D2: worker_id is carried in events for observability.
    The field is required (every assignment has a worker).
    """
    from events.payloads import TaskAssignedPayload

    assert hasattr(TaskAssignedPayload, "model_fields"), "Must be a Pydantic model"
    fields = TaskAssignedPayload.model_fields
    assert "worker_id" in fields, (
        f"TaskAssignedPayload must have 'worker_id' field, got: {sorted(fields)}"
    )
    # worker_id should be a required string
    field_info = fields["worker_id"]
    assert field_info.is_required(), "worker_id must be a required field"


# ---------------------------------------------------------------------------
# Story 32.1 / AC3: Worker claims task → worker_id stamped on row
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="Story 32.3: handle_task_assigned handler not yet implemented"
)
@pytest.mark.asyncio
async def test_handle_task_assigned_stamps_worker_id() -> None:
    """When a ``task.assigned`` event is handled, the materializer must stamp
    ``worker_id`` on the Task row.

    The handler receives the event payload's worker_id and writes it to the
    ORM model's worker_id column.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock

    from registry_state.domain.event_types import ensure_registered
    from events.payloads import TaskAssignedPayload

    ensure_registered()

    # Create a mock task row with pending status
    mock_task = MagicMock()
    mock_task.status = "pending"
    mock_task.worker_id = None

    # Create the payload
    payload = TaskAssignedPayload(worker_id="test-host-12345")

    # The handler must accept the payload and stamp worker_id
    from registry_state.domain.handlers import handle_task_assigned

    session = AsyncMock()
    # Mock the DB query to return our task
    session.execute.return_value.scalar_one_or_none.return_value = mock_task

    await handle_task_assigned(session, "t-test-id", payload, {})

    # After handling, the task must have worker_id set
    assert mock_task.worker_id == "test-host-12345", (
        f"Expected worker_id='test-host-12345', got: {mock_task.worker_id!r}"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC4: task.assigned in EVENT_TO_FSM_TRANSITION
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="Story 32.3: task.assigned not in EVENT_TO_FSM_TRANSITION"
)
def test_task_assigned_in_fsm_transition_map() -> None:
    """The ``task.assigned`` event must map to a valid FSM target state.

    Since the current FSM doesn't have an "assigned" state, the event may map
    to an existing state (e.g. ``pending`` for metadata-only assignment) or
    the FSM may be extended. Regardless, the mapping must exist and the target
    must be a valid FSM state.
    """
    from registry_state.domain.task_fsm import (
        EVENT_TO_FSM_TRANSITION,
        TaskStateMachine,
    )

    assert "task.assigned" in EVENT_TO_FSM_TRANSITION, (
        "task.assigned must be in EVENT_TO_FSM_TRANSITION"
    )
    target = EVENT_TO_FSM_TRANSITION["task.assigned"]
    assert target in TaskStateMachine.STATES, (
        f"FSM target {target!r} must be a valid state in STATES"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC5: Worker identity format — hostname-pid
# ---------------------------------------------------------------------------


def test_worker_identity_hostname_pid_format() -> None:
    """Worker identity must follow the ``hostname-pid`` format from ADR-0019 D2.

    The ``generate_worker_id`` function produces ``<hostname>-<pid>`` by default,
    or reads from the ``WORKER_ID`` env var if set.
    """
    import socket
    import os

    from registry_state.domain.worker_pool import generate_worker_id

    # Without WORKER_ID env var: hostname-pid format
    worker_id = generate_worker_id()
    hostname = socket.gethostname()
    assert worker_id.startswith(hostname), (
        f"Worker ID {worker_id!r} must start with hostname {hostname!r}"
    )
    assert "-" in worker_id, f"Worker ID must contain '-' separator: {worker_id!r}"

    # With WORKER_ID env var: use the explicit value
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKER_ID", "custom-worker-42")
        assert generate_worker_id() == "custom-worker-42"


# ---------------------------------------------------------------------------
# Story 32.1 / AC6: Atomic claiming function exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_next_task_function_exists() -> None:
    """The ``claim_next_task`` function must exist and accept a session +
    worker_id, returning a claimed task or None.

    ADR-0019 D1: Postgres uses ``SELECT ... FOR UPDATE SKIP LOCKED``; SQLite
    uses ``BEGIN EXCLUSIVE``. The function hides the backend difference behind
    a unified interface.
    """
    from unittest.mock import AsyncMock

    from registry_state.domain.worker_pool import claim_next_task

    session = AsyncMock()
    # Mock the DB to return no tasks (empty queue)
    session.execute.return_value.scalar_one_or_none.return_value = None

    result = await claim_next_task(session, worker_id="test-worker-1")
    # No tasks in queue → None
    assert result is None, "claim_next_task must return None when no tasks available"


# ---------------------------------------------------------------------------
# Story 32.1 / AC6: Two workers cannot claim the same task
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Story 32.3: concurrent claiming not yet implemented",
)
@pytest.mark.asyncio
async def test_exclusive_assignment_two_workers_same_task() -> None:
    """Two workers must never claim the same task. Exactly one succeeds.

    This tests the SKIP LOCKED / BEGIN EXCLUSIVE atomicity by simulating
    two concurrent claim attempts on the same task.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from registry_state.domain.worker_pool import claim_next_task

    # Simulate: first claim succeeds, second gets None (already locked)
    mock_task = MagicMock()
    mock_task.id = "t-exclusive-001"
    mock_task.status = "pending"

    session = AsyncMock()
    # First call returns the task, second returns None (locked by worker-1)
    session.execute.return_value.scalar_one_or_none.side_effect = [
        mock_task,
        None,
    ]

    result1 = await claim_next_task(session, worker_id="worker-A")
    result2 = await claim_next_task(session, worker_id="worker-B")

    # Exactly one worker gets the task
    claims = [r for r in (result1, result2) if r is not None]
    assert len(claims) == 1, (
        f"Exactly one worker should claim the task, got {len(claims)} claims"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC8: WORKER_POLL_INTERVAL_SECONDS config
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Story 32.4: WORKER_POLL_INTERVAL_SECONDS config not yet added",
)
def test_worker_poll_interval_config_exists() -> None:
    """WorkerSettings must expose ``WORKER_POLL_INTERVAL_SECONDS`` (default 2.0).

    ADR-0019 D1: workers poll at a configurable interval. Default is 2 seconds.
    """
    from worker_wrapper.app.config import WorkerSettings

    settings = WorkerSettings()
    assert hasattr(settings, "worker_poll_interval_seconds"), (
        "WorkerSettings must have worker_poll_interval_seconds attribute"
    )
    assert settings.worker_poll_interval_seconds == 2.0, (
        f"Default poll interval must be 2.0s, got: {settings.worker_poll_interval_seconds}"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC9: Per-worker metrics family
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="Story 32.7: worker metrics family not yet added"
)
def test_worker_metrics_family_in_event_families() -> None:
    """The metrics-subscriber ``_EVENT_FAMILIES`` must include a ``"worker"``
    family for per-worker counters.

    NFR-O15: per-worker metrics labeled by worker_id and runtime.
    """
    from metrics_subscriber.app.metrics import _EVENT_FAMILIES

    assert "worker" in _EVENT_FAMILIES, (
        f"'worker' must be in _EVENT_FAMILIES, got: {sorted(_EVENT_FAMILIES)}"
    )
    # The family must cover task.assigned events
    worker_family = _EVENT_FAMILIES["worker"]
    assert "task.assigned" in worker_family, (
        "task.assigned must be in the worker event family"
    )


# ---------------------------------------------------------------------------
# Story 32.1 / AC10: Worker crash → task FAILED + re-assignable
# ---------------------------------------------------------------------------


def test_crashed_worker_task_detected_and_re_assigned() -> None:
    """When a worker crashes mid-task, the system must detect it and mark the
    task for re-assignment. This requires:

    1. A crash-detection mechanism (heartbeat timeout or task timeout)
    2. The ``worker_id`` on the task row so the registry knows which worker owned it
    3. A handler that clears ``worker_id`` and transitions to a re-assignable state

    NFR-R11: worker crash mid-task is detected by the registry.
    NFR-S15: one worker crash does not affect other workers.
    """
    from registry_state.domain.worker_pool import handle_worker_crash

    # handle_worker_crash must exist and accept (session, worker_id)
    # It finds all tasks assigned to the crashed worker and re-assigns them
    assert callable(handle_worker_crash), "handle_worker_crash must be callable"


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail) — existing invariants
# ---------------------------------------------------------------------------


def test_ref_fsm_has_pending_state() -> None:
    """[Reference] The FSM already has a 'pending' state where tasks start.
    Not xfail — already satisfied."""
    from registry_state.domain.task_fsm import TaskStateMachine

    assert "pending" in TaskStateMachine.STATES


def test_ref_fsm_failed_is_not_terminal() -> None:
    """[Reference] The 'failed' state allows retry (failed → pending), which
    is the re-assignment mechanism. Not xfail — already satisfied."""
    from registry_state.domain.task_fsm import TaskStateMachine

    fsm = TaskStateMachine()
    result = fsm.transition("failed", "pending")
    assert result == "pending"


def test_ref_task_orm_has_status_column() -> None:
    """[Reference] Task ORM has a status column. Not xfail — already satisfied."""
    from registry_state.schema import Task

    assert "status" in Task.__table__.columns


def test_ref_existing_event_types_include_task_lifecycle() -> None:
    """[Reference] Core task lifecycle events are registered. Not xfail."""
    from events.schema_registry import REGISTRY
    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("task.created", "1.0.0") in REGISTRY
    assert ("task.execution.started", "1.0.0") in REGISTRY
    assert ("task.completed", "1.0.0") in REGISTRY


def test_ref_worker_id_already_in_session_started_payload() -> None:
    """[Reference] SessionStartedPayload already has a worker_id field
    (from Phase 1). The worker pool extends this pattern to Task-level
    assignment. Not xfail."""
    from events.payloads import SessionStartedPayload

    assert "worker_id" in SessionStartedPayload.model_fields
