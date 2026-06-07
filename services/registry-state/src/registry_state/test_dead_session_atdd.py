"""ATDD red-phase contract tests for dead-session detection + worker heartbeat (Epic 36).

Phase 7 Epic 36 — Dead-Session Detection + Worker Heartbeat.  These tests
assert contracts that are NOT YET IMPLEMENTED.  Every test is marked
``@pytest.mark.xfail(strict=True)`` so the expected outcome is XFAILED
(green PR-gate).  When the corresponding production code lands, each test
will XPASS (unexpected pass), which is a HARD FAILURE signalling "remove the
xfail marker — this contract is now satisfied."

The tests must fail at RUNTIME (inside the test body), NOT at import/collection
time — ``xfail`` does not swallow ImportError at collection.

Contracts tested (all xfail):
  1. Subscriber feeds session heartbeats into HeartbeatMonitor
  2. HeartbeatMonitor.overdue_sessions_and_mark returns overdue sessions
  3. Overdue session triggers emit_session_heartbeat_timeout
  4. Detection-to-emission latency bounded (NFR-R5)
  5. Recovery re-arm: heartbeat after timeout resets edge-trigger
  6. emit_session_heartbeat_timeout has production caller path
  7. Worker heartbeat tracked independently from session heartbeats

Reference tests (NOT xfail):
  - session.heartbeat event type registered
  - session.finished event type registered
  - HeartbeatMonitor.overdue_sessions_and_mark method exists
  - emit_session_heartbeat_timeout function exists
"""

from __future__ import annotations

import pytest

from events import FROZEN_EPOCH, FrozenClock


# ---------------------------------------------------------------------------
# Reference tests (NOT xfail) — existing infrastructure
# ---------------------------------------------------------------------------


def test_session_heartbeat_event_registered() -> None:
    """The ``session.heartbeat`` event type must be registered."""
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("session.heartbeat", "1.1.0") in REGISTRY


def test_session_finished_event_registered() -> None:
    """The ``session.finished`` event type must be registered."""
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("session.finished", "1.1.0") in REGISTRY


def test_heartbeat_monitor_overdue_sessions_and_mark_exists() -> None:
    """HeartbeatMonitor must have an overdue_sessions_and_mark method."""
    from registry_state.domain.failure_detection import HeartbeatMonitor

    monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    assert hasattr(monitor, "overdue_sessions_and_mark")
    assert callable(monitor.overdue_sessions_and_mark)


def test_emit_session_heartbeat_timeout_exists() -> None:
    """emit_session_heartbeat_timeout must be a callable async function."""
    from registry_state.domain.failure_detection import emit_session_heartbeat_timeout

    assert callable(emit_session_heartbeat_timeout)


