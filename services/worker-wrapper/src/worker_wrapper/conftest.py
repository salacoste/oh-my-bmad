"""Shared pytest fixtures for worker-wrapper tests.

Story 9.6 review pass-2 PH5: a single autouse fixture replaces the four
duplicate ``_clean_trace_id_env`` fixtures previously scattered across
``test_config.py``, ``test_session_lifecycle.py``, ``test_run_task.py``,
and ``test_claude_code_runner.py``.  The env-var name list is referenced
from :mod:`worker_wrapper.app.config` so the fixture stays in sync with
the actual ``AliasChoices`` and the H2 flag env var (review pass-2 PM3 /
PM8) when those names change.
"""

from __future__ import annotations

import pytest

from worker_wrapper.app.config import _EMIT_TRACE_ID_FLAG_ENV, _TRACE_ID_ALIASES


@pytest.fixture(autouse=True)
def _clean_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all trace_id-related env vars so tests are deterministic.

    Story 9.6 review pass-2 PH5 / PM3 / PM8: previously the dev shell or CI
    runner could leak ``WORKER_TRACE_ID`` / ``OMB_*_TRACE_ID`` /
    ``WORKER_EMIT_TRACE_ID_FLAG`` into a :class:`WorkerSettings` construction,
    silently flipping the AC9 flag-gating or polluting the resolved trace_id.
    The autouse fixture clears all of them per test.
    """
    for name in _TRACE_ID_ALIASES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(_EMIT_TRACE_ID_FLAG_ENV, raising=False)
    # Backwards-compat alias from the PH7 rename — also clear so tests
    # constructed via the legacy name don't surprise-pass.
    monkeypatch.delenv("WORKER_WORKER_EMIT_TRACE_ID_FLAG", raising=False)
