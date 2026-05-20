"""Integration tests for ``scripts/verify_approval.py`` — Story 11.4 / FR65.

Covers AC3, AC4, AC5, AC6 (offline HMAC verification recipe).

All tests use ``tmp_path`` for filesystem isolation — none spawn
registry-api / registry-state / Docker. Pure-Python + subprocess.

Golden vector (Story 11.1 D3 / DoD compatibility requirement):
  key:    'test-key-known-vector-must-be-32-chars-minimum-x'
  input:  'test-task-uuid-001|approve|2026-01-01T00:00:00+00:00|test-actor'
  result: 40a928fd23a98785a4beadcd450051b807f1eb4d77599ad369a7b54a4b79ef36
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from registry_api.adapters.approval_signing import compute_approval_hmac

# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_approval.py"

# 32-byte test key used by most tests (passed via OPERATOR_HMAC_KEY env var).
_TEST_KEY_STR = "test-key-32-bytes-padded-out-yes"
assert len(_TEST_KEY_STR.encode("utf-8")) == 32

# Golden-vector constants from Story 11.1 test_approval_signing.py P1-H3.
_GOLDEN_KEY_STR = "test-key-known-vector-must-be-32-chars-minimum-x"
_GOLDEN_TASK_ID = "test-task-uuid-001"
_GOLDEN_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_GOLDEN_ACTOR_ID = "test-actor"
_GOLDEN_HMAC = "40a928fd23a98785a4beadcd450051b807f1eb4d77599ad369a7b54a4b79ef36"

# Sample task_id that satisfies TaskApprovalSignedPayload regex (alphanumeric + _:.-).
_TASK_ID = "t-00000000-0000-7000-8000-000000000042"
_ACTOR_ID = "http-api"
_EVENT_ID = "01HZX000000000000000000001"
_DECISION_ID = "d-00000000-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(
    *,
    event_id: str = _EVENT_ID,
    event_type: str = "task.approval_signed",
    task_id: str = _TASK_ID,
    decision_id: str = _DECISION_ID,
    actor_id: str = _ACTOR_ID,
    timestamp: datetime | None = None,
    key_str: str = _TEST_KEY_STR,
    hmac_override: str | None = None,
) -> dict[str, object]:
    """Build a minimal envelope dict for writing to JSONL.

    Computes real HMAC via compute_approval_hmac (Story 11.1 D3 SSoT)
    unless hmac_override is supplied (for mismatch tests).
    """
    if timestamp is None:
        timestamp = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)

    hmac_val = hmac_override or compute_approval_hmac(
        key=SecretStr(key_str),
        task_id=task_id,
        action="approve",
        timestamp=timestamp,
        actor_id=actor_id,
    )

    return {
        "event_id": event_id,
        "schema_version": "1.0.0",
        "type": event_type,
        "emitted_at": timestamp.isoformat(),
        "emitted_at_monotonic_ns": 0,
        "actor": {"kind": "operator", "id": actor_id},
        "payload": {
            "task_id": task_id,
            "decision_id": decision_id,
            "actor_id": actor_id,
            "action": "approve",
            "timestamp": timestamp.isoformat(),
            "hmac_sha256": hmac_val,
        },
        "trace_id": "00000000-0000-7000-8000-000000000001",
        "request_id": "00000000-0000-7000-8000-000000000002",
        "parent_event_id": None,
        "extensions": {},
    }


def _write_jsonl(path: Path, envelopes: list[dict[str, object]]) -> None:
    """Write envelopes as JSONL (one JSON object per line)."""
    with path.open("w", encoding="utf-8") as f:
        for env in envelopes:
            f.write(json.dumps(env) + "\n")


def _run_cli(
    *args: str,
    env_extra: dict[str, str] | None = None,
    key_str: str = _TEST_KEY_STR,
) -> subprocess.CompletedProcess[str]:
    """Invoke verify_approval.py in a subprocess and capture stdout/stderr."""
    env: dict[str, str] = {**os.environ, "OPERATOR_HMAC_KEY": key_str}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# AC1 + AC6 — fresh approval verifies green
# ---------------------------------------------------------------------------


def test_verify_approval_cli_returns_match_on_valid_signature(tmp_path: Path) -> None:
    """AC1 + AC6 Test 1: write a freshly-signed event, invoke CLI, assert exit 0 + match."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["reason"] == "signature_match"
    assert data["event_id"] == _EVENT_ID
    assert data["task_id"] == _TASK_ID


