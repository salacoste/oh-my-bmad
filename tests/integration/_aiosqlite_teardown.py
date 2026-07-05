"""Helpers for sync integration harnesses that own an asyncio loop."""

from __future__ import annotations

import asyncio
import gc
import threading
import time


def _is_aiosqlite_worker(thread: threading.Thread) -> bool:
    if "_connection_worker_thread" in thread.name:
        return True
    target = getattr(thread, "_target", None)
    module = getattr(target, "__module__", "") if target is not None else ""
    return bool(module.startswith("aiosqlite"))


def drain_aiosqlite_workers_before_loop_close(
    loop: asyncio.AbstractEventLoop,
    *,
    timeout_s: float = 1.0,
) -> None:
    """Let aiosqlite worker threads finish before a sync harness closes its loop.

    Several Hypothesis-style integration harnesses own a private event loop and
    close it in fixture teardown. aiosqlite worker threads report results back to
    the loop with ``call_soon_threadsafe``; closing the loop immediately after
    ASGI lifespan teardown can race those callbacks and make pytest report
    ``PytestUnhandledThreadExceptionWarning`` at the next test or session exit.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        gc.collect()
        if not loop.is_closed():
            loop.run_until_complete(asyncio.sleep(0))
        live = [thread for thread in threading.enumerate() if _is_aiosqlite_worker(thread)]
        if not live or time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def current_event_loop_or_none() -> asyncio.AbstractEventLoop | None:
    """Return the thread's current loop without creating one on Python 3.12+."""
    policy = asyncio.get_event_loop_policy()
    local = getattr(policy, "_local", None)
    loop = getattr(local, "_loop", None)
    return loop if isinstance(loop, asyncio.AbstractEventLoop) else None
