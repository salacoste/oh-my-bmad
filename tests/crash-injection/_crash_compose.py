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

Compose project name is unique per harness
(``omb-crash-{pid}-{uuid4().hex[:12]}``) so concurrent harness instances
don't share stack state and the ``__exit__`` teardown only removes its
own volumes. The full uuid + pid prefix gives effectively zero collision
probability across concurrent runs (vs. an 8-hex-char suffix which has
a birthday-collision risk around 65k concurrent runs).

Kill mechanics:

* **SIGKILL on ALL platforms** (``docker compose kill --signal SIGKILL``):
  ``compose stop --timeout 1`` sends SIGTERM and gives the process a
  1-second grace window. The registry-state subscriber installs a SIGTERM
  handler that calls ``stop_event.set()`` and drains cleanly in <100ms
  for the tiny payloads the harness writes — so ``compose stop`` exercises
  a *graceful shutdown* path, not a crash. NFR-R1 mandates crash recovery,
  not graceful-shutdown recovery. Using SIGKILL on both Linux and macOS
  ensures the process is never given a chance to flush or drain, making
  the test a true NFR-R1 exercise.

Restart polls the docker healthcheck (a ``test -f /tmp/ready`` probe
against the file the subscriber touches after wiring is complete) up to
70s (60s healthcheck poll + 10s buffer for start-up subprocess).

Bind-mount uid/gid:

The registry-state container runs as uid 10002 / gid 10000 (``omb`` group).
The host user on GitHub Actions ubuntu-latest is uid 1001. Pre-creating
the bind-mount directories with mode 0o777 ensures the container process
can write without ``EACCES`` regardless of host uid. On macOS Docker
Desktop this is transparent (VirtioFS uid translation), but on Linux the
raw UID mapping is used.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Literal
from uuid import uuid4

# tests/crash-injection/_crash_compose.py → tests/crash-injection/docker-compose.test.yml
COMPOSE_FILE: Path = Path(__file__).parent / "docker-compose.test.yml"

# "hard" = SIGKILL (NFR-R1 crash path; default on all platforms).
# "graceful" = compose stop --timeout 1 (SIGTERM → drain → SIGKILL via tini;
# kept for debugging/comparison but NOT used as the default because it lets
# the subscriber drain cleanly, defeating the crash-recovery test).
KillMethod = Literal["hard", "graceful"]

# Container uid:gid that runs registry-state (set in services/registry-state/Dockerfile).
# Used to export OMB_HARNESS_UID / OMB_HARNESS_GID so docker-compose.test.yml
# can pass the correct `user:` directive — avoiding EACCES on Linux where raw
# UID mapping is in effect (macOS Docker Desktop hides this via VirtioFS).
_CONTAINER_UID = 10002
_CONTAINER_GID = 10000

_log = logging.getLogger(__name__)


