"""worker-wrapper domain layer (Story 2.12+).

Hosts pure-Python primitives consumed by worker-wrapper's runtime modules.
First inhabitant: :mod:`worker_wrapper.domain.atomic_edit` (Story 2.12 — FR30).

Future inhabitants land in Epic 5 (state machine, lifecycle, resume-after-approval).
"""

from __future__ import annotations

from worker_wrapper.domain.atomic_edit import atomic_write_bytes, atomic_write_text

__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
]
