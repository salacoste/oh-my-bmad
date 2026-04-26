"""Failure-detection emission primitives + in-memory tracking helpers (Story 2.10).

This module ships **callable primitives** for the four FR24a failure signals:

  - ``service.crashed``           — :func:`emit_service_crashed`
  - ``session.heartbeat_timeout`` — :func:`emit_session_heartbeat_timeout`
  - ``sink.delivery_failed``      — :func:`emit_sink_delivery_failed`
  - ``task.stop_requested``       — :func:`emit_task_stop_requested`

…plus two in-memory detection helpers used by the polling-loop wiring that
will be added in later epics:

  - :class:`HeartbeatMonitor`   — per-session last-heartbeat tracking with a
                                   ``> 2 × interval`` overdue boundary.
  - :class:`SinkFailureTracker` — per-sink consecutive-failure counter with a
                                   ``>= threshold`` emit gate.

**What this module does NOT do**

* No materializer handlers for any of the 4 new event types — state
  transitions (e.g. ``task.stop_requested`` → ``tasks.status = "stopped"``)
  are explicitly deferred (per AC-5) to Epic 3 (stop dispatch) and Epic 5
  (worker / session lifecycle).
* No background polling loop — the **NFR-R5 60 s detection-to-emission
  SLA** is the contract of the polling-loop wiring (Epic 3 / Epic 5),
  not of this module. This module ships only the synchronous + async
  primitives that those loops will call.
* No process supervision, no Telegram-sink retry counter, no session
  table updates. Those are wired in their respective epics; Story 2.10
  ships the typed-event substrate so those wirings can reference known,
  schema-validated event types.

**Single-writer compliance**: every emission function appends through an
``EventLogWriter`` only — no SQLAlchemy mutation. ``check_single_writer``
remains green for ``services/registry-state``.

**Caller responsibility for ``last_error``**: :func:`emit_sink_delivery_failed`
writes a ``last_error`` string verbatim into the event payload.  The caller
MUST sanitize it: no API tokens, bot secrets, OAuth credentials, or PII.
The ``scan-secrets`` gate runs over committed source — runtime payload
sanitization is an emit-site contract.
"""

from __future__ import annotations

from datetime import datetime

from events import EventEnvelope
from events.clock import Clock
from events.envelope import Actor
from events.ids import new_event_id, new_request_id

from registry_state.adapters.event_log import EventLogWriter
from registry_state.domain.event_types import (
    ServiceCrashedPayload,
    SessionHeartbeatTimeoutPayload,
    SinkDeliveryFailedPayload,
    TaskStopRequestedPayload,
)

# ---------------------------------------------------------------------------
# Emission primitives (AC-3a)
#
# Note on Actor.kind: the spec text in this story uses ``kind="service"`` but
# the canonical ``ActorKind`` Literal in ``packages/events/src/events/envelope.py``
# does not include ``"service"`` (allowed kinds: operator | orchestrator |
# worker | system | clawhip).  ``"system"`` is the closest match and is
# already the convention used by registry-state's own test envelopes
# (see ``test_handlers.py`` etc.).  Widening ``ActorKind`` would touch the
# shared envelope contract and is out of scope for Story 2.10.
# ---------------------------------------------------------------------------


async def emit_service_crashed(
    writer: EventLogWriter,
    *,
    clock: Clock,
    service: str,
    exit_code: int,
    actor_id: str = "registry-state",
) -> EventEnvelope:
    """Emit a ``service.crashed`` event for a supervised process exit.

    Args:
        writer:    Open :class:`EventLogWriter` (single-writer instance).
        clock:     Injected :class:`Clock` for ``emitted_at`` + ``monotonic_ns``.
        service:   Logical service name, e.g. ``"worker-wrapper"``.
        exit_code: Non-zero exit code reported by the supervisor.
        actor_id:  Free-form actor identifier (default: ``"registry-state"``).

    Returns:
        The persisted :class:`EventEnvelope`.
    """
    payload = ServiceCrashedPayload(service=service, exit_code=exit_code)
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="service.crashed",
        schema_version="1.0.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        request_id=new_request_id(clock=clock),
        parent_event_id=None,
    )
    await writer.append(envelope)
    return envelope


