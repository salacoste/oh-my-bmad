"""Shared SQLite test helpers for integration tests (Story 11.2.1 PP8).

Extracted from per-file inline copies. Future integration tests touching
registry-state SQLite tables should consume these helpers rather than
re-vending the same 6 lines. Mirrors the sibling ``_compose_helpers.py``
module pattern.
"""

from __future__ import annotations

from pathlib import Path


def integration_db_url(db_path: Path) -> str:
    """Return an aiosqlite URL for the given on-disk SQLite path."""
    return f"sqlite+aiosqlite:///{db_path}"


async def integration_seed_tables(db_url: str) -> None:
    """Create registry-state ORM tables in *db_url* without seeding any rows.

    Import-local so this module's import chain stays light when tests
    that don't touch the SQLite store load it transitively.
    """
    from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — tests/* can cross services
        create_engine,
    )
    from registry_state.schema import Base  # noqa: IMP001 — tests/* can cross services

    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


__all__ = ["integration_db_url", "integration_seed_tables"]
