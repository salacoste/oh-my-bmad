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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """DateTime type that stores values as UTC-naive and returns UTC-aware.

    SQLite stores datetimes as naive ISO 8601 text (the ``timezone=True``
    parameter has no effect on the storage format in the SQLite dialect).
    On read, ``datetime.fromisoformat()`` returns a naive datetime. This
    decorator guarantees the instant is preserved across the roundtrip and
    callers always receive timezone-aware UTC datetimes — matching the
    Story 2.1 convention of ms-precision UTC everywhere.

    Write path behaviour:
      - ``None`` passes through unchanged.
      - Naive datetimes raise ``ValueError`` — silently assuming "local time"
        or "UTC" would corrupt data. Callers must pass ``datetime.now(UTC)``
        or similar.
      - Aware datetimes in any offset are converted to UTC via
        ``astimezone(UTC)`` BEFORE stripping ``tzinfo`` — preserving the
        instant. Previously the implementation only stripped ``tzinfo``,
        which silently re-interpreted a ``+05:00`` wall clock as UTC (a 5h
        jump into the future on read-back).

    Read path: re-attaches ``tzinfo=UTC`` to the naive value pulled from
    SQLite.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UTCDateTime requires tz-aware datetime; got naive. "
                "Pass datetime.now(UTC) or similar."
            )
        # Convert to UTC *before* stripping tzinfo so the stored wall-clock
        # actually represents the original instant.
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class Task(Base):
    """Persistent task entity. Created on ``task.created`` event; updated by materializer.

    Story 3.9 AC-2: ``chat_id`` + ``reply_to_message_id`` carry the Telegram
    thread binding so outbound sinks (clawhip-daemon's TelegramSink) can
    deliver progress events to the originating thread (FR13). Both columns
    are ``BigInteger`` because Telegram supergroup chat ids exceed 2**31;
    both are nullable for back-compat with pre-3.9 / non-Telegram tasks.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(38), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(38), nullable=True)
    # Story 3.9: Telegram thread binding (FR13).
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Story 6.11: blocker reason when status="blocked" (FR44).
    blocker_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Story 7.1: reconstituted-state fields (FR4). Additive columns — existing
    # rows get NULL; populated by materializer handlers.
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    last_agent_action: Mapped[str | None] = mapped_column(String(2000), nullable=True, default=None)
    # Story 7.6: retry hint injection (FR7). Populated by materializer on task.retry_requested.
    hint: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Story 12.4: per-task budget policy (FR68a). Additive nullable columns —
    # NULL means "inherit the .env default" (OMB_DEFAULT_TASK_BUDGET_*). Populated
    # by the materializer from the additive task.created 1.2.0 payload. The
    # ``budget_action`` enum (failed|awaiting_approval) is validated at the
    # Pydantic/model boundary (TaskCreatedPayload / CreateTaskRequest), NOT a DB
    # CHECK constraint — consistent with how the codebase constrains enums.
    budget_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    budget_action: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)


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
    # Story 9.7 / FR57 / FR59a: trace_id column for WHERE trace_id = ? query.
    # Nullable: pre-1.1.0 events (no trace_id) remain queryable; post-1.1.0
    # events all carry a non-null value (EventEnvelope enforces required).
    trace_id: Mapped[str | None] = mapped_column(String(38), nullable=True)
    # Story 9.8 deferred-work D6 (Epic 9 retro): provenance label for synthetic
    # trace_ids. NULL for operator-originated (real) traces; non-NULL only when
    # the trace_id was minted by an internal back-fill or system-initiated
    # emission. Known labels:
    #   - "migrator-v1_0_0-to-v1_0_1"          — offline migrator back-fill
    #   - "subscriber-pre110-replay"           — online subscriber back-fill
    #   - "failure-detection-system-initiated" — emit_* without operator context
    # No index: low-cardinality (≤ handful of distinct values) and no anticipated
    # WHERE clauses; operators consume the column via /trace per-row inspection.
    trace_id_synthetic_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Story 9.8 deferred-work D7 (Epic 9 retro): canonical-JSON text of
    # ``envelope.extensions`` so the /trace presentation view can populate the
    # ``extensions`` field instead of always returning ``{}``. NULL when the
    # envelope's extensions dict is empty (the v1.0.1+ default); non-empty
    # extensions are serialised via :func:`events.canonical.to_canonical_json`'s
    # underlying rule (recursive sort_keys, no whitespace, UTF-8). No index:
    # extensions is presentation-only and never queried.
    extensions: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class ApprovalInbox(Base):
    """One row per operator chat with an open approval inbox (Story 11.3 AC2).

    Materialized by the registry-state event subscriber from
    ``approval.inbox_opened`` events (FR63). Read via registry-api by
    clawhip-daemon (outbound delivery) to determine routing for
    ``task.approval_requested`` events — when a row exists for the
    operator's chat, the approval request is delivered into the pinned
    ``inbox_thread_id`` (Forum-Topic) instead of the originating task
    thread. Also read by the ``/approvals`` Telegram handler to detect
    whether an operator already has an inbox open.

    Primary key = ``operator_chat_id`` (one inbox per operator chat —
    UPSERT semantics on duplicate ``approval.inbox_opened`` events; the
    operator re-running ``/approvals`` replaces the row in place rather
    than producing two pinned threads). The ``inbox_thread_id`` and
    ``opened_at`` / ``opened_by_actor_id`` fields are refreshed on each
    replay so the row always reflects the operator's latest pinned
    Forum-Topic.

    Column type notes:

    * ``operator_chat_id`` and ``inbox_thread_id`` are ``BigInteger``
      because Telegram supergroup chat ids and forum-topic message
      thread ids both routinely exceed ``2**31``.
    * ``opened_at`` uses :class:`UTCDateTime` to preserve the timezone
      round-trip (matches the Story 2.1 ms-precision UTC convention).
    * ``opened_by_actor_id`` is ``String(128)`` per the Story 11.2 P1-H1
      codebase-wide ``actor_id`` length invariant.
    """

    __tablename__ = "approval_inbox"

    operator_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbox_thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    opened_by_actor_id: Mapped[str] = mapped_column(String(128), nullable=False)


