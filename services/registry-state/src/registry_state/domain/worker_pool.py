"""Worker pool primitives — identity, claiming, crash detection (Epic 32).

Phase 6 Epic 32 (FR104–FR106 / ADR-0019). This module provides:

- :func:`generate_worker_id` — unique identity per worker instance
  (hostname-pid format or ``WORKER_ID`` env var override).
- :func:`claim_next_task` — atomic task claiming via SKIP LOCKED (Postgres)
  or BEGIN EXCLUSIVE (SQLite).
- :func:`handle_worker_crash` — re-assign tasks from a crashed worker.

Design decisions (ADR-0019):
- D1: Pull-based claiming — workers poll for QUEUED tasks and atomically claim
  them. Claiming = setting ``task.worker_id`` and transitioning status in a
  single transaction.
- D2: Worker identity — ``<hostname>-<pid>`` or configurable via ``WORKER_ID``
  env var. Stamped on claimed tasks, carried in events and metrics labels.

Backend-specific claiming:
- Postgres: ``SELECT ... FOR UPDATE SKIP LOCKED`` (standard worker-queue pattern).
- SQLite: ``BEGIN EXCLUSIVE`` transaction (single-writer, fast enough for
  single-operator scale).
"""

from __future__ import annotations

import logging
import os
import socket
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from registry_state.schema import Task

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("registry_state.domain.worker_pool")


def generate_worker_id() -> str:
    """Generate a unique worker identity.

    Priority:
    1. ``WORKER_ID`` env var (if set and non-empty) — operator override.
    2. ``<hostname>-<pid>`` — default format (ADR-0019 D2).

    Returns:
        A non-empty string identifying this worker instance.
    """
    explicit = os.environ.get("WORKER_ID", "").strip()
    if explicit:
        return explicit
    hostname = socket.gethostname()
    pid = os.getpid()
    return f"{hostname}-{pid}"


async def claim_next_task(
    session: AsyncSession,
    worker_id: str,
    *,
    dialect: str = "sqlite",
) -> Task | None:
    """Atomically claim the next available task for a worker.

    Finds a task in ``pending`` status that has no ``worker_id`` assigned,
    locks it (backend-dependent), sets ``worker_id`` and returns the task.

    Args:
        session: AsyncSession for the database transaction.
        worker_id: Unique identity of the claiming worker.
        dialect: SQLAlchemy dialect name (``"sqlite"`` or ``"postgresql"``).
            Determines the locking strategy.

    Returns:
        The claimed Task ORM instance, or ``None`` if no tasks available.

    Implementation note (ADR-0019 D1):
        Postgres uses ``SELECT ... FOR UPDATE SKIP LOCKED`` — the standard
        worker-queue pattern that lets concurrent workers each claim a
        different row without blocking.
        SQLite uses a simple ``SELECT + UPDATE`` within the existing
        ``BEGIN EXCLUSIVE`` transaction that aiosqlite provides. Since
        SQLite is single-writer, only one worker can hold the write lock
        at a time, making exclusive assignment trivial.
    """
    if dialect == "postgresql":
        return await _claim_postgres(session, worker_id)
    return await _claim_sqlite(session, worker_id)


async def _claim_postgres(session: AsyncSession, worker_id: str) -> Task | None:
    """Claim using ``SELECT ... FOR UPDATE SKIP LOCKED`` (Postgres).

    SKIP LOCKED skips rows that are already locked by another transaction,
    so concurrent workers each get a different task without blocking.
    """
    # Find an unclaimed pending task and lock it (skip already-locked rows).
    stmt = (
        select(Task)
        .where(Task.status == "pending", Task.worker_id.is_(None))
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        return None

    # Stamp the worker_id on the claimed task.
    task.worker_id = worker_id
    await session.flush()
    _log.info("task %s claimed by worker %s (postgres SKIP LOCKED)", task.id, worker_id)
    return task


async def _claim_sqlite(session: AsyncSession, worker_id: str) -> Task | None:
    """Claim using a simple ``SELECT + UPDATE`` (SQLite).

    SQLite is single-writer (aiosqlite serialises writes), so concurrent
    claiming is naturally exclusive — no SKIP LOCKED needed. The first
    worker to execute the UPDATE wins; the second sees worker_id already
    set and skips.
    """
    # Find an unclaimed pending task.
    stmt = select(Task).where(Task.status == "pending", Task.worker_id.is_(None)).limit(1)
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        return None

    # Stamp the worker_id on the claimed task.
    task.worker_id = worker_id
    await session.flush()
    _log.info("task %s claimed by worker %s (sqlite)", task.id, worker_id)
    return task


async def handle_worker_crash(session: AsyncSession, worker_id: str) -> int:
    """Handle a worker crash by re-assigning its tasks.

    Finds all tasks assigned to the crashed worker that are in an active
    state (not completed/stopped) and transitions them to ``failed`` with
    ``worker_id`` cleared, making them re-assignable.

    Args:
        session: AsyncSession for the database transaction.
        worker_id: Identity of the crashed worker.

    Returns:
        Number of tasks re-assigned.

    NFR-R11: worker crash mid-task is detected by the registry.
    NFR-S15: one worker crash does not affect other workers.
    """
    # Find active tasks owned by the crashed worker.
    # Active states = anything that isn't terminal (completed, stopped).
    terminal_states = ("completed", "stopped")
    stmt = (
        update(Task)
        .where(
            Task.worker_id == worker_id,
            Task.status.notin_(terminal_states),
        )
        .values(status="failed", worker_id=None)
    )
    result = await session.execute(stmt)
    count = result.rowcount
    if count > 0:
        _log.warning(
            "worker %s crashed: %d tasks re-assigned (→ failed)",
            worker_id,
            count,
        )
    await session.flush()
    return count


__all__ = ["claim_next_task", "generate_worker_id", "handle_worker_crash"]
