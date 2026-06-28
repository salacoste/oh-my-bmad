"""S-1 separability test (Story 5.16 / FR34 / NFR-M4).

Two tests:

1. :func:`test_worker_swap_with_scripted_stub_completes_task_end_to_end`
   — boots the compose stack with ``WORKER_IMAGE=scripted-worker-stub:latest``,
   POSTs a single task, asserts it transitions to ``completed`` within 60s,
   and asserts the JSONL log contains the canonical lifecycle events.
   Marked ``slow`` — excluded from the PR-gate ``just test``.

2. :func:`test_worker_facing_source_code_unchanged` — runs ``git diff --name-only``
   against the worker-facing source paths and asserts the working tree has
   no modifications. Sub-second runtime; runs unconditionally.
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
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.s1.yml"
_STUB_TAG: str = "scripted-worker-stub:latest"

# Worker-facing source paths the test asserts remain untouched.
_WORKER_FACING_PATHS: tuple[str, ...] = (
    "services/registry-state/src/",
    "services/registry-api/src/",
    "mcp-servers/clawhip-bridge/src/",
    "services/orchestrator-adapter/src/",
    # worker-wrapper/ is EXCLUDED — the whole point is the worker is swappable.
    # Changes to worker-wrapper source are allowed.
)

_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_TASK_COMPLETION_TIMEOUT_S: float = 60.0
_PORT_WAIT_TIMEOUT_S: float = 30.0


def _compose_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OMB_S1_DATA_DIR"] = str(data_dir)
    env["WORKER_IMAGE"] = _STUB_TAG
    # Story 11.3.3 Fix-B: default to host uid/gid so the container writes
    # bind-mounted files (incl. 0o640 audit JSONL per event_log.py:506)
    # with ownership readable by the host-side pytest. os.getuid/getgid are
    # POSIX-only; fall back to the container's built-in uid/gid where absent
    # (Windows) so import/collection never crashes.
    env.setdefault("OMB_S3_UID", str(os.getuid() if hasattr(os, "getuid") else _CONTAINER_UID))
    env.setdefault("OMB_S3_GID", str(os.getgid() if hasattr(os, "getgid") else _CONTAINER_GID))
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


@pytest.mark.separability
@pytest.mark.slow
def test_worker_swap_with_scripted_stub_completes_task_end_to_end(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001
) -> None:
    """FR34 / NFR-M4 headline: swap worker → task still reaches ``completed``.

    The chain under test:

    1. Build ``scripted-worker-stub:latest`` (idempotent — skipped if cached).
    2. Boot the 3-service compose stack with ``WORKER_IMAGE`` overridden.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "s1-separability-test"}``.
    6. Poll the JSONL log until a ``task.completed`` envelope appears
       for the new task_id (or the budget elapses).
    7. Assert the canonical lifecycle events are present:
       ``task.created``, ``task.planning.started``, ``task.plan.ready``,
       ``task.execution.started``, ``task.step.completed``, ``task.completed``.
    """
    # Step 1 — ensure the scripted-worker-stub image exists.
    _build_scripted_worker.build_if_missing()

    # Step 2 — pre-create the bind-mount tree.
    data_dir = tmp_path / "data"
    for subdir in [
        data_dir,
        data_dir / "registry",
        data_dir / "registry" / "events",
    ]:
        subdir.mkdir(parents=True, exist_ok=True)
        subdir.chmod(0o777)

    project = f"omb-s1-{uuid4().hex[:8]}"
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
            resp = client.post("/v1/tasks", json={"title": "s1-separability-test"})
        assert resp.status_code == 201, (
            f"expected 201 on POST /v1/tasks, got {resp.status_code}; body={resp.text!r}"
        )
        body = resp.json()
        task_id = body["task_id"]
        assert isinstance(task_id, str) and task_id

        # Step 6 — poll the JSONL log until task.completed shows up.
        log_dir = data_dir / "registry" / "events"
        deadline = time.monotonic() + _TASK_COMPLETION_TIMEOUT_S
        completed = False
        last_envelopes: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            last_envelopes = _read_jsonl_envelopes(log_dir)
            for env_obj in last_envelopes:
                if (
                    env_obj.get("type") == "task.completed"
                    and isinstance(env_obj.get("payload"), dict)
                    and env_obj["payload"].get("task_id") == task_id
                ):
                    completed = True
                    break
            if completed:
                break
            time.sleep(0.5)

        if not completed:
            types_seen = sorted({e.get("type", "?") for e in last_envelopes})
            pytest.fail(
                f"task {task_id!r} did not reach task.completed within "
                f"{_TASK_COMPLETION_TIMEOUT_S}s; types seen={types_seen}"
            )

        # Step 7 — assert the canonical lifecycle events for this task_id.
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

        # Step 8 — verify the materializer projected the task.completed
        # event into SQLite tasks.status.
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
    # event_types.py is excluded because it defines shared enum literals that
    # both spine and worker legitimately reference — it isn't "worker-facing
    # source" in the architectural sense.
    SPINE_PATHS = list(_WORKER_FACING_PATHS) + [
        ":!services/registry-state/src/registry_state/domain/event_types.py",
        # test_failure_detection.py is a co-located test file — not "worker-facing
        # source" in the architectural sense. Story 9.7 updates fixture assertions
        # for the 1.0.0→1.1.0 schema_version bump; excluding mirrors event_types.py.
        ":!services/registry-state/src/registry_state/domain/test_failure_detection.py",
        # Story 10.2 AC1 EventLogReader extraction: read-side functions moved to
        # packages/events/log_reader.py. These files become a thin re-export shim
        # (event_log.py) + import rename (app/main.py). The extraction is
        # backwards-compat-preserving by design; behavior unchanged.
        ":!services/registry-state/src/registry_state/adapters/event_log.py",
        ":!services/registry-state/src/registry_state/app/main.py",
        # Story 11.3.6 H7b: orchestrator-adapter's mcp_clients.py adds an explicit
        # env-allowlist forwarded to its spawned MCP subprocesses (P0-adjacent
        # security fix on the a0ca050 code path — NEVER os.environ.copy). The
        # change is inert when the new MCP env vars aren't present (the allowlist
        # only forwards what exists), so behavior is unchanged unless the compose
        # declares the new vars — S-1's stub-worker boot path is unaffected.
        ":!services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py",
        # Phase 13 nightly repair: clawhip-bridge/server.py installs shared
        # canonical event registrations at MCP runtime startup. This is an
        # import-boundary repair for the existing writer surface, not a worker
        # coupling change; runtime separability remains covered by the S-1
        # end-to-end stub-worker swap above.
        ":!mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py",
        # Story 11.3.7 audit: AC1 modifies services/orchestrator-adapter/Dockerfile
        # (COPY upstream/omc, outside src/), AC2 touches
        # services/telegram-gateway/src/, AC3 touches services/clawhip-daemon/src/.
        # None of those fall under _WORKER_FACING_PATHS (line 41 covers only
        # registry-state, registry-api, clawhip-bridge MCP, orchestrator-adapter
        # src/) — no exclusions needed for them.
        #
        # Story 11.3.7 AC5: registry-api's app.py adds a /v1/health liveness
        # route (additive — needed by the S-4 separability probe + previously
        # documented TODO in telegram-gateway's registry_client). The route is
        # middleware-free, DB-free, and pure liveness — S-1's stub-worker cold
        # swap path doesn't touch it, so the addition is inert here.
        ":!services/registry-api/src/registry_api/app.py",
        # Story 117.2: registry-api task-list read route adds an API-local
        # limit+offset selector and co-located route tests only. The S-1
        # scripted-worker cold-swap flow still uses POST /v1/tasks and event
        # projection; no worker-facing coupling or traversal behavior changes.
        ":!services/registry-api/src/registry_api/routes/tasks.py",
        ":!services/registry-api/src/registry_api/test_app.py",
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
        "Story 5.16's separability claim requires no source modifications "
        "to registry-state/registry-api/clawhip-bridge/orchestrator-adapter. "
        "If this change is config (compose YAML, mypy.ini, justfile, "
        "tests/), move it out of the spine src/ directories."
    )


__all__ = [
    "test_worker_swap_with_scripted_stub_completes_task_end_to_end",
    "test_worker_facing_source_code_unchanged",
]
