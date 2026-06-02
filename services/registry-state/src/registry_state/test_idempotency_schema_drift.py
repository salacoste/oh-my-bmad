"""Schema-drift detector for the duplicated idempotency_cache Table (Story 2.7 AC-11).

`packages/idempotency/src/idempotency/cache.py` defines the `idempotency_cache`
table as a SQLAlchemy Core ``Table`` to keep `packages/` independent of
`services/`. This duplicates the ORM model in `registry_state.schema.IdempotencyCache`.

This test ensures the two definitions stay in sync. It lives in
`services/registry-state/` because `services/` is allowed to import from
`packages/`; the reverse direction is forbidden by the project's
`scripts/check_imports.py` IMP001 rule.

If this test fails, update either the Core Table in `cache.py` OR the ORM
model in `schema.py` so they match column-by-column.
"""

from __future__ import annotations

from typing import cast

from idempotency.cache import _IDEMPOTENCY_TABLE
from sqlalchemy import Table

from registry_state.schema import IdempotencyCache


class TestIdempotencyCacheSchemaDrift:
    def test_column_names_and_nullability_match_orm(self) -> None:
        orm_cols = {col.name: col for col in IdempotencyCache.__table__.columns}
        core_cols = {col.name: col for col in _IDEMPOTENCY_TABLE.columns}

        # Same set of column names
        assert set(orm_cols.keys()) == set(core_cols.keys()), (
            f"Column name mismatch.\nORM:  {sorted(orm_cols)}\nCore: {sorted(core_cols)}"
        )

        # Same nullability per column
        for name in orm_cols:
            assert orm_cols[name].nullable == core_cols[name].nullable, (
                f"Nullability mismatch on {name}: "
                f"ORM={orm_cols[name].nullable}, Core={core_cols[name].nullable}"
            )

        # Same primary-key columns
        orm_pks = {col.name for col in IdempotencyCache.__table__.primary_key}
        core_pks = {col.name for col in _IDEMPOTENCY_TABLE.primary_key}
        assert orm_pks == core_pks, f"Primary-key mismatch: ORM={orm_pks}, Core={core_pks}"

    def test_indexes_match_orm(self) -> None:
        """Story 11.3.12 (code-review L2) — indexes must match too.

        Story 11.3.12 added ``ix_idempotency_cache_expires_at`` to the Core
        ``_IDEMPOTENCY_TABLE`` so a DB bootstrapped from it via
        ``create_idempotency_schema`` (the separate idempotency.sqlite3 file)
        gets the same index the migrator-created table had. Without this
        assertion a future edit could drop/rename the index on one side and
        the bootstrapped file would silently lack the index backing
        ``sweep_expired``'s ``expires_at`` range delete (a slow-sweep perf
        regression with no test failure).
        """
        # ``IdempotencyCache.__table__`` is a SQLAlchemy ``Table`` at runtime,
        # but the ORM attribute is typed as ``FromClause`` (no ``.indexes``).
        # ``_IDEMPOTENCY_TABLE`` is already a ``Table``; cast the ORM side so
        # mypy sees ``.indexes``.
        orm_table = cast(Table, IdempotencyCache.__table__)
        orm_indexes = {(idx.name, tuple(c.name for c in idx.columns)) for idx in orm_table.indexes}
        core_indexes = {
            (idx.name, tuple(c.name for c in idx.columns)) for idx in _IDEMPOTENCY_TABLE.indexes
        }
        assert orm_indexes == core_indexes, (
            f"Index mismatch.\nORM:  {sorted(orm_indexes)}\nCore: {sorted(core_indexes)}"
        )
