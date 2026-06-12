"""Hermetic compose-contract test for the litestream WAL-replication sidecar.

Closes traceability gap G4 (LOW): the litestream sidecar's compose
profile-gating and env-default-disable were previously verified ONLY by a
manual ``docker compose config`` run — no automated coverage.

Requirements under test (Story 13.1 / ADR-0007):

* **FR69** — the optional litestream sidecar in ``docker-compose.yml``
  replicates the data volume's SQLite WAL to S3-compatible storage. It MUST be
  OPTIONAL (off by default). Enforced here by asserting the service is gated
  behind ``profiles: ["litestream"]`` (so a plain ``docker compose up`` never
  starts it) AND that the default activation set is unchanged.

* **FR70** — ``OMB_LITESTREAM_CONFIG_PATH`` in ``.env`` points to the mounted
  YAML; an ABSENT config disables the sidecar entirely (no replication, no
  startup error — the stack runs without it). Enforced here by asserting the
  config bind uses ``${OMB_LITESTREAM_CONFIG_PATH:-./litestream.yml}`` (a
  shell-default expansion, so an unset var degrades to the default path rather
  than erroring) and that the gating profile keeps the sidecar out of the
  default stack.

Design notes:

* HERMETIC by default — parses ``docker-compose.yml`` with PyYAML
  (``yaml.safe_load``); requires NO Docker daemon. Mirrors the YAML-parse
  convention in ``tests/crash-injection/conftest.py``.
* One OPTIONAL second test shells out to ``docker compose config`` to verify
  the profile actually flips the activation set, gated behind a collection-time
  ``_docker_available()`` skipif (the two-probe pattern from
  ``tests/separability/test_s4_metrics_subscriber_optional.py``).
* Lives at the ``tests/`` top level (like
  ``tests/test_no_undocumented_spawn_sites.py``) so it is collected by the
  default ``testpaths`` and is mypy-exempt via the broad ``[mypy-tests.*]
  ignore_errors = True`` override (only the named subpackages are type-checked).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

# Repo root from this top-level ``tests/`` module: parents[1] == repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[1]
_COMPOSE_PATH: Path = _REPO_ROOT / "docker-compose.yml"

# The 7 services that constitute the DEFAULT (no-profile) stack — the
# sprint-status "7 default / 8 with --profile litestream" contract. Kept
# explicit so an accidental addition/removal of a default service trips this
# test. ``migrator`` and ``litestream`` are the two profile-gated services and
# are intentionally NOT in this set.
_EXPECTED_DEFAULT_SERVICES: frozenset[str] = frozenset(
    {
        "registry-api",
        "registry-state",
        "telegram-gateway",
        "orchestrator-adapter",
        "worker-wrapper",
        "clawhip-daemon",
        "metrics-subscriber",
    }
)

# Profile-gated services that are EXPECTED to exist and be off-by-default.
# ``migrator`` pre-existed (profiles: ["migrate"]); ``litestream`` is FR69's
# sidecar. Remote MCP services are also intentionally profile-gated so the
# default local stack remains the same 7 core services.
_EXPECTED_PROFILE_GATED: dict[str, str] = {
    "migrator": "migrate",
    "litestream": "litestream",
    "task-registry-mcp": "remote-mcp",
    "session-registry-mcp": "remote-mcp",
    "git-mcp": "remote-mcp",
    "github-mcp": "remote-mcp",
    "verification-mcp": "remote-mcp",
    "memory-mcp": "remote-mcp",
    "artifact-mcp": "remote-mcp",
    "browser-mcp": "remote-mcp",
    "clawhip-bridge-mcp": "remote-mcp",
}


def _load_compose() -> dict[str, Any]:
    """Parse ``docker-compose.yml`` hermetically (no Docker)."""
    text = _COMPOSE_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), "docker-compose.yml did not parse to a mapping"
    return parsed


def _services() -> dict[str, Any]:
    services = _load_compose().get("services")
    assert isinstance(services, dict), "docker-compose.yml has no 'services' mapping"
    return services


def _docker_available() -> bool:
    """Collection-time Docker probe for the OPTIONAL second test.

    Mirrors ``tests/separability/test_s4_metrics_subscriber_optional.py``: two
    probes (``docker info`` + ``docker compose version``) so a host with the
    engine but a broken/missing compose v2 plugin gets a clean skip rather than
    an opaque failure inside the test body.
    """
    try:
        info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if info.returncode != 0:
        return False
    try:
        compose = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10.0,
            check=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return compose.returncode == 0


def test_litestream_service_is_profile_gated() -> None:
    """FR69: the litestream sidecar is off by default via ``profiles:``.

    A service carrying ``profiles: ["litestream"]`` is excluded from a plain
    ``docker compose up`` activation set — that is the mechanism that makes the
    replication sidecar OPTIONAL (FR69) and lets an operator who never enables
    the profile run the whole stack without it (FR70 absent-disables).
    """
    litestream = _services().get("litestream")
    assert litestream is not None, "expected a 'litestream' service in docker-compose.yml"
    assert litestream.get("profiles") == ["litestream"], (
        "FR69: litestream must declare exactly profiles: ['litestream'] so it is "
        f"OFF in the default stack; got {litestream.get('profiles')!r}"
    )


def test_litestream_mounts_data_volume_read_write() -> None:
    """Story 13.1 / ADR-0007 §3 correction: the data volume mount is READ-WRITE.

    litestream is not a passive reader — it takes over checkpointing and writes
    its own metadata dir alongside the DB, so a ``:ro`` mount would break
    replication outright. This guards against a regression back to ``:ro``.
    """
    volumes = _services()["litestream"].get("volumes")
    assert isinstance(volumes, list), "litestream service must declare a volumes list"

    data_mounts = [v for v in volumes if isinstance(v, str) and v.startswith("oh-my-bmad-data:")]
    assert len(data_mounts) == 1, (
        f"expected exactly one oh-my-bmad-data mount on litestream; got {data_mounts!r}"
    )
    mount = data_mounts[0]
    # Short-form mount is ``source:target[:mode]``. Read-write means the mode
    # field is absent or explicitly ``rw`` (never ``ro``).
    parts = mount.split(":")
    mode = parts[2] if len(parts) >= 3 else "rw"
    assert mode != "ro", (
        "Story 13.1/ADR-0007 §3: litestream must mount oh-my-bmad-data READ-WRITE "
        f"(checkpointing writes the DB); found read-only mount {mount!r}"
    )
    assert mode in ("rw", "z", "Z", "rw,z", "rw,Z"), (
        f"unexpected mount mode {mode!r} on litestream data volume {mount!r}"
    )


def test_litestream_config_path_uses_env_default() -> None:
    """FR70: config bind uses ``${OMB_LITESTREAM_CONFIG_PATH:-./litestream.yml}``.

    The ``:-`` shell-default expansion means an UNSET ``OMB_LITESTREAM_CONFIG_PATH``
    degrades to the ``./litestream.yml`` default rather than producing an empty /
    invalid bind — part of the FR70 "absent config does not error the stack"
    contract (combined with the profile gate keeping the sidecar off entirely).
    """
    volumes = _services()["litestream"]["volumes"]
    config_mounts = [
        v for v in volumes if isinstance(v, str) and "/etc/litestream/litestream.yml" in v
    ]
    assert len(config_mounts) == 1, (
        "expected exactly one litestream config bind mounted at "
        f"/etc/litestream/litestream.yml; got {config_mounts!r}"
    )
    config_mount = config_mounts[0]
    assert config_mount.startswith("${OMB_LITESTREAM_CONFIG_PATH:-"), (
        "FR70: config bind must source-expand "
        "${OMB_LITESTREAM_CONFIG_PATH:-<default>} so an unset var falls back to a "
        f"default path; got {config_mount!r}"
    )
    assert "${OMB_LITESTREAM_CONFIG_PATH:-./litestream.yml}" in config_mount, (
        f"FR70: expected the documented default './litestream.yml'; got {config_mount!r}"
    )


def test_default_stack_unchanged_by_litestream_profile_gating() -> None:
    """FR69/FR70: enabling litestream adds ONLY the litestream service.

    Verifies the sprint-status "7 default / 8 with --profile litestream"
    contract hermetically:

    * the default (no-profile) activation set is exactly the 7 known services;
    * the profile-gated services are exactly the expected optional groups
      (``migrator``, ``litestream``, and ``remote-mcp`` tools) with their
      expected profile names — i.e. litestream did not accidentally alter the
      core default stack.
    """
    services = _services()

    gated = {
        name: cfg["profiles"]
        for name, cfg in services.items()
        if isinstance(cfg, dict) and "profiles" in cfg
    }
    # Only the expected optional services are profile-gated.
    assert set(gated) == set(_EXPECTED_PROFILE_GATED), (
        "unexpected change to the set of profile-gated services; "
        f"expected {sorted(_EXPECTED_PROFILE_GATED)}, got {sorted(gated)}"
    )
    for name, profile in _EXPECTED_PROFILE_GATED.items():
        assert gated[name] == [profile], (
            f"service {name!r} must declare profiles: [{profile!r}]; got {gated[name]!r}"
        )

    # The default (no-profile) activation set is exactly the 7 known services.
    default_services = frozenset(services) - set(_EXPECTED_PROFILE_GATED)
    assert default_services == _EXPECTED_DEFAULT_SERVICES, (
        "FR69/FR70: the default (no-profile) stack must be unchanged "
        f"(expected {sorted(_EXPECTED_DEFAULT_SERVICES)}); got {sorted(default_services)}"
    )
    assert len(default_services) == 7, (
        f"sprint-status contract: 7 services ACTIVE by default; got {len(default_services)}"
    )
    # sprint-status "8 with --profile litestream": the 7 default services PLUS
    # litestream become active when only the litestream profile is on (migrator
    # stays gated behind its own 'migrate' profile). Computed hermetically as
    # default ∪ {litestream}.
    active_with_litestream_profile = default_services | {"litestream"}
    assert len(active_with_litestream_profile) == 8, (
        "sprint-status contract: 8 services active with --profile litestream "
        f"(7 default + litestream); got {len(active_with_litestream_profile)}"
    )
    # Sanity: the YAML always DECLARES all services; profiles only gate
    # activation, not declaration.
    expected_declared = len(_EXPECTED_DEFAULT_SERVICES) + len(_EXPECTED_PROFILE_GATED)
    assert len(services) == expected_declared, (
        f"expected {expected_declared} declared services "
        "(7 default + optional profile-gated services); "
        f"got {len(services)}"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_available(),
    reason="optional docker-gated profile check requires Docker + compose v2 plugin",
)
def test_docker_compose_config_excludes_litestream_without_profile() -> None:
    """FR69/FR70 (optional, docker-gated): the profile flips the activation set.

    Confirms with the real compose resolver that ``docker compose config`` (no
    profile) OMITS the litestream service, while ``--profile litestream``
    INCLUDES it. Hermetic siblings above already assert the static contract;
    this is belt-and-suspenders against compose parsing the YAML differently
    than PyYAML.
    """

    def _config_service_names(*profile_args: str) -> set[str]:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(_COMPOSE_PATH),
                *profile_args,
                "config",
                "--services",
            ],
            capture_output=True,
            timeout=60.0,
            check=False,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        assert proc.returncode == 0, (
            f"`docker compose config` failed (args={profile_args}): {proc.stderr}"
        )
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    default_names = _config_service_names()
    assert "litestream" not in default_names, (
        "FR69: litestream must be absent from the default `docker compose config`; "
        f"got {sorted(default_names)}"
    )

    profile_names = _config_service_names("--profile", "litestream")
    assert "litestream" in profile_names, (
        f"FR69: litestream must appear under `--profile litestream`; got {sorted(profile_names)}"
    )
    # Enabling the profile adds exactly the litestream service.
    assert profile_names - default_names == {"litestream"}, (
        "enabling the litestream profile must add ONLY the litestream service; "
        f"delta={sorted(profile_names - default_names)}"
    )
