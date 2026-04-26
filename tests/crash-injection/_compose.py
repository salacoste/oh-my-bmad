"""Compose orchestration helpers for the Story 2.11 crash-injection harness.

``CrashHarness`` is a context manager that boots a registry-state-only
docker-compose stack against a pytest ``tmp_path`` bind-mount, exposes
host-side filesystem access to the JSONL event log + materialized SQLite
DB, and drives kill/restart cycles for the per-phase tests.

The compose file used is :data:`COMPOSE_FILE` (a self-contained overlay
that declares only the ``registry-state`` service). Phase 1 has no
real workers / telegram-gateway / orchestrator-adapter to test against,
so booting only ``registry-state`` is the canonical Phase 1 testing
surface (matches the rationale in the Story 2.11 spec).

Compose project name is unique per harness (``omb-crash-{uuid4().hex[:8]}``)
so concurrent harness instances don't share stack state and the
``__exit__`` teardown only removes its own volumes.

Kill mechanics:

* Linux: ``docker compose -p <project> stop --timeout 1`` — sends SIGTERM,
  waits 1s, then SIGKILL via tini (the base compose has ``init: true``).
* macOS: ``docker compose -p <project> kill --signal SIGKILL`` —
  ``stop --timeout 1`` is unreliable on Docker Desktop's host VM
  (signal-forwarding latency), so we go straight to SIGKILL per NFR-R1.

Restart polls the docker healthcheck (a ``test -f /tmp/ready`` probe
against the file the subscriber touches after wiring is complete) up to
60s.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Literal
from uuid import uuid4

# tests/crash-injection/_compose.py → tests/crash-injection/docker-compose.test.yml
COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.test.yml"

KillMethod = Literal["stop", "sigkill"]


def _is_macos() -> bool:
    """Return True when running on macOS (Darwin) — drives kill-method default."""
    return platform.system() == "Darwin"


class CrashHarness:
    """Context manager: boot → drive → kill → restart → assert → tear down.

    Usage::

        with CrashHarness(tmp_path) as harness:
            # event_log_dir() and db_path() exist on disk
            harness.kill()       # SIGKILL on macOS, stop on Linux
            harness.restart()    # waits for healthcheck → "healthy"
            ...

    The harness uses a unique compose project name per instance
    (``omb-crash-{uuid4().hex[:8]}``) so concurrent test runs do not
    share stack state. ``__exit__`` runs ``docker compose down -v`` to
    drop the bind-mount references and any auxiliary volumes.

    Args:
        tmp_path: pytest tmp_path. The bind-mount points at
            ``tmp_path / "data"``; the harness creates that subdirectory
            before invoking compose.
        project_name: optional explicit project name; defaults to a
            random ``omb-crash-{uuid4().hex[:8]}`` (AC-14 idempotency).
    """

    def __init__(self, tmp_path: Path, project_name: str | None = None) -> None:
        self._tmp_path = tmp_path
        self._project = project_name or f"omb-crash-{uuid4().hex[:8]}"
        self._data_dir = tmp_path / "data"
        # Pre-create the bind-mount source — Docker creates missing host
        # paths as root-owned directories, which then defeats subsequent
        # writes from the harness running as the host user.
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create the events sub-directory so the registry-state
        # subscriber's ``recover_all_logs(base_dir)`` call sees an
        # existing directory on first boot (Story 2.4 behaviour: the
        # writer creates the dir, but the subscriber expects it).
        (self._data_dir / "registry" / "events").mkdir(parents=True, exist_ok=True)
        self._kill_method: KillMethod = "sigkill" if _is_macos() else "stop"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_name(self) -> str:
        """Compose project name (the ``-p`` flag value)."""
        return self._project

    @property
    def kill_method(self) -> KillMethod:
        """The default kill method auto-routed by host OS."""
        return self._kill_method

    def event_log_dir(self) -> Path:
        """Host-side bind-mount path to the JSONL event log directory."""
        return self._data_dir / "registry" / "events"

    def db_path(self) -> Path:
        """Host-side bind-mount path to ``state.sqlite3``."""
        return self._data_dir / "registry" / "state.sqlite3"

    # ------------------------------------------------------------------
    # Compose primitives — every subprocess.run uses check=True so any
    # docker failure surfaces as a CalledProcessError with the docker
    # output in the .stderr attribute.
    # ------------------------------------------------------------------

    def _compose_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["OMB_HARNESS_DATA_DIR"] = str(self._data_dir)
        return env

    def _compose_cmd(self, *args: str) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self._project,
            "-f",
            str(COMPOSE_FILE),
            *args,
        ]

    def __enter__(self) -> CrashHarness:
        subprocess.run(
            self._compose_cmd("up", "-d", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
        )
        # Wait for the first-boot healthcheck so the test can append events
        # immediately upon ``__enter__`` returning.
        self._wait_for_healthy()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # `down -v` removes the compose project's auxiliary volumes (none
        # in our overlay — the bind-mount is unaffected by `-v` because
        # it's a host path, not a named volume) and stops + removes the
        # service containers.
        subprocess.run(
            self._compose_cmd("down", "-v"),
            check=False,  # best-effort during cleanup; tear-down errors should not mask test failures
            env=self._compose_env(),
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # Kill / restart
    # ------------------------------------------------------------------

    def kill_with_compose_stop(self, *, timeout: int = 1) -> None:
        """Send SIGTERM with a *timeout*-second grace period (Linux path)."""
        subprocess.run(
            self._compose_cmd("stop", "--timeout", str(timeout), "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
        )

    def kill_with_signal_kill(self) -> None:
        """SIGKILL the registry-state container (macOS path)."""
        subprocess.run(
            self._compose_cmd("kill", "--signal", "SIGKILL", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
        )

    def kill(self, method: KillMethod | None = None) -> None:
        """Auto-route to the platform-default kill method, or use *method*."""
        chosen = method if method is not None else self._kill_method
        if chosen == "sigkill":
            self.kill_with_signal_kill()
        else:
            self.kill_with_compose_stop()

    def restart(self, *, timeout_s: float = 60.0) -> float:
        """Bring registry-state back up and wait for healthcheck → ``healthy``.

        Returns the wall-clock seconds spent in the restart cycle (boot +
        healthcheck) — tests record this in the summary artifact.
        """
        start = time.monotonic()
        subprocess.run(
            self._compose_cmd("up", "-d", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
        )
        self._wait_for_healthy(timeout_s=timeout_s)
        return time.monotonic() - start

    # ------------------------------------------------------------------
    # Healthcheck poll
    # ------------------------------------------------------------------

    def _container_id(self) -> str:
        proc = subprocess.run(
            self._compose_cmd("ps", "-q", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )
        cid = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        if not cid:
            raise RuntimeError(f"compose project {self._project!r} has no registry-state container")
        return cid

    def _wait_for_healthy(self, *, timeout_s: float = 60.0) -> None:
        """Poll docker inspect at 1s intervals until State.Health.Status == 'healthy'."""
        deadline = time.monotonic() + timeout_s
        last_status = ""
        while time.monotonic() < deadline:
            cid = self._container_id()
            proc = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    cid,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            last_status = proc.stdout.strip()
            if last_status == "healthy":
                return
            time.sleep(1.0)
        raise TimeoutError(
            f"registry-state did not become healthy within {timeout_s}s; "
            f"last status={last_status!r}"
        )


__all__ = ["COMPOSE_FILE", "CrashHarness", "KillMethod"]
