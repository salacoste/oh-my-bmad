"""Story 11.3.8 / AC5 — regression test for the events/ dir permission gap.

This is **the** regression gate that proves the Story 11.3.7-Task-7
discovery is closed: on a brand-new named volume, the FIRST
``POST /v1/tasks`` against registry-api must return 201, not 500-with-
``PermissionError: [Errno 13] Permission denied:
'/var/lib/oh-my-bmad/registry/events/2026-05-30.jsonl'``.

The bug: ``EventLogWriter.__init__`` (and metrics-subscriber's
``__main__.py:230``) used to call ``base_dir.mkdir(parents=True,
exist_ok=True)`` without an explicit mode. Python's ``Path.mkdir``
defaults to ``0o777 & ~umask`` → typically ``0o755``. Whichever service
in the ``omb`` group lost the cold-boot race (and didn't get to create
the dir) was then locked out of writing into it. Story 11.3.8 wraps
both sites in :func:`events.ensure_shared_dir` which mkdirs THEN
chmods ``0o2775`` (setgid + group-write).

Test shape mirrors ``tests/separability/test_s4_metrics_subscriber_optional.py``
Phase 1 for the compose-lifecycle scaffolding (same ``_wait_for_all_healthy``
poll, same ``try/finally: docker compose down -v --remove-orphans``
discipline, same hermetic env injection). Additional asserts:

1. POST /v1/tasks against registry-api returns 201 (the prod gap).
2. ``ls -ld /var/lib/oh-my-bmad/registry/events/`` inside the
   registry-api container shows mode ``drwxrwsr-x`` (setgid + group-
   write present) — the proof-of-fix at the filesystem layer.

``@pytest.mark.slow + @pytest.mark.integration`` — Docker boot ~30s +
healthcheck arming budget per S-4 pattern (~180s).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_log = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_ROOT_COMPOSE_FILE: Path = _REPO_ROOT / "docker-compose.yml"

# Per S-4 pattern: 180s budget covers cold healthcheck arming on macOS.
_HEALTHCHECK_TIMEOUT_S: float = 180.0

# All 7 services in the ROOT compose must reach healthy before POST /v1/tasks.
_EXPECTED_SERVICES: tuple[str, ...] = (
    "registry-api",
    "registry-state",
    "telegram-gateway",
    "orchestrator-adapter",
    "worker-wrapper",
    "clawhip-daemon",
    "metrics-subscriber",
)


def _compose_cmd(project: str, compose_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-p", project, "-f", str(compose_file), *args]


def _docker_available() -> bool:
    """Two-probe Docker availability check (engine + compose v2 plugin).

    Mirrors ``test_s4_metrics_subscriber_optional._docker_available`` — the
    compose v2 plugin is a separate install on some hosts, so probing both
    avoids opaque mid-test failures.
    """
    try:
        info = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10.0, check=False, text=True
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if info.returncode != 0:
        return False
    try:
        cv = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return cv.returncode == 0


def _wait_for_all_healthy(
    project: str,
    compose_file: Path,
    env: dict[str, str],
    *,
    timeout_s: float,
    expected_services: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Poll ``docker compose ps --format json`` until expected_services healthy."""
    deadline = time.monotonic() + timeout_s
    last_state: list[dict[str, Any]] = []
    expected_set = set(expected_services)
    while time.monotonic() < deadline:
        proc = subprocess.run(
            _compose_cmd(project, compose_file, "ps", "--format", "json"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            time.sleep(1.0)
            continue
        text = proc.stdout.strip()
        services: list[dict[str, Any]] = []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    services = [s for s in parsed if isinstance(s, dict)]
            except json.JSONDecodeError:
                services = []
        else:
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    services.append(obj)
        last_state = services
        seen = {s.get("Service") for s in services if isinstance(s.get("Service"), str)}
        if (
            services
            and expected_set.issubset(seen)
            and all(
                s.get("Health") == "healthy" for s in services if s.get("Service") in expected_set
            )
        ):
            return services
        time.sleep(1.0)
    raise TimeoutError(
        f"compose project {project!r}: not all expected services {expected_set!r} "
        f"became healthy within {timeout_s}s; last state={last_state!r}"
    )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_available(),
    reason="event-log-dir permission regression requires Docker + compose v2 plugin",
)
def test_fresh_volume_post_v1_tasks_returns_201_not_500(
    skip_if_no_docker: None,  # noqa: ARG001 — session-scoped fixture from conftest
) -> None:
    """Story 11.3.7 Task 7 discovery: regression gate.

    1. Tear down + bring up ROOT compose against a fresh per-project named
       volume (zero history).
    2. Wait for all 7 services healthy (180s budget).
    3. POST /v1/tasks via ``docker compose exec`` on registry-api itself
       (no port mapping required → independent of the host's port
       availability). Assert HTTP 201.
    4. Inspect ``/var/lib/oh-my-bmad/registry/events/`` mode — must show
       group-write (the ``w`` in the group triad of ``drwxrwsr-x``).

    Pre-Story-11.3.8 code (bare ``mkdir`` without chmod) reproduces the
    500 + 0o755 mode. Post-Story-11.3.8 (``ensure_shared_dir``) yields
    201 + 0o2775.
    """
    # Prerequisite: the base image is shared by every service in the stack.
    base_check = subprocess.run(
        ["docker", "image", "inspect", "oh-my-bmad-base:local"],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if base_check.returncode != 0:
        pytest.fail(
            "prerequisite missing: run 'just build-base' first to build the "
            "oh-my-bmad-base:local image"
        )

    project = f"omb-11-3-8-{uuid4().hex[:8]}"
    env = os.environ.copy()
    # Per ROOT-compose hermetic discipline established in Story 11.3.5/11.3.7:
    env["REGISTRY_STATE_AUTO_CREATE_SCHEMA"] = "1"
    env["TELEGRAM_BOT_TOKEN"] = "0:dummytesttoken"
    env["TELEGRAM_SKIP_WEBHOOK_SET"] = "1"

    try:
        proc_up = subprocess.run(
            _compose_cmd(project, _ROOT_COMPOSE_FILE, "up", "-d"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc_up.returncode != 0:
            pytest.fail(f"compose up failed (rc={proc_up.returncode}); stderr={proc_up.stderr!r}")

        _wait_for_all_healthy(
            project,
            _ROOT_COMPOSE_FILE,
            env,
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
            expected_services=_EXPECTED_SERVICES,
        )

        # POST /v1/tasks via docker exec on registry-api itself — bypasses
        # any host-port-mapping concerns. The synthetic title is unique per
        # test invocation so log greps stay attributable.
        title = f"11-3-8-regression-{uuid4().hex[:8]}"
        post_script = (
            "import urllib.request, json, sys; "
            "req = urllib.request.Request("
            "    'http://127.0.0.1:8080/v1/tasks', method='POST', "
            "    headers={'Content-Type': 'application/json'}, "
            f"    data=json.dumps({{'title': '{title}'}}).encode()"
            "); "
            "r = urllib.request.urlopen(req, timeout=5); "
            "print(r.status); print(r.read().decode())"
        )
        proc_post = subprocess.run(
            _compose_cmd(
                project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "python",
                "-c",
                post_script,
            ),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # The pre-Story-11.3.8 failure mode is HTTP 500 → urlopen raises
        # HTTPError → script exits non-zero. Post-fix: 201, exit 0.
        assert proc_post.returncode == 0, (
            f"POST /v1/tasks failed (rc={proc_post.returncode}). This is the "
            f"Story 11.3.7-Task-7 regression — events/ dir permissions block "
            f"the first event-log write.\nstdout={proc_post.stdout!r}\n"
            f"stderr={proc_post.stderr!r}"
        )
        first_line = proc_post.stdout.strip().splitlines()[0] if proc_post.stdout.strip() else ""
        assert first_line == "201", (
            f"POST /v1/tasks: expected status 201, got {first_line!r}; "
            f"full stdout={proc_post.stdout!r}"
        )

        # Filesystem proof-of-fix: events/ must be group-writable (the `w`
        # in the group triad of `drwxrwsr-x`). We grep for the mode string
        # rather than parsing `stat` numeric output — `ls -ld` is universal
        # across the slim/alpine variants registry-api is built on.
        proc_ls = subprocess.run(
            _compose_cmd(
                project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "ls",
                "-ld",
                "/var/lib/oh-my-bmad/registry/events/",
            ),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc_ls.returncode == 0, (
            f"ls -ld events/ failed (rc={proc_ls.returncode}); stderr={proc_ls.stderr!r}"
        )
        # `drwxrwsr-x` = dir + setgid + rwx for owner + rws (s=setgid) for
        # group + r-x for others. The `rws` triad in the group position is
        # the proof: it requires BOTH group-write (`w`) AND setgid (`s`)
        # together. Permission triad lives at chars 1-9 of `ls -ld` output.
        triad = proc_ls.stdout.split()[0] if proc_ls.stdout.split() else ""
        assert triad.startswith("drwxrws"), (
            f"events/ dir mode regression: expected 'drwxrws...' (setgid + "
            f"group-write), got triad={triad!r}; full ls={proc_ls.stdout!r}"
        )
    finally:
        # `down -v` is critical — leaks the per-project named volume
        # otherwise, which would mask the gap on the next test run.
        subprocess.run(
            _compose_cmd(project, _ROOT_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
