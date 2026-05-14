"""session compound index

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13 00:00:00.000000+00:00

Story 7.5.2 AC-2 — replace the single-column ``ix_sessions_task_id`` index
with a compound ``ix_sessions_task_id_status`` index on ``(task_id, status)``.
The compound index covers both:

  - task_id-only queries (left-prefix rule)
  - _close_active_session_for_task queries (WHERE task_id = ? AND status IN (...))

The old single-column index is a proper subset of the new compound index,
so removing it eliminates redundancy without losing query coverage.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("ix_sessions_task_id", table_name="sessions")
    op.create_index("ix_sessions_task_id_status", "sessions", ["task_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_sessions_task_id_status", table_name="sessions")
    op.create_index("ix_sessions_task_id", "sessions", ["task_id"])
