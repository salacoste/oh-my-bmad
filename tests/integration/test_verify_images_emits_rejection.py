"""Integration test for the ``just verify-images`` → ``deployment.signature_rejected``
emission wiring (G2 traceability close, FR56a / NFR-S9).

**Why this exists (the gap it closes)**

``scripts/emit_signature_rejected.py`` is the spec'd single writer for the
``deployment.signature_rejected`` event (FR56a / NFR-S9), and it is unit-tested
in isolation by ``test_emit_signature_rejected.py``. But the *gate* —
``just verify-images`` — historically collected supply-chain verification
failures and ``exit 1`` WITHOUT ever invoking that helper, so the event was
never actually emitted by the gate. These tests assert the gate now best-effort
emits one ``deployment.signature_rejected`` per failed cosign-verify check,
while still always exiting 1 on failure (conservatism: emission must never
change the outcome).

**Hermetic strategy (no real registry / Sigstore / Docker)**

We never touch the repo's real ``.env`` or event-log directory. Instead each
test builds an isolated working directory containing:

* a fake ``cosign`` executable (a shell script that always ``exit 1``),
  prepended to ``PATH`` so every cosign check fails as a *verify* failure
  (which is emittable — distinct from the non-emittable "digest not set /
  invalid format" failures);
* a temp ``.env`` with the canonical ``OMB_GHCR_OWNER=salacoste`` +
  ``OMB_ACK_CUSTOM_OWNER=1`` (avoids the fork warning), a valid
  ``OMB_VERSION``, ``OMB_EVENT_LOG_DIR`` pointing at a temp log dir, and all
  eight ``OMB_IMAGE_DIGEST_<svc>`` set to a valid-format ``sha256:<64 hex>``;
* a ``scripts`` symlink back to the real repo so the helper is found.

The recipe is then run via ``just --justfile <repo>/justfile
--working-directory <tmp>`` so the recipe's cwd is the temp dir (its
``source .env`` + ``scripts/`` resolve to the temp copies), while
``UV_PROJECT=<repo>`` anchors the recipe's bare ``uv run`` to the real uv
workspace (the helper imports ``events`` from the workspace). This avoids
mutating the repo's real ``.env`` entirely.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JUSTFILE = _REPO_ROOT / "justfile"
_VALID_DIGEST = "sha256:" + ("a" * 64)

# Mirrors the SERVICES array in justfile:verify-images. Underscored to match the
# ``OMB_IMAGE_DIGEST_<svc>`` env-var naming (the recipe does ``${svc//-/_}``).
_SERVICE_ENV_KEYS = [
    "base",
    "registry_api",
    "registry_state",
    "telegram_gateway",
    "orchestrator_adapter",
    "worker_wrapper",
    "clawhip_daemon",
    "console_cli",
]

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _require_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("`just` not on PATH — required to exercise the verify-images recipe")


def _build_sandbox(
    tmp_path: Path, *, extra_env_lines: list[str] | None = None
) -> tuple[Path, Path]:
    """Build a hermetic working dir + event-log dir for the recipe.

    Returns ``(work_dir, event_log_dir)``. The work dir holds the temp ``.env``,
    a ``scripts`` symlink to the real repo, and a ``bin/cosign`` fake that always
    fails. Nothing in the real repo is modified.
    """
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    log_dir = tmp_path / "eventlog"
    log_dir.mkdir()
    bin_dir = work_dir / "bin"
    bin_dir.mkdir()

    # Fake cosign: always fails so each check is an emittable *verify* failure.
    fake_cosign = bin_dir / "cosign"
    fake_cosign.write_text(
        "#!/usr/bin/env bash\n"
        'echo "fake cosign failure: no matching signatures (hermetic test)" >&2\n'
        "exit 1\n"
    )
    fake_cosign.chmod(0o755)

    # Symlink scripts/ so the recipe's relative `scripts/emit_signature_rejected.py`
    # resolves while cwd is the temp work dir.
    (work_dir / "scripts").symlink_to(_REPO_ROOT / "scripts")

    env_lines = [
        "OMB_GHCR_OWNER=salacoste",
        "OMB_ACK_CUSTOM_OWNER=1",
        "OMB_VERSION=v0.1.5",  # must satisfy the payload model's ^v<semver> pattern
        f"OMB_EVENT_LOG_DIR={log_dir}",
    ]
    if extra_env_lines:
        env_lines.extend(extra_env_lines)
    env_lines.extend(f"OMB_IMAGE_DIGEST_{svc}={_VALID_DIGEST}" for svc in _SERVICE_ENV_KEYS)
    (work_dir / ".env").write_text("\n".join(env_lines) + "\n")

    return work_dir, log_dir


def _run_verify_images(work_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run ``just verify-images`` with cwd anchored to the hermetic work dir."""
    env = os.environ.copy()
    # Prepend the fake-cosign dir; anchor bare `uv run` to the real workspace.
    env["PATH"] = f"{work_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["UV_PROJECT"] = str(_REPO_ROOT)
    return subprocess.run(
        [
            "just",
            "--justfile",
            str(_JUSTFILE),
            "--working-directory",
            str(work_dir),
            "verify-images",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _read_events(log_dir: Path) -> list[dict]:
    events: list[dict] = []
    for jsonl in log_dir.glob("*.jsonl"):
        for line in jsonl.read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


@pytest.mark.integration
@pytest.mark.slow
def test_verify_images_emits_rejection_events_on_failure(tmp_path: Path) -> None:
    """FR56a / NFR-S9: a failed verify-images gate best-effort emits one
    ``deployment.signature_rejected`` event per failed cosign-verify check, and
    still exits 1.

    With a fake always-failing cosign and all 8 services having valid-format
    digests, every one of the 3 per-service cosign checks fails as a *verify*
    failure (the emittable kind), so we expect 8 services × 3 checks = 24
    events, balanced across the three attestation types.
    """
    _require_just()
    work_dir, log_dir = _build_sandbox(tmp_path)

    result = _run_verify_images(work_dir)

    # Outcome unchanged: verification failure always exits 1 (conservatism).
    assert result.returncode == 1, (
        f"expected exit 1 on verification failure; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    events = _read_events(log_dir)
    assert events, (
        "expected at least one deployment.signature_rejected event to be "
        f"emitted by the gate; got none.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    # Every emitted event is the rejection type (FR56a).
    assert all(ev["type"] == "deployment.signature_rejected" for ev in events), (
        f"unexpected event types: {sorted({ev['type'] for ev in events})}"
    )

    # One event per failed cosign-verify check: 8 services × 3 checks.
    assert len(events) == len(_SERVICE_ENV_KEYS) * 3, (
        f"expected {len(_SERVICE_ENV_KEYS) * 3} events (8 svc × 3 checks); got {len(events)}"
    )

    # All three attestation types represented (one per check kind per service).
    attestation_types = {ev["payload"]["attestation_type"] for ev in events}
    assert attestation_types == {"signature", "slsaprovenance", "cyclonedx"}, (
        f"expected all 3 attestation types; got {sorted(attestation_types)}"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_opt_out_env_skips_emission_but_still_exits_1(tmp_path: Path) -> None:
    """FR56a / NFR-S9 opt-out: ``OMB_SKIP_REJECTION_EVENT=1`` records NO event
    but the gate still exits 1 on verification failure (emission never changes
    the outcome).
    """
    _require_just()
    work_dir, log_dir = _build_sandbox(tmp_path, extra_env_lines=["OMB_SKIP_REJECTION_EVENT=1"])

    result = _run_verify_images(work_dir)

    assert result.returncode == 1, (
        f"expected exit 1 even with emission opted out; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
    assert _read_events(log_dir) == [], (
        "OMB_SKIP_REJECTION_EVENT=1 must skip emission entirely; found events in the log dir"
    )
    # A notice acknowledging the opt-out should be surfaced to the operator.
    assert "OMB_SKIP_REJECTION_EVENT=1" in result.stdout
