"""registry-state — Event-sourced task + session registry (single writer per FR26; SQLite WAL materialized state; event log is source of truth).

Story 2.3 ships: SQLAlchemy 2.x schema (5 tables + 6 indexes) + async SQLite engine
factory + Alembic initial migration.
Story 2.4 ships: event-log writer (JSONL append).
Story 2.5 ships: materializer (subscriber → SQLite mutations) + 4 event-type payload
models + subscriber loop entrypoint.

Public surface re-exported here:
  - ORM models: Base, Task, SessionRow (= Session), Event, IdempotencyCache, Snapshot
  - Engine factory: create_engine, get_session
  - Event-log writer: EventLogWriter, current_day_path, read_log_lines
  - Materializer: Materializer, MaterializerError
  - Payload models: TaskCreatedPayload, TaskPlanningStartedPayload,
                    TaskPlanReadyPayload, TaskExecutionStartedPayload
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
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
)
from registry_state.domain.materializer import Materializer
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

__version__ = "0.4.0"

__all__ = [
    "Base",
    "Event",
    "EventLogWriter",
    "IdempotencyCache",
    "Materializer",
    "MaterializerError",
    "SessionRow",
    "Snapshot",
    "Task",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "create_engine",
    "current_day_path",
    "get_session",
    "main",
    "read_log_lines",
    "recover_all_logs",
    "run_subscriber",
]
