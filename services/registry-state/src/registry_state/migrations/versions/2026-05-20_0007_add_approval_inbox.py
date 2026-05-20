"""Story 11.3 — add ``approval_inbox`` table for FR63 pinned-thread routing.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-20 00:00:00.000000+00:00

Adds a new top-level table ``approval_inbox`` that materializes the
``approval.inbox_opened`` event (Story 11.3 AC2 / AC3). One row per
operator chat; UPSERT semantics on duplicate events. Read via registry-api
by clawhip-daemon to route ``task.approval_requested`` events into the
operator's pinned Forum-Topic inbox instead of the originating task
thread (FR63).

Additive migration (no DROP of existing data; no FK constraints into
existing tables). SQLite CREATE TABLE is a metadata-only operation —
no row rewrite, no table lock — and the schema is fully forward- and
backward-compatible with deployments that have not yet seen the
``/approvals`` command.

Column layout matches :class:`registry_state.schema.ApprovalInbox`:

* ``operator_chat_id`` (BigInteger, PRIMARY KEY) — Telegram chat_id;
  supergroups have negative ids that exceed int32, hence BigInteger.
* ``inbox_thread_id`` (BigInteger, NOT NULL) — Forum-Topic
  ``message_thread_id`` (always >= 1 per Telegram Bot API).
* ``opened_at`` (DateTime, NOT NULL) — timezone-aware via the same
  pattern as ``events.emitted_at`` (UTC text storage; UTC-aware on
  read via the ``UTCDateTime`` decorator).
* ``opened_by_actor_id`` (String(128), NOT NULL) — Story 11.2 P1-H1
  codebase-wide ``actor_id`` length invariant.

No secondary index: the only query pattern is point-lookup by
``operator_chat_id`` (already the PRIMARY KEY).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Story 11.3 review P16: ``branch_labels`` / ``depends_on`` are Sequence-typed
# per Alembic's runtime contract — single-string forms work but type-check
# cleaner as ``Sequence[str] | None``.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_inbox",
        sa.Column("operator_chat_id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("inbox_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by_actor_id", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("approval_inbox")
