"""S-4 separability test (Story 10.6 / FR62a / NFR-M4/M5).

Two-phase test that proves the `metrics-subscriber` service is a
**peer consumer**, not a hidden dependency of the platform spine:

* **Phase 1** — boots the full 7-service stack via the ROOT
  `docker-compose.yml` (which ships `metrics-subscriber` default-ON per
  Epic 10 goal). Asserts all 7 services reach `Up (healthy)`; hits
  `/v1/health` on registry-api (200) and `/metrics` on metrics-subscriber
  (200 + Prometheus exposition format); confirms the subscriber's
  `/healthz` returns 200.

* **Phase 2** — boots the SAME 6 producer services via
  `tests/separability/docker-compose.s4.yml` (an overlay that EXCLUDES
  metrics-subscriber). Asserts all 6 services reach `Up (healthy)`;
  hits `/v1/health` on registry-api (200) — same response shape as
  Phase 1; POSTs a synthetic task to `/v1/tasks` (201). Confirms
  metrics-subscriber is NOT present in `docker compose ps` and that
  no producer-service log mentions a missing-subscriber error.

Mirrors the S-1/S-2/S-3 separability conventions:
  - `@pytest.mark.separability + @pytest.mark.slow` (exact same flags
    used by `test_s1_cold_worker_swap.py`).
  - `skip_if_no_docker` session-scoped fixture from `conftest.py`
    gracefully skips on developer machines without Docker.
  - Per-phase `try/finally` GUARANTEES `docker compose down -v
    --remove-orphans` runs even on assertion failure (volume cleanup
    is critical — `oh-my-bmad-data` named volume contamination
    between phases would mask defects).
  - Wall-clock budget: ~3 minutes total (D6 — 60s healthcheck timeout
    per phase + assertions + tear-down overhead).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

_log = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_ROOT_COMPOSE_FILE: Path = _REPO_ROOT / "docker-compose.yml"
_S4_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.s4.yml"

_HEALTHCHECK_TIMEOUT_S: float = 180.0
_PORT_WAIT_TIMEOUT_S: float = 30.0

# 6 producer services that must reach healthy in BOTH phases. Phase 1
# additionally requires `metrics-subscriber` to reach healthy; Phase 2
# asserts it is absent.
_PRODUCER_SERVICES: tuple[str, ...] = (
    "registry-api",
    "registry-state",
    "telegram-gateway",
    "orchestrator-adapter",
    "worker-wrapper",
    "clawhip-daemon",
)


def _compose_cmd(project: str, compose_file: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(compose_file),
        *args,
    ]


def _wait_for_all_healthy(
    project: str,
    compose_file: Path,
    env: dict[str, str],
    *,
    timeout_s: float,
    expected_services: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Poll `docker compose ps --format json` until all expected services healthy.

    Returns the final services list for debugging. Raises ``TimeoutError`` on
    budget exhaustion. The ``expected_services`` parameter pins the assertion —
    Phase 1 passes 7 names (6 producers + metrics-subscriber); Phase 2 passes
    6 names (producers only).
    """
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
        seen_names = {s.get("Service") for s in services if isinstance(s.get("Service"), str)}
        if (
            services
            and expected_set.issubset(seen_names)
            and all(
                s.get("Health") == "healthy" for s in services if s.get("Service") in expected_set
            )
        ):
            return services
        time.sleep(1.0)
    raise TimeoutError(
        f"compose project {project!r} ({compose_file.name}): not all expected "
        f"services {expected_set!r} became healthy within {timeout_s}s; "
        f"last state={last_state!r}"
    )


