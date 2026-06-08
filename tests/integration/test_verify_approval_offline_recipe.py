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
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
from events import FROZEN_EPOCH, FrozenClock
from events.approval_signing import compute_approval_hmac
from events.ids import new_event_id
from pydantic import SecretStr

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


def test_just_verify_approval_succeeds_against_fresh_signed_approval(tmp_path: Path) -> None:
    """AC1 + AC6 Test 1: write a freshly-signed event, invoke CLI, assert exit 0 + match.

    Story 11.4 PP16: renamed from test_verify_approval_cli_returns_match_on_valid_signature
    to spec-verbatim name (anchors to /approvals recipe terminology).
    """
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


def test_just_verify_approval_detects_corrupted_hmac(tmp_path: Path) -> None:
    """AC1 + AC6 Test 2: mutate hmac_sha256, assert exit 1 + mismatch + investigation steps.

    Story 11.4 PP16: renamed from test_verify_approval_cli_returns_mismatch_on_corrupted_hmac
    to spec-verbatim name.
    """
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


def test_verify_approval_emits_payload_invalid_on_malformed_timestamp(
    tmp_path: Path,
) -> None:
    """AC5 + PP13: malformed timestamp surfaces as payload_invalid (exit 2),
    NOT internal_error (exit 5).

    Before PP13, ValueError from datetime.fromisoformat() in _verify() was
    swallowed by the broad except-Exception block in main() and collapsed
    to internal_error. PP13 narrowed the scope so distinct failure modes
    (unparseable field vs true bug) produce distinct reason codes.
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
    assert data["reason"] == "payload_invalid"
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Story 11.4 PP3 — verifier MUST NOT transitively import FastAPI/SQLAlchemy/etc.
# ---------------------------------------------------------------------------


def test_verify_approval_does_not_import_fastapi_or_sqlalchemy(tmp_path: Path) -> None:
    """Story 11.4 PP3: verifier is "offline" — no service-layer imports.

    Loads the verifier module in a clean subprocess and introspects
    ``sys.modules`` for forbidden packages. Pre-PP3, the
    ``from registry_api.adapters.approval_signing import …`` transitively
    triggered ``registry_api/__init__.py``'s ``build_app`` import which
    pulled in FastAPI, SQLAlchemy, Anthropic, and the registry-state SQL
    stack. After PP3, the import is ``from events.approval_signing
    import …`` and the verifier process should not touch any service-layer
    code.
    """
    probe = (
        "import sys; "
        f"exec(open({str(_SCRIPT)!r}).read().replace("
        '"if __name__ ==", "if False and __name__ =="));'
        "leaked = sorted(m for m in sys.modules if any("
        "x in m for x in ('fastapi','sqlalchemy','anthropic','httpx',"
        "'registry_api','registry_state')));"
        "print('LEAKED:' + ','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    assert "LEAKED:" in result.stdout
    leaked_line = next(line for line in result.stdout.splitlines() if line.startswith("LEAKED:"))
    leaked = leaked_line[len("LEAKED:") :]
    assert leaked == "", f"PP3: verifier transitively imported forbidden modules: {leaked}"


# ---------------------------------------------------------------------------
# Story 11.4 PP2 — ms-precision truncation in compute_approval_hmac
# ---------------------------------------------------------------------------


def test_compute_approval_hmac_truncates_microseconds_to_ms_precision() -> None:
    """Story 11.4 PP2: sign-time canonical MUST match storage canonical
    (events.canonical._datetime_to_iso_z truncates microseconds to ms + Z).

    Without truncation, ANY real production event with non-zero sub-ms µs
    fails offline verification. Pre-PP2 tests passed only because all
    fixtures used microsecond=0.

    Two timestamps that differ only in their sub-ms microseconds MUST
    produce the same HMAC (because both serialize to the same ms-truncated
    ISO string on disk).
    """
    key = SecretStr(_TEST_KEY_STR)
    ts_with_us = datetime(2026, 5, 21, 12, 0, 0, 789123, tzinfo=UTC)
    ts_ms_only = datetime(2026, 5, 21, 12, 0, 0, 789000, tzinfo=UTC)

    hmac_us = compute_approval_hmac(
        key=key,
        task_id="t-1",
        action="approve",
        timestamp=ts_with_us,
        actor_id="a-1",
    )
    hmac_ms = compute_approval_hmac(
        key=key,
        task_id="t-1",
        action="approve",
        timestamp=ts_ms_only,
        actor_id="a-1",
    )
    assert hmac_us == hmac_ms, "PP2: sub-ms microseconds must be truncated before hashing"


def test_verify_approval_handles_non_zero_microseconds_end_to_end(tmp_path: Path) -> None:
    """PP2 + PP6 (Edge F4): sign event with non-zero sub-ms µs, write through
    EventLogWriter (which canonicalises to ms-truncated + Z suffix), read back
    via the CLI verifier, assert match.

    This is the regression test that pre-PP2 would have failed: the stored
    ``timestamp`` field is ``"…789Z"`` (ms-truncated by canonical serializer)
    but the HMAC was previously signed against ``…789123+00:00`` so the
    verifier's recomputation diverged.
    """
    import asyncio

    from events import EventEnvelope
    from events.clock import FrozenClock
    from events.envelope import Actor
    from events.event_log_writer import EventLogWriter
    from events.ids import new_event_id
    from events.payloads import TaskApprovalSignedPayload
    from registry_state.adapters.event_log import current_day_path
    from registry_state.domain.event_types import ensure_registered

    ensure_registered()

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Non-zero sub-ms microseconds — would expose the PP2 bug pre-fix.
    ts = datetime(2026, 5, 21, 12, 0, 0, 789123, tzinfo=UTC)
    clock = FrozenClock(mono_ns=0, now=ts)
    key_str = _TEST_KEY_STR

    hmac_val = compute_approval_hmac(
        key=SecretStr(key_str),
        task_id=_TASK_ID,
        action="approve",
        timestamp=ts,
        actor_id=_ACTOR_ID,
    )
    payload = TaskApprovalSignedPayload(
        task_id=_TASK_ID,
        decision_id=_DECISION_ID,
        actor_id=_ACTOR_ID,
        action="approve",
        timestamp=ts,
        hmac_sha256=hmac_val,
    )
    event_id = new_event_id(clock=clock)
    from events.ids import new_request_id

    request_id = new_request_id(clock=clock)
    envelope = EventEnvelope(
        event_id=event_id,
        schema_version="1.0.0",
        type="task.approval_signed",
        emitted_at=ts,
        emitted_at_monotonic_ns=0,
        actor=Actor(kind="operator", id=_ACTOR_ID),
        payload=payload.model_dump(),
        trace_id=request_id,
        request_id=request_id,
    )

    async def _write() -> None:
        writer = EventLogWriter(base_dir=log_dir, clock=clock)
        await writer.append(envelope)
        await writer.close()

    asyncio.run(_write())

    # Confirm the writer used canonical (ms-truncated Z) form on disk.
    written_path = current_day_path(log_dir, ts)
    raw = written_path.read_text(encoding="utf-8").strip()
    assert ".789Z" in raw, f"canonical serializer did not produce .789Z form: {raw[:200]}"

    result = _run_cli(event_id, "--log-dir", str(log_dir), "--json")
    assert result.returncode == 0, (
        f"PP2 regression: end-to-end verify failed. stderr={result.stderr}\nstdout={result.stdout}"
    )
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert data["reason"] == "signature_match"


# ---------------------------------------------------------------------------
# Story 11.4 PP1 — verifier reads payload.action (not hardcoded "approve")
# ---------------------------------------------------------------------------


def test_verify_approval_uses_payload_action_field(tmp_path: Path) -> None:
    """PP1: sign with a non-"approve" action; verifier MUST re-compute HMAC
    against the payload's action field (not literal "approve").

    Before PP1, the verifier hardcoded action="approve" in its recomputation,
    so any signed envelope with action != "approve" would falsely fail
    verification (or worse, falsely pass when an attacker had mutated the
    action field while preserving the original HMAC). PP1 widened
    compute_approval_hmac's action parameter to ``str`` and the verifier
    now reads ``payload["action"]``.

    We use the test-only envelope shape (raw dict written to JSONL) so we
    can sign with action="reject" — the payload's Pydantic
    Literal["approve"] constraint is producer-side only.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    reject_hmac = compute_approval_hmac(
        key=SecretStr(_TEST_KEY_STR),
        task_id=_TASK_ID,
        action="reject",
        timestamp=ts,
        actor_id=_ACTOR_ID,
    )
    envelope: dict[str, object] = {
        "event_id": _EVENT_ID,
        "type": "task.approval_signed",
        "payload": {
            "task_id": _TASK_ID,
            "decision_id": _DECISION_ID,
            "actor_id": _ACTOR_ID,
            "action": "reject",
            "timestamp": ts.isoformat(),
            "hmac_sha256": reject_hmac,
        },
    }
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"


