"""Story 9.7: add trace_id column to events table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-18 00:00:00.000000+00:00

FR57, FR59a. Additive: events.trace_id is NULL for pre-1.1.0 events (written
before the schema bump), non-null for 1.1.0+ events (EventEnvelope enforces
required). Migration 0005 is purely additive (ADD COLUMN ... NULL); existing
rows get NULL. SQLite ADD COLUMN ... NULL is an in-place metadata-only
operation — no row rewrite, no table lock.

Index ix_events_trace_id supports SELECT * FROM events WHERE trace_id = ?
(the /trace operator query, AC8 / FR59a).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("trace_id", sa.String(38), nullable=True),
    )
    op.create_index("ix_events_trace_id", "events", ["trace_id"])


def downgrade() -> None:
    # PM-B6 (Story 9.7 pass-1): downgrade is DESTRUCTIVE — batch_alter_table
    # rebuilds the SQLite table, dropping all trace_id values. Operators must
    # ensure no 1.1.0-schema events are in the table before running downgrade,
    # or those events will become unqueryable by trace_id.
    # The index must be dropped INSIDE the batch_alter_table context so it is
    # reflected in the rebuilt table schema (not left dangling).
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_index("ix_events_trace_id")
        batch_op.drop_column("trace_id")
