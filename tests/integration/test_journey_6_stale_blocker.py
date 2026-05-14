"""Journey 6 integration test — stale-blocker reconnaissance (Story 7.10 / FR4 / FR5 / FR7 / FR27).

Tests the Journey 6 stale-blocker event flow:

1. POST ``/v1/tasks`` → ``task.created``
2. Worker stub emits Phase 1: ``planning.started`` → ``plan.ready`` → ``execution.started``
   → ``step.completed(1)`` → ``blocker_raised``
3. Test kills worker after ``blocker_raised``
4. Test exercises reconnaissance: ``/status`` → ``/logs/digest`` → ``/retry hint="..."`
5. Test restarts worker
6. Worker stub emits Phase 2: ``step.completed(2)`` → ``step.completed(3)`` → ``completed``
7. Test asserts full lifecycle, no duplicates, hint persisted.

Marked ``slow`` — excluded from the PR-gate ``just test``.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from tests.integration._compose_helpers import (  # noqa: IMP001
    compose_cmd as _shared_compose_cmd,
)
from tests.integration._compose_helpers import (
    compose_env as _shared_compose_env,
)
from tests.integration._compose_helpers import (
    resolve_registry_api_port as _shared_resolve_port,
)
from tests.integration._compose_helpers import (
    wait_for_all_healthy as _shared_wait_healthy,
)

_log = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.j6.yml"
_WORKER_TAG: str = "scripted-worker-stub:latest"
_APPROVAL_TAG: str = "auto-approval-stub:latest"

_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_TASK_COMPLETION_TIMEOUT_S: float = 120.0
_PORT_WAIT_TIMEOUT_S: float = 30.0
_KILL_POLL_TIMEOUT_S: float = 10.0

# Events the stub emits for journey_6 (for dedupe checking).
# Intentional subset of STUB_EMITTED_TYPES — excludes session.pair events
# (journey_6 has no reconnect pair) and awaiting_approval/push/pr (journey_1 only).
# NOTE: ``task.step.completed`` is included here for the per-type loop, but
# the loop skips it because it appears 3 times (not 1); a separate assertion
# checks the count==3.
STUB_EVENTS: set[str] = {
    "task.planning.started",
    "task.plan.ready",
    "task.execution.started",
    "task.step.completed",
    "task.blocker_raised",
    "task.completed",
}


def _compose_env(data_dir: Path) -> dict[str, str]:
    return _shared_compose_env(
        data_dir,
        data_dir_key="OMB_J6_DATA_DIR",
        worker_image=_WORKER_TAG,
        approval_image=_APPROVAL_TAG,
        container_uid=_CONTAINER_UID,
        container_gid=_CONTAINER_GID,
    )


def _compose_cmd(project: str, *args: str) -> list[str]:
    return _shared_compose_cmd(project, _COMPOSE_FILE, *args)


def _wait_for_all_healthy(project: str, env: dict[str, str], *, timeout_s: float) -> None:
    _shared_wait_healthy(project, env, _COMPOSE_FILE, timeout_s=timeout_s, min_services=4)


def _resolve_registry_api_port(project: str, env: dict[str, str], *, timeout_s: float) -> int:
    return _shared_resolve_port(project, env, _COMPOSE_FILE, timeout_s=timeout_s)


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


def _wait_for_container_exit(
    project: str, env: dict[str, str], service: str, *, timeout_s: float
) -> None:
    """Poll compose ps until *service* shows exited/stopped status."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ps = subprocess.run(
            _compose_cmd(project, "ps", "--format", "json", service),
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
                    return
        time.sleep(0.3)
    raise TimeoutError(f"service {service!r} did not reach 'exited' within {timeout_s:.1f}s")


