"""add task state columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-14 00:00:00.000000+00:00

Stories 6.11, 7.1, 7.6 — additive migration adding five nullable columns
to the ``tasks`` table for reconstituted state, blocker tracking, and retry
hints:

    blocker_reason      — String(64), reason when status="blocked" (Story 6.11)
    current_step        — Integer, step progress counter (Story 7.1)
    total_steps         — Integer, total estimated steps from plan (Story 7.1)
    last_agent_action   — String(2000), last file edit or breadcrumb (Story 7.1)
    hint                — Text, retry hint injected into next planning (Story 7.6)

All nullable so existing rows remain valid. ``ADD COLUMN ... NULL`` in
SQLite is an in-place metadata-only change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("blocker_reason", sa.String(64), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("current_step", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("total_steps", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("last_agent_action", sa.String(2000), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("hint", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("hint")
        batch_op.drop_column("last_agent_action")
        batch_op.drop_column("total_steps")
        batch_op.drop_column("current_step")
        batch_op.drop_column("blocker_reason")