def test_verify_approval_detects_action_mutation(tmp_path: Path) -> None:
    """PP1: sign with action="approve"; mutate the stored envelope's
    payload.action to "reject" (keep the original HMAC); verifier MUST
    return signature_mismatch.

    Pre-PP1 (with hardcoded action="approve"), this attack would silently
    pass — the verifier's recomputation used "approve" regardless of what
    the stored payload said, so the HMAC matched and the mutated action
    was accepted.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    real_approve_hmac = compute_approval_hmac(
        key=SecretStr(_TEST_KEY_STR),
        task_id=_TASK_ID,
        action="approve",
        timestamp=ts,
        actor_id=_ACTOR_ID,
    )
    envelope: dict[str, object] = {
        "event_id": _EVENT_ID,
        "type": "task.approval_signed",
        "payload": {
            "task_id": _TASK_ID,
            "decision_id": _DECISION_ID,
            "actor_id": _ACTOR_ID,
            "action": "reject",  # mutated from "approve" — HMAC stays the same
            "timestamp": ts.isoformat(),
            "hmac_sha256": real_approve_hmac,
        },
    }
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
    assert result.returncode == 1, f"PP1: action mutation must mismatch: {result.stdout}"
    data = json.loads(result.stdout)
    assert data["status"] == "mismatch"
    assert data["reason"] == "signature_mismatch"


# ---------------------------------------------------------------------------
# Story 11.4 PP4 — --key-file with non-UTF-8 bytes returns key_file_unreadable
# ---------------------------------------------------------------------------


def test_load_key_returns_key_file_unreadable_on_non_utf8_bytes(tmp_path: Path) -> None:
    """PP4: binary key files (non-UTF-8 bytes, e.g. `openssl rand 32`) MUST
    surface as key_file_unreadable (exit 3) — not internal_error (exit 5).
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])

    key_path = tmp_path / "binary.key"
    # 32+ bytes of non-UTF-8 sequences (high bytes that don't form valid UTF-8 starts).
    key_path.write_bytes(b"\xff\xfe\xfd\xfc" * 10)

    env: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            _EVENT_ID,
            "--log-dir",
            str(log_dir),
            "--key-file",
            str(key_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 3, f"PP4: expected exit 3, got {result.returncode}"
    data = json.loads(result.stdout)
    assert data["reason"] == "key_file_unreadable"


# ---------------------------------------------------------------------------
# Story 11.4 PP5 — pipe-in-canonical-string surfaces structured reason
# ---------------------------------------------------------------------------


def test_verify_approval_detects_pipe_in_actor_id_as_canonical_violation(
    tmp_path: Path,
) -> None:
    """PP5: payload.actor_id containing '|' MUST return
    payload_canonical_violation (exit 2), not internal_error (exit 5).

    Story 11.1 P1-H1 guard raises ValueError inside compute_approval_hmac;
    pre-PP5 the verifier caught it as internal_error and a partial canonical
    string could leak in the traceback. PP5 pre-validates and returns the
    structured reason.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    envelope: dict[str, object] = {
        "event_id": _EVENT_ID,
        "type": "task.approval_signed",
        "payload": {
            "task_id": _TASK_ID,
            "decision_id": _DECISION_ID,
            "actor_id": "org|alice",  # pipe injection — P1-H1 guard
            "action": "approve",
            "timestamp": datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC).isoformat(),
            "hmac_sha256": "a" * 64,
        },
    }
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), "--json")
    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["reason"] == "payload_canonical_violation"


# ---------------------------------------------------------------------------
# Story 11.4 PP7 — --key-file strips trailing newline
# ---------------------------------------------------------------------------


def test_load_key_strips_trailing_newline_from_key_file(tmp_path: Path) -> None:
    """PP7: `echo "$KEY" > key.bin` adds '\\n'; verifier must strip trailing
    whitespace/newlines so the file-key matches the env-var key (which has
    no newline).
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])

    key_path = tmp_path / "key.txt"
    # Use the SAME key string as _make_envelope (default _TEST_KEY_STR), but
    # write it with a trailing newline as `echo` would.
    key_path.write_text(_TEST_KEY_STR + "\n", encoding="utf-8")

    env: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            _EVENT_ID,
            "--log-dir",
            str(log_dir),
            "--key-file",
            str(key_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"PP7: trailing-newline in key file caused failure: {result.stderr}"
    )
    data = json.loads(result.stdout)
    assert data["status"] == "match"