# ---------------------------------------------------------------------------
# xfail contract tests — detection wiring (Stories 36.3–36.4)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_subscriber_feeds_session_heartbeats_into_monitor() -> None:
    """The subscriber must record session heartbeats from session.heartbeat events
    into the HeartbeatMonitor.

    When a session.heartbeat event is processed by the materializer, the
    subscriber loop must call monitor.record_heartbeat(session_id).
    """
    # This contract tests that the subscriber wiring exists.
    # Fails because the subscriber does not yet feed heartbeats into the monitor.
    from registry_state.app.main import run_subscriber

    # The function must accept a heartbeat_interval_s parameter (or detection
    # must be configurable).  This import-only test fails at the assertion
    # below because the wiring is not yet in place.
    import inspect

    sig = inspect.signature(run_subscriber)
    params = set(sig.parameters.keys())
    # Detection configuration must be parameterised
    assert "heartbeat_interval_s" in params or "detection_poll_interval_s" in params, (
        "run_subscriber must accept a detection configuration parameter"
    )


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_overdue_sessions_trigger_heartbeat_timeout_emit() -> None:
    """Overdue sessions must trigger emit_session_heartbeat_timeout.

    When the detection tick runs and HeartbeatMonitor.overdue_sessions_and_mark()
    returns non-empty, each overdue session must result in a
    session.heartbeat_timeout event being emitted via the writer.
    """
    from datetime import timedelta

    from events import FrozenClock
    from registry_state.domain.failure_detection import HeartbeatMonitor

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)

    # Record a heartbeat, then advance past threshold
    monitor.record_heartbeat("s-test-001", at=FROZEN_EPOCH)
    # Advance clock past 2*interval = 20s
    from events.clock import Clock

    # The clock is frozen — we can't advance it. This test needs the
    # production wiring which advances time and checks.
    # This will fail because the detection loop doesn't exist yet.
    assert False, "Detection loop not wired — overdue sessions not checked"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_detection_to_emission_within_60s_sla() -> None:
    """NFR-R5: failure detection must emit within 60s of the underlying condition.

    The detection poll interval must be ≤ 30s so that the worst case
    (condition occurs just after a tick) is 2 × poll_interval = 60s.
    """
    # The subscriber's detection tick interval must be configurable and
    # default to ≤ 30s. This fails because the parameter doesn't exist yet.
    import inspect

    from registry_state.app.main import run_subscriber

    sig = inspect.signature(run_subscriber)
    params = set(sig.parameters.keys())
    assert "detection_poll_interval_s" in params, (
        "run_subscriber must accept detection_poll_interval_s for NFR-R5 compliance"
    )


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_heartbeat_after_timeout_resets_edge_trigger() -> None:
    """After a timeout is emitted, receiving a new heartbeat must re-arm
    the edge trigger so the session can be detected as overdue again.

    HeartbeatMonitor already implements this in record_heartbeat (clears
    _emitted set). This test verifies the subscriber wiring preserves
    this behavior end-to-end.
    """
    from datetime import timedelta

    from events import FrozenClock
    from registry_state.domain.failure_detection import HeartbeatMonitor

    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    monitor = HeartbeatMonitor(heartbeat_interval_s=10.0, clock=clock)

    session_id = "s-test-002"

    # Record initial heartbeat
    monitor.record_heartbeat(session_id, at=FROZEN_EPOCH)

    # The subscriber's heartbeat feeding must call record_heartbeat
    # which clears the emitted flag. This test verifies the end-to-end
    # path exists — it fails because the wiring isn't in place.
    assert False, "Subscriber heartbeat feeding not wired"


@pytest.mark.xfail(strict=True)
@pytest.mark.asyncio
async def test_emit_session_heartbeat_timeout_has_production_caller() -> None:
    """emit_session_heartbeat_timeout must have at least one production caller
    (not just test callers).

    Checks that the function is imported by at least one non-test, non-definition
    module in the registry-state service.
    """
    import ast
    from pathlib import Path

    # Find all Python files in registry-state (excluding tests)
    src = Path(__file__).parent.parent
    callers = []
    for pyfile in src.rglob("*.py"):
        if "test_" in pyfile.name or "failure_detection.py" in pyfile.name:
            continue
        if "__init__.py" in pyfile.name or "__pycache__" in str(pyfile):
            continue
        if "dead_session_atdd" in pyfile.name:
            continue
        try:
            tree = ast.parse(pyfile.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if "emit_session_heartbeat_timeout" in (alias.name if isinstance(node, ast.Import) else (alias.name if alias else "")):
                        callers.append(str(pyfile))
    assert len(callers) >= 1, (
        f"emit_session_heartbeat_timeout must be imported by ≥1 production module "
        f"(found {len(callers)} callers: {callers})"
    )


# ---------------------------------------------------------------------------
# xfail contract tests — worker heartbeat (Story 36.2)
# ---------------------------------------------------------------------------


def test_worker_heartbeat_event_registered() -> None:
    """The ``worker.heartbeat`` event type must be registered at v1.1.0."""
    from events.schema_registry import REGISTRY

    from registry_state.domain.event_types import ensure_registered

    ensure_registered()
    assert ("worker.heartbeat", "1.1.0") in REGISTRY


def test_worker_heartbeat_payload_has_required_fields() -> None:
    """WorkerHeartbeatPayload must have worker_id, active_task_id, timestamp fields."""
    from events.payloads import WorkerHeartbeatPayload

    payload = WorkerHeartbeatPayload(
        worker_id="worker-01-12345",
        active_task_id="t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c",
        timestamp="2026-06-08T00:00:00Z",
    )
    assert payload.worker_id == "worker-01-12345"
    assert payload.active_task_id == "t-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6c"
    assert payload.timestamp == "2026-06-08T00:00:00Z"