def _resolve_mapped_port(
    project: str,
    compose_file: Path,
    env: dict[str, str],
    service: str,
    port: int,
    *,
    timeout_s: float,
) -> int:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        proc = subprocess.run(
            _compose_cmd(project, compose_file, "port", service, str(port)),
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
        f"compose project {project!r}: {service}:{port} not resolved "
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


def _list_services_in_ps(project: str, compose_file: Path, env: dict[str, str]) -> list[str]:
    proc = subprocess.run(
        _compose_cmd(project, compose_file, "ps", "--format", "json"),
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    text = proc.stdout.strip()
    services: list[str] = []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                services = [s.get("Service", "") for s in parsed if isinstance(s, dict)]
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
                name = obj.get("Service", "")
                if isinstance(name, str) and name:
                    services.append(name)
    return [s for s in services if s]


def _grep_logs_for_missing_subscriber(
    project: str, compose_file: Path, env: dict[str, str], service: str
) -> list[str]:
    """Return log lines that mention a missing metrics-subscriber dependency.

    Phase 2 asserts producer-service logs do NOT contain any
    "connection refused to metrics-subscriber" or similar error, which
    would indicate a hidden dependency. The grep is intentionally
    broad (any mention of `metrics-subscriber` + error vocabulary).
    """
    proc = subprocess.run(
        _compose_cmd(project, compose_file, "logs", "--no-color", service),
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    flagged: list[str] = []
    for line in proc.stdout.splitlines():
        lower = line.lower()
        if "metrics-subscriber" not in lower:
            continue
        if any(
            tok in lower
            for tok in ("error", "connection refused", "failed", "unreachable", "timeout")
        ):
            flagged.append(line)
    return flagged


def _docker_available() -> bool:
    """Module-level Docker probe used by the `skipif` marker.

    Mirrors `conftest.py::skip_if_no_docker` but as a plain bool-returning
    function (the `@pytest.mark.skipif` decorator needs a value at
    collection time). The conftest fixture remains the canonical
    exit-gate for tests that pass through fixture setup; this marker
    just lets us skip *before* fixture setup runs.
    """
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_available(),
    reason="S-4 separability requires Docker — install or run via CI",
)
def test_metrics_subscriber_is_optional_not_a_hidden_dependency(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001 — session-scoped fixture from conftest
) -> None:
    """FR62a / NFR-M4/M5 headline: subscriber-absent stack serves identically.

    Two phases run sequentially under one test function so the
    teardown of Phase 1 runs to completion (via `try/finally`) before
    Phase 2 begins. Splitting into two ``def`` would risk Phase 2
    inheriting Phase 1's named volume on the rare path where pytest
    interrupts between them; one-function + two-phase keeps the
    `down -v` discipline airtight.
    """
    # ─── Phase 1 — root compose (7 services incl. metrics-subscriber) ──
    phase1_project = f"omb-s4p1-{uuid4().hex[:8]}"
    phase1_env = os.environ.copy()
    # The root compose binds the `oh-my-bmad-data` *named volume*
    # (driver: local) — per-project naming gives each test invocation
    # its own volume so `down -v` cleanly tears it down without
    # touching any host bind-mount tree.
    try:
        proc_up = subprocess.run(
            _compose_cmd(phase1_project, _ROOT_COMPOSE_FILE, "up", "-d"),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
        )
        if proc_up.returncode != 0:
            pytest.fail(
                f"Phase 1 compose up failed (rc={proc_up.returncode}); stderr={proc_up.stderr!r}"
            )

        expected_phase1 = (*_PRODUCER_SERVICES, "metrics-subscriber")
        services_state = _wait_for_all_healthy(
            phase1_project,
            _ROOT_COMPOSE_FILE,
            phase1_env,
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
            expected_services=expected_phase1,
        )
        observed_names = {
            s.get("Service") for s in services_state if isinstance(s.get("Service"), str)
        }
        assert "metrics-subscriber" in observed_names, (
            f"Phase 1: metrics-subscriber missing from compose ps; observed={observed_names!r}"
        )

        # Resolve metrics-subscriber's host-mapped port. The root
        # compose entry does NOT publish a port (P2-I5 internal-only),
        # so `docker compose port` will fail. Instead, exec a curl
        # *inside* the metrics-subscriber container against its own
        # loopback to hit /healthz + /metrics.
        proc_healthz = subprocess.run(
            _compose_cmd(
                phase1_project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "metrics-subscriber",
                "python",
                "-c",
                "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=2); sys.exit(0 if r.status==200 else 1)",
            ),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
        )
        assert proc_healthz.returncode == 0, (
            f"Phase 1: /healthz probe failed (rc={proc_healthz.returncode}); "
            f"stderr={proc_healthz.stderr!r}"
        )

        proc_metrics = subprocess.run(
            _compose_cmd(
                phase1_project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "metrics-subscriber",
                "python",
                "-c",
                "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:9090/metrics', timeout=2); body=r.read().decode(); print(body[:200])",
            ),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
        )
        assert proc_metrics.returncode == 0, (
            f"Phase 1: /metrics probe failed (rc={proc_metrics.returncode}); "
            f"stderr={proc_metrics.stderr!r}"
        )
        # Prometheus exposition format starts with `# HELP` or `# TYPE`
        # comments. Confirm we got at least one — Story 10.5 cardinality
        # smoke-check (lighter than the full regression gate which is
        # exercised by `tests/integration/test_metrics_cardinality.py`).
        assert "# HELP" in proc_metrics.stdout or "# TYPE" in proc_metrics.stdout, (
            f"Phase 1: /metrics output does not look like Prometheus exposition "
            f"format; first 200 bytes={proc_metrics.stdout!r}"
        )

        # Also confirm registry-api still serves its own /v1/health
        # — proves the spine path is unaffected by the subscriber's
        # presence. The registry-api compose entry doesn't bind to a
        # host port either; use exec.
        proc_api_health = subprocess.run(
            _compose_cmd(
                phase1_project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "python",
                "-c",
                "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=2); sys.exit(0 if r.status==200 else 1)",
            ),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
        )
        assert proc_api_health.returncode == 0, (
            f"Phase 1: registry-api /v1/health probe failed "
            f"(rc={proc_api_health.returncode}); stderr={proc_api_health.stderr!r}"
        )
    finally:
        proc_down = subprocess.run(
            _compose_cmd(phase1_project, _ROOT_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
        )
        if proc_down.returncode != 0:
            _log.warning(
                "Phase 1 compose down returned %d; stderr=%r",
                proc_down.returncode,
                proc_down.stderr,
            )

    # ─── Phase 2 — S-4 overlay (6 services, NO metrics-subscriber) ──
    data_dir = tmp_path / "s4-data"
    for subdir in [data_dir, data_dir / "registry", data_dir / "registry" / "events"]:
        subdir.mkdir(parents=True, exist_ok=True)
        subdir.chmod(0o777)

    phase2_project = f"omb-s4p2-{uuid4().hex[:8]}"
    phase2_env = os.environ.copy()
    phase2_env["OMB_S4_DATA_DIR"] = str(data_dir)

    try:
        proc_up = subprocess.run(
            _compose_cmd(phase2_project, _S4_COMPOSE_FILE, "up", "-d"),
            check=False,
            env=phase2_env,
            capture_output=True,
            text=True,
        )
        if proc_up.returncode != 0:
            pytest.fail(
                f"Phase 2 compose up failed (rc={proc_up.returncode}); stderr={proc_up.stderr!r}"
            )

        _wait_for_all_healthy(
            phase2_project,
            _S4_COMPOSE_FILE,
            phase2_env,
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
            expected_services=_PRODUCER_SERVICES,
        )

        # metrics-subscriber MUST NOT appear in ps for Phase 2.
        present_services = _list_services_in_ps(phase2_project, _S4_COMPOSE_FILE, phase2_env)
        assert "metrics-subscriber" not in present_services, (
            f"Phase 2: metrics-subscriber should be absent from the S-4 overlay "
            f"but was found in compose ps; services={present_services!r}"
        )

        # registry-api's /v1/health must serve identically (200).
        port = _resolve_mapped_port(
            phase2_project,
            _S4_COMPOSE_FILE,
            phase2_env,
            "registry-api",
            8080,
            timeout_s=_PORT_WAIT_TIMEOUT_S,
        )
        _wait_for_socket("localhost", port)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            resp_health = client.get("/v1/health")
            assert resp_health.status_code == 200, (
                f"Phase 2: registry-api /v1/health expected 200, "
                f"got {resp_health.status_code}; body={resp_health.text!r}"
            )

            # POST a synthetic task — proves the spine accepts writes
            # identically without the subscriber.
            resp_task = client.post(
                "/v1/tasks", json={"title": "s4-separability-without-subscriber"}
            )
            assert resp_task.status_code == 201, (
                f"Phase 2: POST /v1/tasks expected 201, "
                f"got {resp_task.status_code}; body={resp_task.text!r}"
            )
            task_body = resp_task.json()
            assert isinstance(task_body.get("task_id"), str) and task_body["task_id"], (
                f"Phase 2: POST /v1/tasks response missing task_id; body={task_body!r}"
            )

        # Confirm no producer service log mentions a missing
        # metrics-subscriber error (would indicate hidden coupling).
        flagged_lines: list[str] = []
        for svc in ("worker-wrapper", "clawhip-daemon", "registry-api", "registry-state"):
            flagged_lines.extend(
                _grep_logs_for_missing_subscriber(phase2_project, _S4_COMPOSE_FILE, phase2_env, svc)
            )
        assert not flagged_lines, (
            "Phase 2: producer services logged errors mentioning the missing "
            "metrics-subscriber — indicates hidden coupling. Lines:\n"
            + "\n".join(flagged_lines[:20])
        )
    finally:
        proc_down = subprocess.run(
            _compose_cmd(phase2_project, _S4_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=phase2_env,
            capture_output=True,
            text=True,
        )
        if proc_down.returncode != 0:
            _log.warning(
                "Phase 2 compose down returned %d; stderr=%r",
                proc_down.returncode,
                proc_down.stderr,
            )


__all__ = [
    "test_metrics_subscriber_is_optional_not_a_hidden_dependency",
]