async def emit_session_heartbeat_timeout(
    writer: EventLogWriter,
    *,
    clock: Clock,
    session_id: str,
    task_id: str,
    last_heartbeat_at: datetime,
    timeout_threshold_s: float,
    actor_id: str = "registry-state",
) -> EventEnvelope:
    """Emit a ``session.heartbeat_timeout`` event for an overdue session.

    Args:
        writer:              Open :class:`EventLogWriter`.
        clock:               Injected :class:`Clock`.
        session_id:          ``s-<uuidv7>`` of the overdue session.
        task_id:             ``t-<uuidv7>`` of the owning task.
        last_heartbeat_at:   UTC timestamp of the session's last heartbeat.
        timeout_threshold_s: Configured ``2 × heartbeat_interval_s`` boundary.
        actor_id:            Free-form actor identifier.
    """
    payload = SessionHeartbeatTimeoutPayload(
        session_id=session_id,
        task_id=task_id,
        last_heartbeat_at=last_heartbeat_at,
        timeout_threshold_s=timeout_threshold_s,
    )
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="session.heartbeat_timeout",
        schema_version="1.0.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        request_id=new_request_id(clock=clock),
        parent_event_id=None,
    )
    await writer.append(envelope)
    return envelope


async def emit_sink_delivery_failed(
    writer: EventLogWriter,
    *,
    clock: Clock,
    sink_name: str,
    consecutive_failures: int,
    last_error: str | None = None,
    actor_id: str = "registry-state",
) -> EventEnvelope:
    """Emit a ``sink.delivery_failed`` event after ``consecutive_failures``.

    The caller MUST sanitize ``last_error`` before passing it: no tokens,
    secrets, or PII. Sanitization is *not* enforced at the emission boundary
    — the secret-hygiene gate scans committed source, not runtime payloads.

    Args:
        writer:               Open :class:`EventLogWriter`.
        clock:                Injected :class:`Clock`.
        sink_name:            Logical sink identifier, e.g. ``"telegram"``.
        consecutive_failures: Current streak count (always ``>= 3`` when the
                              ``SinkFailureTracker.should_emit`` gate fires).
        last_error:           Sanitized error description, or ``None``.
        actor_id:             Free-form actor identifier.
    """
    payload = SinkDeliveryFailedPayload(
        sink_name=sink_name,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
    )
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="sink.delivery_failed",
        schema_version="1.0.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        request_id=new_request_id(clock=clock),
        parent_event_id=None,
    )
    await writer.append(envelope)
    return envelope


async def emit_task_stop_requested(
    writer: EventLogWriter,
    *,
    clock: Clock,
    task_id: str,
    actor_id: str,
) -> EventEnvelope:
    """Emit a ``task.stop_requested`` event for an operator-initiated stop.

    ``actor_id`` is REQUIRED here (no default) — the operator who issued the
    stop must be identified, e.g. ``"telegram:12345678"`` or ``"console"``.

    Args:
        writer:   Open :class:`EventLogWriter`.
        clock:    Injected :class:`Clock`.
        task_id:  ``t-<uuidv7>`` of the task being stopped.
        actor_id: Identity of the operator that requested the stop.
    """
    payload = TaskStopRequestedPayload(task_id=task_id, actor_id=actor_id)
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="task.stop_requested",
        schema_version="1.0.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        request_id=new_request_id(clock=clock),
        parent_event_id=None,
    )
    await writer.append(envelope)
    return envelope


# ---------------------------------------------------------------------------
# HeartbeatMonitor (AC-3b)
# ---------------------------------------------------------------------------


