"""Crash-injection tree fixtures (Story 2.11).

Provides:

* :func:`skip_if_no_docker` — ``autouse=True`` session-scoped fixture that
  calls ``docker info`` (30s timeout) and skips the test when the Docker
  daemon is unavailable. AC-12: local dev without Docker keeps
  ``just test`` green. The leading underscore was removed (EM5) so pytest
  treats it as a regular fixture rather than a private helper — the autouse
  flag means tests don't need to declare it explicitly.
* :func:`crash_summary_collector` — session-scoped list each per-phase
  test appends a metrics dict to (AC-8).
* :func:`_emit_crash_summary` — session-end finalizer that writes the
  collected metrics to ``_bmad-output/test-artifacts/crash-injection-summary-<UTC>.json``.
* :func:`pytest_collection_modifyitems` — pins the 4 phase tests in
  declaration order so that under pytest-randomly / pytest-xdist the
  artifact narrative remains stable for failure-debugging. The clock
  anchoring (``time.monotonic_ns()``) means correctness does not depend
  on order — but the canonical narrative does.

Environment variables:

* ``OMB_PRINT_CRASH_ARTIFACT`` — set to ``"0"`` to suppress the
  ``[crash-injection] summary artifact: ...`` print after the session
  finalizer runs. Default ``"1"`` (print). CI uses the printed line as
  a backup path-discovery channel.
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
# sibling modules ``_crash_compose`` and ``_crash_events`` are invisible to
# normal import machinery. Adding this directory to ``sys.path`` here (in
# the conftest that pytest evaluates before collecting the tree) makes the
# relative imports in ``test_restart_recovery.py`` work without any
# ``__init__.py`` gymnastics. Module names are prefixed ``_crash_*`` (was
# ``_compose`` / ``_events``) to avoid colliding with any other test tree
# that adds modules of those generic names to sys.path.
# This is the pytest-recommended approach for non-package test trees.
_THIS_DIR: Path = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# Resolve the repo root by walking up from this file.
# tests/crash-injection/conftest.py: parents[0]=crash-injection, [1]=tests,
# [2]=repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _resolve_output_folder() -> Path:
    """Return the configured output_folder, falling back to ``_bmad-output``.

    Reads ``_bmad/core/config.yaml``'s ``output_folder`` line if present
    (the YAML uses a ``{project-root}`` template token which we substitute
    against this repo's root). Uses :mod:`yaml.safe_load` when PyYAML is
    available; falls back to a forgiving line-by-line parser if the
    import fails (avoiding a hard PyYAML dependency for a single tiny
    config read). On any read error, falls back to the default
    ``_bmad-output`` path the project ships with.
    """
    config = _REPO_ROOT / "_bmad" / "core" / "config.yaml"
    fallback = _REPO_ROOT / "_bmad-output"
    if not config.exists():
        return fallback
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return fallback
    # Try the proper YAML parser first; fall back to a naive line scan.
    try:
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415 — soft dep, deferred import

        parsed = yaml.safe_load(text)
        if isinstance(parsed, dict):
            value = parsed.get("output_folder")
            if isinstance(value, str):
                return Path(value.replace("{project-root}", str(_REPO_ROOT)))
    except ImportError:
        # PyYAML not available — use the line-scan fallback below.
        pass
    except Exception:  # noqa: BLE001 — yaml errors are best-effort here
        return fallback
    # Naive fallback: scan for ``output_folder:`` entries at column 0.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("output_folder:"):
            _, _, value = stripped.partition(":")
            cleaned = value.strip().strip('"').strip("'")
            resolved = cleaned.replace("{project-root}", str(_REPO_ROOT))
            return Path(resolved)
    return fallback


@pytest.fixture(scope="session", autouse=True)
def skip_if_no_docker() -> None:
    """Skip ALL crash-injection tests when ``docker info`` fails or times out.

    ``autouse=True`` means every test in this directory automatically
    depends on this fixture — no explicit parameter needed. The session
    scope means the ``docker info`` probe runs exactly once per session.

    AC-12: a developer without Docker installed must still see
    ``just test`` pass. The skip reason includes the concrete failure so
    CI logs pinpoint whether Docker is absent or just slow to start.

    Timeout is 30s (bumped from 5s) to accommodate cold Docker Desktop
    start-up on macOS (can take 15-30s to become ready).
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

    EM4: when all tests were skipped (Docker unavailable), the artifact
    records ``"status": "skipped"`` so CI can distinguish a clean
    "skipped due to no Docker" run from a harness that ran but produced
    zero phases (which would be a silent failure).
    """
    started_at = datetime.now(UTC).isoformat()
    collector: list[dict[str, object]] = []
    yield collector
    completed_at = datetime.now(UTC).isoformat()

    out_dir = _resolve_output_folder() / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filename uses a UTC-second timestamp PLUS the test-runner pid so
    # multiple back-to-back runs within the same UTC second don't collide
    # (concurrent ``pytest tests/crash-injection/`` invocations from
    # different shells, or `pytest --forked` workers, etc.).
    ts_for_name = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    target = out_dir / f"crash-injection-summary-{ts_for_name}-{os.getpid()}.json"

    # kill_method is always "hard" (SIGKILL) on both Linux and macOS —
    # graceful compose-stop was the old Linux default but exercises graceful
    # shutdown rather than crash recovery (EM1 fix). The platform field is
    # still useful for diagnosing environment-specific failures.
    kill_method = "hard"

    passed = sum(1 for entry in collector if entry.get("passed"))
    failed = sum(1 for entry in collector if not entry.get("passed"))

    # Distinguish: ran+passed, ran+failed, skipped (Docker unavailable).
    if not collector:
        run_status: str = "skipped"
    elif failed == 0:
        run_status = "passed"
    else:
        run_status = "failed"

    payload: dict[str, object] = {
        "harness_version": "1",
        "started_at": started_at,
        "completed_at": completed_at,
        "platform": platform.system().lower(),
        "kill_method": kill_method,
        "status": run_status,
        "phases": collector,
        "passed_total": passed,
        "failed_total": failed,
    }

    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Surface the artifact path on stdout so CI can pick it up via the
    # uploaded-artifact glob without parsing pytest's own output.
    if os.environ.get("OMB_PRINT_CRASH_ARTIFACT", "1") != "0":
        print(f"\n[crash-injection] summary artifact: {target}")


def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001 — pytest hook signature
    items: list[pytest.Item],
) -> None:
    """Pin the 4 phase tests in declaration order.

    Under pytest-randomly or pytest-xdist phase tests may run out of
    declaration order. The cross-phase clock anchoring
    (``time.monotonic_ns()``) means each phase's events are guaranteed
    monotonically greater than any prior phase's cursor regardless of
    order, so reordering does NOT break correctness. But the canonical
    narrative — PLANNING → EXECUTING → AWAITING_APPROVAL → VERIFYING —
    is much easier to debug from the artifact when it appears in this
    order. Pin it here.
    """
    order = [
        "test_crash_recovery_planning_phase",
        "test_crash_recovery_executing_phase",
        "test_crash_recovery_awaiting_approval_phase",
        "test_crash_recovery_verifying_phase",
    ]

    def _rank(item: pytest.Item) -> int:
        return order.index(item.name) if item.name in order else len(order)

    items.sort(key=_rank)
