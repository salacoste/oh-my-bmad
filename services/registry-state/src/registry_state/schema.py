"""SQLAlchemy 2.x ORM models for the registry-state SQLite store (Story 2.3).

Scope: schema definition only — 5 tables + 6 indexes. No business logic here.
Business logic arrives in:
  - Story 2.4: event-log writer (JSONL append)
  - Story 2.5: materializer (subscriber → SQLite mutations)
  - Story 2.6: snapshot capture + replay
  - Story 2.7: idempotency-cache TTL sweep

All models use the modern ``Mapped[...] + mapped_column(...)`` declarative
pattern (SQLAlchemy 2.0+). The shared ``Base`` carries all metadata for
Alembic autogenerate.

Column type notes:
- ``String(38)`` for prefixed IDs: ``t-<uuidv7>`` / ``s-<uuidv7>`` / ``e-<uuidv7>``
  (2-char prefix + 36-char UUID = 38 chars). Matches EventEnvelope regex from Story 2.1.
- ``UTCDateTime()`` for all timestamps — UTC-aware, ms-precision (Story 2.1).
- ``BigInteger`` for ``emitted_at_monotonic_ns`` / ``event_count`` / ``byte_size`` —
  64-bit signed (int64.max ≈ 9.2e18 ns ≈ 292 years, ample for monotonic clocks).
- ``Text`` for variable-length strings (title, worktree_path, payload_json).
- FK status enums are application-enforced (not CHECK constraints) so future
  status additions do NOT require a migration (Arch line 220-ish).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (  # noqa: F401 — DateTime used by UTCDateTime impl
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """DateTime type that enforces UTC-aware datetimes on read.

    SQLite stores datetimes as naive ISO 8601 text (the ``timezone=True``
    parameter has no effect on the storage format in the SQLite dialect).
    On read, ``datetime.fromisoformat()`` returns a naive datetime. This
    decorator re-attaches ``tzinfo=UTC`` so callers always receive
    timezone-aware datetimes — matching the Story 2.1 convention of
    ms-precision UTC everywhere.

    Write path: strips tzinfo before storage (sqlite3 module cannot handle
    aware datetimes in parameterised queries without the timezone suffix in
    the string, and SQLAlchemy's SQLite DateTime type stores as text anyway).
    Actually we store as-is and let SQLAlchemy's DateTime handle the bind;
    the result_processor re-attaches UTC.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            # Store as UTC naive for SQLite text compatibility.
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class Task(Base):
    """Persistent task entity. Created on ``task.created`` event; updated by materializer."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)


class Session(Base):
    """Worker session bound to a task. FK to tasks.id with RESTRICT on delete."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(38), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    worker_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Event(Base):
    """Flat SQL mirror of the JSONL event log — enables SQL queries over event history.

    ``payload_json`` stores canonical-JSON text from ``to_canonical_json`` (Story 2.1).
    NOT the SQLAlchemy JSON type — byte-stable text for auditability; re-parsed on read.

    ``parent_event_id`` has no FK — parent may arrive out of order during replay,
    and integrity is guaranteed by the event log, not SQL row order.
    ``task_id`` / ``session_id`` are nullable — some events (e.g. service.started)
    don't bind to a task/session.
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    emitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    emitted_at_monotonic_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        String(38), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(
        String(38), ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=True
    )
    parent_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class IdempotencyCache(Base):
    """Idempotency key → result-event mapping. Schema only — TTL sweep is Story 2.7.

    ``expires_at = created_at + 7 days`` is enforced by the caller (Story 2.7).
    ``result_event_id`` points to the event the first-successful call produced;
    on collision the caller returns its payload.
    """

    __tablename__ = "idempotency_cache"

    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    result_event_id: Mapped[str] = mapped_column(String(38), nullable=False)
    request_id_on_first_hit: Mapped[str] = mapped_column(String(36), nullable=False)


class Snapshot(Base):
    """Materialized state snapshot. Schema only — capture + replay logic is Story 2.6.

    ``cursor_event_id`` = last event consumed before this snapshot; replay
    re-applies events ``> cursor_event_id``.
    ``id`` uses a bare UUIDv7 (no prefix) — a snapshot isn't an event/task/session.
    """

    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    cursor_event_id: Mapped[str] = mapped_column(String(38), nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# Indexes — declared after all model classes so the tables exist in
# Base.metadata before we reference their columns. Naming: ix_<table>_<cols>.
# ---------------------------------------------------------------------------

# Hot path: materializer loads all events for a task in chronological order.
Index("ix_events_task_id_emitted_at", Event.task_id, Event.emitted_at)

# Symmetric: session-scoped event queries.
Index("ix_events_session_id_emitted_at", Event.session_id, Event.emitted_at)

# Audit/debug: filter events by type.
Index("ix_events_type_emitted_at", Event.type, Event.emitted_at)

# List all sessions for a task.
Index("ix_sessions_task_id", Session.task_id)

# TTL-sweep scan (Story 2.7 will use this).
Index("ix_idempotency_cache_expires_at", IdempotencyCache.expires_at)

# List active tasks by status + recency.
Index("ix_tasks_status_updated_at", Task.status, Task.updated_at)

__all__ = [
    "Base",
    "Event",
    "IdempotencyCache",
    "Session",
    "Snapshot",
    "Task",
]
