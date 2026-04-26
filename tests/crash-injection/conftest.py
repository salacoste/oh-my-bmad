"""Crash-injection tree fixtures (Story 2.11).

Provides:

* :func:`_skip_if_no_docker` — autouse-style fixture that calls
  ``docker info`` (5s timeout) and skips the test when the Docker
  daemon is unavailable. AC-12: local dev without Docker keeps
  ``just test`` green.
* :func:`crash_summary_collector` — session-scoped list each per-phase
  test appends a metrics dict to (AC-8).
* :func:`_emit_crash_summary` — session-end finalizer that writes the
  collected metrics to ``_bmad-output/test-artifacts/crash-injection-summary-<UTC>.json``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ``tests/crash-injection/`` has a hyphen in its directory name so it is NOT
# a valid Python package. With pytest's ``--import-mode=importlib`` the
# sibling modules ``_compose`` and ``_events`` are invisible to normal import
# machinery. Adding this directory to ``sys.path`` here (in the conftest that
# pytest evaluates before collecting the tree) makes the relative imports in
# ``test_restart_recovery.py`` work without any ``__init__.py`` gymnastics.
# This is the pytest-recommended approach for non-package test trees.
_THIS_DIR: Path = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# Resolve the repo root by walking up from this file.
# tests/crash-injection/conftest.py → repo root (3 parents up: file, dir, tests, root).
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _resolve_output_folder() -> Path:
    """Return the configured output_folder, falling back to ``_bmad-output``.

    Reads ``_bmad/core/config.yaml``'s ``output_folder`` line if present
    (the YAML uses a ``{project-root}`` template token which we substitute
    against this repo's root). On any read error, falls back to the
    default ``_bmad-output`` path the project ships with.
    """
    config = _REPO_ROOT / "_bmad" / "core" / "config.yaml"
    fallback = _REPO_ROOT / "_bmad-output"
    if not config.exists():
        return fallback
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("output_folder:"):
                _, _, value = stripped.partition(":")
                cleaned = value.strip().strip('"').strip("'")
                resolved = cleaned.replace("{project-root}", str(_REPO_ROOT))
                return Path(resolved)
    except OSError:
        return fallback
    return fallback


@pytest.fixture(scope="session")
def _skip_if_no_docker() -> None:
    """Skip dependent tests when ``docker info`` fails or times out.

    AC-12: a developer without Docker installed must still see
    ``just test`` pass. Tests that take this fixture (or take it
    transitively via :func:`crash_harness`) skip with a stable reason
    whenever the daemon is unreachable.
    """
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(
            "Story 2.11 crash-injection requires Docker — install or run via CI "
            f"(docker info failed: {exc!r})"
        )
    if proc.returncode != 0:
        pytest.skip(
            "Story 2.11 crash-injection requires Docker — install or run via CI "
            f"(docker info exit={proc.returncode})"
        )


@pytest.fixture(scope="session")
def crash_summary_collector() -> Iterator[list[dict[str, object]]]:
    """Session-scoped accumulator: per-phase tests append a metrics dict.

    On session teardown the finalizer writes the collected list (with
    aggregate counts + platform + kill-method) to the JSON artifact in
    ``_bmad-output/test-artifacts/``.
    """
    started_at = datetime.now(UTC).isoformat()
    collector: list[dict[str, object]] = []
    yield collector
    completed_at = datetime.now(UTC).isoformat()

    out_dir = _resolve_output_folder() / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filename uses a UTC-second timestamp; multiple back-to-back runs
    # within the same second would overwrite — acceptable for CI.
    ts_for_name = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    target = out_dir / f"crash-injection-summary-{ts_for_name}.json"

    kill_method = "sigkill" if platform.system() == "Darwin" else "stop"

    payload: dict[str, object] = {
        "harness_version": "1",
        "started_at": started_at,
        "completed_at": completed_at,
        "platform": platform.system().lower(),
        "kill_method": kill_method,
        "phases": collector,
        "passed_total": sum(1 for entry in collector if entry.get("passed")),
        "failed_total": sum(1 for entry in collector if not entry.get("passed")),
    }

    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Surface the artifact path on stdout so CI can pick it up via the
    # uploaded-artifact glob without parsing pytest's own output.
    if os.environ.get("OMB_PRINT_CRASH_ARTIFACT", "1") != "0":
        print(f"\n[crash-injection] summary artifact: {target}")
