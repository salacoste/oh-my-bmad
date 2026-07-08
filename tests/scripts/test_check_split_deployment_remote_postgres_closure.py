"""Tests for Story 132.8 Epic 132 closure evidence gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_split_deployment_remote_postgres_closure.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_split_deployment_remote_postgres_closure", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_split_deployment_remote_postgres_closure"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    mod._copy_live_fixture(tmp_path)  # type: ignore[attr-defined]
    mod._write_clean_self_test_quality_gate_records(tmp_path)  # type: ignore[attr-defined]


def _validate(mod: object, root: Path, *, run_subordinate_gates: bool = False) -> list[object]:
    return mod.validate(root, run_subordinate_gates=run_subordinate_gates)  # type: ignore[attr-defined]


def _remove_omx_runtime(tmp_path: Path) -> None:
    shutil.rmtree(tmp_path / ".omx", ignore_errors=True)


def _load_contract(tmp_path: Path, mod: object) -> dict[str, Any]:
    raw: object = json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    assert isinstance(raw, dict)
    return cast("dict[str, Any]", raw)


def _write_contract(tmp_path: Path, mod: object, data: dict[str, Any]) -> None:
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")  # type: ignore[attr-defined]


def _messages(violations: list[object]) -> str:
    return "\n".join(v.message for v in violations)  # type: ignore[attr-defined]


def _add_completed_tracker_thread(
    tmp_path: Path,
    mod: object,
    thread_id: str,
    completed_at: str,
    output_preview: str | None = None,
) -> None:
    tracker_path = tmp_path / mod.SUBAGENT_TRACKING_PATH  # type: ignore[attr-defined]
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    tracker = (
        json.loads(tracker_path.read_text(encoding="utf-8"))
        if tracker_path.exists()
        else {"schemaVersion": 1, "sessions": {}}
    )
    sessions = tracker.setdefault("sessions", {})
    session = sessions.setdefault(
        "test-session",
        {"session_id": "test-session", "leader_thread_id": "leader-thread", "threads": {}},
    )
    threads = session.setdefault("threads", {})
    threads[thread_id] = {
        "thread_id": thread_id,
        "kind": "subagent",
        "completed_at": completed_at,
        "first_seen_at": completed_at,
        "last_seen_at": completed_at,
        "last_turn_id": f"{thread_id}-turn",
        "turn_count": 1,
    }
    tracker_path.write_text(json.dumps(tracker, indent=2) + "\n", encoding="utf-8")
    if output_preview is not None:
        log_dir = tmp_path / mod.SUBAGENT_TURN_LOG_DIR  # type: ignore[attr-defined]
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "turns-2026-07-08.jsonl"
        event = {
            "timestamp": completed_at,
            "type": "agent-turn-complete",
            "thread_id": thread_id,
            "turn_id": f"{thread_id}-turn",
            "input_preview": "synthetic validation fixture",
            "input_message_count": 1,
            "output_preview": output_preview,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean_or_only_blocked_by_stale_native_subagent_output() -> None:
    mod = _load_module()
    violations = _validate(mod, REPO_ROOT)  # type: ignore[attr-defined]
    if not violations:
        assert mod.main([]) == 0  # type: ignore[attr-defined]
        return
    assert violations
    assert all("stale failure-state language" in v.message for v in violations)  # type: ignore[attr-defined]


def test_clean_checkout_without_omx_uses_committed_native_subagent_provenance(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _remove_omx_runtime(tmp_path)

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]

    assert violations == []


def test_closure_validation_fails_when_subordinate_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    remote_postgres_contract = tmp_path / mod.REQUIRED_STORIES["132.2"]["contract"]  # type: ignore[attr-defined]
    remote_postgres_contract.write_text('{"story": "132.2", "broken": true}\n', encoding="utf-8")

    violations = _validate(mod, tmp_path, run_subordinate_gates=True)

    message = _messages(violations)
    assert "subordinate Story 132.2 checker failed" in message
    assert "scripts/check_remote_postgres_readiness.py" in message


def test_missing_ci_or_just_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    for rel in [mod.JUSTFILE_PATH, mod.CI_PATH]:  # type: ignore[attr-defined]
        target = tmp_path / rel
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                mod.CHECKER_COMMAND,  # type: ignore[attr-defined]
                "uv run python scripts/other.py",
            ),
            encoding="utf-8",
        )
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("missing required reference" in v.message for v in violations)


def test_live_activation_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["production_activation"] = "active"
    closure = data["epic_closure"]
    assert isinstance(closure, dict)
    closure["live_activation"] = True
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "production_activation" in message
    assert "live_activation" in message


def test_forbidden_production_surface_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["live_activation"] = True
    _write_contract(tmp_path, mod, data)

    surface_mutations = {
        mod.OPERATOR_RUNBOOK_PATH: "\nStory 132.8 provisioning_enabled: true\n",  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH: "\nStory 132.8 runtime_audit_emitter_enabled: true\n",  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH: "\nStory 132.8 live_load_generation: true\n",  # type: ignore[attr-defined]
        mod.BACKUP_RESTORE_PATH: "\nStory 132.8 production_restore: true\n",  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH: "\nStory 132.8 production_host_mutation: true\n",  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH: "\n132-8 production_migration: true\n",  # type: ignore[attr-defined]
    }
    for relpath, mutation in surface_mutations.items():
        target = tmp_path / relpath
        target.write_text(target.read_text(encoding="utf-8") + mutation, encoding="utf-8")

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "live activation" in message
    assert "provisioning" in message
    assert "runtime audit emitters" in message
    assert "live load" in message
    assert "live restore" in message
    assert "production host mutation" in message
    assert "production migration" in message


def test_secret_like_value_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    artifact = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + "\npassword=abcdefghijklmnopqrstuvwx123456\n",
        encoding="utf-8",
    )
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_missing_required_story_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    evidence = data["required_story_evidence"]
    assert isinstance(evidence, dict)
    evidence.pop("132.6")
    evidence["132.7"].pop("checker")
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "132.6" in message
    assert "132.7 checker" in message


def test_missing_required_readiness_domain_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    domains = data["required_readiness_domains"]
    assert isinstance(domains, dict)
    domains.pop("db_mtls_epic_133_composition")
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("db_mtls_epic_133_composition" in v.message for v in violations)


def test_missing_just_ci_self_test_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    commands = data["required_ci_gates"]["commands"]
    assert isinstance(commands, dict)
    commands["132.8"].pop("self_test_command")
    commands["132.4"]["ci_wired"] = False
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "132.8 self-test" in message
    assert "132.4 just/CI" in message


def test_epic_or_stories_not_done_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = status.read_text(encoding="utf-8")
    text = text.replace("epic-132: done", "epic-132: in-progress")
    text = text.replace("132-8-closure-evidence: done", "132-8-closure-evidence: review")
    status.write_text(text, encoding="utf-8")
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "epic-132: done" in message
    assert "132-8-closure-evidence: done" in message


def test_missing_fail_closed_statement_or_live_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    statements = data["required_fail_closed_statements"]
    assert isinstance(statements, list)
    statements.remove("no runtime audit emitter")
    data["summary"] = "Epic 132 closure is production-ready and activated for split deployment."
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "no runtime audit emitter" in message
    assert "overclaim" in message


def test_missing_quality_gates_code_review_or_ultraqa_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    gates.pop("code_review")
    gates.pop("ultraqa")
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "quality_gates.code_review missing" in message
    assert "quality_gates.ultraqa missing" in message


def test_pending_quality_gate_placeholder_does_not_satisfy_final_closure(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    gates["code_review"] = {"status": "pending_autopilot_gate", "source": "pending_autopilot_gate"}
    gates["ultraqa"] = {"status": "pending_autopilot_gate", "source": "pending_autopilot_gate"}
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "pending placeholder" in message


def test_fake_or_self_attested_passed_quality_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    for source in ["leader", "self_attested", "manual_summary"]:
        data = _load_contract(tmp_path, mod)
        gates = data["quality_gates"]
        assert isinstance(gates, dict)
        for gate_name in ["code_review", "ultraqa"]:
            gate = gates[gate_name]
            assert isinstance(gate, dict)
            gate["status"] = "passed"
            gate["source"] = source
            gate["source_type"] = source
            gate["source_reference"] = "artifact-only-summary"
            gate["reviewed_by_non_leader"] = False
        _write_contract(tmp_path, mod, data)
        violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
        message = _messages(violations)
        assert "self-attested" in message
        assert "non-leader" in message
        assert "source_reference" in message


def test_passed_quality_gate_without_external_reference_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    code_review = gates["code_review"]
    ultraqa = gates["ultraqa"]
    assert isinstance(code_review, dict)
    assert isinstance(ultraqa, dict)
    code_review["source_reference"] = "review-summary"
    ultraqa["source_reference"] = "qa-summary"
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("source_reference" in v.message for v in violations)


def test_passed_final_quality_gate_rejects_tool_record_source_type(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    fake_record_path = Path("docs/fake-quality-records.json")
    refs = {
        "code_review": ("tool:fake-code-review-record-123456", "code-reviewer"),
        "ultraqa": ("tool:fake-ultraqa-record-123456", "verifier"),
    }
    records: dict[str, dict[str, object]] = {}
    for gate_name, (ref, role) in refs.items():
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["status"] = "passed"
        gate["source_type"] = "tool_record"
        gate["source"] = "external_tool_record"
        gate["source_reference"] = ref
        gate["source_record_path"] = str(fake_record_path)
        gate["agent_role"] = role
        gate["reviewed_by_non_leader"] = True
        records[ref] = {
            "source_reference": ref,
            "source_type": "tool_record",
            "agent_role": role,
            "status": "completed",
            "recommendation": "APPROVE",
            "architectural_status": "CLEAR",
            "verdict": "PASS",
            "clean": True,
            "raw_completed": (
                "Fake clean tool record that previously looked sufficient without durable "
                "native subagent tracking or matching agent-turn-complete log evidence."
            ),
        }
    fake_path = tmp_path / fake_record_path
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    fake_path.write_text(json.dumps({"records": records}, indent=2) + "\n", encoding="utf-8")
    _write_contract(tmp_path, mod, data)

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "quality_gates.code_review.source_type must be native_subagent" in message
    assert "quality_gates.ultraqa.source_type must be native_subagent" in message


def test_pattern_shaped_fabricated_quality_gate_record_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    fake_thread_id = "019f9999-fake-7000-aaaa-fabricated001"
    fake_ref = f"subagent:{fake_thread_id}"
    for gate_name, role in [("code_review", "code-reviewer"), ("ultraqa", "verifier")]:
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["status"] = "passed"
        gate["source_type"] = "native_subagent"
        gate["source"] = "multi_agent_v1.spawn_agent"
        gate["source_reference"] = fake_ref
        gate["source_record_path"] = str(mod.QUALITY_GATE_RECORD_PATH)  # type: ignore[attr-defined]
        gate["agent_role"] = role
        gate["reviewed_by_non_leader"] = True
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    records["records"][fake_ref] = {
        "source_reference": fake_ref,
        "source_type": "native_subagent",
        "thread_id": fake_thread_id,
        "agent_role": "code-reviewer",
        "status": "completed",
        "completed_at": "2026-07-08T00:00:00.000Z",
        "recommendation": "APPROVE",
        "architectural_status": "CLEAR",
        "verdict": "PASS",
        "clean": True,
        "raw_completed": "fabricated pattern-shaped source record pretending to be an external subagent pass",
    }
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "source record is not durable" in message
    assert "completed subagent tracker evidence" in message


def test_plausible_subagent_record_without_tracker_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    fake_thread_id = "019faaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    fake_ref = f"subagent:{fake_thread_id}"
    completed_at = "2026-07-08T00:00:00.000Z"
    for gate_name, role in [("code_review", "code-reviewer"), ("ultraqa", "verifier")]:
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["status"] = "passed"
        gate["source_type"] = "native_subagent"
        gate["source"] = "multi_agent_v1.spawn_agent"
        gate["source_reference"] = fake_ref
        gate["source_record_path"] = str(mod.QUALITY_GATE_RECORD_PATH)  # type: ignore[attr-defined]
        gate["agent_role"] = role
        gate["reviewed_by_non_leader"] = True
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    records["records"][fake_ref] = {
        "source_reference": fake_ref,
        "source_type": "native_subagent",
        "thread_id": fake_thread_id,
        "agent_role": "code-reviewer",
        "status": "completed",
        "completed_at": completed_at,
        "recommendation": "APPROVE",
        "architectural_status": "CLEAR",
        "verdict": "PASS",
        "clean": True,
        "raw_completed": "Plausible-looking non-leader source record with enough text to look like a completed external subagent gate.",
    }
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _write_contract(tmp_path, mod, data)

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("completed subagent tracker evidence" in v.message for v in violations)


def test_native_subagent_source_record_requires_matching_tracker_completion(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    thread_id = "019fabcd-0000-7000-8000-000000000001"
    ref = f"subagent:{thread_id}"
    completed_at = "2026-07-08T01:02:03.000Z"
    for gate_name, role in [("code_review", "code-reviewer"), ("ultraqa", "verifier")]:
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["status"] = "passed"
        gate["source_type"] = "native_subagent"
        gate["source"] = "multi_agent_v1.spawn_agent"
        gate["source_reference"] = ref
        gate["source_record_path"] = str(mod.QUALITY_GATE_RECORD_PATH)  # type: ignore[attr-defined]
        gate["agent_role"] = role
        gate["reviewed_by_non_leader"] = True
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    raw_completed = (
        "PASS\nRecommendation: APPROVE; Architectural status: CLEAR; "
        "A completed externally tracked subagent source record with enough text to validate "
        "the durable tracker-backed path."
    )
    records["records"][ref] = {
        "source_reference": ref,
        "source_type": "native_subagent",
        "thread_id": thread_id,
        "agent_role": "code-reviewer",
        "status": "completed",
        "completed_at": completed_at,
        "recommendation": "APPROVE",
        "architectural_status": "CLEAR",
        "verdict": "PASS",
        "clean": True,
        "raw_completed": raw_completed,
    }
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _add_completed_tracker_thread(tmp_path, mod, thread_id, completed_at, raw_completed)
    _write_contract(tmp_path, mod, data)

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "completed subagent tracker evidence" not in message
    assert "completed_at must match" not in message


def test_native_subagent_source_record_raw_completed_must_match_turn_log(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    code_review = gates["code_review"]
    assert isinstance(code_review, dict)
    source_ref = code_review["source_reference"]
    assert isinstance(source_ref, str)
    record = records["records"][source_ref]
    assert isinstance(record, dict)
    assert record["thread_id"]
    assert record["completed_at"]
    record["raw_completed"] = (
        "Invented clean review text with APPROVE and CLEAR that reuses the real "
        "thread_id and completed_at but was never emitted by the subagent."
    )
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any(
        "raw source record must match durable subagent completion output" in v.message
        for v in violations
    )


def test_passed_native_subagent_output_rejects_stale_failure_state_language(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _remove_omx_runtime(tmp_path)
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    provenance_path = tmp_path / mod.NATIVE_SUBAGENT_PROVENANCE_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    code_review = gates["code_review"]
    assert isinstance(code_review, dict)
    source_ref = code_review["source_reference"]
    assert isinstance(source_ref, str)
    stale_output = (
        "Recommendation: APPROVE; Architectural status: CLEAR; Evidence: Story 132.8 "
        "reviewed the closure checker, but current 132.8 closure checker fails only on "
        "missing durable source records until those records are written."
    )
    records["records"][source_ref]["raw_completed"] = stale_output
    provenance["records"][source_ref]["completion_event"]["output_preview"] = stale_output
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)

    assert (
        "quality_gates.code_review raw source record contains stale failure-state language"
        in message
    )
    assert (
        "quality_gates.code_review native subagent log output contains stale failure-state language"
        in message
    )


def test_native_subagent_source_record_rejects_real_preview_with_forged_tail(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    code_review = gates["code_review"]
    assert isinstance(code_review, dict)
    source_ref = code_review["source_reference"]
    assert isinstance(source_ref, str)
    record = records["records"][source_ref]
    assert isinstance(record, dict)
    real_preview = record["raw_completed"]
    assert isinstance(real_preview, str)
    record["raw_completed"] = (
        real_preview
        + "\nFabricated clean tail: Recommendation APPROVE, Architectural status CLEAR, no findings."
    )
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]

    assert any(
        "raw source record must match durable subagent completion output" in v.message
        for v in violations
    )


def test_committed_native_subagent_provenance_mismatch_fails_without_omx(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _remove_omx_runtime(tmp_path)
    provenance_path = tmp_path / mod.NATIVE_SUBAGENT_PROVENANCE_PATH  # type: ignore[attr-defined]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    code_review = gates["code_review"]
    assert isinstance(code_review, dict)
    source_ref = code_review["source_reference"]
    assert isinstance(source_ref, str)
    record = provenance["records"][source_ref]
    assert isinstance(record, dict)
    record["source_type"] = "tool_record"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]

    assert any("completed subagent tracker evidence" in v.message for v in violations)


def test_artifact_only_source_record_fails_without_external_evidence(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    ref = "tool:artifact-only-quality-summary"
    for gate_name, role in [("code_review", "code-reviewer"), ("ultraqa", "verifier")]:
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["status"] = "passed"
        gate["source_type"] = "tool_record"
        gate["source"] = "manual_summary"
        gate["source_reference"] = ref
        gate["source_record_path"] = str(mod.QUALITY_GATE_RECORD_PATH)  # type: ignore[attr-defined]
        gate["agent_role"] = role
        gate["reviewed_by_non_leader"] = True
    record_path = tmp_path / mod.QUALITY_GATE_RECORD_PATH  # type: ignore[attr-defined]
    records = json.loads(record_path.read_text(encoding="utf-8"))
    records["records"][ref] = {
        "source_reference": ref,
        "source_type": "tool_record",
        "agent_role": "code-reviewer",
        "status": "completed",
        "recommendation": "APPROVE",
        "architectural_status": "CLEAR",
        "verdict": "PASS",
        "clean": True,
        "raw_completed": "Artifact-only manual summary pretending to satisfy quality gates without a durable external stage, thread, or tool transcript.",
    }
    record_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _write_contract(tmp_path, mod, data)

    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    message = _messages(violations)
    assert "forbidden self-attested source" in message
    assert "implementation-artifact source record requires externally verifiable" in message


def test_quality_gate_source_record_must_not_be_contract_or_artifact(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    gates = data["quality_gates"]
    assert isinstance(gates, dict)
    for gate_name in ["code_review", "ultraqa"]:
        gate = gates[gate_name]
        assert isinstance(gate, dict)
        gate["source_record_path"] = str(mod.CONTRACT_PATH)  # type: ignore[attr-defined]
    _write_contract(tmp_path, mod, data)
    violations = _validate(mod, tmp_path)  # type: ignore[attr-defined]
    assert any("durable source record" in v.message for v in violations)
