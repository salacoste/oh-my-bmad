"""Integration tests for resume-after-approval lifecycle (Story 5.17b).

Proves:
  AC-1 — restart during ``awaiting_approval`` (approval before or after
          restart) results in exactly-once gated action execution.
  AC-2 — retry storm: 10 concurrent ``handle_approval`` calls with the
          same idempotency key result in exactly one gated action.

Uses real LifecycleFSM, real IdempotencyCacheStore (in-memory SQLite),
and stub callbacks for event emission and gated actions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from events.clock import FROZEN_EPOCH, FrozenClock
from idempotency.cache import _IDEMPOTENCY_TABLE, IdempotencyCacheStore
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from worker_wrapper.adapters.lifecycle_manager import LifecycleManager
from worker_wrapper.domain.lifecycle import (
    InvalidTransitionError,
    LifecycleEvent,
    LifecycleFSM,
    WorkerState,
)

# ---------------------------------------------------------------------------
# Inline fixtures (per project convention — no conftest.py)
# ---------------------------------------------------------------------------

_MEM_URL = "sqlite+aiosqlite:///:memory:"
_TASK_ID = "t-0000000000000000000000000001"
_APPROVAL_KEY = "approve-key-001"
_CACHE_ENGINES = []


@pytest_asyncio.fixture(autouse=True)
async def _dispose_cache_engines() -> AsyncGenerator[None, None]:
    try:
        yield
    finally:
        while _CACHE_ENGINES:
            await _CACHE_ENGINES.pop().dispose()


async def _make_cache() -> IdempotencyCacheStore:
    """Create an in-memory IdempotencyCacheStore with the schema table."""
    engine = create_async_engine(
        _MEM_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _CACHE_ENGINES.append(engine)
    async with engine.begin() as conn:
        await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return IdempotencyCacheStore(
        session_maker=session_maker,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )


class _StubCallbacks:
    """Records calls to emit_event and gated_action for assertions."""

    def __init__(self) -> None:
        self.emitted_events: list[tuple[str, dict]] = []
        self.gated_action_calls: int = 0

    async def emit_event(self, event_type: str, payload: dict) -> str:
        self.emitted_events.append((event_type, payload))
        return f"evt-{len(self.emitted_events)}"

    async def gated_action(self) -> None:
        self.gated_action_calls += 1


# ---------------------------------------------------------------------------
# AC-1: Restart during awaiting_approval
# ---------------------------------------------------------------------------


class TestRestartDuringApproval:
    """AC-1: Approval arrives either before or after restart."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_approval_after_restart(self, tmp_path: Path) -> None:
        """FSM persists AWAITING_APPROVAL → restore → approval → gated action once."""
        cache = await _make_cache()
        stubs = _StubCallbacks()
        state_path = tmp_path / ".oh-my-bmad-lifecycle.json"

        # Phase 1: original process — task enters awaiting_approval
        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)
        assert mgr.current_state == WorkerState.AWAITING_APPROVAL
        assert state_path.exists()  # state persisted

        # Phase 2: simulating restart — create new manager from sidecar
        restored = LifecycleManager.restore_from(
            state_path,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        assert restored is not None
        assert restored.current_state == WorkerState.AWAITING_APPROVAL

        # Phase 3: approval arrives after restart
        state = await restored.handle_approval(_APPROVAL_KEY)
        assert state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1

        # Verify exactly-one: no duplicate events
        approval_events = [e for e in stubs.emitted_events if e[0] == "approval.granted"]
        assert len(approval_events) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_sidecar_means_fresh_start(self, tmp_path: Path) -> None:
        """restore_from returns None when no sidecar file exists."""
        state_path = tmp_path / "does-not-exist.json"
        result = LifecycleManager.restore_from(state_path)
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_approval_without_idempotency_cache(self, tmp_path: Path) -> None:
        """Approval works even without idempotency cache (cache=None)."""
        stubs = _StubCallbacks()
        state_path = tmp_path / "lifecycle.json"

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)
        state = await mgr.handle_approval(_APPROVAL_KEY)
        assert state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_approval_before_restart_duplicate_after_restore(
        self,
        tmp_path: Path,
    ) -> None:
        """Approval processed before crash → restore → duplicate approval is deduped."""
        cache = await _make_cache()
        stubs = _StubCallbacks()
        state_path = tmp_path / ".oh-my-bmad-lifecycle.json"

        # Phase 1: original process — approval arrives and completes
        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)
        await mgr.handle_approval(_APPROVAL_KEY)
        assert mgr.current_state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1

        # Phase 2: simulate restart — restore from sidecar (COMPLETED)
        restored = LifecycleManager.restore_from(
            state_path,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        assert restored is not None
        assert restored.current_state == WorkerState.COMPLETED

        # Phase 3: duplicate approval with same key — must be deduped
        state = await restored.handle_approval(_APPROVAL_KEY)
        assert state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1  # no extra execution

        approval_events = [e for e in stubs.emitted_events if e[0] == "approval.granted"]
        assert len(approval_events) == 1  # no extra events


# ---------------------------------------------------------------------------
# AC-2: Retry storm — 10 rapid approvals, exactly once
# ---------------------------------------------------------------------------


class TestRetryStorm:
    """AC-2: 10 concurrent approvals with same key → exactly one execution."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_retry_storm_exactly_once(self, tmp_path: Path) -> None:
        cache = await _make_cache()
        stubs = _StubCallbacks()
        state_path = tmp_path / "lifecycle.json"

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)

        # Fire 10 concurrent approvals with the SAME idempotency key
        results = await asyncio.gather(
            *[mgr.handle_approval(_APPROVAL_KEY) for _ in range(10)],
        )

        # All return COMPLETED (the FSM is shared)
        assert all(s == WorkerState.COMPLETED for s in results)

        # Exactly one gated action executed
        assert stubs.gated_action_calls == 1

        # Exactly one approval.granted event emitted
        approval_events = [e for e in stubs.emitted_events if e[0] == "approval.granted"]
        assert len(approval_events) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_different_keys_execute_independently(self, tmp_path: Path) -> None:
        """Different idempotency keys should each execute their own approval."""
        cache = await _make_cache()
        stubs = _StubCallbacks()
        state_path = tmp_path / "lifecycle.json"

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)

        # First approval succeeds
        state = await mgr.handle_approval("key-A")
        assert state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1

        # Second approval with different key — FSM is already COMPLETED,
        # so this will raise InvalidTransitionError (terminal state).
        with pytest.raises(InvalidTransitionError):
            await mgr.handle_approval("key-B")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_persist_and_restore_roundtrip(self, tmp_path: Path) -> None:
        state_path = tmp_path / "lifecycle.json"
        stubs = _StubCallbacks()

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
        )
        await mgr.handle_event(LifecycleEvent.TASK_PAUSED)
        assert state_path.exists()

        restored = LifecycleManager.restore_from(state_path)
        assert restored is not None
        assert restored.current_state == WorkerState.PAUSED

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_approval_flow(self, tmp_path: Path) -> None:
        """Canonical RUNNING → AWAITING_APPROVAL → RESUMED → COMPLETED."""
        cache = await _make_cache()
        stubs = _StubCallbacks()
        state_path = tmp_path / "lifecycle.json"

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
            idempotency_cache=cache,
        )

        assert mgr.current_state == WorkerState.RUNNING
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)
        assert mgr.current_state == WorkerState.AWAITING_APPROVAL
        await mgr.handle_approval(_APPROVAL_KEY)
        assert mgr.current_state == WorkerState.COMPLETED
        assert stubs.gated_action_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejection_flow(self, tmp_path: Path) -> None:
        """AWAITING_APPROVAL → APPROVAL_REJECTED → FAILED."""
        stubs = _StubCallbacks()
        state_path = tmp_path / "lifecycle.json"

        mgr = LifecycleManager(
            fsm=LifecycleFSM(),
            state_path=state_path,
            task_id=_TASK_ID,
            emit_event=stubs.emit_event,
            gated_action=stubs.gated_action,
        )
        await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)
        await mgr.handle_event(LifecycleEvent.APPROVAL_REJECTED)
        assert mgr.current_state == WorkerState.FAILED
        assert stubs.gated_action_calls == 0
