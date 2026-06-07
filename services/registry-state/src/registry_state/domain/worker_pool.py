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

import os
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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


async def claim_next_task(session: AsyncSession, worker_id: str) -> None:
    """Atomically claim the next available task for a worker.

    Finds a task in ``pending`` status that has no ``worker_id`` assigned,
    locks it (backend-dependent), sets ``worker_id`` and updates status.

    Args:
        session: AsyncSession for the database transaction.
        worker_id: Unique identity of the claiming worker.

    Returns:
        The claimed Task ORM instance, or ``None`` if no tasks available.

    Implementation note (Story 32.3):
        Postgres uses ``SELECT ... FOR UPDATE SKIP LOCKED``.
        SQLite uses ``BEGIN EXCLUSIVE`` transaction.
        This function hides the backend difference behind a unified interface.
    """
    # Story 32.3 will implement the actual claiming logic with
    # backend-specific SQL. This stub returns None (no tasks available)
    # so the ATDD contract test can import and call it.
    return None


async def handle_worker_crash(session: AsyncSession, worker_id: str) -> int:
    """Handle a worker crash by re-assigning its tasks.

    Finds all tasks assigned to the crashed worker and transitions them
    to a re-assignable state (failed → pending). Clears ``worker_id``.

    Args:
        session: AsyncSession for the database transaction.
        worker_id: Identity of the crashed worker.

    Returns:
        Number of tasks re-assigned.

    NFR-R11: worker crash mid-task is detected by the registry.
    NFR-S15: one worker crash does not affect other workers.
    """
    # Story 32.7 will implement crash detection + re-assignment.
    # This stub returns 0 so the ATDD contract test can import it.
    return 0


__all__ = ["claim_next_task", "generate_worker_id", "handle_worker_crash"]
