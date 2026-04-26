"""registry_state.domain — domain logic for the registry-state service.

Story 2.5 ships:
  - MaterializerError: typed exception for state-transition failures.
  - Materializer: event-log → SQLite state dispatch core.
  - event_types: 4 payload models + schema-registry registrations.
  - handlers: 4 state-transition handler functions.

Story 2.10 adds:
  - 4 failure-detection payload models (service.crashed,
    session.heartbeat_timeout, sink.delivery_failed, task.stop_requested).
  - 4 ``emit_*`` async emission primitives (failure_detection module).
  - HeartbeatMonitor + SinkFailureTracker in-memory detection helpers.
"""

from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (
    ServiceCrashedPayload,
    SessionHeartbeatTimeoutPayload,
    SinkDeliveryFailedPayload,
    TaskStopRequestedPayload,
)
from registry_state.domain.failure_detection import (
    HeartbeatMonitor,
    SinkFailureTracker,
    emit_service_crashed,
    emit_session_heartbeat_timeout,
    emit_sink_delivery_failed,
    emit_task_stop_requested,
)

__all__ = [
    "HeartbeatMonitor",
    "MaterializerError",
    "ServiceCrashedPayload",
    "SessionHeartbeatTimeoutPayload",
    "SinkDeliveryFailedPayload",
    "SinkFailureTracker",
    "TaskStopRequestedPayload",
    "emit_service_crashed",
    "emit_session_heartbeat_timeout",
    "emit_sink_delivery_failed",
    "emit_task_stop_requested",
]
