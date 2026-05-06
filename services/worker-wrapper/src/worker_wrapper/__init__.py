"""worker-wrapper — Claude Code CLI subprocess supervisor; emits typed events via MCP (no stdout parsing per NFR-O1); hosts the resume-after-approval HIGH-RISK state machine.

Story 1.2 ships only `__version__`. Story 2.12 adds the atomic-edit primitive
(``worker_wrapper.domain.atomic_edit``) per FR30 / NFR-R2. Real wrapper logic
arrives in: Stories 5.1–5.18 (Claude Code wrapper, lifecycle, S-1/S-2 separability).
"""

from __future__ import annotations

# __version__ is assigned BEFORE the re-export to guarantee a fully
# initialized module attribute even if a sub-module ever imports
# ``worker_wrapper.__version__`` during its own import.  Re-export order
# matters here — see Story 2.12 code-review M16.
__version__ = "0.3.0"

from worker_wrapper.domain.atomic_edit import atomic_write_bytes, atomic_write_text

__all__ = [
    "__version__",
    "atomic_write_bytes",
    "atomic_write_text",
]
