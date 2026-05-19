"""Journey 1 integration test — "The Overnight PR" (Story 5.18 / FR31 / NFR-R6).

Tests the complete Journey 1 end-to-end event flow:

1. POST ``/v1/tasks`` → ``task.created``
2. Worker stub emits: ``planning.started`` → ``plan.ready`` → ``execution.started``
   → ``step.completed`` → ``awaiting_approval``
3. Auto-approval stub detects ``awaiting_approval`` → emits ``approval.granted``
4. Worker stub continues: ``push.completed`` → ``pr.opened`` → ``task.completed``
5. Test asserts the full lifecycle is present with correct ordering and zero
   event duplication.

Marked ``slow`` — excluded from the PR-gate ``just test``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

_log = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.j1.yml"
_WORKER_TAG: str = "scripted-worker-stub:latest"
_APPROVAL_TAG: str = "auto-approval-stub:latest"

# Worker-facing source paths the test asserts remain untouched.
_WORKER_FACING_PATHS: tuple[str, ...] = (
    "services/registry-state/src/",
    "services/registry-api/src/",
    "mcp-servers/clawhip-bridge/src/",
    "services/orchestrator-adapter/src/",
)

_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_TASK_COMPLETION_TIMEOUT_S: float = 120.0
_PORT_WAIT_TIMEOUT_S: float = 30.0

# Events emitted by stubs (for dedupe checking).
STUB_EVENTS: set[str] = {
    "task.planning.started",
    "task.plan.ready",
    "task.execution.started",
    "task.step.completed",
    "task.awaiting_approval",
    "approval.granted",
    "task.push.completed",
    "task.pr.opened",
    "task.completed",
}


def _compose_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OMB_J1_DATA_DIR"] = str(data_dir)
    env["WORKER_IMAGE"] = _WORKER_TAG
    env["AUTO_APPROVAL_IMAGE"] = _APPROVAL_TAG
    env.setdefault("OMB_S3_UID", str(_CONTAINER_UID))
    env.setdefault("OMB_S3_GID", str(_CONTAINER_GID))
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


def _wait_for_task_status_completed(
    data_dir: Path, task_id: str, *, timeout_s: float = 15.0
) -> None:
    db_path = data_dir / "registry" / "state.sqlite3"
    uri = f"file:{db_path}?mode=ro"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if row and row[0] == "completed":
                    return
        except sqlite3.OperationalError:
            pass
        time.sleep(0.5)
    raise AssertionError(
        f"tasks.status did not reach 'completed' for {task_id!r} within {timeout_s}s"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_journey_1_overnight_pr(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001
) -> None:
    """Journey 1 "Overnight PR" end-to-end: submit → plan → execute → approve → push → PR → complete.

    The chain under test:

    1. Build scripted-worker-stub and auto-approval-stub images.
    2. Boot the 4-service compose stack.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "j1-overnight-test"}``.
    6. Poll JSONL until ``task.completed`` appears.
    7. Assert the complete Journey 1 event sequence is present.
    8. Assert ``approval.granted`` appears after ``task.awaiting_approval``.
    9. Assert no event-type duplication (stub events).
    10. Verify materializer: SQLite ``tasks.status = completed``.
    """
    # Deferred imports — conftest injects tests/integration/ onto sys.path at
    # session start, but pytest collects (imports) test modules *before* running
    # conftest fixtures.  Using late imports avoids ModuleNotFoundError during
    # collection when the test is marker-skipped (e.g. ``-m "not slow"``).
    import _build_auto_approval  # type: ignore[import-not-found]
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

    project = f"omb-j1-{uuid4().hex[:8]}"
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
            resp = client.post("/v1/tasks", json={"title": "j1-overnight-test"})
        assert resp.status_code == 201, (
            f"expected 201 on POST /v1/tasks, got {resp.status_code}; body={resp.text!r}"
        )
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str) and task_id

        log_dir = data_dir / "registry" / "events"

        # Step 6 — poll JSONL until task.completed appears.
        last_envelopes = _poll_for_event(
            log_dir, task_id, "task.completed", timeout_s=_TASK_COMPLETION_TIMEOUT_S
        )

        # Step 7 — assert the complete Journey 1 event sequence.
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
            "task.awaiting_approval",
            "approval.granted",
            "task.push.completed",
            "task.pr.opened",
            "task.completed",
        }
        missing = expected - set(types_for_task)
        assert not missing, (
            f"task {task_id!r}: missing Journey 1 events {missing!r}; "
            f"types observed={types_for_task!r}"
        )

        # Step 8 — assert approval.granted appears after task.awaiting_approval.
        task_type_list = [
            t for t in types_for_task if t in {"task.awaiting_approval", "approval.granted"}
        ]
        assert task_type_list == ["task.awaiting_approval", "approval.granted"], (
            f"task {task_id!r}: expected approval.granted after task.awaiting_approval, "
            f"got order={task_type_list!r}"
        )

        # Step 9 — assert no event-type duplication for stub-emitted events.
        for ut in STUB_EVENTS:
            count = types_for_task.count(ut)
            assert count == 1, (
                f"task {task_id!r}: expected exactly 1 {ut!r}, got {count}; "
                f"types={types_for_task!r}"
            )

        # Step 10 — verify the materializer projected task.completed into SQLite.
        _wait_for_task_status_completed(data_dir, task_id)

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


@pytest.mark.integration
def test_worker_facing_source_code_unchanged() -> None:
    """Sentinel: worker-facing source must remain untouched by this story."""
    _ALLOWED = {
        "services/registry-state/src/registry_state/domain/event_types.py",
        "services/orchestrator-adapter/src/orchestrator_adapter/adapters/test_github_adapter.py",
        "services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py",
        # test_failure_detection.py is a co-located test file — not worker-facing
        # source. Story 9.7 updates fixture assertions for schema_version 1.1.0.
        "services/registry-state/src/registry_state/domain/test_failure_detection.py",
        # Story 10.2 AC1 EventLogReader extraction: see test_s1 for details.
        # event_log.py becomes thin re-export shim; app/main.py uses renamed public imports.
        "services/registry-state/src/registry_state/adapters/event_log.py",
        "services/registry-state/src/registry_state/app/main.py",
    }

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
            ["git", "diff", "--name-only", "HEAD~1", "HEAD", "--", *_WORKER_FACING_PATHS],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.skip("HEAD~1 not available (shallow clone or initial commit)")

    changed = [f for f in proc.stdout.strip().splitlines() if f not in _ALLOWED]
    assert not changed, (
        f"worker-facing source touched in last commit:\n{chr(10).join(changed)}\n"
        "Story 5.18's separability claim requires no source modifications "
        "to registry-state/registry-api/clawhip-bridge/orchestrator-adapter."
    )


__all__ = [
    "test_journey_1_overnight_pr",
    "test_worker_facing_source_code_unchanged",
]
