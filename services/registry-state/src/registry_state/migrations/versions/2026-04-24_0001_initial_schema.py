"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24 00:00:00.000000+00:00

Initial schema for the registry-state SQLite store (Story 2.3).
Creates 5 tables + 6 indexes:

Tables: tasks, sessions, events, idempotency_cache, snapshots
Indexes:
  ix_events_task_id_emitted_at     — materializer hot path
  ix_events_session_id_emitted_at  — session-scoped queries
  ix_events_type_emitted_at        — audit/debug by event type
  ix_sessions_task_id              — list sessions for a task
  ix_idempotency_cache_expires_at  — TTL-sweep scan (Story 2.7)
  ix_tasks_status_updated_at       — list active tasks
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- tasks -----------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(38), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("last_event_id", sa.String(38), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_status_updated_at", "tasks", ["status", "updated_at"])

    # --- sessions --------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(38), nullable=False),
        sa.Column("task_id", sa.String(38), nullable=False),
        sa.Column("worker_kind", sa.String(32), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_task_id", "sessions", ["task_id"])

    # --- events ----------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.String(38), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitted_at_monotonic_ns", sa.BigInteger(), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(38), nullable=True),
        sa.Column("session_id", sa.String(38), nullable=True),
        sa.Column("parent_event_id", sa.String(38), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_task_id_emitted_at", "events", ["task_id", "emitted_at"])
    op.create_index("ix_events_session_id_emitted_at", "events", ["session_id", "emitted_at"])
    op.create_index("ix_events_type_emitted_at", "events", ["type", "emitted_at"])

    # --- idempotency_cache -----------------------------------------------
    op.create_table(
        "idempotency_cache",
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_event_id", sa.String(38), nullable=False),
        sa.Column("request_id_on_first_hit", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_index("ix_idempotency_cache_expires_at", "idempotency_cache", ["expires_at"])

    # --- snapshots -------------------------------------------------------
    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(38), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_event_id", sa.String(38), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("snapshots")
    op.drop_index("ix_idempotency_cache_expires_at", table_name="idempotency_cache")
    op.drop_table("idempotency_cache")
    op.drop_index("ix_events_type_emitted_at", table_name="events")
    op.drop_index("ix_events_session_id_emitted_at", table_name="events")
    op.drop_index("ix_events_task_id_emitted_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_sessions_task_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_tasks_status_updated_at", table_name="tasks")
    op.drop_table("tasks")
