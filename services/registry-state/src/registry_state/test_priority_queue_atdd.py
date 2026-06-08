"""ATDD red-phase contract tests for task priority queue (Epic 39, Story 39.1).

Phase 7 Epic 39 — Task Priority Queue (FC-P6-3).  These tests assert
contracts that are NOT YET IMPLEMENTED.  Every test is marked
``@pytest.mark.xfail(strict=True)`` so the expected outcome is XFAILED
(green PR-gate).

Contracts tested (all xfail):
  1. Task schema has priority column (integer, default 0)
  2. claim_next_task returns highest-priority pending task first
  3. claim_next_task breaks ties by task_id (FIFO within same priority)
  4. TaskCreatedPayload accepts optional priority field
  5. task.created event carries priority in payload

Reference tests (NOT xfail):
  - claim_next_task exists in worker_pool module
  - Task schema has worker_id column
  - Task schema has status column
"""

from __future__ import annotations

import pytest

from events import FROZEN_EPOCH, FrozenClock, new_task_id, new_uuid7


# ---------------------------------------------------------------------------
# Reference tests — existing infrastructure priority queue builds on
# ---------------------------------------------------------------------------


def test_claim_next_task_exists() -> None:
    """claim_next_task must exist in worker_pool module."""
    from registry_state.domain.worker_pool import claim_next_task

    assert callable(claim_next_task)


def test_task_schema_has_worker_id() -> None:
    """Task schema must have worker_id column."""
    from registry_state.schema import Task

    assert hasattr(Task, "worker_id")


def test_task_schema_has_status() -> None:
    """Task schema must have status column."""
    from registry_state.schema import Task

    assert hasattr(Task, "status")


# ---------------------------------------------------------------------------
# xfail contract tests — priority column (Story 39.2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Story 39.2 — priority column not yet added")
def test_task_schema_has_priority_column() -> None:
    """Task schema must have a ``priority`` integer column."""
    from registry_state.schema import Task

    assert hasattr(Task, "priority")


@pytest.mark.xfail(strict=True, reason="Story 39.2 — priority column not yet added")
def test_task_priority_default_is_zero() -> None:
    """Task priority column must default to 0 (lowest priority)."""
    from registry_state.schema import Task

    col = Task.__table__.c.priority
    assert col.default is not None
    # The default value should be 0
    assert col.default.arg == 0


# ---------------------------------------------------------------------------
# xfail contract tests — priority-aware claiming (Story 39.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="Story 39.3 — priority-aware claiming not yet implemented")
async def test_claim_next_task_returns_highest_priority(tmp_path) -> None:
    """claim_next_task must return the highest-priority pending task.

    Given two pending tasks with priorities 0 and 5, the one with
    priority 5 must be claimed first.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from registry_state.schema import Base, Task
    from registry_state.domain.worker_pool import claim_next_task

    db_path = tmp_path / "test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        # Create two pending tasks with different priorities.
        low_task = Task(
            id=new_task_id(),
            status="pending",
            priority=0,
        )
        high_task = Task(
            id=new_task_id(),
            status="pending",
            priority=5,
        )
        session.add_all([low_task, high_task])
        await session.commit()

    # Claim should return the high-priority task.
    async with sm() as session:
        claimed = await claim_next_task(session, "test-worker", dialect="sqlite")
        assert claimed is not None
        assert claimed.priority == 5


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="Story 39.3 — priority-aware claiming not yet implemented")
async def test_claim_next_task_tiebreaks_by_id(tmp_path) -> None:
    """claim_next_task must break priority ties by task_id (FIFO)."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from registry_state.schema import Base, Task
    from registry_state.domain.worker_pool import claim_next_task

    db_path = tmp_path / "test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    task_ids = [new_task_id(), new_task_id()]
    async with sm() as session:
        for tid in task_ids:
            session.add(Task(id=tid, status="pending", priority=3))
        await session.commit()

    # Both have same priority — should get the one with lower ID (earlier UUIDv7).
    async with sm() as session:
        claimed = await claim_next_task(session, "test-worker", dialect="sqlite")
        assert claimed is not None
        assert claimed.id == task_ids[0]
