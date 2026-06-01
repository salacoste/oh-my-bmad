"""Story 11.3.12 / AC6 — regression test for the cross-uid SQLite WAL gap.

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
    reason="SQLite WAL cross-uid regression requires Docker + compose v2 plugin",
)
def test_fresh_volume_state_sqlite_wal_is_group_writable(
    skip_if_no_docker: None,  # noqa: ARG001 — session-scoped fixture from conftest
) -> None:
    """Story 11.3.12 — the genuine Epic-11.3 fresh-deploy-green close-out.

    state.sqlite3 runs in WAL mode; any omb-group process that opens it
    (incl. registry-api's read-only consumer) creates the -wal/-shm
    sidecars inheriting the MAIN db file's mode. Pre-Story-11.3.12 the main
    file was 0o640 (no group-write) → sidecars 0o640 → whichever uid
    created them first locked the OTHER omb uid out → registry-state
    "OperationalError: attempt to write a readonly database" crash-loop →
    never reached 7/7. Post-fix: registry-state chmods state.sqlite3 to
    0o660, sidecars inherit group-write, 7/7 healthy + stable.

    1. Boot ROOT compose fresh.
    2. Assert ALL 7 services healthy (registry-state stable).
    3. POST /v1/tasks → 201 (exercises a materializer write through WAL).
    4. Assert state.sqlite3 AND its -wal/-shm sidecars are -rw-rw----
       (group-write present, others NONE = audit invariant preserved).
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

    project = f"omb-11-3-12-{uuid4().hex[:8]}"
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

        # Headline: all 7 reach healthy. If registry-state crash-loops on the
        # readonly-database WAL bug, this raises TimeoutError — the regression.
        _wait_for_all_healthy(
            project,
            _ROOT_COMPOSE_FILE,
            env,
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
            expected_services=_EXPECTED_SERVICES,
        )

        # POST /v1/tasks → 201 (drives a materializer write via the WAL DB).
        title = f"11-3-12-regression-{uuid4().hex[:8]}"
        post_script = (
            "import urllib.request, json, sys; "
            "req = urllib.request.Request("
            "    'http://127.0.0.1:8080/v1/tasks', method='POST', "
            "    headers={'Content-Type': 'application/json'}, "
            f"    data=json.dumps({{'title': '{title}'}}).encode()"
            "); "
            "r = urllib.request.urlopen(req, timeout=5); "
            "print(r.status)"
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

        # Filesystem proof-of-fix: state.sqlite3 + its -wal/-shm sidecars are
        # all -rw-rw---- (group-write present, others NONE). We `ls -l` and
        # check each state.sqlite3* line's mode triad.
        proc_ls = subprocess.run(
            _compose_cmd(
                project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "ls",
                "-l",
                "/var/lib/oh-my-bmad/registry/",
            ),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc_ls.returncode == 0, (
            f"ls -l registry/ failed (rc={proc_ls.returncode}); stderr={proc_ls.stderr!r}"
        )
        state_lines = [
            ln
            for ln in proc_ls.stdout.splitlines()
            if "state.sqlite3" in ln and ln.strip().split()[-1].startswith("state.sqlite3")
        ]
        assert state_lines, f"no state.sqlite3* files found after POST; ls={proc_ls.stdout!r}"
        for ln in state_lines:
            triad = ln.split()[0] if ln.split() else ""
            # -rw-rw---- : file + owner rw + group rw + others none. The
            # group-`w` (char 5) is the proof; others `---` (chars 7-9) is the
            # preserved non-world-readable audit invariant.
            assert triad.startswith("-rw-rw----"), (
                f"state.sqlite3* mode regression: expected '-rw-rw----' "
                f"(group-write + non-world), got triad={triad!r} on line {ln!r}; "
                f"full ls={proc_ls.stdout!r}"
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
