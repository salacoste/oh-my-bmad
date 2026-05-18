"""Story 9.8 deferred-work D6+D7 — synthetic-source provenance + extensions canonical round-trip

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-19 00:00:00.000000+00:00

Resolves Epic 9 deferred-work D6 and D7 as a combined additive migration:

* **D6 — ``events.trace_id_synthetic_source``** (String(64), nullable):
  Labels the origin of synthetic trace_ids so operators inspecting /trace
  can distinguish operator-originated traces (NULL) from internally minted
  ones. Known label values:

  - ``"migrator-v1_0_0-to-v1_0_1"``          — offline migrator back-fill
  - ``"subscriber-pre110-replay"``           — online subscriber back-fill
  - ``"failure-detection-system-initiated"`` — emit_* without operator context

  Replaces the pass-2 ``X-Trace-Has-Synthetic`` header heuristic that was
  DROPPED in pass-3 UH-4 because it relied on row-level inference
  (``row.trace_id == row.request_id``) and produced false +/- without test
  coverage.

* **D7 — ``events.extensions``** (Text, nullable): Stores the canonical
  JSON of ``envelope.extensions`` so the ``/trace`` route can return
  envelope-equivalent data instead of always ``{}``. NULL when the
  envelope's extensions dict is empty (the v1.0.1+ default); non-empty
  extensions are serialised via :func:`events.canonical.to_canonical_payload_json`.

Additive: both columns are nullable; existing rows pre-9.8 keep NULL on
both. SQLite ADD COLUMN ... NULL is a metadata-only operation — no row
rewrite, no table lock. No index on either column: low-cardinality (D6)
+ presentation-only (D7), no anticipated WHERE clauses.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("trace_id_synthetic_source", sa.String(64), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("extensions", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Both columns are nullable and not part of any index, so drop is a
    # plain metadata operation. batch_alter_table is required by SQLite
    # for DROP COLUMN (the operation rebuilds the table).
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_column("extensions")
        batch_op.drop_column("trace_id_synthetic_source")
