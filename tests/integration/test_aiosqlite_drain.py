"""Story 8.7.6 PP11 — regression test for session-end aiosqlite drain.

Spawns a subprocess that uses aiosqlite + per-loop pattern (mimicking the
pytest-asyncio per-test-loop topology that caused the original SIGABRT
race). Asserts the subprocess exits cleanly (0) rather than with SIGABRT
(134).

If the drain logic in repo-root ``conftest.py`` (Story 8.7.6 PP1/PP3/PP6)
is removed or regressed, this subprocess CAN still exit clean — because
the subprocess uses only ~20 loops, not the ~2376 of the full test suite.
However, the test still acts as a sanity gate that aiosqlite itself
doesn't SIGABRT under the basic pattern. The real regression detection
remains the full ``pytest -m "not slow"`` run in CI.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_aiosqlite_subprocess_exits_clean_no_sigabrt() -> None:
    """Subprocess that uses aiosqlite + per-loop pattern must exit clean.

    Reproduces the test-time topology that previously produced exit 134
    on Linux CI. Reduced to 20 loops for fast execution; full-suite
    regression is the green ``pytest -m "not slow"`` CI gate.
    """
    script = textwrap.dedent(
        """
        import asyncio
        import aiosqlite


        async def use_db() -> None:
            async with aiosqlite.connect(":memory:") as db:
                await db.execute("CREATE TABLE t (x INT)")
                await db.commit()


        # Mimic pytest-asyncio creating per-test event loops.
        for _ in range(20):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(use_db())
            finally:
                loop.close()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}; expected 0; stderr: {result.stderr.decode()[:500]}"
    )
    # Explicit 134 check for clarity in test failure output.
    assert result.returncode != 134, "SIGABRT regression — aiosqlite teardown race returned"
