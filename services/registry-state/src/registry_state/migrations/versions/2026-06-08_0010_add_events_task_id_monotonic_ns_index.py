"""Story 41.2 — add ``ix_events_task_id_emitted_at_monotonic_ns`` composite index.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-08 00:00:00.000000+00:00

Adds a composite index on ``events(task_id, emitted_at_monotonic_ns)`` to
optimise the ``GET /v1/tasks/{task_id}/events?after=<cursor>`` pagination
query.  The existing ``ix_events_task_id_emitted_at`` index covers wall-clock
time; this new index covers monotonic nanosecond cursors used by the CLI
follow-mode polling loop.

Design rationale:
- The CLI ``events --follow`` endpoint filters by ``task_id`` and orders by
  ``emitted_at_monotonic_ns`` for strict cursor pagination.  Without this
  index the query falls back to a full scan of all events for the task.
- Composite index supports both the left-prefix query (task_id only) and the
  full range scan (task_id + monotonic_ns > cursor).
- Additive migration (no DROP, no data rewrite).  SQLite CREATE INDEX is
  online-safe for read workloads.

Deferred-work item D1(7-5-6): "Add ix_events_task_id_mono_ns index when query
latency warrants."  Phase 8 Epic 41 resolves this item.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None

_INDEX_NAME = "ix_events_task_id_emitted_at_monotonic_ns"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "events",
        ["task_id", "emitted_at_monotonic_ns"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="events")