def _is_macos() -> bool:
    """Return True when running on macOS (Darwin)."""
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
    (``omb-crash-{pid}-{uuid4().hex[:12]}``) so concurrent test runs do
    not share stack state. ``__exit__`` runs ``docker compose down -v
    --remove-orphans`` to drop the bind-mount references and any
    auxiliary volumes/orphan containers from prior runs.

    Args:
        tmp_path: pytest tmp_path. The bind-mount points at
            ``tmp_path / "data"``; the harness creates that subdirectory
            before invoking compose so Docker doesn't auto-create the
            host path as a root-owned directory (which would defeat
            subsequent writes from the harness running as the host
            user).
        project_name: optional explicit project name; defaults to a
            random ``omb-crash-{pid}-{uuid4().hex[:12]}`` (AC-14
            idempotency; collision-safe under concurrent runs).
    """

    def __init__(self, tmp_path: Path, project_name: str | None = None) -> None:
        self._tmp_path = tmp_path
        # Long, unique project name (pid + 12-hex-char uuid suffix) makes
        # birthday-collision under concurrent runs effectively impossible.
        self._project = project_name or f"omb-crash-{os.getpid()}-{uuid4().hex[:12]}"
        self._data_dir = tmp_path / "data"
        # Pre-create the bind-mount source directory and the full events sub-tree.
        # Docker auto-creates missing host bind-mount paths as root-owned, which
        # defeats subsequent writes from the harness (running as the host user).
        # Pre-creating with mode 0o777 sidesteps both the root-ownership issue AND
        # the Linux uid-mismatch problem: the registry-state container runs as
        # uid 10002 / gid 10000 (omb group); on GitHub Actions ubuntu-latest the
        # runner is uid 1001 — without 0o777 the container gets EACCES on every
        # write. macOS Docker Desktop hides this via VirtioFS uid translation, so
        # the mode change is Linux-critical but harmless on macOS.
        for subdir in [
            self._data_dir,
            self._data_dir / "registry",
            self._data_dir / "registry" / "events",
        ]:
            subdir.mkdir(parents=True, exist_ok=True)
            subdir.chmod(0o777)
        # Default: SIGKILL on all platforms. ``compose stop --timeout 1`` would
        # send SIGTERM and give the process a 1s grace window; the subscriber
        # drains cleanly in <100ms for the tiny harness payloads, so it never
        # exercises a true crash. SIGKILL on both platforms is the NFR-R1 kill.
        self._kill_method: KillMethod = "hard"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_name(self) -> str:
        """Compose project name (the ``-p`` flag value)."""
        return self._project

    @property
    def kill_method(self) -> KillMethod:
        """The default kill method (``"hard"`` = SIGKILL on all platforms)."""
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
        # Export uid:gid so docker-compose.test.yml can set ``user:`` on the
        # registry-state service. This prevents EACCES on Linux where Docker
        # uses raw UID mapping (uid 10002 cannot write to host-uid-1001-owned
        # directories). The compose file references these as
        # ``${OMB_HARNESS_UID:-10002}:${OMB_HARNESS_GID:-10000}``.
        env.setdefault("OMB_HARNESS_UID", str(_CONTAINER_UID))
        env.setdefault("OMB_HARNESS_GID", str(_CONTAINER_GID))
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
            text=True,
        )
        # Verify the bind-mount is shared with Docker Desktop on macOS.
        # On macOS Docker only sees host paths inside the file-sharing
        # allowlist. If our tmp_path falls outside that list, the container
        # will boot but the bind-mount inside will be a fresh empty
        # directory — every read from the harness will see nothing.
        # Surface this as a clear error rather than letting the per-phase
        # assertions fail with confusing "task not found" symptoms.
        sentinel = self.event_log_dir() / ".harness-sentinel"
        try:
            sentinel.write_text("crash-harness-bind-mount-check\n", encoding="utf-8")
            check = subprocess.run(
                self._compose_cmd(
                    "exec",
                    "-T",
                    "registry-state",
                    "test",
                    "-f",
                    "/var/lib/oh-my-bmad/registry/events/.harness-sentinel",
                ),
                check=False,
                env=self._compose_env(),
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                # Best-effort cleanup before raising.
                subprocess.run(
                    self._compose_cmd("down", "-v", "--remove-orphans"),
                    check=False,
                    env=self._compose_env(),
                    capture_output=True,
                    text=True,
                )
                raise RuntimeError(
                    "crash-injection bind-mount not visible inside container; "
                    "on macOS, ensure the pytest tmp directory's parent (e.g. "
                    "/private/var/folders) is in Docker Desktop's File Sharing "
                    "allowlist. Sentinel host path: "
                    f"{sentinel}"
                )
        finally:
            # Clean up the sentinel file so it isn't conflated with real
            # events during subsequent log scans.
            sentinel.unlink(missing_ok=True)

        # Wait for the first-boot healthcheck so the test can append events
        # immediately upon ``__enter__`` returning. Clean up on timeout so
        # an aborted boot doesn't leave a leaked container behind.
        try:
            self._wait_for_healthy()
        except TimeoutError:
            subprocess.run(
                self._compose_cmd("down", "-v", "--remove-orphans"),
                check=False,
                env=self._compose_env(),
                capture_output=True,
                text=True,
            )
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # `down -v --remove-orphans` removes the compose project's auxiliary
        # volumes (none in our overlay — the bind-mount is unaffected by `-v`
        # because it's a host path, not a named volume), stops + removes the
        # service containers, and sweeps any orphan containers from prior
        # runs that share the same project label.
        proc = subprocess.run(
            self._compose_cmd("down", "-v", "--remove-orphans"),
            check=False,  # best-effort during cleanup; tear-down errors should not mask test failures
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )
        # Surface compose-down failure stderr in logs (was previously
        # silently swallowed) so a flaky teardown is debuggable from CI logs.
        if proc.returncode != 0:
            _log.warning(
                "crash-harness teardown 'compose down' returned %d; stderr=%r",
                proc.returncode,
                proc.stderr,
            )

    # ------------------------------------------------------------------
    # Kill / restart
    # ------------------------------------------------------------------

    def kill_hard(self) -> None:
        """SIGKILL the registry-state container (NFR-R1 crash path; all platforms).

        Uses ``docker compose kill --signal SIGKILL`` — no grace window, no
        drain, no SIGTERM. This is the correct NFR-R1 kill: the subscriber
        cannot call its finally-block, cannot flush SQLite WAL, cannot update
        /tmp/ready. The post-restart subscriber must reconstruct state from
        JSONL replay alone.
        """
        subprocess.run(
            self._compose_cmd("kill", "--signal", "SIGKILL", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )

    def kill_graceful(self, *, timeout: int = 1) -> None:
        """Send SIGTERM with a *timeout*-second grace period.

        NOT used as the default kill path: ``compose stop --timeout 1`` allows
        the subscriber to drain cleanly (it sets stop_event in the SIGTERM
        handler and exits in <100ms for tiny harness payloads), so it exercises
        graceful-shutdown recovery rather than crash recovery. Kept for
        debugging/comparison only. The NFR-R1 kill is :meth:`kill_hard`.
        """
        subprocess.run(
            self._compose_cmd("stop", "--timeout", str(timeout), "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )

    def kill(self, method: KillMethod | None = None) -> None:
        """Kill the container using *method* (default: ``"hard"`` = SIGKILL).

        ``"hard"`` is the only method that exercises the NFR-R1 crash-recovery
        path. ``"graceful"`` is provided for debugging and comparison but must
        not be used as the default in regression CI.
        """
        chosen = method if method is not None else self._kill_method
        if chosen == "hard":
            self.kill_hard()
        else:
            self.kill_graceful()

    def restart(self, *, timeout_s: float = 70.0) -> float:
        """Bring registry-state back up and wait for healthcheck → ``healthy``.

        Uses ``compose start registry-state`` first (semantically clearer:
        the container exists but is stopped/dead, we want to restart it),
        falling back to ``compose up -d registry-state`` if start fails
        (e.g. container was removed by an external process). The
        healthcheck poll budget (60s default) plus a 10s buffer for the
        start-up subprocess gives a 70s total restart timeout.

        Returns the wall-clock seconds spent in the restart cycle (boot +
        healthcheck) — tests record this in the summary artifact.
        """
        start = time.monotonic()
        proc = subprocess.run(
            self._compose_cmd("start", "registry-state"),
            check=False,
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            # Container may have been removed (e.g. SIGKILL on macOS leaves
            # a dead container that some compose versions auto-prune). Fall
            # back to up -d which recreates it.
            subprocess.run(
                self._compose_cmd("up", "-d", "registry-state"),
                check=True,
                env=self._compose_env(),
                capture_output=True,
                text=True,
            )
        self._wait_for_healthy(timeout_s=timeout_s)
        return time.monotonic() - start

    # ------------------------------------------------------------------
    # Healthcheck poll
    # ------------------------------------------------------------------

    def _container_id(self) -> str:
        """Return the single running registry-state container ID.

        Filters to ``--status=running`` so a dead/exited container left
        behind by a recent SIGKILL doesn't shadow the freshly-started
        replacement during the healthcheck poll.

        Raises ``RuntimeError`` if zero containers are running (expected
        during the brief window between kill and start). Raises
        ``RuntimeError`` if more than one container is running (indicates
        a compose project collision — should never happen given the unique
        per-run project name).
        """
        proc = subprocess.run(
            self._compose_cmd("ps", "--status=running", "-q", "registry-state"),
            check=True,
            env=self._compose_env(),
            capture_output=True,
            text=True,
        )
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError(
                f"compose project {self._project!r} has no RUNNING registry-state container"
            )
        if len(lines) > 1:
            raise RuntimeError(
                f"compose project {self._project!r} has {len(lines)} RUNNING registry-state "
                f"containers (expected exactly 1); container IDs: {lines}"
            )
        return lines[0]

    def _wait_for_healthy(self, *, timeout_s: float = 60.0) -> None:
        """Poll docker inspect at 1s intervals until State.Health.Status == 'healthy'.

        The format string captures all three pieces of state needed for
        a useful timeout error: container status, health status, and
        exit code. An empty health-status field means the container has
        no healthcheck configured (programmer error) or hasn't yet
        completed its first health probe.
        """
        deadline = time.monotonic() + timeout_s
        last_status_line = ""
        last_err: str | None = None
        while time.monotonic() < deadline:
            try:
                cid = self._container_id()
            except RuntimeError as exc:
                # No running container yet — race window during start-up.
                last_err = repr(exc)
                time.sleep(1.0)
                continue
            proc = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Status}} {{.State.Health.Status}} {{.State.ExitCode}}",
                    cid,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # Container ID is gone (race with restart) — re-poll.
                last_err = proc.stderr.strip() or "(no stderr)"
                time.sleep(1.0)
                continue
            last_status_line = proc.stdout.strip()
            # Format: "<status> <health> <exit_code>"
            parts = last_status_line.split(maxsplit=2)
            health = parts[1] if len(parts) >= 2 else ""
            if health == "healthy":
                return
            time.sleep(1.0)
        raise TimeoutError(
            f"registry-state did not become healthy within {timeout_s}s; "
            f"last inspect line={last_status_line!r} (status health exit_code); "
            f"last error={last_err!r}"
        )


__all__ = ["COMPOSE_FILE", "CrashHarness", "KillMethod", "_CONTAINER_GID", "_CONTAINER_UID"]
