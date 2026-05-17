"""Ratchet test: registry-state must not spawn subprocesses without
trace_id propagation.

Story 9.6 review pass-2 PH8: pass-1 H0 was marked complete because no
spawn site existed in registry-state at the time, but absence-of-evidence
is not evidence-of-absence.  This test asserts that registry-state stays
free of process-spawn primitives — if a future commit adds one, the
ratchet trips and the author is reminded to propagate ``WORKER_TRACE_ID``
through the spawn env (mirroring Story 9.6 PH0's ``OMCRunner`` fix in
orchestrator-adapter).
"""

from __future__ import annotations

import re
from pathlib import Path

_SPAWN_PATTERNS = re.compile(
    r"subprocess\.Popen"
    r"|create_subprocess_exec"
    r"|create_subprocess_shell"
    r"|subprocess\.run"
    r"|subprocess\.call"
    r"|os\.exec[lv]"
    r"|os\.spawn[lv]"
)

# This file lives at ``services/registry-state/src/registry_state/test_no_subprocess_spawn.py``.
# Scan the ``registry_state`` package directory (siblings of this file).
_SRC_ROOT = Path(__file__).parent


def test_no_subprocess_spawn_in_registry_state() -> None:
    """If you're adding a spawn site, also propagate ``WORKER_TRACE_ID`` via
    env (Epic 9 / Story 9.6 PH0 / PH8)."""
    offenders: list[str] = []
    for py in _SRC_ROOT.rglob("*.py"):
        # Skip this test file itself — its regex source contains the pattern.
        if py.resolve() == Path(__file__).resolve():
            continue
        text = py.read_text(encoding="utf-8")
        if _SPAWN_PATTERNS.search(text):
            offenders.append(str(py.relative_to(_SRC_ROOT)))
    assert not offenders, (
        "Registry-state must not spawn subprocesses (Story 9.6 PH0/PH8): "
        f"{offenders}. If you're adding a worker spawn site, propagate "
        "WORKER_TRACE_ID via env (see orchestrator-adapter/OMCRunner for "
        "the canonical pattern)."
    )
