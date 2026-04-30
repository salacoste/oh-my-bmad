"""add task thread binding

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30 00:00:00.000000+00:00

Story 3.9 AC-2 / AC-11 — additive (NFR-M3) migration adding two nullable
``BigInteger`` columns to the ``tasks`` table:

    chat_id              — Telegram chat id (negative for supergroups)
    reply_to_message_id  — Telegram message id of the originating /task message

Both nullable so pre-3.9 rows and non-Telegram-originated tasks remain
valid. Zero-downtime: ``ADD COLUMN ... NULL`` in SQLite is an in-place
metadata-only change that does not rewrite existing rows.

L14 — IMPORTANT: registry-state MUST be stopped before running this migration
(FR26 single-writer constraint). Running ``alembic upgrade`` while registry-state
is writing will result in SQLite ``database is locked`` errors.

L13 — Scaling note: ``chat_id`` and ``reply_to_message_id`` columns have no
index in this migration. Queries filtering or joining on ``chat_id`` (e.g. Story
3.10+ broadcast-to-chat features) will full-table-scan at scale. Add a covering
index in a future migration when that access pattern is implemented.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    # L6: wrap in batch_alter_table for SQLite < 3.35 compatibility
    # (older SQLite does not support DROP COLUMN directly).
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("reply_to_message_id")
        batch_op.drop_column("chat_id")
