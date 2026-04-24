"""registry-state — Event-sourced task + session registry (single writer per FR26; SQLite WAL materialized state; event log is source of truth).

Story 2.3 ships: SQLAlchemy 2.x schema (5 tables + 6 indexes) + async SQLite engine
factory + Alembic initial migration. Real logic arrives in:
  - Story 2.4: event-log writer (JSONL append)  ← this version
  - Story 2.5: materializer (subscriber → SQLite mutations)
  - Story 2.6: snapshot capture + replay
  - Story 2.7: idempotency-cache TTL sweep

Public surface re-exported here:
  - ORM models: Base, Task, SessionRow (= Session), Event, IdempotencyCache, Snapshot
  - Engine factory: create_engine, get_session
  - Event-log writer: EventLogWriter, current_day_path, read_log_lines

``Session`` is re-exported as ``SessionRow`` to avoid clashing with
SQLAlchemy's own ``Session`` class in downstream code.
"""

from registry_state.adapters.event_log import (
    EventLogWriter,
    current_day_path,
    read_log_lines,
)
from registry_state.adapters.sqlite_store import create_engine, get_session
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

__version__ = "0.3.0"

__all__ = [
    "Base",
    "Event",
    "EventLogWriter",
    "IdempotencyCache",
    "SessionRow",
    "Snapshot",
    "Task",
    "create_engine",
    "current_day_path",
    "get_session",
    "read_log_lines",
]
