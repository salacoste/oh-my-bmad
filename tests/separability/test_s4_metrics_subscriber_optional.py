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
  - Wall-clock budget: ~7 minutes total (D6 — 180s healthcheck budget
    per phase + teardown + assertion overhead). P1-L1 review patch:
    previous docstring claim of "~3 minutes" was wrong — actual budget
    is dominated by ``_HEALTHCHECK_TIMEOUT_S=180.0`` per phase plus
    teardown. CI job budgets should be sized accordingly (~7 min, not 4).
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
from prometheus_client.parser import text_string_to_metric_families

_log = logging.getLogger(__name__)


# P1-L3 review patch — duplicate of the 6-line helper from
# ``tests/integration/test_metrics_cardinality.py`` (kept inline to avoid
# importing across the test-package boundary). Story 10.5 owns the
# cardinality regression gate; this helper backs only the lightweight
# smoke check in Phase 1.
def _count_canonical_timeseries(body: str) -> int:
    """Count canonical Prometheus timeseries in ``body``.

    Filters out the ``_created`` bookkeeping samples that
    ``prometheus_client`` emits alongside every Counter labelset (per
    Story 10.4 — ``_created`` is metadata, not a real indexed timeseries).
    """
    count = 0
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if not sample.name.endswith("_created"):
                count += 1
    return count


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
        # P1-H1: bounded poll — `ps` should return in <1s; 60s upper bound prevents
        # CI hang on a stalled Docker socket. TimeoutExpired propagates (no swallow).
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
        # P1-H1: 60s timeout — `docker compose port` is normally <1s.
        proc = subprocess.run(
            _compose_cmd(project, compose_file, "port", service, str(port)),
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
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
    # P1-H1: 60s timeout — `ps --format json` is a fast read against the Docker socket.
    proc = subprocess.run(
        _compose_cmd(project, compose_file, "ps", "--format", "json"),
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
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
    broad (any mention of the service NAME *or* its CONTAINER NAME +
    error vocabulary):

      * service name form: ``metrics-subscriber`` (used in compose
        DNS + service references);
      * container name form: ``omb-metrics-subscriber`` (set by the
        root compose ``container_name:`` directive).

    P1-M2 review patch — both forms are now matched (false-negative
    hardening: a producer that logs the container name would have
    previously slipped past the grep).
    """
    # P1-H1: bounded `logs` call. `--tail 200` caps the byte-volume even on a
    # 180s-old stack with verbose producers (worst case ~tens of thousands of
    # JSON log lines). 60s timeout is a hard upper bound — `logs --tail 200`
    # against the local Docker socket is normally <1s.
    proc = subprocess.run(
        _compose_cmd(project, compose_file, "logs", "--no-color", "--tail", "200", service),
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return []
    flagged: list[str] = []
    for line in proc.stdout.splitlines():
        lower = line.lower()
        # P1-M2: match BOTH service name and container name forms.
        if "metrics-subscriber" not in lower and "omb-metrics-subscriber" not in lower:
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

    P1-H2 review patch — also verifies the ``docker compose`` v2 plugin
    is installed. The test body exclusively uses ``docker compose ...``
    (v2 form); on systems with Docker Engine but a broken/missing
    compose plugin, the previous one-probe check would let the test
    execute and then fail opaquely at the first ``docker compose up``.
    The two-probe form gives a clean skip instead.
    """
    try:
        proc_info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if proc_info.returncode != 0:
        return False
    try:
        proc_compose = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc_compose.returncode == 0


@pytest.mark.separability
@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_available(),
    reason="S-4 separability requires Docker + compose v2 plugin — install or run via CI",
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
    # P1-M4 review patch — prerequisite check for the base image. The
    # metrics-subscriber Dockerfile is a thin override of
    # ``oh-my-bmad-base:local`` (see D7 in spec). Without that base
    # image present, ``docker compose up`` would fail with an opaque
    # ``pull access denied`` error mid-flight. Fail fast with a clear
    # remediation message instead.
    base_check = subprocess.run(
        ["docker", "image", "inspect", "oh-my-bmad-base:local"],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if base_check.returncode != 0:
        pytest.fail(
            "prerequisite missing: run 'just build-base' first to build the "
            "oh-my-bmad-base:local image required by "
            "services/metrics-subscriber/Dockerfile"
        )

    phase1_project = f"omb-s4p1-{uuid4().hex[:8]}"
    phase1_env = os.environ.copy()
    # Story 11.3.5: the ROOT compose is production-shaped — its schema comes from
    # the operator/migrator's `alembic upgrade head`, NOT in-process. This
    # self-contained separability stack has no migrator step, so opt registry-state
    # into the same in-process schema bootstrap the S-1/S-2/S-3 test composes use.
    # The ROOT compose reads this via `${REGISTRY_STATE_AUTO_CREATE_SCHEMA:-}`
    # (default OFF in production); setting it here is test-scoped only.
    phase1_env["REGISTRY_STATE_AUTO_CREATE_SCHEMA"] = "1"
    # The root compose binds the `oh-my-bmad-data` *named volume*
    # (driver: local) — per-project naming gives each test invocation
    # its own volume so `down -v` cleanly tears it down without
    # touching any host bind-mount tree.
    try:
        # P1-H1: 5-minute timeout on `up -d` — covers cold-cache image pulls
        # + build + initial healthcheck arming. TimeoutExpired propagates.
        proc_up = subprocess.run(
            _compose_cmd(phase1_project, _ROOT_COMPOSE_FILE, "up", "-d"),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
            timeout=300,
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
        # P1-L3 review patch — sanity-check baseline cardinality <= 52.
        # Story 10.5 owns the cardinality regression gate; this is a smoke
        # check only. The `proc_metrics` `python -c` printed only the
        # first 200 bytes via slicing — re-fetch the full body for the
        # cardinality count.
        proc_metrics_full = subprocess.run(
            _compose_cmd(
                phase1_project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "metrics-subscriber",
                "python",
                "-c",
                "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9090/metrics', timeout=2).read().decode(), end='')",
            ),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc_metrics_full.returncode == 0 and proc_metrics_full.stdout:
            ts_count = _count_canonical_timeseries(proc_metrics_full.stdout)
            assert ts_count <= 52, (
                f"Phase 1: cardinality smoke check failed — "
                f"{ts_count} canonical timeseries > 52 budget. Story 10.5 "
                f"owns the authoritative regression gate; this is a smoke "
                f"backstop."
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

        # P1-M3 review patch — Phase 1 POST /v1/tasks write-path probe.
        # Phase 2 exercises the write path; without symmetric coverage
        # in Phase 1, a regression that breaks writes ONLY when the
        # subscriber is present (e.g., volume contention, file-lock) would
        # slip past. The body uses S-1's canonical shape ({"title": ...} →
        # 201 with task_id in JSON body). A unique idempotency-key-style
        # title per test run avoids cross-run collisions.
        phase1_idem = f"s4-phase1-{uuid4()}"
        phase1_post_script = (
            "import json, urllib.request, sys\n"
            "req = urllib.request.Request(\n"
            "    'http://127.0.0.1:8080/v1/tasks',\n"
            f"    data=json.dumps({{'title': '{phase1_idem}'}}).encode(),\n"
            "    headers={'Content-Type': 'application/json'},\n"
            "    method='POST',\n"
            ")\n"
            "with urllib.request.urlopen(req, timeout=5) as r:\n"
            "    body = r.read().decode()\n"
            "    print(r.status)\n"
            "    print(body)\n"
            "    sys.exit(0 if r.status == 201 else 1)\n"
        )
        proc_post = subprocess.run(
            _compose_cmd(
                phase1_project,
                _ROOT_COMPOSE_FILE,
                "exec",
                "-T",
                "registry-api",
                "python",
                "-c",
                phase1_post_script,
            ),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc_post.returncode == 0, (
            f"Phase 1: POST /v1/tasks expected 201 but the exec probe failed "
            f"(rc={proc_post.returncode}); stdout={proc_post.stdout!r}; "
            f"stderr={proc_post.stderr!r}"
        )
    finally:
        # P1-H1: 5-minute timeout on `down -v --remove-orphans` — covers
        # volume cleanup on contended hosts. TimeoutExpired propagates;
        # cleanup failure is preferable to silent CI hang.
        proc_down = subprocess.run(
            _compose_cmd(phase1_project, _ROOT_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=phase1_env,
            capture_output=True,
            text=True,
            timeout=300,
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
        # P1-H1: 5-minute timeout on `up -d`.
        proc_up = subprocess.run(
            _compose_cmd(phase2_project, _S4_COMPOSE_FILE, "up", "-d"),
            check=False,
            env=phase2_env,
            capture_output=True,
            text=True,
            timeout=300,
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
        # P1-L5 review patch — iterate ALL 6 producer services (was 4;
        # `telegram-gateway` and `orchestrator-adapter` were previously
        # excluded with no rationale). All 6 producers are checked now;
        # no exclusions. The matcher matches both `metrics-subscriber`
        # (service name) and `omb-metrics-subscriber` (container name)
        # forms per P1-M2.
        flagged_lines: list[str] = []
        for svc in _PRODUCER_SERVICES:
            flagged_lines.extend(
                _grep_logs_for_missing_subscriber(phase2_project, _S4_COMPOSE_FILE, phase2_env, svc)
            )
        assert not flagged_lines, (
            "Phase 2: producer services logged errors mentioning the missing "
            "metrics-subscriber — indicates hidden coupling. Lines:\n"
            + "\n".join(flagged_lines[:20])
        )
    finally:
        # P1-H1: 5-minute timeout on `down -v --remove-orphans`.
        proc_down = subprocess.run(
            _compose_cmd(phase2_project, _S4_COMPOSE_FILE, "down", "-v", "--remove-orphans"),
            check=False,
            env=phase2_env,
            capture_output=True,
            text=True,
            timeout=300,
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
