"""Integration tests for ``scripts/emit_signature_rejected.py`` — Story 8.6.

Covers AC3, AC4, AC5, AC6:

* AC3 — CLI arg contract (all 6 required args mandatory)
* AC4 — envelope round-trips through ``EventEnvelope.from_canonical_json``
* AC5 — flock contention path returns exit 3 with the FR26 message
* AC6 — atomic durable append (fsync before lock release; daily-file naming)

All tests use ``tmp_path`` for filesystem isolation — none touch the system
event-log directory.
"""

from __future__ import annotations

import fcntl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from events.canonical import from_canonical_json
from events.envelope import EventEnvelope

_HELPER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "emit_signature_rejected.py"
_VALID_DIGEST = "sha256:" + ("a" * 64)
_DEFAULT_ARGS = [
    "--image", "ghcr.io/salacoste/oh-my-bmad-registry-api",
    "--digest", _VALID_DIGEST,
    "--attestation-type", "signature",
    "--error-message", "no matching signatures: cert subject doesn't match",
    "--omb-version", "v0.1.5-rc1",
    "--ghcr-owner", "salacoste",
]


def _invoke_helper(
    *, args: list[str], event_log_dir: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the helper script as a subprocess; return CompletedProcess."""
    cmd = [
        sys.executable,
        str(_HELPER_PATH),
        *args,
        "--event-log-dir",
        str(event_log_dir),
    ]
    env = None
    if env_extra is not None:
        import os

        env = {**os.environ, **env_extra}
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.mark.integration
class TestHappyPath:
    """AC3 + AC4 + AC6 — well-formed invocation produces a round-trippable envelope."""

    def test_helper_writes_well_formed_envelope(self, tmp_path: Path) -> None:
        result = _invoke_helper(args=_DEFAULT_ARGS, event_log_dir=tmp_path)
        assert result.returncode == 0, (
            f"helper exited {result.returncode}; stderr={result.stderr!r}"
        )

        # Exactly one daily-log file exists (AC6 path computation).
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        log_path = jsonl_files[0]

        # Filename matches today's UTC date (AC6).
        today_utc = datetime.now(timezone.utc).date().isoformat()
        assert log_path.name == f"{today_utc}.jsonl"

        # Stdout is the new event_id (AC3 contract).
        event_id = result.stdout.strip()
        assert event_id.startswith("e-")

        # Envelope round-trips (AC4).
        lines = log_path.read_bytes().splitlines()
        assert len(lines) == 1
        env = from_canonical_json(lines[0])
        assert env.event_id == event_id
        assert env.type == "deployment.signature_rejected"
        assert env.schema_version == "1.0.0"
        assert env.actor.kind == "operator"
        assert env.actor.id == "cli-helper"
        assert isinstance(env.payload, dict)
        assert env.payload["image"] == "ghcr.io/salacoste/oh-my-bmad-registry-api"
        assert env.payload["digest"] == _VALID_DIGEST
        assert env.payload["attestation_type"] == "signature"
        assert env.payload["omb_version"] == "v0.1.5-rc1"
        assert env.payload["ghcr_owner"] == "salacoste"
        # operator_id defaults to None on Phase 2 Epic 8 emissions.
        assert env.payload.get("operator_id") is None

    def test_three_attestation_types_all_accepted(self, tmp_path: Path) -> None:
        for attestation in ("signature", "slsaprovenance", "cyclonedx"):
            subdir = tmp_path / attestation
            args = list(_DEFAULT_ARGS)
            idx = args.index("--attestation-type")
            args[idx + 1] = attestation
            result = _invoke_helper(args=args, event_log_dir=subdir)
            assert result.returncode == 0, result.stderr
            jsonl_files = list(subdir.glob("*.jsonl"))
            assert len(jsonl_files) == 1
            env = from_canonical_json(jsonl_files[0].read_bytes().strip())
            assert isinstance(env.payload, dict)
            assert env.payload["attestation_type"] == attestation

    def test_operator_id_propagates_when_set(self, tmp_path: Path) -> None:
        args = [*_DEFAULT_ARGS, "--operator-id", "op-hmac-abc123"]
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 0
        jsonl = next(iter(tmp_path.glob("*.jsonl")))
        env = from_canonical_json(jsonl.read_bytes().strip())
        assert isinstance(env.payload, dict)
        assert env.payload["operator_id"] == "op-hmac-abc123"

    def test_operator_id_free_form_rejected(self, tmp_path: Path) -> None:
        """Code-review fix F13: operator_id must match ``^op-[a-zA-Z0-9-]+$``.

        Pre-Epic-11 (HMAC-keyed identity), this prevents free-form claims
        like ``--operator-id admin`` from polluting the audit trail.
        """
        args = [*_DEFAULT_ARGS, "--operator-id", "admin"]
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 1  # Pydantic path → exit 1
        assert "invalid payload field" in result.stderr.lower()

    def test_long_error_message_truncated_to_4096_chars(self, tmp_path: Path) -> None:
        long_error = "x" * 5000
        args = list(_DEFAULT_ARGS)
        idx = args.index("--error-message")
        args[idx + 1] = long_error
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        jsonl = next(iter(tmp_path.glob("*.jsonl")))
        env = from_canonical_json(jsonl.read_bytes().strip())
        assert isinstance(env.payload, dict)
        assert len(env.payload["error_message"]) == 4096

    def test_event_log_dir_from_env_var(self, tmp_path: Path) -> None:
        # Drop --event-log-dir to exercise the EVENT_LOG_DIR env-var path.
        # Code-review fix F9: inherit the parent's env (HOME, LANG, TMPDIR,
        # locale vars) so subprocesses behave like operator-shell-invocations
        # on any CI host (macOS SIP, locale-sensitive structlog, etc.).
        # Only override EVENT_LOG_DIR.
        import os as _os

        env = _os.environ.copy()
        env["EVENT_LOG_DIR"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, str(_HELPER_PATH), *_DEFAULT_ARGS],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert list(tmp_path.glob("*.jsonl"))


@pytest.mark.integration
class TestArgumentValidation:
    """AC3 — argparse rejects missing required args + invalid values."""

    def test_missing_required_arg_exits_2(self, tmp_path: Path) -> None:
        # Drop --image
        partial = [a for a in _DEFAULT_ARGS if a not in ("--image", "ghcr.io/salacoste/oh-my-bmad-registry-api")]
        result = _invoke_helper(args=partial, event_log_dir=tmp_path)
        assert result.returncode == 2
        assert "--image" in result.stderr

    def test_invalid_attestation_choice_exits_2(self, tmp_path: Path) -> None:
        args = [
            *_DEFAULT_ARGS,
        ]
        idx = args.index("--attestation-type")
        args[idx + 1] = "sbom"  # not in Literal
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 2
        assert "invalid choice" in result.stderr.lower()

    def test_malformed_digest_exits_1(self, tmp_path: Path) -> None:
        # Code-review fix F4: Pydantic validation failures now exit 1 (not 2);
        # exit 2 is reserved for argparse usage errors.
        args = list(_DEFAULT_ARGS)
        idx = args.index("--digest")
        args[idx + 1] = "abc123"  # missing sha256: prefix
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 1
        assert "invalid payload field" in result.stderr.lower()

    def test_malformed_image_exits_1(self, tmp_path: Path) -> None:
        args = list(_DEFAULT_ARGS)
        idx = args.index("--image")
        args[idx + 1] = "docker.io/salacoste/oh-my-bmad-registry-api"  # wrong registry
        result = _invoke_helper(args=args, event_log_dir=tmp_path)
        assert result.returncode == 1


@pytest.mark.integration
class TestLockContention:
    """AC5 — flock contention returns exit 3 + FR26 message; no partial write."""

    def test_lock_held_returns_exit_3(self, tmp_path: Path) -> None:
        # Pre-create today's daily file, then hold LOCK_EX on it.
        today_utc = datetime.now(timezone.utc).date().isoformat()
        log_path = tmp_path / f"{today_utc}.jsonl"
        log_path.touch()
        size_before = log_path.stat().st_size

        with open(log_path, "ab") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            try:
                result = _invoke_helper(args=_DEFAULT_ARGS, event_log_dir=tmp_path)
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)

        assert result.returncode == 3, (
            f"expected exit 3 on lock contention; got {result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        # FR26 single-writer message present (substring match).
        assert "FR26 single-writer" in result.stderr
        # No partial write — file size unchanged.
        size_after = log_path.stat().st_size
        assert size_after == size_before
