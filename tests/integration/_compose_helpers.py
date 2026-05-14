"""Shared Docker Compose helpers for journey and separability integration tests.

Extracted from test_journey_1_overnight.py, test_journey_3_recovery.py, and
test_journey_6_stale_blocker.py (ADR-0002).

Usage::

    from tests.integration._compose_helpers import (
        compose_cmd,
        compose_env,
        resolve_registry_api_port,
        wait_for_all_healthy,
    )
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

__all__ = [
    "compose_env",
    "compose_cmd",
    "wait_for_all_healthy",
    "resolve_registry_api_port",
]


def compose_env(
    data_dir: Path,
    *,
    data_dir_key: str,
    worker_image: str,
    approval_image: str,
    container_uid: int = 10002,
    container_gid: int = 10000,
) -> dict[str, str]:
    """Build the environment dict for docker compose.

    Parameters
    ----------
    data_dir_key:
        Journey-specific env var name (e.g. ``"OMB_J1_DATA_DIR"``).
    """
    env = os.environ.copy()
    env[data_dir_key] = str(data_dir)
    env["WORKER_IMAGE"] = worker_image
    env["AUTO_APPROVAL_IMAGE"] = approval_image
    env.setdefault("OMB_S3_UID", str(container_uid))
    env.setdefault("OMB_S3_GID", str(container_gid))
    return env


def compose_cmd(
    project: str,
    compose_file: Path,
    *args: str,
) -> list[str]:
    """Build a docker compose command line."""
    if not compose_file.is_file():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(compose_file),
        *args,
    ]


def wait_for_all_healthy(
    project: str,
    env: dict[str, str],
    compose_file: Path,
    *,
    timeout_s: float,
    min_services: int = 0,
) -> None:
    """Poll ``docker compose ps`` until all services report healthy.

    Parameters
    ----------
    min_services:
        Minimum number of services expected before declaring healthy.
        Set to the expected service count to guard against partial output.
    """
    deadline = time.monotonic() + timeout_s
    last_state: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        proc = subprocess.run(
            compose_cmd(project, compose_file, "ps", "--format", "json"),
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
        if (
            (not min_services or len(services) >= min_services)
            and services
            and all(s.get("Health") == "healthy" for s in services)
        ):
            return
        time.sleep(1.0)
    raise TimeoutError(
        f"compose project {project!r}: not all services became healthy "
        f"within {timeout_s}s; last state={last_state!r}"
    )


def resolve_registry_api_port(
    project: str,
    env: dict[str, str],
    compose_file: Path,
    *,
    timeout_s: float,
) -> int:
    """Resolve the host port for the registry-api service.

    Assumes the container exposes port 8080.
    """
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        proc = subprocess.run(
            compose_cmd(project, compose_file, "port", "registry-api", "8080"),
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
