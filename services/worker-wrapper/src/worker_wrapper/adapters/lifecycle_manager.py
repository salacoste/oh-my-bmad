"""Lifecycle manager adapter — wraps LifecycleFSM with IO (Story 5.17b).

Couples the pure domain FSM to cross-restart state persistence (FR29),
idempotent approval handling (FR28), and event emission. The domain
module (``lifecycle.py``) remains untouched — all IO lives here.

Production usage wires ``emit_event`` to ``clawhip_bridge.call_tool``
and ``gated_action`` to git push + PR creation. Tests inject stubs.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from worker_wrapper.domain.lifecycle import (
    InvalidTransitionError,
    LifecycleEvent,
    LifecycleFSM,
    WorkerState,
)

if TYPE_CHECKING:
    from idempotency import IdempotencyCacheStore

    from worker_wrapper.domain.lifecycle import TransitionLogEntry

logger = structlog.get_logger(__name__)


class LifecycleManager:
    """Adapter wrapping :class:`LifecycleFSM` with IO operations.

    Parameters
    ----------
    fsm:
        The domain FSM instance.
    state_path:
        Path to the sidecar JSON file for state persistence (FR29).
    task_id:
        The task this manager is bound to.
    emit_event:
        Async callback ``emit_event(type: str, payload: dict) -> str``
        (typically wraps ``clawhip_bridge.call_tool("emit_event", ...)``).
    gated_action:
        Async callback that executes the gated action (git push + PR draft).
    idempotency_cache:
        Optional idempotency store for approval deduplication (FR28).
    """

    def __init__(
        self,
        *,
        fsm: LifecycleFSM,
        state_path: Path,
        task_id: str,
        emit_event: Callable[[str, dict], Awaitable[str]] | None = None,
        gated_action: Callable[[], Awaitable[None]] | None = None,
        idempotency_cache: IdempotencyCacheStore | None = None,
    ) -> None:
        self._fsm = fsm
        self._state_path = state_path
        self._task_id = task_id
        self._emit_event = emit_event
        self._gated_action = gated_action
        self._cache = idempotency_cache
        self._gated_action_count = 0

    @property
    def current_state(self) -> WorkerState:
        return self._fsm.current_state

    @property
    def transition_log(self) -> tuple[TransitionLogEntry, ...]:
        return self._fsm.transition_log

    @property
    def gated_action_count(self) -> int:
        """Number of times the gated action actually executed."""
        return self._gated_action_count

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_event(self, event: LifecycleEvent) -> WorkerState:
        """Apply FSM transition, persist state, emit event."""
        try:
            new_state = self._fsm.transition(event)
        except InvalidTransitionError:
            logger.error(
                "invalid_transition",
                task_id=self._task_id,
                fsm_event=event.value,
                current=self._fsm.current_state.value,
            )
            raise
        self._persist_state()
        if self._emit_event is not None:
            await self._emit_event(
                event.value,
                {"task_id": self._task_id, "state": new_state.value},
            )
        logger.info("lifecycle_event", fsm_event=event.value, state=new_state.value)
        return new_state

    async def handle_approval(self, idempotency_key: str) -> WorkerState:
        """Idempotent approval processing (FR28).

        Uses :meth:`IdempotencyCacheStore.get_or_run` to guarantee
        exactly-once execution under retry storms (AC-2 / NFR-R4).
        """
        if self._cache is None:
            await self._execute_approval()
            return self._fsm.current_state

        _, was_new = await self._cache.get_or_run(
            key=idempotency_key,
            request_id=self._task_id,
            factory=self._execute_approval,
        )
        # Cache hit on a restored FSM that never reached COMPLETED — fast-forward.
        # Guard: only apply APPROVAL_GRANTED if the FSM is in a state that
        # accepts it (AWAITING_APPROVAL). If the sidecar was written before
        # AWAITING_APPROVAL was persisted, the FSM might be in RUNNING where
        # APPROVAL_GRANTED is an invalid transition.
        if not was_new and self._fsm.current_state != WorkerState.COMPLETED:
            try:
                transitioned = False
                if self._fsm.current_state == WorkerState.AWAITING_APPROVAL:
                    self._fsm.transition(LifecycleEvent.APPROVAL_GRANTED)
                    transitioned = True
                if self._fsm.current_state in {WorkerState.RUNNING, WorkerState.RESUMED}:
                    self._fsm.transition(LifecycleEvent.TASK_COMPLETED)
                    transitioned = True
                if transitioned:
                    self._gated_action_count += 1
                    self._persist_state()
            except InvalidTransitionError:
                logger.warning(
                    "fast_forward_skipped",
                    task_id=self._task_id,
                    current_state=self._fsm.current_state.value,
                )
        logger.info("approval_processed", idem_key=idempotency_key, was_new=was_new)
        return self._fsm.current_state

    async def resume_gated_action(self) -> None:
        """Execute the gated action (git push + PR draft) exactly once."""
        if self._gated_action is not None:
            await self._gated_action()
            self._gated_action_count += 1
        await self.handle_event(LifecycleEvent.TASK_COMPLETED)
        logger.info("gated_action_executed")

    # ------------------------------------------------------------------
    # State persistence (FR29)
    # ------------------------------------------------------------------

    def _persist_state(self) -> None:
        """Write FSM state to sidecar file for restart recovery (atomic)."""
        data = json.dumps(
            {"state": self._fsm.current_state.value, "task_id": self._task_id},
        )
        fd, tmp = tempfile.mkstemp(dir=self._state_path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, self._state_path)
        except BaseException:
            os.unlink(tmp)
            raise

    @classmethod
    def restore_from(
        cls,
        state_path: Path,
        **kwargs,
    ) -> LifecycleManager | None:
        """Restore from sidecar file, or ``None`` if not found or corrupt."""
        if not state_path.exists():
            return None
        try:
            data = json.loads(state_path.read_text())
            state = WorkerState(data["state"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("corrupt_sidecar", path=str(state_path), error=str(exc))
            return None
        task_id = data.get("task_id", kwargs.get("task_id", ""))
        fsm = LifecycleFSM(initial_state=state)
        return cls(fsm=fsm, state_path=state_path, task_id=task_id, **kwargs)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_approval(self) -> str:
        """Execute approval: transition + emit + gated action.

        Returns a result event ID for the idempotency cache.
        Idempotent: skips transition if FSM already left AWAITING_APPROVAL.
        """
        if self._fsm.current_state == WorkerState.AWAITING_APPROVAL:
            new_state = self._fsm.transition(LifecycleEvent.APPROVAL_GRANTED)
            self._persist_state()
        else:
            new_state = self._fsm.current_state
        result_event_id = f"approval-{self._task_id}-{len(self._fsm.transition_log)}"
        if self._emit_event is not None:
            await self._emit_event(
                "approval.granted",
                {"task_id": self._task_id, "state": new_state.value},
            )
        await self.resume_gated_action()
        return result_event_id