class KeyFingerprint(Base):
    """Singleton row tracking the current HMAC signing-key fingerprint (Story 11.5 AC2).

    Materialized from ``key.rotated`` events. Read by registry-api at startup
    (``adapters/key_rotation.py``) to detect whether ``OPERATOR_HMAC_KEY`` has
    changed since last boot; if so, emit a fresh ``key.rotated`` event
    recording the transition (FR65a + NFR-S10).

    Single-row table — primary key is the literal string ``"current"`` so
    UPSERT semantics on every rotation overwrite the previous row.

    Column type notes:

    * ``id`` is ``String(16)``; the only valid value is ``"current"`` (the
      table is a singleton and the PK constraint enforces it).
    * ``fingerprint`` is ``String(16)`` matching ``KeyRotatedPayload.
      new_key_fingerprint`` field constraint (16 lowercase hex chars =
      SHA-256[:8] = 64 bits).
    * ``rotated_at`` uses :class:`UTCDateTime` for the same UTC-aware ms-
      precision round-trip discipline as the rest of the schema.
    * ``rotated_by_actor_id`` is ``String(128)`` per the Story 11.2 P1-H1
      codebase-wide ``actor_id`` length invariant.
    """

    __tablename__ = "key_fingerprint"
    # Story 11.5 PP6 — CHECK constraint enforces the singleton-row
    # invariant at the SQL layer (parallel to migration 0008's
    # ``sa.CheckConstraint``). Pre-PP6 the singleton invariant was
    # convention-only; the constraint name matches the migration so
    # SQLite reports them as a single canonical constraint.
    __table_args__ = (CheckConstraint("id = 'current'", name="ck_key_fingerprint_singleton"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="current")
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    rotated_by_actor_id: Mapped[str] = mapped_column(String(128), nullable=False)


class Snapshot(Base):
    """Materialized state snapshot. Schema only — capture + replay logic is Story 2.6.

    ``cursor_event_id`` = last event consumed before this snapshot; replay
    re-applies events ``> cursor_event_id``.
    ``id`` uses a bare UUIDv7 — 36 hex+hyphen chars, no 2-char prefix, because
    a snapshot isn't an event/task/session. Width is ``String(36)`` (not 38)
    to match UUIDv7's canonical textual length.
    """

    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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

# Story 7.5.2: compound index covering both task_id-only queries (left-prefix)
# and _close_active_session_for_task (WHERE task_id = ? AND status IN (...)).
Index("ix_sessions_task_id_status", Session.task_id, Session.status)

# TTL-sweep scan (Story 2.7 will use this).
Index("ix_idempotency_cache_expires_at", IdempotencyCache.expires_at)

# List active tasks by status + recency.
Index("ix_tasks_status_updated_at", Task.status, Task.updated_at)

# Story 9.7 / FR59a: trace_id query — SELECT * FROM events WHERE trace_id = ?
# Non-unique: many events share the same trace_id (one per operator command).
Index("ix_events_trace_id", Event.trace_id)

__all__ = [
    "ApprovalInbox",
    "Base",
    "Event",
    "IdempotencyCache",
    "KeyFingerprint",
    "Session",
    "Snapshot",
    "Task",
]
