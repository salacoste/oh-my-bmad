"""Story 11.3.11 / AC6 — regression test for the event-log FILE permission gap.

THE regression gate proving the Story 11.3.10-AC5 discovery is closed: on a
fresh ROOT-compose boot, once all 7 services are healthy and a task is
submitted, the event-log day-file
``/var/lib/oh-my-bmad/registry/events/YYYY-MM-DD.jsonl`` must be created
``-rw-rw----`` (0o660 — owner+group read/write, NO others) so that any
``omb``-group service uid can append to / recover it. Pre-Story-11.3.11 the
file was ``-rw-r-----`` (0o640, no group-write), which crash-looped
``registry-state`` (`PermissionError` opening the file ``r+b`` for recovery
at ``event_log.py:191``) once the Story-11.3.10 MCP-init fix let the
spawners reach healthy and advance the stack to multi-uid event-log writes.

This is the FILE-level sibling of Story 11.3.8's DIRECTORY regression test
(`test_event_log_dir_perm.py`) — same compose-lifecycle scaffolding (shared
`_wait_for_all_healthy` poll, same `try/finally: docker compose down -v`
discipline, same hermetic env). The headline assertion that distinguishes
this from 11.3.8: with BOTH fixes in place the stack reaches **7/7 healthy**
on fresh boot (registry-state no longer crash-loops) — the close-out of the
Epic-11.3 fresh-deploy-green tail.

``@pytest.mark.slow + @pytest.mark.integration`` — Docker boot + healthcheck
arming budget (~180s, plus the 100s MCP-spawner start_period from Story
11.3.10).
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

# Per S-4 pattern + Story 11.3.10's 100s MCP start_period: a generous boot
# budget. The 2 spawners need ~95s to reach ready; 240s covers cold arming.
_HEALTHCHECK_TIMEOUT_S: float = 240.0

# All 7 services in the ROOT compose must reach healthy — including
# registry-state, which is the service this story keeps from crash-looping.
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
    """Two-probe Docker availability check (engine + compose v2 plugin)."""
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
    reason="event-log-file permission regression requires Docker + compose v2 plugin",
)
def test_fresh_volume_event_log_file_is_group_writable(
    skip_if_no_docker: None,  # noqa: ARG001 — session-scoped fixture from conftest
) -> None:
    """Story 11.3.10-AC5 discovery: regression gate (the Epic-11.3 green close-out).

    1. Tear down + bring up ROOT compose against a fresh per-project named
       volume (zero history).
    2. Wait for ALL 7 services healthy — registry-state included (it
       crash-looped pre-Story-11.3.11 once the spawners reached healthy).
    3. POST /v1/tasks (writes the day-file via the event-log writer).
    4. Inspect the day-file mode — must be ``-rw-rw----`` (group-write
       present, others-none).

    Pre-Story-11.3.11: file 0o640 → registry-state `r+b` recovery
    `PermissionError` → crash-loop → never reaches 7/7. Post-fix: 0o660 →
    cross-uid append/recover works → 7/7 healthy.
    """
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

    project = f"omb-11-3-11-{uuid4().hex[:8]}"
    env = os.environ.copy()
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

        # The headline: all 7 reach healthy. If registry-state crash-loops on
        # the 0o640 file bug, this raises TimeoutError — the regression.
        _wait_for_all_healthy(
            project,
            _ROOT_COMPOSE_FILE,
            env,
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
            expected_services=_EXPECTED_SERVICES,
        )

        # POST /v1/tasks — drives an event-log write so the day-file exists.
        title = f"11-3-11-regression-{uuid4().hex[:8]}"
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
        assert proc_post.returncode == 0, (
            f"POST /v1/tasks failed (rc={proc_post.returncode}); "
            f"stdout={proc_post.stdout!r} stderr={proc_post.stderr!r}"
        )
        first_line = proc_post.stdout.strip().splitlines()[0] if proc_post.stdout.strip() else ""
        assert first_line == "201", (
            f"POST /v1/tasks: expected 201, got {first_line!r}; stdout={proc_post.stdout!r}"
        )

        # Filesystem proof-of-fix: the day-file(s) must be group-writable
        # (`rw-rw----`). `ls -l` the events dir; every *.jsonl line must show
        # the `-rw-rw----` triad. We assert on the mode string (universal
        # across slim/alpine) rather than numeric stat.
        proc_ls = subprocess.run(
            _compose_cmd(
                project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "ls",
                "-l",
                "/var/lib/oh-my-bmad/registry/events/",
            ),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc_ls.returncode == 0, (
            f"ls -l events/ failed (rc={proc_ls.returncode}); stderr={proc_ls.stderr!r}"
        )
        jsonl_lines = [ln for ln in proc_ls.stdout.splitlines() if ln.strip().endswith(".jsonl")]
        assert jsonl_lines, f"no *.jsonl day-file found after POST /v1/tasks; ls={proc_ls.stdout!r}"
        for ln in jsonl_lines:
            triad = ln.split()[0] if ln.split() else ""
            # `-rw-rw----` = regular file + owner rw + group rw + others none.
            # The group-`w` (char 5) is the proof of the fix; the others-triad
            # (chars 7-9 = `---`) is the preserved non-world-readable invariant.
            assert triad.startswith("-rw-rw----"), (
                f"event-log FILE mode regression: expected '-rw-rw----' "
                f"(group-write + non-world-readable), got triad={triad!r} on "
                f"line {ln!r}; full ls={proc_ls.stdout!r}"
            )
    finally:
        subprocess.run(
            _compose_cmd(project, _ROOT_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
