"""worker-wrapper domain layer (Story 2.12+).

Hosts pure-Python primitives consumed by worker-wrapper's runtime modules.
First inhabitant: :mod:`worker_wrapper.domain.atomic_edit` (Story 2.12 — FR30).

Story 5.3 adds: :mod:`worker_wrapper.domain.worktree_lock` (FR27, FR32).
Story 5.17a adds: :mod:`worker_wrapper.domain.lifecycle` (FR36 — resume-after-approval FSM).
"""

from __future__ import annotations

from worker_wrapper.domain.approval_gate import needs_approval
from worker_wrapper.domain.atomic_edit import atomic_write_bytes, atomic_write_text
from worker_wrapper.domain.lifecycle import (
    InvalidTransitionError,
    LifecycleEvent,
    LifecycleFSM,
    TransitionLogEntry,
    WorkerState,
)
from worker_wrapper.domain.worktree_lock import (
    WorktreeLockHeld,
    acquire_lock,
    is_lock_held,
    read_lock,
    release_lock,
)

__all__ = [
    "InvalidTransitionError",
    "needs_approval",
    "LifecycleEvent",
    "LifecycleFSM",
    "TransitionLogEntry",
    "WorktreeLockHeld",
    "WorkerState",
    "acquire_lock",
    "atomic_write_bytes",
    "atomic_write_text",
    "is_lock_held",
    "read_lock",
    "release_lock",
]
