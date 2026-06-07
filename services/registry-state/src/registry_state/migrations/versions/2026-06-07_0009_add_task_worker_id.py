"""Story 32.2 — add ``worker_id`` column to ``tasks`` table (P6-I4 / ADR-0019 D2).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-07 00:00:00.000000+00:00

Adds a nullable ``worker_id`` column (String(64)) to the ``tasks`` table.
This column is populated by the materializer when a ``task.assigned`` event
is processed — the claiming worker's unique identity (hostname-pid or
``WORKER_ID`` env var override) is stamped on the task row.

Design rationale:
- Nullable for backward compatibility — Phase 5 tasks were never "assigned"
  to a specific worker; their ``worker_id`` remains NULL.
- String(64) accommodates the hostname-pid format (e.g. ``worker-01-12345``)
  with generous headroom for unusual hostnames.
- No index yet — Story 32.7 may add one for crash-detection queries
  (``WHERE worker_id = ? AND status NOT IN ('completed', 'stopped')``).

Additive migration (no DROP, no data rewrite, no table lock). Fully
forward- and backward-compatible — existing deployments continue running
with all ``worker_id`` values NULL until Epic 32 ships.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("worker_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "worker_id")
