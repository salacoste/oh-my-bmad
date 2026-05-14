"""Journey 3 integration test — restart recovery (Story 7.9 / FR16 / FR24 / FR29 / NFR-R1).

Tests the Journey 3 restart-recovery event flow:

1. POST ``/v1/tasks`` → ``task.created``
2. Worker stub emits Phase 1: ``planning.started`` → ``plan.ready`` → ``execution.started``
3. Test kills worker mid-execution (after ``execution.started``)
4. Test restarts worker
5. Worker stub resumes: ``session.reconnecting`` → ``task.execution.resumed`` → ``step.completed`` → ``completed``
6. Test asserts full lifecycle, no duplicates, and ``detect_overnight_restart`` detects the restart pair.

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
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.j3.yml"
_WORKER_TAG: str = "scripted-worker-stub:latest"
_APPROVAL_TAG: str = "auto-approval-stub:latest"

_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_TASK_COMPLETION_TIMEOUT_S: float = 120.0
_PORT_WAIT_TIMEOUT_S: float = 30.0
_KILL_POLL_TIMEOUT_S: float = 10.0

# Events the stub emits for journey_3 (for dedupe checking).
STUB_EVENTS: set[str] = {
    "task.planning.started",
    "task.plan.ready",
    "task.execution.started",
    "session.reconnecting",
    "task.execution.resumed",
    "task.step.completed",
    "task.completed",
}


def _compose_env(data_dir: Path) -> dict[str, str]:
    return _shared_compose_env(
        data_dir,
        data_dir_key="OMB_J3_DATA_DIR",
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
def test_journey_3_recovery(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001
) -> None:
    """Journey 3 restart-recovery: submit → Phase 1 → kill → restart → resume → complete.

    The chain under test:

    1. Build scripted-worker-stub and auto-approval-stub images.
    2. Boot the 4-service compose stack.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "j3-recovery-test"}``.
    6. Poll JSONL until ``task.execution.started`` appears (Phase 1 complete).
    7. Kill the worker container (SIGTERM, 1s timeout).
    8. Poll until worker container reaches exited state.
    9. Restart the worker container.
    10. Wait for all services healthy again.
    11. Poll JSONL until ``task.completed`` appears.
    12. Assert full lifecycle present with no duplicates.
    13. Assert ``session.reconnecting`` + ``task.execution.resumed`` appear.
    14. Call ``detect_overnight_restart`` → assert recovery info returned.
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

    # Import detect_overnight_restart for self-recovered verification.
    _daemon_src = str(_REPO_ROOT / "services" / "clawhip-daemon" / "src")
    if _daemon_src not in sys.path:
        sys.path.insert(0, _daemon_src)
    from clawhip_daemon.adapters.sinks.telegram_sink import detect_overnight_restart

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

    project = f"omb-j3-{uuid4().hex[:8]}"
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

        # Step 5 — POST a task.
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            resp = client.post("/v1/tasks", json={"title": "j3-recovery-test"})
        assert resp.status_code == 201, (
            f"expected 201 on POST /v1/tasks, got {resp.status_code}; body={resp.text!r}"
        )
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str) and task_id

        log_dir = data_dir / "registry" / "events"

        # Step 6 — wait for Phase 1 complete (task.execution.started).
        _poll_for_event(log_dir, task_id, "task.execution.started", timeout_s=30.0)

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

        # Step 9 — restart the worker container.
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

        # Step 10 — wait for the restarted worker to become healthy.
        _wait_for_all_healthy(project, env, timeout_s=60.0)

        # Step 11 — poll JSONL until task.completed appears.
        last_envelopes = _poll_for_event(
            log_dir, task_id, "task.completed", timeout_s=_TASK_COMPLETION_TIMEOUT_S
        )

        # Step 12 — assert the complete Journey 3 event sequence.
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
            "session.reconnecting",
            "task.execution.resumed",
            "task.step.completed",
            "task.completed",
        }
        missing = expected - set(types_for_task)
        assert not missing, (
            f"task {task_id!r}: missing Journey 3 events {missing!r}; "
            f"types observed={types_for_task!r}"
        )

        # Step 12b — assert no event-type duplication for stub-emitted events.
        for ut in STUB_EVENTS:
            count = types_for_task.count(ut)
            assert count == 1, (
                f"task {task_id!r}: expected exactly 1 {ut!r}, got {count}; "
                f"types={types_for_task!r}"
            )

        # Step 12c — assert no unexpected extra event types beyond the expected set.
        _allowed_non_stub = {
            "task.created",
            "approval.granted",
            "session.started",
            "session.finished",
        }
        unexpected = set(types_for_task) - STUB_EVENTS - _allowed_non_stub
        assert not unexpected, (
            f"task {task_id!r}: unexpected event types {unexpected!r}; types={types_for_task!r}"
        )

        # Step 13 — assert session.reconnecting appears before task.execution.resumed.
        reconnect_idx = types_for_task.index("session.reconnecting")
        resumed_idx = types_for_task.index("task.execution.resumed")
        assert reconnect_idx < resumed_idx, (
            f"task {task_id!r}: expected session.reconnecting before "
            f"task.execution.resumed, got indices {reconnect_idx} vs {resumed_idx}"
        )

        # Step 13b — assert reconnect pair appears after execution.started (Phase 1).
        execution_idx = types_for_task.index("task.execution.started")
        assert reconnect_idx > execution_idx, (
            f"task {task_id!r}: expected session.reconnecting after "
            f"task.execution.started, got indices {reconnect_idx} vs {execution_idx}"
        )

        # Step 14 — verify detect_overnight_restart detects the restart pair.
        task_events = [
            e
            for e in last_envelopes
            if isinstance(e.get("payload"), dict) and e["payload"].get("task_id") == task_id
        ]
        # Pre-condition: detect_overnight_restart requires emitted_at on the
        # task.execution.resumed envelope (set by clawhip-bridge). Verify it
        # exists so failures point to the right cause.
        resumed_events = [e for e in task_events if e.get("type") == "task.execution.resumed"]
        assert resumed_events, f"task {task_id!r}: no task.execution.resumed event found"
        assert resumed_events[0].get("emitted_at") is not None, (
            f"task {task_id!r}: task.execution.resumed envelope missing 'emitted_at' "
            f"(required by detect_overnight_restart); keys={list(resumed_events[0])}"
        )
        recovery = detect_overnight_restart(task_events, task_id=task_id)
        assert recovery is not None, (
            f"task {task_id!r}: detect_overnight_restart returned None; "
            f"expected recovery info from session.reconnecting → task.execution.resumed pair"
        )
        assert recovery["events_replayed"] == 3, (
            f"task {task_id!r}: expected events_replayed=3, got {recovery['events_replayed']}"
        )
        assert recovery["replay_duration_ms"] == 2800, (
            f"task {task_id!r}: expected replay_duration_ms=2800, "
            f"got {recovery['replay_duration_ms']}"
        )
        assert "recovered_at" in recovery, (
            f"task {task_id!r}: recovery info missing 'recovered_at'; got keys={list(recovery)}"
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


__all__ = ["test_journey_3_recovery"]
