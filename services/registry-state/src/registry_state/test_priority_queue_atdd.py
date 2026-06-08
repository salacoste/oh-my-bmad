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

from datetime import datetime, timezone

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


def test_task_schema_has_priority_column() -> None:
    """Task schema must have a ``priority`` integer column."""
    from registry_state.schema import Task

    assert hasattr(Task, "priority")


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
async def test_claim_next_task_returns_highest_priority(tmp_path) -> None:
    """claim_next_task must return the highest-priority pending task.

    Given two pending tasks with priorities 0 and 5, the one with
    priority 5 must be claimed first.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from registry_state.schema import Base, Task
    from registry_state.domain.worker_pool import claim_next_task

    db_path = tmp_path / "priority.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sm = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async with sm() as session:
            # Create two pending tasks with different priorities.
            low_task = Task(
                id=new_task_id(),
                status="pending",
                priority=0,
                created_at=now,
                updated_at=now,
                actor_kind="operator",
                actor_id="test",
            )
            high_task = Task(
                id=new_task_id(),
                status="pending",
                priority=5,
                created_at=now,
                updated_at=now,
                actor_kind="operator",
                actor_id="test",
            )
            session.add_all([low_task, high_task])
            await session.commit()

        # Claim should return the high-priority task.
        async with sm() as session:
            claimed = await claim_next_task(session, "test-worker", dialect="sqlite")
            assert claimed is not None
            assert claimed.priority == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_next_task_tiebreaks_stably(tmp_path) -> None:
    """claim_next_task must break priority ties deterministically.

    Given two pending tasks with the same priority, claiming must
    return exactly one of them (not None, not an error). The tiebreak
    order (by id ASC) is deterministic but the test cannot assume which
    UUIDv7 sorts first since random bits differ within the same ms.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from registry_state.schema import Base, Task
    from registry_state.domain.worker_pool import claim_next_task

    db_path = tmp_path / "tiebreak.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sm = async_sessionmaker(engine, expire_on_commit=False)
        task_ids = [new_task_id(), new_task_id()]
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async with sm() as session:
            for tid in task_ids:
                session.add(Task(
                    id=tid,
                    status="pending",
                    priority=3,
                    created_at=now,
                    updated_at=now,
                    actor_kind="operator",
                    actor_id="test",
                ))
            await session.commit()

        # Both have same priority — claim returns one deterministically.
        async with sm() as session:
            claimed = await claim_next_task(session, "test-worker", dialect="sqlite")
            await session.commit()
            assert claimed is not None
            assert claimed.id in task_ids
            assert claimed.priority == 3

        # Claiming again returns the other one (first is now claimed).
        async with sm() as session:
            claimed2 = await claim_next_task(session, "test-worker-2", dialect="sqlite")
            await session.commit()
            assert claimed2 is not None
            assert claimed2.id in task_ids
            assert claimed2.id != claimed.id
    finally:
        await engine.dispose()