# ---------------------------------------------------------------------------
# Story 11.4 PP8 — passing a file (not directory) to --log-dir
# ---------------------------------------------------------------------------


def test_verify_approval_handles_log_dir_pointing_at_file(tmp_path: Path) -> None:
    """PP8: passing a file path to --log-dir MUST surface as log_dir_missing
    (exit 4), not internal_error (exit 5).
    """
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = _run_cli(_EVENT_ID, "--log-dir", str(file_path), "--json")
    assert result.returncode == 4
    data = json.loads(result.stdout)
    assert data["reason"] == "log_dir_missing"


# ---------------------------------------------------------------------------
# Story 11.4 PP10 — Justfile recipe shells through `just`
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("just"), reason="just CLI not installed")
def test_just_verify_approval_recipe_works_via_just_cli(tmp_path: Path) -> None:
    """PP10: AC7 recipe verified end-to-end via `just verify-approval ...`.

    Documents the positional-binding caveat: ``just verify-approval EVT --json``
    binds ``--json`` to the LOG_DIR positional. Operators must use either the
    full positional form ``just verify-approval EVT /path/to/dir --json`` OR
    invoke the script directly. This test uses the full positional form.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])

    repo_root = Path(__file__).resolve().parents[2]
    env: dict[str, str] = {**os.environ, "OPERATOR_HMAC_KEY": _TEST_KEY_STR}
    result = subprocess.run(
        ["just", "verify-approval", _EVENT_ID, str(log_dir), "--json"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
    )
    assert result.returncode == 0, (
        f"PP10: just recipe failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    # `just` echoes the command itself + script output on stdout.
    assert "match" in result.stdout or "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Story 11.4 PP11 — NFR-S10 key isolation across stdout AND stderr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    ["match", "mismatch", "key_too_short"],
)
def test_verify_approval_never_logs_key_value(tmp_path: Path, scenario: str) -> None:
    """PP11 / NFR-S10: the canary key value MUST NOT appear in stdout or
    stderr across match / mismatch / key_too_short paths.

    The verifier may log the key BYTE COUNT (operator-friendly) but never
    the key bytes themselves. This test guards against future regressions
    that echo the key (e.g. via traceback / debug log / error formatting).
    """
    canary = "CANARY-KEY-NEVER-LOG-THIS-VALUE-X"
    assert len(canary) == 33  # >= 32 bytes for match/mismatch scenarios

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    if scenario == "match":
        envelope = _make_envelope(key_str=canary)
        _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), key_str=canary)
    elif scenario == "mismatch":
        envelope = _make_envelope(key_str=canary, hmac_override="0" * 64)
        _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), key_str=canary)
    elif scenario == "key_too_short":
        _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])
        short_canary = canary[:5]  # < 32 bytes
        result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir), key_str=short_canary)
        canary = short_canary  # check the actual value passed in
    else:
        raise AssertionError(f"unknown scenario: {scenario}")

    assert canary not in result.stdout, f"NFR-S10 leak in stdout ({scenario}): {result.stdout!r}"
    assert canary not in result.stderr, f"NFR-S10 leak in stderr ({scenario}): {result.stderr!r}"


# ---------------------------------------------------------------------------
# Story 11.4 PP14 — bounded-memory test (large log directory)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_verify_approval_handles_large_log_directory(tmp_path: Path) -> None:
    """PP14: scan 100k synthetic envelopes completes in < 5s with target hit.

    Bounded-memory invariant: the streaming line-by-line reader must not
    accumulate per-line state. Relaxed from spec's < 1s wall-clock to < 5s
    to absorb CI host variance; marked slow so it's gated out of fast runs.
    """
    import time

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    target_id = "01HZX000000000000000099999"
    # Build target envelope (real HMAC so verifier returns match).
    target_envelope = _make_envelope(event_id=target_id)

    decoy_template: dict[str, object] = {
        "event_id": "OTHER-0000000000000000000000",
        "type": "task.created",
        "payload": {"value": "decoy"},
    }
    # Write 100k events across 10 files (10k each); target lands in the LAST file
    # at a deep offset to stress the scan path.
    events_per_file = 10_000
    total_files = 10
    for file_idx in range(total_files):
        date_str = f"2026-05-{file_idx + 1:02d}.jsonl"
        path = log_dir / date_str
        with path.open("w", encoding="utf-8") as f:
            for i in range(events_per_file):
                if file_idx == total_files - 1 and i == events_per_file // 2:
                    f.write(json.dumps(target_envelope) + "\n")
                else:
                    decoy = dict(decoy_template)
                    decoy["event_id"] = f"OTHER-{file_idx:04d}-{i:05d}"
                    f.write(json.dumps(decoy) + "\n")

    start = time.monotonic()
    result = _run_cli(target_id, "--log-dir", str(log_dir), "--json")
    elapsed = time.monotonic() - start

    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    assert elapsed < 5.0, f"PP14: scan took {elapsed:.2f}s (>5s wall clock)"


# ---------------------------------------------------------------------------
# Story 11.4 PP15 — first-file finds event + scan stops on first match
# ---------------------------------------------------------------------------


def test_verify_approval_finds_event_in_first_file(tmp_path: Path) -> None:
    """PP15: 100 events in a single file, target at index 0, scan completes
    quickly. Also asserts first-match-wins: a malformed JSON line placed
    AFTER the target must NOT emit a decode-error warning (scan stops on
    match).
    """
    import time

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    target_id = "01HZX000000000000000000111"
    target_envelope = _make_envelope(event_id=target_id)

    jsonl_path = log_dir / "2026-05-21.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        # Target FIRST.
        f.write(json.dumps(target_envelope) + "\n")
        # 99 valid decoys after the target.
        for i in range(99):
            f.write(
                json.dumps(
                    {
                        "event_id": f"OTHER-{i:03d}",
                        "type": "task.created",
                        "payload": {"value": f"item-{i}"},
                    }
                )
                + "\n"
            )
        # Malformed line LAST — must not be visited.
        f.write("{this is not json}\n")

    start = time.monotonic()
    result = _run_cli(target_id, "--log-dir", str(log_dir), "--json")
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "match"
    # Scan stopped at index 0; the malformed line at index 100 was not parsed.
    assert "skip malformed" not in result.stderr, (
        f"PP15: scan continued past target (decode warning emitted): {result.stderr}"
    )
    assert elapsed < 2.0, f"PP15: single-file scan took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Story 11.4 PP18 — ASCII glyphs (no Unicode crash on Windows cp1252)
# ---------------------------------------------------------------------------


def test_verify_approval_human_output_uses_ascii_markers(tmp_path: Path) -> None:
    """PP18: human output uses [PASS] / [FAIL] / -- (no ✓ ✗ — glyphs) so the
    text renders on Windows cp1252 console without UnicodeEncodeError.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(log_dir / "2026-05-21.jsonl", [_make_envelope()])

    result = _run_cli(_EVENT_ID, "--log-dir", str(log_dir))
    assert result.returncode == 0
    # ASCII markers present.
    assert "[PASS]" in result.stdout
    # Non-ASCII glyphs absent.
    assert "✓" not in result.stdout  # ✓
    assert "✗" not in result.stdout  # ✗
    assert "—" not in result.stdout  # —


