"""Hermetic compose-contract test for the FR77 digest-deploy overlay.

Requirements under test (Epic 14 / Story 14.1 / FR77 — digest-deprecation
cutover):

* **FR77** — production deploys MUST pin every published CORE service to an
  immutable CONTENT-DIGEST, not the mutable ``:${OMB_VERSION}`` tag. Delivered
  as a DEPLOY-ONLY overlay (``docker-compose.digest.yml``) merged on top of the
  base file, because the base ``image:`` field is dual-purpose (local-build tag
  + GHCR ref) and rewriting it to a digest would break ``docker compose build``.

  This module asserts, hermetically (PyYAML, no Docker):

  - the overlay pins EACH of the 6 core services by ``@${OMB_IMAGE_DIGEST_<svc>:?``
    (fail-loud, no tag between repo path and ``@``) and resets ``build:``;
  - the digest segment has NO ``:-`` default fallback (a missing digest must
    abort the deploy, never silently fall back to a tag);
  - the BASE compose still carries tag + build for those 6 services (local
    build preserved — the overlay is deploy-only);
  - the overlay's service set is EXACTLY the 6 core published services, and
    metrics-subscriber / migrator / litestream are intentionally NOT pinned
    (Option A — they have no release-published digest);
  - the orchestrator-adapter ``${ORCHESTRATOR_IMAGE:-...}`` swap indirection
    (Story 2.15 / FR35) is preserved through the overlay.

Design notes:

* HERMETIC by default — parses the compose YAML with PyYAML
  (``yaml.safe_load``); requires NO Docker daemon. Mirrors
  ``tests/test_litestream_compose_profile_gating.py``.
* The overlay uses the compose ``!reset`` custom tag on ``build:``; PyYAML's
  ``safe_load`` would choke on the unknown ``!reset`` tag, so a minimal
  no-op constructor is registered on a private loader (see ``_load_yaml``).
* One OPTIONAL second test shells out to ``docker compose config`` to verify
  the overlay's fail-loud + digest resolution against the real compose
  resolver, gated behind a collection-time ``_docker_available()`` skipif (the
  two-probe pattern from the litestream test).
* Lives at the ``tests/`` top level so it is collected by the default
  ``testpaths`` and is mypy-exempt via the broad ``[mypy-tests.*]`` override.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

# Repo root from this top-level ``tests/`` module: parents[1] == repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[1]
_BASE_COMPOSE: Path = _REPO_ROOT / "docker-compose.yml"
_DIGEST_OVERLAY: Path = _REPO_ROOT / "docker-compose.digest.yml"

# The 6 CORE published services that FR77 pins by digest. These are exactly the
# services release.yml publishes + signs + ``just verify-images`` covers (minus
# ``base`` / ``console-cli`` which are not compose services).
_CORE_SERVICES: tuple[str, ...] = (
    "registry-api",
    "registry-state",
    "telegram-gateway",
    "orchestrator-adapter",
    "worker-wrapper",
    "clawhip-daemon",
)

# Services that MUST NOT appear in the digest overlay (Option A: stay on the
# base tag). metrics-subscriber + migrator have no release-published/signed
# digest; litestream is a third-party upstream image (ADR-0007).
_INTENTIONALLY_UNPINNED: tuple[str, ...] = (
    "metrics-subscriber",
    "migrator",
    "litestream",
)


def _svc_underscored(service: str) -> str:
    """``svc//-/_`` convention shared with verify-images + .env digest vars."""
    return service.replace("-", "_")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a compose file hermetically, tolerating compose's ``!reset`` tag.

    PyYAML ``safe_load`` raises on the unknown ``!reset`` custom tag the overlay
    uses to reset ``build:``. Register a no-op constructor (we never need the
    reset node's value — its mere PRESENCE under ``build:`` is what we assert)
    on a private loader subclass so the global SafeLoader stays untouched.
    """

    class _ComposeLoader(yaml.SafeLoader):
        pass

    def _reset_ctor(loader: yaml.SafeLoader, node: yaml.Node) -> None:
        return None

    _ComposeLoader.add_constructor("!reset", _reset_ctor)
    parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(parsed, dict), f"{path.name} did not parse to a mapping"
    return parsed


def _services(path: Path) -> dict[str, Any]:
    services = _load_yaml(path).get("services")
    assert isinstance(services, dict), f"{path.name} has no 'services' mapping"
    return services


def _docker_available() -> bool:
    """Collection-time Docker probe for the OPTIONAL second test.

    Two probes (``docker info`` + ``docker compose version``) so a host with the
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


def test_digest_overlay_pins_every_core_service_by_digest() -> None:
    """FR77: every core service is pinned by a fail-loud ``@${...:?}`` digest.

    For each of the 6 core services the overlay's ``image:`` MUST:
    * contain ``@${OMB_IMAGE_DIGEST_<svc>:?`` (digest segment, fail-loud form);
    * carry NO mutable tag between the repo path and ``@`` (no ``:${OMB_VERSION``
      and no bare ``:<tag>`` after the repo name) — otherwise compose would
      resolve the tag, defeating digest pinning;
    * reset ``build:`` (present as a reset/None) so the digest path can never
      build + re-tag over the pinned ref.
    """
    services = _services(_DIGEST_OVERLAY)
    for svc in _CORE_SERVICES:
        assert svc in services, f"FR77: overlay must override core service {svc!r}"
        cfg = services[svc]
        image = cfg.get("image")
        assert isinstance(image, str), f"{svc}: overlay must set an 'image:' string"

        digest_var = f"OMB_IMAGE_DIGEST_{_svc_underscored(svc)}"
        assert f"@${{{digest_var}:?" in image, (
            f"FR77: {svc} image must pin a fail-loud digest "
            f"'@${{{digest_var}:?...}}'; got {image!r}"
        )

        # No mutable tag may sit between the repo path and the '@' digest. Split
        # off everything from the first '@' (the digest segment) and check the
        # repo segment for a ':<tag>' that is NOT a shell ``${...}`` expansion.
        repo_segment = image.split("@", 1)[0]
        # Strip shell ``${...}`` expansions so a literal ':' inside one (none
        # here, but defensive) does not false-positive as a tag.
        repo_no_expansions = re.sub(r"\$\{[^}]*\}", "", repo_segment)
        assert ":" not in repo_no_expansions, (
            f"FR77: {svc} must NOT carry a tag between the repo path and '@'; "
            f"found a ':' in the repo segment of {image!r}"
        )
        assert "${OMB_VERSION" not in repo_segment, (
            f"FR77: {svc} must NOT reference the mutable OMB_VERSION tag on the "
            f"digest path; got {image!r}"
        )

        assert "build" in cfg, f"FR77: {svc} overlay must reset 'build:' (got no build key)"
        assert cfg["build"] is None, (
            f"FR77: {svc} overlay 'build:' must be reset to null (via !reset null) "
            f"so the digest path never builds; got {cfg['build']!r}"
        )


def test_digest_overlay_has_no_tag_fallback() -> None:
    """FR77: no digest segment uses a ``:-`` default fallback.

    A ``@${OMB_IMAGE_DIGEST_x:-...}`` default would let an unset digest silently
    fall back to whatever the default is — exactly the mutable-tag risk FR77
    eliminates. Only the fail-loud ``:?`` form is permitted on the digest var.
    """
    services = _services(_DIGEST_OVERLAY)
    for svc in _CORE_SERVICES:
        image = services[svc]["image"]
        digest_var = f"OMB_IMAGE_DIGEST_{_svc_underscored(svc)}"
        assert f"${{{digest_var}:-" not in image, (
            f"FR77: {svc} must NOT provide a ':-' default fallback for "
            f"{digest_var} (would silently degrade to a tag); got {image!r}"
        )


def test_base_compose_core_services_keep_build_and_tag() -> None:
    """FR77: the BASE compose still has tag + build for the 6 core services.

    The overlay is DEPLOY-ONLY; the base file's dual-purpose ``image:`` (local
    build tag + GHCR ref) and its ``build:`` directive MUST survive so
    ``just dev`` / ``build`` / the tag deploy path still build locally.
    """
    services = _services(_BASE_COMPOSE)
    for svc in _CORE_SERVICES:
        assert svc in services, f"base compose missing core service {svc!r}"
        cfg = services[svc]
        assert isinstance(cfg.get("build"), dict), (
            f"FR77: base {svc} must KEEP its 'build:' directive (local build "
            f"preserved); got {cfg.get('build')!r}"
        )
        image = cfg.get("image")
        assert isinstance(image, str) and "${OMB_VERSION" in image, (
            f"FR77: base {svc} must KEEP its mutable '${{OMB_VERSION}}' tag "
            f"(dual-purpose local-build tag); got {image!r}"
        )
        assert "@" not in image, (
            f"FR77: base {svc} image must stay tag-based (no '@digest'); got {image!r}"
        )


def test_digest_pinned_set_is_exactly_the_six_core() -> None:
    """FR77 / Option A: the overlay pins EXACTLY the 6 core services.

    metrics-subscriber + migrator stay on the base tag (no release-published /
    signed digest — pinning them is a tracked follow-up needing release.yml to
    publish them); litestream is a third-party image (ADR-0007). Encoding the
    reason here guards against an accidental pin or an accidental drop.
    """
    overlay_services = set(_services(_DIGEST_OVERLAY))
    assert overlay_services == set(_CORE_SERVICES), (
        "FR77: digest overlay must pin EXACTLY the 6 core services "
        f"{sorted(_CORE_SERVICES)}; got {sorted(overlay_services)}"
    )
    for svc in _INTENTIONALLY_UNPINNED:
        assert svc not in overlay_services, (
            f"Option A: {svc!r} must NOT be in the digest overlay — it has no "
            "release-published/signed digest (metrics-subscriber/migrator) or is "
            "a third-party upstream image (litestream). Pinning is a tracked "
            "follow-up requiring release.yml to publish it first."
        )


def test_orchestrator_adapter_swap_indirection_preserved() -> None:
    """Story 2.15 / FR35: overlay keeps the ``${ORCHESTRATOR_IMAGE:-...}`` wrap.

    The orchestrator pass-through swap (operators / the S-3 separability test
    replace the adapter image via one env var) must survive digest pinning: only
    the DEFAULT (ORCHESTRATOR_IMAGE unset) path is digest-pinned.
    """
    image = _services(_DIGEST_OVERLAY)["orchestrator-adapter"]["image"]
    assert image.startswith("${ORCHESTRATOR_IMAGE:-"), (
        "FR35: overlay orchestrator-adapter must keep the outer "
        f"'${{ORCHESTRATOR_IMAGE:-...}}' swap wrap; got {image!r}"
    )
    # The default branch (inside the wrap) must itself be digest-pinned.
    assert "@${OMB_IMAGE_DIGEST_orchestrator_adapter:?" in image, (
        "FR77: the default branch of the orchestrator-adapter swap must be "
        f"digest-pinned; got {image!r}"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_available(),
    reason="optional docker-gated config check requires Docker + compose v2 plugin",
)
def test_docker_compose_config_enforces_digest_pinning() -> None:
    """FR77 (optional, docker-gated): real resolver enforces fail-loud + digest.

    Confirms with the real compose resolver that, with all core digests UNSET,
    ``docker compose -f base -f overlay config`` FAILS (the ``:?`` fail-loud);
    and with dummy valid digests SET, every core image resolves to an
    ``@sha256:...`` ref. Hermetic siblings above assert the static contract;
    this is belt-and-suspenders against compose parsing the YAML differently.
    """
    base_args = [
        "docker",
        "compose",
        "-f",
        str(_BASE_COMPOSE),
        "-f",
        str(_DIGEST_OVERLAY),
    ]
    # UNSET → fail loud. Use --env-file /dev/null so a populated repo .env does
    # not satisfy the ``:?`` vars and mask the failure.
    unset = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(_BASE_COMPOSE),
            "-f",
            str(_DIGEST_OVERLAY),
            "config",
        ],
        capture_output=True,
        timeout=60.0,
        check=False,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert unset.returncode != 0, (
        "FR77: `docker compose config` with digests UNSET must FAIL LOUD "
        f"(:? form); exit={unset.returncode}, stderr={unset.stderr!r}"
    )

    # SET dummy valid digests → every core image ends in @sha256:...
    dummy = "sha256:" + "1" * 64
    env_overrides = {f"OMB_IMAGE_DIGEST_{_svc_underscored(svc)}": dummy for svc in _CORE_SERVICES}
    import os

    proc = subprocess.run(
        [*base_args, "config"],
        capture_output=True,
        timeout=60.0,
        check=False,
        text=True,
        cwd=str(_REPO_ROOT),
        env={**os.environ, **env_overrides},
    )
    assert proc.returncode == 0, (
        f"FR77: `docker compose config` with dummy digests set must succeed; stderr={proc.stderr!r}"
    )
    for svc in _CORE_SERVICES:
        # The resolved config lists each core image; assert the digest form.
        assert re.search(rf"oh-my-bmad-{re.escape(svc)}@sha256:", proc.stdout), (
            f"FR77: resolved config for {svc} must be a @sha256 digest ref"
        )
