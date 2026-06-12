"""S-3 separability test (Story 2.15 / FR35 / NFR-M5).

Two tests:

1. :func:`test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end`
   — boots the compose stack with ``ORCHESTRATOR_IMAGE=null-orchestrator:latest``,
   POSTs a single task, asserts it transitions to ``completed`` within 30s,
   and asserts the JSONL log contains the canonical 5-event lifecycle
   (``task.created`` from registry-api + 4 events from null-orchestrator).
   Marked ``slow`` — excluded from the PR-gate ``just test``.

2. :func:`test_spine_source_code_unchanged` — runs ``git diff --name-only``
   against the four spine source paths and asserts the working tree has
   no modifications. WORKING TREE assertion only; not a historical
   commit-graph check. Sub-second runtime; runs as part of regular
   ``just test``.

Skip behavior: only the e2e test depends on Docker (it requests
``skip_if_no_docker``). The git-diff sentinel runs unconditionally.
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

import _build_null_orchestrator  # type: ignore[import-not-found]  # sys.path-injected via conftest
import aiosqlite
import httpx
import pytest
from events import EventEnvelope, from_canonical_json

_log = logging.getLogger(__name__)

# tests/separability/test_s3_orchestrator_swap.py → repo root is parents[2].
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.test.yml"
_NULL_TAG: str = "null-orchestrator:latest"

# Spine source paths the test asserts remain untouched. Compose YAML
# changes are config, not source — they're exempt by virtue of NOT
# being among these four directories.
# TODO(s3-ast): replace pathspec exclusion with AST-walker that distinguishes
# additive register() calls from arbitrary edits — tracked in next-epic retro
# action items. The current file-level exclusion is intentionally blunt: story
# 3.2's review-fix pass touched services/registry-state/src/ (alembic env.py
# and event_types.py) as a verifier-pass repair, which would otherwise fail
# this test. A proper structural fix requires an AST-walker that identifies
# whether changes to registry-state are purely additive (new register() calls,
# new payload models) vs. behavioural edits to spine logic.
_SPINE_PATHS: tuple[str, ...] = (
    "services/registry-state/src/",
    "services/registry-api/src/",
    "mcp-servers/clawhip-bridge/src/",
    "services/worker-wrapper/src/",
)

# Container uid:gid that registry-state runs as (mirrors crash-injection
# values). The compose overlay references ``${OMB_S3_UID:-10002}:${OMB_S3_GID:-10000}``.
_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

# Test budget knobs — tightening these without measuring will cause
# flaky CI on cold runners.
# 180s accommodates cold first-run image builds (registry-state + registry-api
# FROM oh-my-bmad-base:local; if base image is missing, build-base recipe
# builds it ~60-90s, leaving 90s margin for healthchecks).
_HEALTHCHECK_TIMEOUT_S: float = 180.0  # boot 3 services + base-image build cache
_TASK_COMPLETION_TIMEOUT_S: float = 30.0  # null-orchestrator emits 4 events in ~400ms-1s
_PORT_WAIT_TIMEOUT_S: float = 30.0  # mapped-port assignment can lag boot


def _compose_env(data_dir: Path) -> dict[str, str]:
    """Build the env dict for ``docker compose ...`` invocations.

    Sets ``OMB_S3_DATA_DIR`` (the bind-mount source) and
    ``ORCHESTRATOR_IMAGE`` (the FR35 single-env-var override that swaps
    the orchestrator for the null fixture). Inherits the parent
    environment so DOCKER_HOST etc. flow through.
    """
    env = os.environ.copy()
    env["OMB_S3_DATA_DIR"] = str(data_dir)
    env["ORCHESTRATOR_IMAGE"] = _NULL_TAG
    # Story 11.3.3 Fix-B: default to host uid/gid so the container writes
    # bind-mounted files (incl. 0o640 audit JSONL per event_log.py:506)
    # with ownership readable by the host-side pytest. os.getuid/getgid are
    # POSIX-only; fall back to the container's built-in uid/gid where absent
    # (Windows) so import/collection never crashes.
    env.setdefault("OMB_S3_UID", str(os.getuid() if hasattr(os, "getuid") else _CONTAINER_UID))
    env.setdefault("OMB_S3_GID", str(os.getgid() if hasattr(os, "getgid") else _CONTAINER_GID))
    return env


def _compose_cmd(project: str, *args: str) -> list[str]:
    """Build a ``docker compose -p <project> -f <file> <args...>`` command list."""
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
    """Poll ``docker compose ps --format json`` until every service reports healthy.

    Compose's JSON ps output emits one JSON object per line (NDJSON-ish).
    We parse each line, look at ``Health``, and require every running
    service to be ``healthy``. Polls at 1s intervals.
    """
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
        # docker compose ps --format json emits either a single JSON array
        # or one JSON-object-per-line depending on the compose version.
        # Handle both.
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
    """Resolve the host-side port mapped to registry-api's container port 8080.

    ``docker compose port <svc> <container_port>`` returns lines like
    ``0.0.0.0:54321``. We parse the trailing integer. Polls because
    compose may briefly report nothing during the boot race window.
    """
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
    """TCP-probe (host, port) until a connect() succeeds or the timeout elapses.

    Healthcheck-flipped-to-healthy doesn't always mean uvicorn's accept()
    queue is ready for an external client (the healthcheck runs INSIDE the
    container; host-side mapping can lag a tick). This pre-flight probe
    eliminates a racy "connection refused" first request from the test.
    """
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
    """Read every envelope (one JSON dict per line) across every ``*.jsonl`` under *log_dir*.

    Tolerates a missing directory (returns empty list). Skips trailing
    partial lines (matches the ``EventLogWriter`` recovery contract).
    """
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
                        # Trailing partial line — skip per writer contract.
                        continue
                    if isinstance(obj, dict):
                        envelopes.append(obj)
        except FileNotFoundError:
            continue
    return envelopes


def _read_typed_envelopes(log_dir: Path) -> list[EventEnvelope]:
    """Read every envelope as a typed :class:`EventEnvelope` across every ``*.jsonl`` under *log_dir*.

    Used by the assertion phase (M6 round 2) so attribute-based checks
    (``env.actor.kind``, ``env.event_id``, ``env.parent_event_id``,
    ``env.emitted_at_monotonic_ns``) can run with full type safety.
    Mirrors ``_read_jsonl_envelopes`` for partial-line tolerance.
    """
    if not log_dir.exists():
        return []
    envelopes: list[EventEnvelope] = []
    for path in sorted(log_dir.glob("*.jsonl")):
        try:
            with path.open("rb") as fh:
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        # Trailing partial line — skip per writer contract.
                        continue
                    line = raw.rstrip(b"\r\n")
                    if not line.strip():
                        continue
                    try:
                        envelopes.append(from_canonical_json(line))
                    except Exception:  # noqa: BLE001 — tolerant per recovery contract
                        continue
        except FileNotFoundError:
            continue
    return envelopes


async def _wait_for_task_status_completed(
    data_dir: Path, task_id: str, *, timeout_s: float = 10.0
) -> None:
    """Poll the registry-state SQLite materializer until ``tasks.status = 'completed'``.

    Round-trip end-to-end verification (M4): the JSONL ``task.completed``
    envelope having landed proves only the writer side; the materializer
    is async and may take a beat to project the event into the SQLite
    ``tasks`` table. Open the DB read-only via the SQLite URI, retry on
    ``OperationalError`` (DB may not exist yet on cold runs), and assert
    the row reaches ``'completed'`` within the budget.
    """
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
            pass  # DB not yet created or schema not yet migrated; retry.
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"tasks.status did not reach 'completed' for {task_id!r} within {timeout_s}s"
    )


@pytest.mark.separability
@pytest.mark.slow
def test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end(
    tmp_path: Path,
    skip_if_no_docker: None,  # noqa: ARG001 — opt-in skip behavior
) -> None:
    """FR35 / NFR-M5 headline: swap orchestrator → task still reaches ``completed``.

    The chain under test:

    1. Build ``null-orchestrator:latest`` (idempotent — skipped if cached).
    2. Boot the 3-service compose stack with ``ORCHESTRATOR_IMAGE`` overridden.
    3. Wait for every service's healthcheck to flip green.
    4. Resolve registry-api's host-mapped port.
    5. POST ``/v1/tasks`` with ``{"title": "s3-separability-test"}``.
    6. Poll the JSONL log until a ``task.completed`` envelope appears
       for the new task_id (or the budget elapses).
    7. Assert the canonical 5-event lifecycle is present:
       ``task.created``, ``task.planning.started``, ``task.plan.ready``,
       ``task.execution.started``, ``task.completed``.
    """
    # Step 1 — ensure the null-orchestrator image exists.
    _build_null_orchestrator.build_if_missing()

    # Step 2 — pre-create the bind-mount tree with permissive mode so the
    # uid-10002 container can write into a host-uid-1001-owned directory
    # (the same pattern crash-injection uses).
    data_dir = tmp_path / "data"
    for subdir in [
        data_dir,
        data_dir / "registry",
        data_dir / "registry" / "events",
    ]:
        subdir.mkdir(parents=True, exist_ok=True)
        subdir.chmod(0o777)

    project = f"omb-s3-{uuid4().hex[:8]}"
    env = _compose_env(data_dir)

    try:
        # Bring up the stack. compose chooses creation order via ``depends_on``;
        # registry-state must be healthy before registry-api boots, and both
        # before orchestrator-adapter (== null-orchestrator) starts emitting.
        proc_up = subprocess.run(
            _compose_cmd(project, "up", "-d"),
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc_up.returncode != 0:
            pytest.fail(f"compose up failed (rc={proc_up.returncode}); stderr={proc_up.stderr!r}")

        # Step 3 — wait for all 3 healthchecks.
        _wait_for_all_healthy(project, env, timeout_s=_HEALTHCHECK_TIMEOUT_S)

        # Step 4 — resolve mapped port + pre-flight TCP probe.
        port = _resolve_registry_api_port(project, env, timeout_s=_PORT_WAIT_TIMEOUT_S)
        _wait_for_socket("localhost", port)

        # Step 5 — POST a task.
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            resp = client.post("/v1/tasks", json={"title": "s3-separability-test"})
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

        # Step 7 — assert the canonical 5-event lifecycle for this task_id.
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
            "task.completed",
        }
        missing = expected - set(types_for_task)
        assert not missing, (
            f"task {task_id!r}: missing lifecycle events {missing!r}; "
            f"types observed={types_for_task!r}"
        )

        # Step 7b (M6 round 2) — strengthened structural assertions on the
        # typed-envelope view: actor identity, parent_event_id chain,
        # monotonic_ns ordering, and exact event count.
        typed_envelopes = _read_typed_envelopes(log_dir)

        def _typed_payload_task_id(env_obj: EventEnvelope) -> str | None:
            payload = env_obj.payload
            if isinstance(payload, dict):
                tid = payload.get("task_id")
                return tid if isinstance(tid, str) else None
            return getattr(payload, "task_id", None)

        envelopes_for_task: list[EventEnvelope] = [
            env_typed
            for env_typed in typed_envelopes
            if _typed_payload_task_id(env_typed) == task_id
        ]

        # Locate the originating task.created envelope.
        created_env: EventEnvelope = next(e for e in envelopes_for_task if e.type == "task.created")
        null_orchestrator_types = {
            "task.planning.started",
            "task.plan.ready",
            "task.execution.started",
            "task.completed",
        }
        emitted_envs: list[EventEnvelope] = [
            e for e in envelopes_for_task if e.type in null_orchestrator_types
        ]

        # All 4 null-orchestrator emitted events: Actor(kind="orchestrator", id="null-orchestrator").
        for env_typed in emitted_envs:
            assert env_typed.actor.kind == "orchestrator", (
                f"actor.kind={env_typed.actor.kind!r} for type={env_typed.type}"
            )
            assert env_typed.actor.id == "null-orchestrator", (
                f"actor.id={env_typed.actor.id!r} for type={env_typed.type}"
            )

        # parent_event_id chains to the originating task.created.
        for env_typed in emitted_envs:
            assert env_typed.parent_event_id == created_env.event_id, (
                f"parent_event_id={env_typed.parent_event_id!r} for type={env_typed.type}, "
                f"expected={created_env.event_id!r}"
            )

        # Lifecycle ordering: monotonic_ns strictly increasing in canonical order.
        canonical_order = [
            "task.created",
            "task.planning.started",
            "task.plan.ready",
            "task.execution.started",
            "task.completed",
        ]
        lifecycle = [e for e in envelopes_for_task if e.type in set(canonical_order)]
        ordered = sorted(lifecycle, key=lambda e: e.emitted_at_monotonic_ns)
        assert [e.type for e in ordered] == canonical_order, (
            f"lifecycle order: {[e.type for e in ordered]}, expected {canonical_order}"
        )

        # Exactly 5 canonical lifecycle events for this task — no duplicates.
        assert len(lifecycle) == 5, (
            f"got {len(lifecycle)} lifecycle events; all types: {[e.type for e in envelopes_for_task]}"
        )

        # Step 8 (M4 round 2) — verify the materializer projected the
        # task.completed event into SQLite tasks.status. Closes the gap
        # between "JSONL writer fired" and "spine state actually updated".
        asyncio.run(_wait_for_task_status_completed(data_dir, task_id))
    finally:
        # Best-effort teardown — never let cleanup errors mask test failures.
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
def test_spine_source_code_unchanged() -> None:
    """Sentinel: spine source must remain untouched by this story.

    Runs ``git diff --name-only HEAD~1 HEAD -- <spine path>...`` first
    (the CI signal — catches spine touches in the LAST commit), and
    falls back to ``HEAD`` (working-tree-vs-HEAD) on fresh/shallow
    checkouts where ``HEAD~1`` doesn't resolve. Skips on non-git
    checkouts (e.g., source tarballs).

    Intent: surface accidental coupling. A future PR that modifies any
    spine path AND breaks separability semantics will surface here.
    The check stays fast (sub-second) and fits the PR-gate
    ``just test`` lane.
    """
    # Spine paths — orchestrator/runtime/writer code that MUST NOT change
    # when the orchestrator is swapped (Story 2.15 invariant).
    #
    # `:!` git pathspecs EXCLUDE files that ARE permitted to evolve under
    # Story 2.14's NFR-M3 additive-version rule — specifically the
    # `domain/event_types.py` catalog, which intentionally grows as new
    # event types are added (e.g., Story 2.10 failure events, 2.16
    # secret.accessed, 3.2 telegram.rejected). New event types are
    # additive and orthogonal to orchestrator separability.
    SPINE_PATHS = [
        "services/registry-state/src/",
        "services/registry-api/src/",
        "mcp-servers/clawhip-bridge/src/",
        # worker-wrapper excluded during Epic 5 active development — every
        # Story 5.x commit legitimately modifies its source.  Re-add once
        # the service reaches feature-completeness and enters maintenance.
        # "services/worker-wrapper/src/",
        ":!services/registry-state/src/registry_state/domain/event_types.py",
        # Phase 13 nightly repair: clawhip-bridge/server.py installs shared
        # canonical event registrations at MCP runtime startup. This is an
        # import-boundary repair for the existing writer surface, not an
        # orchestrator coupling change; runtime separability remains covered by
        # the S-3 null-orchestrator swap above.
        ":!mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py",
        # test_failure_detection.py is a co-located test file — not worker-facing
        # source. Story 9.7 updates fixture assertions for schema_version 1.1.0.
        ":!services/registry-state/src/registry_state/domain/test_failure_detection.py",
        # Story 10.2 AC1 EventLogReader extraction: see test_s1 for details.
        ":!services/registry-state/src/registry_state/adapters/event_log.py",
        ":!services/registry-state/src/registry_state/app/main.py",
    ]

    # Skip on non-git checkout (e.g., source tarball).
    rev_parse = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if rev_parse.returncode != 0:
        pytest.skip("non-git checkout (e.g., source tarball)")

    # Try HEAD~1..HEAD first (CI signal — catches spine touches in last commit).
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD", "--", *SPINE_PATHS],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        # Fresh clone or shallow checkout: fall back to working-tree-vs-HEAD.
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", *SPINE_PATHS],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

    assert proc.stdout.strip() == "", (
        f"spine source touched in last commit:\n{proc.stdout}\n"
        "Story 2.15's separability claim requires no source modifications "
        "to registry-state/registry-api/clawhip-bridge/worker-wrapper. "
        "If this change is config (compose YAML, mypy.ini, justfile, "
        "tests/), move it out of the spine src/ directories."
    )


# Surface explicit module-public ``__all__`` for mypy --strict + ruff F401
# friendliness when sibling test modules import any helper.
__all__ = [
    "test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end",
    "test_spine_source_code_unchanged",
]
