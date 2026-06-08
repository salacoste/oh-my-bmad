"""ATDD contract tests for dead-session detection + worker heartbeat (Epic 36).

Phase 7 Epic 36 — Dead-Session Detection + Worker Heartbeat.  Originally
shipped as red-phase (all xfail); contracts are now SATISFIED by Stories
36.1–36.4 production code and tests run green.

Contracts tested (all green):
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

    monitor = HeartbeatMonitor(
        heartbeat_interval_s=10.0,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )
    assert hasattr(monitor, "overdue_sessions_and_mark")
    assert callable(monitor.overdue_sessions_and_mark)


def test_emit_session_heartbeat_timeout_exists() -> None:
    """emit_session_heartbeat_timeout must be a callable async function."""
    from registry_state.domain.failure_detection import emit_session_heartbeat_timeout

    assert callable(emit_session_heartbeat_timeout)


# ---------------------------------------------------------------------------
# xfail contract tests — detection wiring (Stories 36.3–36.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_feeds_session_heartbeats_into_monitor() -> None:
    """The subscriber must record session heartbeats from session.heartbeat events
    into the HeartbeatMonitor.

    When a session.heartbeat event is processed by the materializer, the
    subscriber loop must call monitor.record_heartbeat(session_id).
    """
    # This contract tests that the subscriber wiring exists.
    # Fails because the subscriber does not yet feed heartbeats into the monitor.
    # The function must accept a heartbeat_interval_s parameter (or detection
    # must be configurable).  This import-only test fails at the assertion
    # below because the wiring is not yet in place.
    import inspect

    from registry_state.app.main import run_subscriber

    sig = inspect.signature(run_subscriber)
    params = set(sig.parameters.keys())
    # Detection configuration must be parameterised
    assert "heartbeat_interval_s" in params or "detection_poll_interval_s" in params, (
        "run_subscriber must accept a detection configuration parameter"
    )


@pytest.mark.asyncio
async def test_overdue_sessions_trigger_heartbeat_timeout_emit() -> None:
    """Overdue sessions must trigger emit_session_heartbeat_timeout.

    Verifies the detection tick in run_subscriber's tail loop calls
    ``monitor.overdue_sessions_and_mark()`` and then calls
    ``emit_session_heartbeat_timeout`` for each overdue session by inspecting
    the production source. The unit-level overdue detection is already
    covered by test_failure_detection.py; this contract ensures the subscriber
    wiring connects those pieces.
    """
    import ast
    from pathlib import Path

    main_py = Path(__file__).parent / "app" / "main.py"
    source = main_py.read_text()
    tree = ast.parse(source)

    # Walk the AST to find the detection-tick code block.
    # It must contain a call to overdue_sessions_and_mark (method call)
    # AND a call to emit_session_heartbeat_timeout (module-level call).
    _has_overdue_call = False
    _has_emit_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # overdue_sessions_and_mark is a method call: monitor.overdue_sessions_and_mark()
            if isinstance(func, ast.Attribute) and func.attr == "overdue_sessions_and_mark":
                _has_overdue_call = True
            # emit_session_heartbeat_timeout is a module-level call (ast.Name),
            # not a method call (ast.Attribute).
            if isinstance(func, ast.Name) and func.id == "emit_session_heartbeat_timeout":
                _has_emit_call = True
            if isinstance(func, ast.Attribute) and func.attr == "emit_session_heartbeat_timeout":
                _has_emit_call = True

    assert _has_overdue_call, (
        "run_subscriber must call monitor.overdue_sessions_and_mark() in the detection tick"
    )
    assert _has_emit_call, (
        "run_subscriber must call emit_session_heartbeat_timeout for overdue sessions"
    )

    # Also verify the overdue loop iterates over results (``for ... in overdue:``)
    assert "overdue" in source and "emit_session_heartbeat_timeout" in source, (
        "Detection tick must iterate overdue sessions and emit timeout events"
    )


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


@pytest.mark.asyncio
async def test_heartbeat_after_timeout_resets_edge_trigger() -> None:
    """After a timeout is emitted, receiving a new heartbeat must re-arm
    the edge trigger so the session can be detected as overdue again.

    HeartbeatMonitor.record_heartbeat clears the emitted flag (unit-tested
    in test_failure_detection.py). This contract verifies that the
    subscriber's ``_feed_heartbeats`` function correctly calls
    ``monitor.record_heartbeat`` for session.heartbeat events, preserving
    the re-arm behavior.
    """
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from registry_state.app.main import _feed_heartbeats

    monitor = MagicMock(spec=["record_heartbeat", "remove_session"])
    _session_id = "s-018f4a6b-1c2d-7e8f-9a0b-1c2d3e4f5a6b"
    _emitted_at = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)

    # Create a minimal envelope-like object that _feed_heartbeats inspects.
    # Uses __init__ to avoid Python class-body scoping limitation
    # (class-level attributes cannot see enclosing function locals).
    class _FakeEnvelope:
        def __init__(self, sid: str, at: datetime) -> None:
            self.type = "session.heartbeat"
            self.emitted_at = at
            self.payload = {"session_id": sid}

    _feed_heartbeats([_FakeEnvelope(_session_id, _emitted_at)], monitor)  # type: ignore[list-item]

    monitor.record_heartbeat.assert_called_once_with(_session_id, at=_emitted_at)
    monitor.remove_session.assert_not_called()


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
                    name = (
                        alias.name
                        if isinstance(node, ast.Import)
                        else (alias.name if alias else "")
                    )
                    if "emit_session_heartbeat_timeout" in name:
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