def test_verify_approval_cli_returns_mismatch_on_corrupted_hmac(tmp_path: Path) -> None:
    """AC1 + AC6 Test 2: mutate hmac_sha256, assert exit 1 + mismatch + investigation steps."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Flip a hex char to corrupt the stored HMAC.
    real_hmac = compute_approval_hmac(
        key=SecretStr(_TEST_KEY_STR),
        task_id=_TASK_ID,
        action="approve",
        timestamp=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        actor_id=_ACTOR_ID,
    )
    corrupted = real_hmac[:-1] + ("0" if real_hmac[-1] != "0" else "1")
    envelope = _make_envelope(hmac_override=corrupted)
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 1, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "mismatch"
    assert data["reason"] == "signature_mismatch"
    assert len(data["investigation_steps"]) >= 3


# ---------------------------------------------------------------------------
# AC6 Test 3 — Epic 11 acceptance gate: 1-month-old approval verifies offline
# ---------------------------------------------------------------------------


def test_just_verify_approval_against_one_month_old_log(tmp_path: Path) -> None:
    """Epic 11 acceptance gate — simulated 1-month-old approval verifies offline.

    The JSONL file is dated 30 days in the past (2026-04-21.jsonl).
    No Platform stack running. Verifier must find + verify.
    """
    log_dir = tmp_path / "archive"
    log_dir.mkdir()
    old_ts = datetime(2026, 4, 21, 10, 30, 0, tzinfo=UTC)
    envelope = _make_envelope(
        event_id="01HZX000000000000000000099",
        timestamp=old_ts,
    )
    _write_jsonl(log_dir / "2026-04-21.jsonl", [envelope])

    result = _run_cli(
        "01HZX000000000000000000099",
        "--log-dir",
        str(log_dir),
        "--json",
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["reason"] == "signature_match"


# ---------------------------------------------------------------------------
# DoD — golden-vector compatibility with Story 11.1
# ---------------------------------------------------------------------------


def test_verify_approval_cli_matches_story_11_1_golden_vector(tmp_path: Path) -> None:
    """DoD: CLI re-computation byte-equals Story 11.1 golden vector 40a928fd...79ef36.

    Inputs match test_approval_signing.py::TestGoldenVector exactly.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope(
        event_id="01GOLDEN0000000000000000001",
        task_id=_GOLDEN_TASK_ID,
        actor_id=_GOLDEN_ACTOR_ID,
        timestamp=_GOLDEN_TIMESTAMP,
        key_str=_GOLDEN_KEY_STR,
        # hmac_override not set — let _make_envelope compute it
    )
    # Verify the HMAC in the envelope matches the golden vector
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["hmac_sha256"] == _GOLDEN_HMAC, (
        f"fixture HMAC mismatch — expected {_GOLDEN_HMAC}, got {payload['hmac_sha256']}"
    )
    _write_jsonl(log_dir / "2026-01-01.jsonl", [envelope])

    result = _run_cli(
        "01GOLDEN0000000000000000001",
        "--log-dir",
        str(log_dir),
        "--json",
        key_str=_GOLDEN_KEY_STR,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["stored_hmac"] == _GOLDEN_HMAC


# ---------------------------------------------------------------------------
# AC3 — JSONL reader edge cases
# ---------------------------------------------------------------------------


def test_verify_approval_finds_event_in_third_of_three_files(tmp_path: Path) -> None:
    """AC3: target event in the last of three sorted JSONL files."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Two decoy files with unrelated events.
    decoy1: dict[str, object] = {"event_id": "OTHER-001", "type": "task.created", "payload": {}}
    decoy2: dict[str, object] = {"event_id": "OTHER-002", "type": "task.created", "payload": {}}
    _write_jsonl(log_dir / "2026-05-19.jsonl", [decoy1])
    _write_jsonl(log_dir / "2026-05-20.jsonl", [decoy2])

    # Target in the third file.
    target_id = "01HZX000000000000000000003"
    envelope = _make_envelope(event_id=target_id)
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(target_id, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["event_id"] == target_id


def test_verify_approval_skips_blank_lines(tmp_path: Path) -> None:
    """AC3: blank lines in JSONL do not raise; scan continues to target."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target_id = "01HZX000000000000000000004"
    envelope = _make_envelope(event_id=target_id)
    jsonl_path = log_dir / "2026-05-21.jsonl"
    # Write with blank lines interspersed.
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write("\n")
        f.write(json.dumps({"event_id": "OTHER", "type": "task.created", "payload": {}}) + "\n")
        f.write("\n")
        f.write(json.dumps(envelope) + "\n")
        f.write("\n")

    result = _run_cli(target_id, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert json.loads(result.stdout)["status"] == "match"


def test_verify_approval_skips_decode_errors_with_warning(tmp_path: Path) -> None:
    """AC3: malformed JSON line emits stderr warning but scan continues."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target_id = "01HZX000000000000000000005"
    envelope = _make_envelope(event_id=target_id)
    jsonl_path = log_dir / "2026-05-21.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write('{"oops}\n')  # malformed
        f.write(json.dumps(envelope) + "\n")

    result = _run_cli(target_id, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0, f"unexpected failure: {result.stderr}"
    assert json.loads(result.stdout)["status"] == "match"
    # Warning must appear on stderr (not stdout).
    assert "WARNING" in result.stderr or "skip malformed" in result.stderr


# ---------------------------------------------------------------------------
# AC4 — Output format tests
# ---------------------------------------------------------------------------


def test_verify_approval_human_output_on_match(tmp_path: Path) -> None:
    """AC4: human text on match contains PASSED and signature hex."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))

    assert result.returncode == 0
    assert "PASSED" in result.stdout
    assert "(matches)" in result.stdout
    assert _TASK_ID in result.stdout


def test_verify_approval_human_output_on_mismatch_includes_next_steps(
    tmp_path: Path,
) -> None:
    """AC4: human text on mismatch includes FAILED + Investigation next steps."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope(hmac_override="a" * 64)
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))

    assert result.returncode == 1
    assert "FAILED" in result.stdout
    assert "Investigation next steps" in result.stdout
    assert "OPERATOR_HMAC_KEY" in result.stdout


def test_verify_approval_json_output_match(tmp_path: Path) -> None:
    """AC4: --json on match produces valid JSON with correct schema."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["reason"] == "signature_match"
    assert data["event_id"] == _EVENT_ID
    assert data["task_id"] == _TASK_ID
    assert data["event_type"] == "task.approval_signed"
    assert len(str(data["stored_hmac"])) == 64


def test_verify_approval_json_output_mismatch(tmp_path: Path) -> None:
    """AC4: --json on mismatch produces valid JSON with both HMACs."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope = _make_envelope(hmac_override="b" * 64)
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["status"] == "mismatch"
    assert data["reason"] == "signature_mismatch"
    assert data["stored_hmac"] == "b" * 64
    assert data["recomputed_hmac"] != "b" * 64
    assert len(data["investigation_steps"]) >= 3


# ---------------------------------------------------------------------------
# AC4 — Exit code parameterization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,expected_code",
    [
        ("match", 0),
        ("mismatch", 1),
        ("event_not_found", 2),
        ("event_type_mismatch", 2),
        ("key_missing", 3),
        ("log_dir_missing", 4),
    ],
)
def test_verify_approval_exits_with_correct_code(
    tmp_path: Path, scenario: str, expected_code: int
) -> None:
    """AC4: exit code is correct for each scenario."""
    log_dir = tmp_path / "logs"

    if scenario == "log_dir_missing":
        # Pass a non-existent directory.
        result = _run_cli(_EVENT_ID, "--log-dir", str(tmp_path / "nonexistent"))
        assert result.returncode == expected_code
        return

    log_dir.mkdir()

    if scenario == "match":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))
        assert result.returncode == expected_code

    elif scenario == "mismatch":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope(hmac_override="c" * 64)])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))
        assert result.returncode == expected_code

    elif scenario == "event_not_found":
        # Empty dir — event not found.
        result = _run_cli("NO-SUCH-EVENT", "--log-dir", str(log_dir))
        assert result.returncode == expected_code

    elif scenario == "event_type_mismatch":
        envelope = _make_envelope(event_type="task.created")
        _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))
        assert result.returncode == expected_code

    elif scenario == "key_missing":
        # Write a valid event, but suppress the OPERATOR_HMAC_KEY.
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        env: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), _EVENT_ID, "--log-dir", str(log_dir), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == expected_code


# ---------------------------------------------------------------------------
# AC5 — Reason code parameterization (all 11 codes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason_code",
    [
        "signature_match",
        "signature_mismatch",
        "event_not_found",
        "event_type_mismatch",
        "payload_missing_field",
        "key_missing",
        "key_too_short",
        "key_file_unreadable",
        "log_dir_missing",
        "log_dir_unreadable",
    ],
)
def test_verify_approval_emits_correct_reason_code(tmp_path: Path, reason_code: str) -> None:
    """AC5: each error path emits the documented reason code in --json output."""
    log_dir = tmp_path / "logs"

    if reason_code == "log_dir_missing":
        result = _run_cli(_EVENT_ID, "--log-dir", str(tmp_path / "gone"), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code
        return

    log_dir.mkdir()

    if reason_code == "signature_match":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "signature_mismatch":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope(hmac_override="d" * 64)])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "event_not_found":
        result = _run_cli("NO-SUCH-ID", "--log-dir", str(log_dir), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "event_type_mismatch":
        envelope = _make_envelope(event_type="approval.granted")
        _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "payload_missing_field":
        # Build envelope missing a required payload field.
        incomplete: dict[str, object] = {
            "event_id": _EVENT_ID,
            "type": "task.approval_signed",
            "payload": {"task_id": _TASK_ID},  # missing hmac_sha256 etc.
        }
        _write_jsonl(log_dir / "2026-05-21.jsonl", [incomplete])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "key_missing":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        env: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), _EVENT_ID, "--log-dir", str(log_dir), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "key_too_short":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        result = _run_cli(
            _EVENT_ID,
            "--log-dir",
            str(log_dir),
            "--json",
            key_str="short",  # < 32 bytes
        )
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "key_file_unreadable":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        env2: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                _EVENT_ID,
                "--log-dir",
                str(log_dir),
                "--key-file",
                "/nonexistent/path/key.txt",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env2,
        )
        data = json.loads(result.stdout)
        assert data["reason"] == reason_code

    elif reason_code == "log_dir_unreadable":
        # Make the directory unreadable.
        unreadable = tmp_path / "unreadable"
        unreadable.mkdir()
        unreadable.chmod(0o000)
        try:
            result = _run_cli(_EVENT_ID, "--log-dir", str(unreadable), "--json")
            data = json.loads(result.stdout)
            assert data["reason"] == reason_code
        finally:
            unreadable.chmod(0o755)


# ---------------------------------------------------------------------------
# AC5 — internal_error reason code (injected via bad payload type)
# ---------------------------------------------------------------------------


def test_verify_approval_emits_internal_error_on_unexpected_exception(
    tmp_path: Path,
) -> None:
    """AC5: internal_error reason code is reachable via unexpected exception path.

    We provoke it by writing a valid-looking envelope where timestamp is not
    ISO-parseable, which causes datetime.fromisoformat() to raise inside _verify().
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope: dict[str, object] = {
        "event_id": _EVENT_ID,
        "type": "task.approval_signed",
        "payload": {
            "task_id": _TASK_ID,
            "decision_id": _DECISION_ID,
            "actor_id": _ACTOR_ID,
            "action": "approve",
            "timestamp": "NOT-A-DATE",  # will cause fromisoformat to raise
            "hmac_sha256": "a" * 64,
        },
    }
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")

    data = json.loads(result.stdout)
    assert data["reason"] == "internal_error"
    assert result.returncode == 5
