"""Tree-specific fixtures for the S-3 separability harness (Story 2.15).

Provides:

* :func:`skip_if_no_docker` — session-scoped fixture that probes
  ``docker info`` (30s timeout) and skips the calling test when the
  Docker daemon is unavailable. Mirrors the Story 2.11 crash-injection
  pattern; explicitly opted into by tests that need Docker (the
  git-diff sentinel test does NOT request it so it runs unconditionally).

The leading underscore convention from Story 2.11 (``_skip_if_no_docker``)
was dropped after EM5 to keep pytest's fixture-resolution pleasant.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ``tests/separability/`` is a flat directory (no ``__init__.py``) so that the
# pytest ``--import-mode=importlib`` setup we use elsewhere also works here.
# Adding this directory to ``sys.path`` makes sibling modules
# (``_build_null_orchestrator``, future ``_s3_compose`` if added) importable
# from the test files via plain ``import _build_null_orchestrator`` rather
# than awkward path manipulation in each test file. The conftest runs before
# pytest collects the tree so this insert is reliable.
_THIS_DIR: Path = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))


@pytest.fixture(scope="session")
def skip_if_no_docker() -> None:
    """Skip a test when ``docker info`` fails or times out.

    Explicit-opt-in: tests requesting Docker declare ``skip_if_no_docker``
    as a parameter; tests that don't (e.g., the git-diff sentinel) omit
    it so they run regardless of Docker availability.

    Session-scoped so the ``docker info`` probe runs at most once per
    pytest invocation. The 30s timeout accommodates cold Docker Desktop
    start-up on macOS (15-30s typical).
    """
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=30.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(
            "Story 2.15 S-3 separability test requires Docker — install or "
            f"run via CI (docker info failed: {exc!r})"
        )
    if proc.returncode != 0:
        pytest.skip(
            "Story 2.15 S-3 separability test requires Docker — install or "
            f"run via CI (docker info exit={proc.returncode})"
        )
