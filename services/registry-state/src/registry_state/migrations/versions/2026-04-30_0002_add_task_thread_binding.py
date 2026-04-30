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
    op.drop_column("tasks", "reply_to_message_id")
    op.drop_column("tasks", "chat_id")
