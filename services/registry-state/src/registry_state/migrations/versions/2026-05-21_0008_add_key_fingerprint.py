"""Story 11.5 — add ``key_fingerprint`` table for FR65a key-rotation audit.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-21 00:00:00.000000+00:00

Adds a new top-level singleton table ``key_fingerprint`` that materializes
the ``key.rotated`` event (Story 11.5 AC2 / AC3). Exactly one row keyed on
the literal string ``"current"``; UPSERT semantics on every rotation
overwrite the previous row.

The row is read by registry-api at startup
(``adapters/key_rotation.py``) to detect whether the operator has changed
``OPERATOR_HMAC_KEY`` since the last boot; mismatch emits a fresh
``key.rotated`` event recording the transition (FR65a + NFR-S10).

Additive migration (no DROP of existing data; no FK constraints into
existing tables). SQLite CREATE TABLE is metadata-only — no row rewrite,
no table lock — and the schema is fully forward- and backward-compatible
with deployments that have never rotated their signing key.

Column layout matches :class:`registry_state.schema.KeyFingerprint`:

* ``id`` (String(16), PRIMARY KEY) — singleton constant ``"current"``;
  enforces one-row semantics at the SQL layer.
* ``fingerprint`` (String(16), NOT NULL) — 16 lowercase hex chars =
  SHA-256(key)[:8] = 64 bits. Matches ``KeyRotatedPayload.
  new_key_fingerprint`` field constraint.
* ``rotated_at`` (DateTime(tz=True), NOT NULL) — timezone-aware via the
  same pattern as ``events.emitted_at`` (UTC text storage; UTC-aware on
  read via the ``UTCDateTime`` decorator).
* ``rotated_by_actor_id`` (String(128), NOT NULL) — Story 11.2 P1-H1
  codebase-wide ``actor_id`` length invariant.

No secondary index: the only query pattern is point-lookup by the
singleton PK ``"current"``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Story 11.4 PP16: align typing with migrations 0001-0007 (``str | None``)
# for consistency across the migration tree.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "key_fingerprint",
        sa.Column("id", sa.String(16), primary_key=True, nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_by_actor_id", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("key_fingerprint")
