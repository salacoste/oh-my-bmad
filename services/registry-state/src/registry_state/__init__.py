"""registry-state — Event-sourced task + session registry (single writer per FR26; SQLite WAL materialized state; event log is source of truth).

Story 2.3 ships: SQLAlchemy 2.x schema (5 tables + 6 indexes) + async SQLite engine
factory + Alembic initial migration.
Story 2.4 ships: event-log writer (JSONL append).
Story 2.5 ships: materializer (subscriber → SQLite mutations) + 4 event-type payload
models + subscriber loop entrypoint.
Story 2.6 ships: snapshot capture (``SnapshotPolicy``) + snapshot-aware startup
recovery (``restore_state_from_latest_snapshot`` + ``compute_events_max_cursor`` +
``compute_replay_cursor``).

Public surface re-exported here:
  - ORM models: Base, Task, SessionRow (= Session), Event, IdempotencyCache, Snapshot
  - Engine factory: create_engine, get_session
  - Event-log writer: EventLogWriter, current_day_path, read_log_lines
  - Materializer: Materializer, MaterializerError
  - Payload models: TaskCreatedPayload, TaskPlanningStartedPayload,
                    TaskPlanReadyPayload, TaskExecutionStartedPayload
  - Snapshot capture: SnapshotPolicy
  - Recovery (HIGH-RISK, Arch §line-834):
        compute_events_max_cursor, compute_replay_cursor,
        restore_state_from_latest_snapshot
  - Subscriber: run_subscriber, main

``Session`` is re-exported as ``SessionRow`` to avoid clashing with
SQLAlchemy's own ``Session`` class in downstream code.
"""

from registry_state.adapters.event_log import (
    EventLogWriter,
    current_day_path,
    read_log_lines,
    recover_all_logs,
)
from registry_state.adapters.sqlite_store import create_engine, get_session
from registry_state.app.main import main, run_subscriber
from registry_state.domain.errors import MaterializerError
from registry_state.domain.event_types import (  # noqa: F401 — side-effect: register() calls
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskSummaryEmittedPayload,
)
from registry_state.domain.materializer import Materializer
from registry_state.domain.recovery import (
    compute_events_max_cursor,
    compute_replay_cursor,
    restore_state_from_latest_snapshot,
)
from registry_state.domain.snapshots import SnapshotPolicy
from registry_state.schema import (
    Base,
    Event,
    IdempotencyCache,
    Snapshot,
    Task,
)
from registry_state.schema import (
    Session as SessionRow,  # rename to avoid clash with SQLAlchemy Session
)

__version__ = "0.5.0"

__all__ = [
    "Base",
    "Event",
    "EventLogWriter",
    "IdempotencyCache",
    "Materializer",
    "MaterializerError",
    "SessionRow",
    "Snapshot",
    "SnapshotPolicy",
    "Task",
    "TaskApprovalRequestedPayload",
    "TaskBlockerRaisedPayload",
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskSummaryEmittedPayload",
    "compute_events_max_cursor",
    "compute_replay_cursor",
    "create_engine",
    "current_day_path",
    "get_session",
    "main",
    "read_log_lines",
    "recover_all_logs",
    "restore_state_from_latest_snapshot",
    "run_subscriber",
]
