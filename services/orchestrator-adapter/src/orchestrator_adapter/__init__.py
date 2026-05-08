"""orchestrator-adapter — Subprocess supervisor for the vendored OMC orchestrator; translates platform-task events into the OMC contract without leaking OMC into registry/worker code.

Story 1.2 ships only ``__version__``. Story 5.10 adds OMC subprocess supervision via ``OMCRunner`` and task-dispatch translation.
"""

from __future__ import annotations

__version__ = "0.2.0"

from orchestrator_adapter.adapters.omc_runner import OMCRunner

__all__ = [
    "__version__",
    "OMCRunner",
]