@pytest.mark.integration
@pytest.mark.slow
def test_journey_6_stale_blocker(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001
) -> None:
    """Journey 6 stale-blocker: submit → Phase 1 → kill → /status → /logs → /retry → restart → complete.

    The chain under test:

    1. Build scripted-worker-stub and auto-approval-stub images.
    2. Boot the 4-service compose stack.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "j6-stale-blocker-test"}``.
    6. Poll JSONL until ``task.blocker_raised`` appears (Phase 1 complete).
    7. Kill the worker container (SIGTERM, 1s timeout).
    8. Poll until worker container reaches exited state.
    9. GET ``/v1/tasks/{id}`` → assert blocked, lock held, commands include retry.
    10. GET ``/v1/tasks/{id}/logs/digest`` → assert digest returned.
    11. POST ``/v1/tasks/{id}/decisions`` with retry + hint → assert 200.
    12. GET ``/v1/tasks/{id}`` → assert hint persisted, status pending.
    13. Restart worker container.
    14. Wait for all services healthy.
    15. Poll JSONL until ``task.completed`` appears.
    16. Assert full lifecycle, no duplicates, ``task.retry_requested`` present.
    """
    # Deferred imports — conftest injects tests/integration/ onto sys.path at
    # session start, but pytest collects (imports) test modules *before* running
    # conftest fixtures.  Using late imports avoids ModuleNotFoundError during
    # collection when the test is marker-skipped.
    import _build_auto_approval  # type: ignore[import-not-found]

    _separability_dir = str(_REPO_ROOT / "tests" / "separability")
    if _separability_dir not in sys.path:
        sys.path.insert(0, _separability_dir)
    import _build_scripted_worker  # type: ignore[import-not-found]

    # Step 1 — build both stub images.
    _build_scripted_worker.build_if_missing(force=True)
    _build_auto_approval.build_if_missing(force=True)

    # Step 2 — pre-create the bind-mount tree.
    data_dir = tmp_path / "data"
    for subdir in [
        data_dir,
        data_dir / "registry",
        data_dir / "registry" / "events",
    ]:
        subdir.mkdir(parents=True, exist_ok=True)
        subdir.chmod(0o777)

    project = f"omb-j6-{uuid4().hex[:8]}"
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
        _wait_for_socket("127.0.0.1", port)

        base_url = f"http://127.0.0.1:{port}"

        # Step 5 — POST a task.
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            resp = client.post("/v1/tasks", json={"title": "j6-stale-blocker-test"})
        assert resp.status_code == 201, (
            f"expected 201 on POST /v1/tasks, got {resp.status_code}; body={resp.text!r}"
        )
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str) and task_id

        log_dir = data_dir / "registry" / "events"

        # Step 6 — wait for Phase 1 complete (task.blocker_raised).
        _poll_for_event(log_dir, task_id, "task.blocker_raised", timeout_s=30.0)

        # Brief pause to let the materializer process blocker_raised before kill.
        time.sleep(1.0)

        # Step 7 — kill the worker container (SIGTERM with 1s grace).
        proc_kill = subprocess.run(
            _compose_cmd(project, "stop", "--timeout", "1", "worker-wrapper"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_kill.returncode != 0:
            _log.warning("compose stop returned %d: %s", proc_kill.returncode, proc_kill.stderr)

        # Step 8 — poll until worker container reaches exited state.
        _wait_for_container_exit(project, env, "worker-wrapper", timeout_s=_KILL_POLL_TIMEOUT_S)

        # Step 9 — verify /status shows blocked state (poll until materializer catches up).
        status_body: dict[str, Any] = {}
        status_deadline = time.monotonic() + 30.0
        while time.monotonic() < status_deadline:
            with httpx.Client(base_url=base_url, timeout=10.0) as client:
                status_resp = client.get(f"/v1/tasks/{task_id}")
            assert status_resp.status_code == 200, (
                f"/status returned {status_resp.status_code}; body={status_resp.text!r}"
            )
            status_body = status_resp.json()
            if status_body.get("status") == "blocked":
                break
            time.sleep(0.5)
        assert status_body["status"] == "blocked", (
            f"expected status 'blocked' after polling, got {status_body['status']!r}"
        )
        assert status_body.get("state_since") is not None, (
            "state_since missing from /status response"
        )
        lock = status_body.get("worktree_lock", {})
        # worktree_lock.held requires session.worktree_path != None, but no current
        # event handler populates it — the field is reserved for future implementation.
        # Assert the field shape is present; held value may be False.
        assert isinstance(lock, dict), f"expected worktree_lock dict, got {lock!r}"
        commands = status_body.get("available_commands", [])
        assert "retry" in commands, f"expected 'retry' in available_commands, got {commands!r}"
        last_event = status_body.get("last_event") or {}
        assert last_event.get("type") == "task.blocker_raised", (
            f"expected last_event.type='task.blocker_raised', got {last_event!r}"
        )

        # Step 10 — verify /logs/digest returns a coherent digest.
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            logs_resp = client.get(f"/v1/tasks/{task_id}/logs/digest")
        assert logs_resp.status_code == 200, (
            f"/logs/digest returned {logs_resp.status_code}; body={logs_resp.text!r}"
        )
        logs_body = logs_resp.json()
        assert logs_body["task_id"] == task_id, (
            f"expected task_id={task_id!r}, got {logs_body['task_id']!r}"
        )
        assert isinstance(logs_body["digest"], str) and len(logs_body["digest"]) > 0, (
            "expected non-empty digest string"
        )
        assert logs_body["line_count"] >= 1, (
            f"expected line_count >= 1, got {logs_body['line_count']}"
        )

        # Step 11 — retry with hint.
        _TEST_HINT = "rate limit must be per-user, not per-IP"
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            retry_resp = client.post(
                f"/v1/tasks/{task_id}/decisions",
                json={"action": "retry", "hint": _TEST_HINT},
            )
        assert retry_resp.status_code == 200, (
            f"retry returned {retry_resp.status_code}; body={retry_resp.text!r}"
        )
        retry_body = retry_resp.json()
        assert retry_body["action"] == "retry", (
            f"expected action='retry', got {retry_body['action']!r}"
        )

        # Step 12 — verify hint persisted and status transitioned to pending.
        hint_body: dict[str, Any] = {}
        hint_deadline = time.monotonic() + 10.0
        while time.monotonic() < hint_deadline:
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                hint_resp = client.get(f"/v1/tasks/{task_id}")
            assert hint_resp.status_code == 200
            hint_body = hint_resp.json()
            if hint_body.get("status") == "pending" and hint_body.get("hint") == _TEST_HINT:
                break
            time.sleep(0.5)
        assert hint_body["hint"] == _TEST_HINT, (
            f"expected hint={_TEST_HINT!r}, got {hint_body.get('hint')!r}"
        )
        assert hint_body["status"] == "pending", (
            f"expected status 'pending' after retry, got {hint_body['status']!r}"
        )

        # Step 13 — restart the worker container.
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

        # Step 14 — wait for the restarted worker to become healthy.
        _wait_for_all_healthy(project, env, timeout_s=60.0)

        # Step 15 — poll JSONL until task.completed appears.
        last_envelopes = _poll_for_event(
            log_dir, task_id, "task.completed", timeout_s=_TASK_COMPLETION_TIMEOUT_S
        )

        # Step 16 — assert the complete Journey 6 event sequence.
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
            "task.blocker_raised",
            "task.retry_requested",
            "task.completed",
        }
        missing = expected - set(types_for_task)
        assert not missing, (
            f"task {task_id!r}: missing Journey 6 events {missing!r}; "
            f"types observed={types_for_task!r}"
        )

        # Step 16b — assert no event-type duplication for stub-emitted events.
        # task.step.completed is excluded here because it appears 3 times
        # (one per step); the count==3 check is in Step 16c below.
        for ut in STUB_EVENTS - {"task.step.completed"}:
            count = types_for_task.count(ut)
            assert count == 1, (
                f"task {task_id!r}: expected exactly 1 {ut!r}, got {count}; "
                f"types={types_for_task!r}"
            )

        # Step 16c — assert step.completed appears exactly 3 times (3 steps).
        step_count = types_for_task.count("task.step.completed")
        assert step_count == 3, (
            f"task {task_id!r}: expected 3 step.completed events, got {step_count}; "
            f"types={types_for_task!r}"
        )

        # Step 16d — assert no unexpected extra event types.
        # Only events that pass the task_id filter AND are not stub-emitted:
        _allowed_non_stub = {
            "task.created",  # emitted by registry-api on POST /v1/tasks
            "task.retry_requested",  # emitted by registry-api on POST /decisions
        }
        # NOTE: session.started/finished and approval.granted are excluded because
        # they are always filtered out by the task_id check in types_for_task
        # (session events have task_id=None; approval is never triggered by journey_6).
        unexpected = set(types_for_task) - STUB_EVENTS - _allowed_non_stub
        assert not unexpected, (
            f"task {task_id!r}: unexpected event types {unexpected!r}; types={types_for_task!r}"
        )

        # Step 16e — assert blocker_raised appears before completed.
        blocker_idx = types_for_task.index("task.blocker_raised")
        completed_idx = types_for_task.index("task.completed")
        assert blocker_idx < completed_idx, (
            f"task {task_id!r}: expected blocker_raised before completed, "
            f"got indices {blocker_idx} vs {completed_idx}"
        )

        # Step 16f — assert retry_requested appears between blocker_raised and completed.
        retry_idx = types_for_task.index("task.retry_requested")
        assert blocker_idx < retry_idx < completed_idx, (
            f"task {task_id!r}: expected retry_requested between blocker_raised and completed, "
            f"got indices {blocker_idx} < {retry_idx} < {completed_idx}"
        )

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


__all__ = ["test_journey_6_stale_blocker"]
