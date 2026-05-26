"""S-2 separability test (Story 5.17c / FR34 / NFR-M4).

Two tests:

1. :func:`test_midflight_worker_swap_completes_task_end_to_end`
   — boots the compose stack with ``WORKER_IMAGE=scripted-worker-stub:latest``,
   POSTs a single task, waits for ``task.plan.ready``, kills the worker
   container via SIGKILL, restarts it, waits for ``task.completed``, and
   asserts the full lifecycle is present with zero event-type duplication.
   Marked ``slow`` — excluded from the PR-gate ``just test``.

2. :func:`test_worker_facing_source_code_unchanged` — runs ``git diff --name-only``
   against the worker-facing source paths and asserts the working tree has
   no modifications. Sub-second runtime; runs unconditionally.

The S-2 test proves FR34 / NFR-M4 under *motion*: a worker is killed
mid-task and a replacement resumes from the event log, driving the task
to completion with no state corruption and no event loss. This is the
stronger sibling of the S-1 cold-swap test (Story 5.16).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import _build_scripted_worker  # type: ignore[import-not-found]  # sys.path-injected via conftest
import aiosqlite
import httpx
import pytest

_log = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.s2.yml"
_STUB_TAG: str = "scripted-worker-stub:latest"

# Worker-facing source paths the test asserts remain untouched.
_WORKER_FACING_PATHS: tuple[str, ...] = (
    "services/registry-state/src/",
    "services/registry-api/src/",
    "mcp-servers/clawhip-bridge/src/",
    "services/orchestrator-adapter/src/",
    # worker-wrapper/ is EXCLUDED — the whole point is the worker is swappable.
)

_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_TASK_COMPLETION_TIMEOUT_S: float = 90.0
_PORT_WAIT_TIMEOUT_S: float = 30.0


def _compose_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OMB_S2_DATA_DIR"] = str(data_dir)
    env["WORKER_IMAGE"] = _STUB_TAG
    # Story 11.3.3 Fix-B: default to host uid/gid so the container writes
    # bind-mounted files (incl. 0o640 audit JSONL per event_log.py:506)
    # with ownership readable by the host-side pytest. os.getuid/getgid are
    # POSIX-only; fall back to the container's built-in uid/gid where absent
    # (Windows) so import/collection never crashes.
    env.setdefault(
        "OMB_CONTAINER_UID", str(os.getuid() if hasattr(os, "getuid") else _CONTAINER_UID)
    )
    env.setdefault(
        "OMB_CONTAINER_GID", str(os.getgid() if hasattr(os, "getgid") else _CONTAINER_GID)
    )
    return env


def _compose_cmd(project: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(_COMPOSE_FILE),
        *args,
    ]


def _wait_for_all_healthy(project: str, env: dict[str, str], *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_state: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        proc = subprocess.run(
            _compose_cmd(project, "ps", "--format", "json"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
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
        if services and all(s.get("Health") == "healthy" for s in services):
            return
        time.sleep(1.0)
    raise TimeoutError(
        f"compose project {project!r}: not all services became healthy "
        f"within {timeout_s}s; last state={last_state!r}"
    )


def _resolve_registry_api_port(project: str, env: dict[str, str], *, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        proc = subprocess.run(
            _compose_cmd(project, "port", "registry-api", "8080"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            line = proc.stdout.strip().splitlines()[0]
            _, _, port_str = line.rpartition(":")
            try:
                return int(port_str)
            except ValueError:
                last_err = f"could not parse port from {line!r}"
        else:
            last_err = proc.stderr.strip() or "(no stderr)"
        time.sleep(1.0)
    raise TimeoutError(
        f"compose project {project!r}: registry-api port not resolved "
        f"within {timeout_s}s; last error={last_err!r}"
    )


def _wait_for_socket(host: str, port: int, *, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError as exc:
            last_err = repr(exc)
            time.sleep(0.5)
    raise TimeoutError(
        f"could not establish TCP connection to {host}:{port} within "
        f"{timeout_s}s; last error={last_err}"
    )


def _read_jsonl_envelopes(log_dir: Path) -> list[dict[str, Any]]:
    if not log_dir.exists():
        return []
    envelopes: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        envelopes.append(obj)
        except FileNotFoundError:
            continue
    return envelopes


async def _wait_for_task_status_completed(
    data_dir: Path, task_id: str, *, timeout_s: float = 15.0
) -> None:
    db_path = data_dir / "registry" / "state.sqlite3"
    uri = f"file:{db_path}?mode=ro"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            async with aiosqlite.connect(uri, uri=True) as conn:
                cur = await conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
                row = await cur.fetchone()
                await cur.close()
                if row and row[0] == "completed":
                    return
        except aiosqlite.OperationalError:
            pass
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"tasks.status did not reach 'completed' for {task_id!r} within {timeout_s}s"
    )


def _poll_for_event(
    log_dir: Path,
    task_id: str,
    event_type: str,
    *,
    timeout_s: float,
) -> list[dict[str, Any]]:
    """Poll JSONL until *event_type* appears for *task_id*. Returns all envelopes."""
    deadline = time.monotonic() + timeout_s
    last_envelopes: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_envelopes = _read_jsonl_envelopes(log_dir)
        for env_obj in last_envelopes:
            if (
                env_obj.get("type") == event_type
                and isinstance(env_obj.get("payload"), dict)
                and env_obj["payload"].get("task_id") == task_id
            ):
                return last_envelopes
        time.sleep(0.5)
    types_seen = sorted({e.get("type", "?") for e in last_envelopes})
    raise TimeoutError(
        f"event {event_type!r} for task {task_id!r} not seen within "
        f"{timeout_s}s; types seen={types_seen}"
    )


@pytest.mark.separability
@pytest.mark.slow
def test_midflight_worker_swap_completes_task_end_to_end(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001
) -> None:
    """FR34 / NFR-M4 mid-flight: kill worker mid-task → restart → task completes.

    The chain under test:

    1. Build ``scripted-worker-stub:latest`` (force-rebuild since stub code changed).
    2. Boot the 3-service compose stack with ``WORKER_IMAGE`` overridden.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "s2-midflight-test"}``.
    6. Poll JSONL until ``task.plan.ready`` appears (proves ≥2 events emitted).
    7. Kill the worker container (SIGKILL).
    8. Restart the worker container.
    9. Poll JSONL until ``task.completed`` appears.
    10. Assert the canonical lifecycle events are present with zero duplicates.
    11. Verify materializer: SQLite ``tasks.status = completed``.
    """
    # Step 1 — force rebuild (stub code changed for event-level dedupe).
    _build_scripted_worker.build_if_missing(force=True)

    # Step 2 — pre-create the bind-mount tree.
    data_dir = tmp_path / "data"
    for subdir in [
        data_dir,
        data_dir / "registry",
        data_dir / "registry" / "events",
    ]:
        subdir.mkdir(parents=True, exist_ok=True)
        subdir.chmod(0o777)

    project = f"omb-s2-{uuid4().hex[:8]}"
    env = _compose_env(data_dir)

    try:
        proc_up = subprocess.run(
            _compose_cmd(project, "up", "-d"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_up.returncode != 0:
            pytest.fail(f"compose up failed (rc={proc_up.returncode}); stderr={proc_up.stderr!r}")

        # Step 3 — wait for all healthchecks.
        _wait_for_all_healthy(project, env, timeout_s=_HEALTHCHECK_TIMEOUT_S)

        # Step 4 — resolve mapped port + pre-flight TCP probe.
        port = _resolve_registry_api_port(project, env, timeout_s=_PORT_WAIT_TIMEOUT_S)
        _wait_for_socket("localhost", port)

        # Step 5 — POST a task.
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            resp = client.post("/v1/tasks", json={"title": "s2-midflight-test"})
        assert resp.status_code == 201, (
            f"expected 201 on POST /v1/tasks, got {resp.status_code}; body={resp.text!r}"
        )
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str) and task_id

        log_dir = data_dir / "registry" / "events"

        # Step 6 — wait for task.plan.ready (proves ≥2 events: planning.started + plan.ready).
        # The stub emits events with 0.5s delay between each, so plan.ready
        # appears ~1s after task.created is detected.
        _poll_for_event(log_dir, task_id, "task.plan.ready", timeout_s=30.0)

        # Step 7 — kill the worker container (SIGKILL, no grace period).
        proc_kill = subprocess.run(
            _compose_cmd(project, "kill", "worker-wrapper"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_kill.returncode != 0:
            _log.warning("compose kill returned %d: %s", proc_kill.returncode, proc_kill.stderr)

        # Step 7b — poll until the worker container reaches "exited" state.
        _KILL_POLL_TIMEOUT_S: float = 10.0
        _kill_deadline = time.monotonic() + _KILL_POLL_TIMEOUT_S
        _kill_confirmed = False
        while time.monotonic() < _kill_deadline:
            ps = subprocess.run(
                _compose_cmd(project, "ps", "--format", "json", "worker-wrapper"),
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            if ps.returncode == 0 and ps.stdout.strip():
                for line in ps.stdout.strip().splitlines():
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = (obj.get("Status") or "").lower()
                    if "exit" in status or status == "stopped" or obj.get("Health") == "stopped":
                        _kill_confirmed = True
                        break
                if _kill_confirmed:
                    break
            time.sleep(0.3)
        if not _kill_confirmed:
            _log.warning(
                "worker-wrapper did not reach 'exited' within %.1fs; proceeding with restart",
                _KILL_POLL_TIMEOUT_S,
            )

        # Step 8 — restart the worker container.
        proc_restart = subprocess.run(
            _compose_cmd(project, "up", "-d", "worker-wrapper"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_restart.returncode != 0:
            pytest.fail(
                f"compose up (restart) failed (rc={proc_restart.returncode}); "
                f"stderr={proc_restart.stderr!r}"
            )

        # Step 8b — wait for the restarted worker to become healthy.
        _wait_for_all_healthy(project, env, timeout_s=60.0)

        # Step 9 — poll JSONL until task.completed appears.
        last_envelopes = _poll_for_event(
            log_dir, task_id, "task.completed", timeout_s=_TASK_COMPLETION_TIMEOUT_S
        )

        # Step 10 — assert the canonical lifecycle events for this task_id.
        types_for_task = [
            e.get("type")
            for e in last_envelopes
            if isinstance(e.get("payload"), dict) and e["payload"].get("task_id") == task_id
        ]
        expected = {
            "task.created",
            "task.planning.started",
            "task.plan.ready",
            "task.execution.started",
            "task.step.completed",
            "task.completed",
        }
        missing = expected - set(types_for_task)
        assert not missing, (
            f"task {task_id!r}: missing lifecycle events {missing!r}; "
            f"types observed={types_for_task!r}"
        )

        # S-2 differentiator: assert NO event-type duplicates for this task.
        # Each of these types must appear exactly once. task.step.completed is
        # also checked since simple_green has exactly 1 step.
        unique_types = {
            "task.created",
            "task.planning.started",
            "task.plan.ready",
            "task.execution.started",
            "task.step.completed",
            "task.completed",
        }
        for ut in unique_types:
            count = types_for_task.count(ut)
            assert count == 1, (
                f"task {task_id!r}: expected exactly 1 {ut!r}, got {count}; "
                f"types={types_for_task!r}"
            )

        # Step 11 — verify the materializer projected task.completed into SQLite.
        asyncio.run(_wait_for_task_status_completed(data_dir, task_id))

    finally:
        proc_down = subprocess.run(
            _compose_cmd(project, "down", "-v", "--remove-orphans"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_down.returncode != 0:
            _log.warning(
                "compose down for %s returned %d; stderr=%r",
                project,
                proc_down.returncode,
                proc_down.stderr,
            )


@pytest.mark.separability
def test_worker_facing_source_code_unchanged() -> None:
    """Sentinel: worker-facing source must remain untouched by this story.

    Checks git diff against registry-state, registry-api, clawhip-bridge,
    and orchestrator-adapter paths. Changes to worker-wrapper source ARE
    allowed — the whole point is that the worker is swappable.
    """
    SPINE_PATHS = list(_WORKER_FACING_PATHS) + [
        ":!services/registry-state/src/registry_state/domain/event_types.py",
        # test_failure_detection.py is a co-located test file — not worker-facing
        # source. Story 9.7 updates fixture assertions for schema_version 1.1.0.
        ":!services/registry-state/src/registry_state/domain/test_failure_detection.py",
        # Story 10.2 AC1 EventLogReader extraction: see test_s1 for details.
        # event_log.py becomes thin re-export shim; main.py uses renamed public imports.
        ":!services/registry-state/src/registry_state/adapters/event_log.py",
        ":!services/registry-state/src/registry_state/app/main.py",
    ]

    rev_parse = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if rev_parse.returncode != 0:
        pytest.skip("non-git checkout (e.g., source tarball)")

    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD", "--", *SPINE_PATHS],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", *SPINE_PATHS],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

    assert proc.stdout.strip() == "", (
        f"worker-facing source touched in last commit:\n{proc.stdout}\n"
        "Story 5.17c's separability claim requires no source modifications "
        "to registry-state/registry-api/clawhip-bridge/orchestrator-adapter. "
        "If this change is config (compose YAML, mypy.ini, justfile, "
        "tests/), move it out of the spine src/ directories."
    )


__all__ = [
    "test_midflight_worker_swap_completes_task_end_to_end",
    "test_worker_facing_source_code_unchanged",
]