class HeartbeatMonitor:
    """In-memory tracker for per-session last-heartbeat timestamps.

    The overdue boundary uses **strict greater-than** ``2 × heartbeat_interval_s``
    — at exactly ``2×`` the session is NOT yet overdue. This matches the
    epic AC: "overdue by MORE THAN 2× its configured interval".

    Thread-safety: state is a plain ``dict[str, datetime]``.  All callers
    must run on a single asyncio loop (no cross-thread access).  No locks.
    """

    def __init__(self, *, heartbeat_interval_s: float, clock: Clock) -> None:
        if heartbeat_interval_s <= 0:
            raise ValueError(f"heartbeat_interval_s must be positive; got {heartbeat_interval_s}")
        self._interval_s = heartbeat_interval_s
        self._clock = clock
        self._last_seen: dict[str, datetime] = {}

    @property
    def timeout_threshold_s(self) -> float:
        """Convenience: the ``2 × interval`` value used in overdue checks."""
        return 2 * self._interval_s

    def record_heartbeat(self, session_id: str) -> None:
        """Record a fresh heartbeat for *session_id* at the current clock time."""
        self._last_seen[session_id] = self._clock.now()

    def remove_session(self, session_id: str) -> None:
        """Stop tracking *session_id*. No-op if not currently tracked."""
        self._last_seen.pop(session_id, None)

    def overdue_sessions(self) -> list[tuple[str, datetime]]:
        """Return ``[(session_id, last_heartbeat_at)]`` for overdue sessions.

        A session is overdue iff
        ``(now - last_heartbeat_at).total_seconds() > 2 * heartbeat_interval_s``.
        At exactly ``2×`` the session is NOT yet overdue (strict ``>``).
        Iteration order matches insertion order (Python 3.7+ dict guarantee).
        """
        now = self._clock.now()
        threshold = self.timeout_threshold_s
        result: list[tuple[str, datetime]] = []
        for session_id, last_at in self._last_seen.items():
            elapsed = (now - last_at).total_seconds()
            if elapsed > threshold:
                result.append((session_id, last_at))
        return result


# ---------------------------------------------------------------------------
# SinkFailureTracker (AC-3c)
# ---------------------------------------------------------------------------


class SinkFailureTracker:
    """Per-sink consecutive-failure counter with an emit-gate threshold.

    ``should_emit(sink_name)`` returns True when the current consecutive
    failure count is ``>= failure_threshold`` (default 3).  ``record_success``
    resets the counter to 0 (an interleaved success ends the streak).
    """

    def __init__(self, *, failure_threshold: int = 3) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1; got {failure_threshold}")
        self._threshold = failure_threshold
        # value is (consecutive_failures, last_error)
        self._state: dict[str, tuple[int, str | None]] = {}

    @property
    def failure_threshold(self) -> int:
        """The configured emit-gate threshold."""
        return self._threshold

    def record_failure(self, sink_name: str, error: str | None = None) -> int:
        """Increment *sink_name*'s counter; return the new consecutive count.

        ``error`` (when provided) is stored as the most-recent ``last_error``
        for that sink and surfaced via :meth:`get_state`.
        """
        prev_count, prev_error = self._state.get(sink_name, (0, None))
        new_count = prev_count + 1
        # Preserve the prior last_error if this call passed None — callers
        # may pass error=None to count a failure whose error string is not
        # available without overwriting the most recent known error.
        new_error = error if error is not None else prev_error
        self._state[sink_name] = (new_count, new_error)
        return new_count

    def record_success(self, sink_name: str) -> None:
        """Reset *sink_name*'s consecutive-failure counter to 0."""
        self._state[sink_name] = (0, None)

    def should_emit(self, sink_name: str) -> bool:
        """Return True when *sink_name*'s consecutive count is at the threshold."""
        count, _ = self._state.get(sink_name, (0, None))
        return count >= self._threshold

    def get_state(self, sink_name: str) -> tuple[int, str | None]:
        """Return ``(consecutive_failures, last_error)`` for *sink_name*."""
        return self._state.get(sink_name, (0, None))


__all__ = [
    "HeartbeatMonitor",
    "SinkFailureTracker",
    "emit_service_crashed",
    "emit_session_heartbeat_timeout",
    "emit_sink_delivery_failed",
    "emit_task_stop_requested",
]
