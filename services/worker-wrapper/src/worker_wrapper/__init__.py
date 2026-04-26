"""worker-wrapper — Claude Code CLI subprocess supervisor; emits typed events via MCP (no stdout parsing per NFR-O1); hosts the resume-after-approval HIGH-RISK state machine.

Story 1.2 ships only `__version__`. Story 2.12 adds the atomic-edit primitive
(``worker_wrapper.domain.atomic_edit``) per FR30 / NFR-R2. Real wrapper logic
arrives in: Stories 5.1–5.18 (Claude Code wrapper, lifecycle, S-1/S-2 separability).
"""

from __future__ import annotations

from worker_wrapper.domain.atomic_edit import atomic_write_bytes, atomic_write_text

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "atomic_write_bytes",
    "atomic_write_text",
]