# ---------------------------------------------------------------------------
# Story 11.4 PP20 — event_id comparison is case-insensitive
# ---------------------------------------------------------------------------


def test_verify_approval_event_id_match_is_case_insensitive(tmp_path: Path) -> None:
    """PP20: UUIDv7/ULID hex variants in different case must still match."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stored_id = "01abcdef0123456789abcdefABCDEF"
    envelope = _make_envelope(event_id=stored_id)
    _write_jsonl(log_dir / "2026-05-21.jsonl", [envelope])

    # Query with all-uppercase variant.
    result = _run_cli(stored_id.upper(), "--log-dir", str(log_dir), "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "match"


# ---------------------------------------------------------------------------
# Story 11.5 AC5 — pre-rotation verification: --key-file accepts archived keys
# ---------------------------------------------------------------------------


def test_just_verify_approval_against_pre_rotation_event_with_prior_key(
    tmp_path: Path,
) -> None:
    """Story 11.5 AC5: pre-rotation approvals verify against prior key only.

    Simulates a real rotation: sign approval A under key A, simulate a
    rotation, sign approval B under key B.

    Verifies three paths:

    * Key B's event verified with CURRENT key (key B) → match.
    * Key A's event verified with ``--key-file <key-A-file>`` → match.
    * Key A's event verified with CURRENT key (key B) → mismatch, and
      the investigation steps mention key.rotated events.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Two distinct 32-byte keys.
    key_a_str = "rotation-test-key-A-32-bytes-pad"
    key_b_str = "rotation-test-key-B-32-bytes-pad"
    assert len(key_a_str.encode("utf-8")) == 32
    assert len(key_b_str.encode("utf-8")) == 32

    # Sign event A under key A (decided 2026-04-21, pre-rotation).
    # Story 11.5 PP13 — use realistic ``e-<uuidv7>`` event_ids (was
    # fake ULID-shape ``01HZX...`` pre-PP13).
    ts_a = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    event_a_id = new_event_id(
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH), rng=Random(0xA)
    )
    env_a = _make_envelope(
        event_id=event_a_id,
        timestamp=ts_a,
        key_str=key_a_str,
    )
    # Sign event B under key B (decided 2026-05-21, post-rotation).
    ts_b = datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
    event_b_id = new_event_id(
        clock=FrozenClock(mono_ns=2_000_000, now=FROZEN_EPOCH), rng=Random(0xB)
    )
    env_b = _make_envelope(
        event_id=event_b_id,
        timestamp=ts_b,
        key_str=key_b_str,
    )
    _write_jsonl(log_dir / "2026-04-21.jsonl", [env_a])
    _write_jsonl(log_dir / "2026-05-21.jsonl", [env_b])

    # Write key A to a file so --key-file can pick it up.
    key_a_file = tmp_path / "key-A.txt"
    key_a_file.write_text(key_a_str, encoding="utf-8")

    # ---- 1. Event B with CURRENT key (B) → match.
    result = _run_cli(event_b_id, "--log-dir", str(log_dir), "--json", key_str=key_b_str)
    assert result.returncode == 0, f"event B + key B: stderr={result.stderr}"
    data = json.loads(result.stdout)
    assert data["status"] == "match"

    # ---- 2. Event A with --key-file pointing at key A → match.
    env_no_hmac: dict[str, str] = {k: v for k, v in os.environ.items() if k != "OPERATOR_HMAC_KEY"}
    result_kf = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            event_a_id,
            "--log-dir",
            str(log_dir),
            "--key-file",
            str(key_a_file),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env_no_hmac,
    )
    assert result_kf.returncode == 0, f"event A + --key-file key A: stderr={result_kf.stderr}"
    data_kf = json.loads(result_kf.stdout)
    assert data_kf["status"] == "match", (
        f"pre-rotation approval must verify against the prior key; got {data_kf}"
    )

    # ---- 3. Event A with CURRENT key (B) → mismatch + investigation
    #         steps mention key.rotated.
    result_mismatch = _run_cli(event_a_id, "--log-dir", str(log_dir), "--json", key_str=key_b_str)
    assert result_mismatch.returncode == 1
    data_mm = json.loads(result_mismatch.stdout)
    assert data_mm["status"] == "mismatch"
    assert data_mm["reason"] == "signature_mismatch"
    # AC5: at least one investigation step references key.rotated.
    joined_steps = "\n".join(data_mm["investigation_steps"])
    assert "key.rotated" in joined_steps, (
        "signature_mismatch investigation steps MUST mention key.rotated "
        f"(Story 11.5 AC5); got steps={data_mm['investigation_steps']}"
    )
