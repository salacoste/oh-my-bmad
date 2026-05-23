"""Shared fixtures for integration test harnesses.

Provides:

* :func:`skip_if_no_docker` — session-scoped fixture that probes
  ``docker info`` (30s timeout) and skips the calling test when the
  Docker daemon is unavailable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add tests/integration/ to sys.path so build scripts like
# _build_auto_approval.py are importable from test files.
_THIS_DIR: Path = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))


@pytest.fixture(scope="session")
def skip_if_no_docker() -> None:
    """Skip a test when ``docker info`` fails or times out."""
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
            "Integration test requires Docker — install or "
            f"run via CI (docker info failed: {exc!r})"
        )
    if proc.returncode != 0:
        pytest.skip(
            "Integration test requires Docker — install or "
            f"run via CI (docker info exit={proc.returncode})"
        )


# Note: Story 11.2.1 PP8 extracted shared SQLite test helpers to
# ``tests/integration/_db_helpers.py`` (sibling module pattern that
# mirrors ``_compose_helpers.py``). Importing helpers from a conftest
# is unusual — pytest treats conftest as fixture-special, so prefer a
# regular module for re-usable plain functions.
