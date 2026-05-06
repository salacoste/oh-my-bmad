"""worker-wrapper domain layer (Story 2.12+).

Hosts pure-Python primitives consumed by worker-wrapper's runtime modules.
First inhabitant: :mod:`worker_wrapper.domain.atomic_edit` (Story 2.12 — FR30).

Story 5.3 adds: :mod:`worker_wrapper.domain.worktree_lock` (FR27, FR32).
Future inhabitants: state machine, lifecycle, resume-after-approval.
"""

from __future__ import annotations

from worker_wrapper.domain.atomic_edit import atomic_write_bytes, atomic_write_text
from worker_wrapper.domain.worktree_lock import (
    WorktreeLockHeld,
    acquire_lock,
    is_lock_held,
    read_lock,
    release_lock,
)

__all__ = [
    "WorktreeLockHeld",
    "acquire_lock",
    "atomic_write_bytes",
    "atomic_write_text",
    "is_lock_held",
    "read_lock",
    "release_lock",
]
