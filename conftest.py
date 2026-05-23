"""Root-level pytest conftest for the oh-my-bmad monorepo.

Centralizes session-scoped fixtures and hooks that ALL tests across the repo
(services/, packages/, tests/, mcp-servers/) need. Currently:

- ``_ensure_event_types_registered`` — session-scoped autouse that calls
  ``registry_state.domain.event_types.ensure_registered()`` exactly
  once at session start. Replaces the per-file fixture pattern introduced
  by Story 7e4ffec; this is the Story 8.7.5 consolidation.

- ``pytest_sessionfinish`` — Story 8.7.6 hook that drains aiosqlite daemon
  worker threads at session end. Hook (vs. autouse fixture) ordering
  guarantees it runs AFTER pytest-asyncio's loop-close teardown. Lives at
  repo root so it applies to ALL ``testpaths`` (PP1 fix — was previously in
  ``tests/conftest.py`` which only applied to tests/ collection).

- ``_assert_no_leaked_tasks_after_test`` — Story 8.7.6 PP7 function-scoped
  autouse that warns if a test leaks background asyncio.Task instances into
  the next test's module-scoped loop.

NOTE (Story 8.7.5 PP5 — xdist not supported): pytest-xdist parallel workers
are NOT currently supported. This session-scoped fixture creates per-worker
registry state but ``unregister_all()``-based test fixtures may behave
unexpectedly under parallel execution. If you need xdist, file a follow-up
Story. See docs/testing-guide.md "pytest-xdist parallel workers not supported".

NOTE (Story 8.7.5 PP8 — critical dependency): this module imports from
``registry_state.domain.event_types``. If that import fails, all tests
fail-fast — by design. There is no graceful fallback.
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
import warnings
from collections.abc import Generator

import pytest

# Story 8.7.5 — register all event types once per session.
# NOTE: this import is cross-service (root conftest imports from services/) but
# is the canonical location for the registry — same pattern as the per-file
# fixtures we're replacing.
from registry_state.domain.event_types import ensure_registered  # noqa: IMP001


@pytest.fixture(scope="session", autouse=True)
def _ensure_event_types_registered() -> None:
    """Story 8.7.5 — call ensure_registered() exactly once per test session.

    Tests that `unregister_all()` mid-run (e.g., packages/events/test_canonical.py)
    are responsible for restoring registry state at function-scope; this
    session-scoped fixture does NOT auto-restore between tests.
    """
    ensure_registered()


# ---------------------------------------------------------------------------
# Story 8.7.6 — aiosqlite daemon-thread drain at session end (PP1 + PP6)
#
# Combined with asyncio_default_fixture_loop_scope = "module" in pyproject.toml,
# this hook reduces the interpreter-shutdown race that previously required the
# exit-134 SIGABRT tolerance shim in .github/workflows/ci.yml.
#
# Root cause: aiosqlite spawns a daemon worker thread per connection. Each
# thread holds a reference to its asyncio event loop. When the pytest session
# ends and the interpreter starts shutdown, daemon threads that haven't yet
# noticed their loop is closed race the stderr buffer teardown — producing a
# SIGABRT (exit 134) AFTER pytest has already printed its clean-pass summary.
#
# PP6: registered as ``pytest_sessionfinish`` (not session-scoped autouse) so
# it runs AFTER all fixtures including pytest-asyncio's loop-close teardown,
# ensuring daemon threads have been signaled to exit before we poll for them.
# PP1: lives in repo-root conftest.py so it applies to ALL testpaths (was
# previously in tests/conftest.py — only applied to tests/ collection).
# ---------------------------------------------------------------------------


def _is_aiosqlite_worker(t: threading.Thread) -> bool:
    """Story 8.7.6 PP3 — match aiosqlite worker threads defensively.

    Primary: thread name contains ``_connection_worker_thread`` (Python's
    auto-naming when ``Thread(target=...)`` is constructed without ``name=``).

    Fallback: thread's ``_target`` callable is from the ``aiosqlite`` module.
    Protects against future upstream renames or explicit ``name=`` arguments
    that would silently turn the drain into a no-op.
    """
    if "_connection_worker_thread" in t.name:
        return True
    target = getattr(t, "_target", None)
    if target is not None:
        module = getattr(target, "__module__", "") or ""
        if module.startswith("aiosqlite"):
            return True
    return False


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Story 8.7.6 PP6 — drain aiosqlite worker threads at session end.

    Runs AFTER all fixtures (including pytest-asyncio's loop-close) so the
    daemon threads have been signaled to exit before we poll for them. The
    drain waits up to ``OMB_AIOSQLITE_DRAIN_TIMEOUT_S`` seconds (default 3.0);
    in practice threads exit within 100-200ms once their loop closes.

    PP5: gc.collect() is called PER-ITERATION so cyclic refs needing multiple
    GC waves are handled. PP4: on timeout, emits a loud
    ``PytestPendingWarning`` listing live thread names so the SIGABRT race
    risk is never silent.
    """
    timeout_s = float(os.environ.get("OMB_AIOSQLITE_DRAIN_TIMEOUT_S", "3.0"))
    deadline = time.monotonic() + timeout_s

    live: list[threading.Thread] = []
    while time.monotonic() < deadline:
        # PP5 — per-iteration gc.collect() catches multi-wave cyclic refs.
        gc.collect()
        live = [t for t in threading.enumerate() if _is_aiosqlite_worker(t)]
        if not live:
            return
        time.sleep(0.05)

    # PP4 — loud warning on drain timeout to prevent silent SIGABRT regression.
    if live:
        thread_names = ", ".join(t.name for t in live)
        warnings.warn(
            f"aiosqlite drain timed out with {len(live)} live worker thread(s): "
            f"{thread_names}. SIGABRT race risk on interpreter shutdown. "
            f"Tune via OMB_AIOSQLITE_DRAIN_TIMEOUT_S (currently {timeout_s}s).",
            category=pytest.PytestPendingWarning,
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# Story 8.7.6 PP7 — bounded asyncio task assertion after each test
#
# Defensive function-scoped autouse that warns if a test leaks background
# asyncio.Task instances into the next test's module-scoped loop. With
# ``asyncio_default_fixture_loop_scope = "module"``, tasks created on the
# module loop persist across tests in the same module — silent failure mode
# that was the root cause of the 3 fixtures patched in Phase 1.
#
# This is a defensive warning, NOT a failure. Tune via env var
# OMB_LEAKED_TASKS_FATAL=1 to escalate to assertion if needed.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _assert_no_leaked_tasks_after_test() -> Generator[None, None, None]:
    """Story 8.7.6 PP7 — warn on leaked background asyncio tasks per test.

    Detects tests that leave background asyncio.Task instances on the
    module-scoped loop (which would then bleed into subsequent tests in the
    same module). If OMB_LEAKED_TASKS_FATAL=1 is set, escalates to an
    assertion failure for use in dedicated audit runs.
    """
    yield
    try:
        # asyncio.get_event_loop() raises DeprecationWarning in 3.12+ when no
        # current loop exists; use a try/except guard rather than the new API.
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        return  # no loop in this test
    if loop.is_closed():
        return
    try:
        tasks = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    # Filter to background tasks that are still active. asyncio.current_task()
    # requires being inside a running loop, so we can't safely call it here —
    # any non-done task is a candidate leak.
    leaked = [t for t in tasks if not t.done()]
    if leaked:
        msg = (
            f"test leaked {len(leaked)} background asyncio.Task(s); "
            "future tests in same module-scoped loop will see them. "
            "Add loop_scope='function' to the offending fixture."
        )
        if os.environ.get("OMB_LEAKED_TASKS_FATAL") == "1":
            raise AssertionError(msg)
        warnings.warn(msg, category=pytest.PytestPendingWarning, stacklevel=2)
