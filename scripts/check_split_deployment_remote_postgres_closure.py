#!/usr/bin/env python3
"""Validate Story 132.8 Epic 132 closure evidence.

This gate is intentionally static/readiness-only. It validates Epic 132 closure
as a completed readiness contract, not live split-deployment or remote Postgres
activation. Closure requires durable non-leader code-review and UltraQA evidence;
pending placeholders may document owed gates, but they never satisfy final
closure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/split-deployment-remote-postgres-closure-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
BACKUP_RESTORE_PATH = Path("docs/backup-restore.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path("_bmad-output/implementation-artifacts/132-8-closure-evidence.md")
QUALITY_GATE_RECORD_PATH = Path(
    "_bmad-output/implementation-artifacts/132-8-quality-gate-source-records.json"
)
NATIVE_SUBAGENT_PROVENANCE_PATH = Path(
    "_bmad-output/implementation-artifacts/132-8-native-subagent-provenance.json"
)
SUBAGENT_TRACKING_PATH = Path(".omx/state/subagent-tracking.json")
SUBAGENT_TURN_LOG_DIR = Path(".omx/logs")
IMPLEMENTATION_ARTIFACTS_DIR = Path("_bmad-output/implementation-artifacts")
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_split_deployment_remote_postgres_closure.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"
SUBORDINATE_GATE_TIMEOUT_SECONDS = 60

REQUIRED_FILES = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    BACKUP_RESTORE_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
    QUALITY_GATE_RECORD_PATH,
    NATIVE_SUBAGENT_PROVENANCE_PATH,
    JUSTFILE_PATH,
    CI_PATH,
)
SECRET_SCAN_PATHS = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    BACKUP_RESTORE_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
    QUALITY_GATE_RECORD_PATH,
    NATIVE_SUBAGENT_PROVENANCE_PATH,
)
REQUIRED_SECTIONS = frozenset(
    {
        "epic_closure",
        "required_story_evidence",
        "required_ci_gates",
        "required_readiness_domains",
        "unsupported_until_later_activation",
        "required_fail_closed_statements",
        "quality_gates",
        "readiness_checks",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_STORIES = {
    "132.1": {
        "artifact": "_bmad-output/implementation-artifacts/132-1-split-deployment-remote-postgres-topology-contract.md",
        "contract": "docs/split-deployment-topology-readiness.json",
        "checker": "scripts/check_split_deployment_topology.py",
        "test": "tests/scripts/test_check_split_deployment_topology.py",
    },
    "132.2": {
        "artifact": "_bmad-output/implementation-artifacts/132-2-remote-postgres-production-mode.md",
        "contract": "docs/remote-postgres-production-readiness.json",
        "checker": "scripts/check_remote_postgres_readiness.py",
        "test": "tests/scripts/test_check_remote_postgres_readiness.py",
    },
    "132.3": {
        "artifact": "_bmad-output/implementation-artifacts/132-3-registry-remote-postgres-deployment-profile.md",
        "contract": "docs/registry-remote-postgres-profile-readiness.json",
        "checker": "scripts/check_registry_remote_postgres_profile.py",
        "test": "tests/scripts/test_check_registry_remote_postgres_profile.py",
        "compose_overlay": "docker-compose.registry-remote-postgres.yml",
    },
    "132.4": {
        "artifact": "_bmad-output/implementation-artifacts/132-4-worker-mcp-event-bus-split-profile.md",
        "contract": "docs/worker-mcp-event-bus-split-readiness.json",
        "checker": "scripts/check_worker_mcp_event_bus_split.py",
        "test": "tests/scripts/test_check_worker_mcp_event_bus_split.py",
        "compose_overlay": "docker-compose.worker-mcp-event-bus-split.yml",
    },
    "132.5": {
        "artifact": "_bmad-output/implementation-artifacts/132-5-operator-dashboard-split-profile.md",
        "contract": "docs/operator-dashboard-split-readiness.json",
        "checker": "scripts/check_operator_dashboard_split.py",
        "test": "tests/scripts/test_check_operator_dashboard_split.py",
        "compose_overlay": "docker-compose.operator-dashboard-split.yml",
    },
    "132.6": {
        "artifact": "_bmad-output/implementation-artifacts/132-6-horizontal-scaling.md",
        "contract": "docs/horizontal-scaling-readiness.json",
        "checker": "scripts/check_horizontal_scaling_readiness.py",
        "test": "tests/scripts/test_check_horizontal_scaling_readiness.py",
    },
    "132.7": {
        "artifact": "_bmad-output/implementation-artifacts/132-7-failure-load-backup-restore-validation.md",
        "contract": "docs/failure-load-backup-restore-readiness.json",
        "checker": "scripts/check_failure_load_backup_restore_readiness.py",
        "test": "tests/scripts/test_check_failure_load_backup_restore_readiness.py",
    },
}
REQUIRED_GATE_COMMANDS = {
    "132.1": "uv run python scripts/check_split_deployment_topology.py",
    "132.2": "uv run python scripts/check_remote_postgres_readiness.py",
    "132.3": "uv run python scripts/check_registry_remote_postgres_profile.py",
    "132.4": "uv run python scripts/check_worker_mcp_event_bus_split.py",
    "132.5": "uv run python scripts/check_operator_dashboard_split.py",
    "132.6": "uv run python scripts/check_horizontal_scaling_readiness.py",
    "132.7": "uv run python scripts/check_failure_load_backup_restore_readiness.py",
    "132.8": CHECKER_COMMAND,
}
SUBORDINATE_GATE_STORIES = tuple(story for story in REQUIRED_GATE_COMMANDS if story != "132.8")
REQUIRED_READINESS_DOMAINS = frozenset(
    {
        "topology",
        "remote_postgres",
        "registry_profile",
        "worker_mcp_event_bus_profile",
        "operator_dashboard_profile",
        "horizontal_scaling",
        "failure_load_backup_restore_validation",
        "db_mtls_epic_133_composition",
    }
)
REQUIRED_UNSUPPORTED = frozenset(
    {
        "live activation",
        "provisioning",
        "credentials",
        "production migration",
        "production host mutation",
        "DB mTLS production activation",
        "load execution",
        "restore execution",
        "scaling activation",
        "runtime audit emitter",
    }
)
REQUIRED_FAIL_CLOSED = frozenset(
    {
        "no live activation",
        "no provisioning",
        "no credentials",
        "no production migration",
        "no production host mutation",
        "no DB mTLS production activation",
        "no load execution",
        "no restore execution",
        "no scaling activation",
        "no runtime audit emitter",
        "local single-host SQLite defaults preserved",
        "future activation requires a later approved activation story",
    }
)
DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#epic-132-closure-evidence-readiness-story-1328",
        f"{PRODUCTION_OPS_PATH}#story-1328-epic-132-closure-evidence-readiness",
        f"{BACKUP_RESTORE_PATH}#epic-132-closure-evidence-readiness-story-1328",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_SPRINT_TOKENS = (
    "epic-132: done",
    "readiness-contract-complete_not_live_activation",
    "132-6-horizontal-scaling: done",
    "132-7-failure-load-backup-restore-validation: done",
    "132-8-closure-evidence: done",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:split[- ]deployment|remote[- ]postgres|epic[- ]132|closure|production)\b"
        r"[^\n.]{0,180}\b(?:live|activated|enabled|provisioned|migrated|"
        r"production[- ]ready|shipped|deployed)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|enabled|provisioned|migrated|production[- ]ready|"
        r"shipped|deployed)\b[^\n.]{0,180}\b(?:split[- ]deployment|remote[- ]postgres|"
        r"epic[- ]132|closure|production)\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|"
    r"is\s+not|remains\s+deferred|future|readiness[- ]only|unsupported|forbidden|"
    r"not[- ]live[- ]activation)\b",
    re.I,
)
FORBIDDEN_SURFACE_PATTERNS = (
    (
        "live activation",
        (
            re.compile(
                r"(?i)\b(?:live_activation|production_activation|split_deployment_activation|"
                r"remote_postgres_activation)\b[\"']?\s*[:=]\s*(?:true|enabled|active|live)"
            ),
            re.compile(
                r"(?i)\b(?:activate|activated|enable|enabled)\s+(?:live\s+)?(?:split\s+deployment|remote\s+postgres|production)\b"
            ),
        ),
    ),
    (
        "production host mutation",
        (
            re.compile(r"(?i)\bproduction_host_mutation\b[\"']?\s*[:=]\s*true"),
            re.compile(r"(?i)\b(?:mutate|mutates|mutated|mutating)\s+production\s+hosts?\b"),
        ),
    ),
    (
        "provisioning",
        (
            re.compile(
                r"(?i)\b(?:provisioning|provisioning_enabled|host_provisioning|live_postgres_provisioning)\b[\"']?\s*[:=]\s*(?:true|enabled|active)"
            ),
            re.compile(
                r"(?i)\b(?:provision|provisioned|provisioning)\s+(?:hosts?|live\s+postgres|production)\b"
            ),
        ),
    ),
    (
        "production migration",
        (
            re.compile(
                r"(?i)\b(?:production_migration|migration_execution)\b[\"']?\s*[:=]\s*(?:true|enabled|active|ran|executed)"
            ),
            re.compile(r"(?i)\b(?:run|ran|execute|executed)\s+production\s+migrations?\b"),
        ),
    ),
    (
        "DB mTLS production activation",
        (
            re.compile(
                r"(?i)\b(?:db_mtls_production_activation|mtls_production_activation)\b[\"']?\s*[:=]\s*(?:true|enabled|active|live)"
            ),
            re.compile(
                r"(?i)\b(?:activate|activated|enable|enabled)\s+(?:DB\s+)?mTLS\s+in\s+production\b"
            ),
        ),
    ),
    (
        "live load",
        (
            re.compile(
                r"(?i)\b(?:live_load|live_load_generation|load_execution|load_generation)\b[\"']?\s*[:=]\s*(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|ran|execute|executed|generate|generated)\s+(?:live\s+|production\s+)?load\b"
            ),
        ),
    ),
    (
        "live restore",
        (
            re.compile(
                r"(?i)\b(?:live_restore|restore_execution|production_restore)\b[\"']?\s*[:=]\s*(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|ran|execute|executed)\s+(?:live\s+|production\s+)?(?:restore|backup/restore|backup\s+restore)\b"
            ),
        ),
    ),
    (
        "live scaling activation",
        (
            re.compile(
                r"(?i)\b(?:live_scaling_activation|horizontal_scaling_activation|scaling_activation)\b[\"']?\s*[:=]\s*(?:true|enabled|active|live)"
            ),
            re.compile(
                r"(?i)\b(?:activate|activated|enable|enabled)\s+(?:live\s+)?(?:horizontal\s+)?scaling\b"
            ),
        ),
    ),
    (
        "runtime audit emitters",
        (
            re.compile(
                r"(?i)\b(?:runtime_(?:production_)?audit_emitter(?:s)?_(?:enabled|live|activated|activation)|runtime_audit_emitters?)\b[\"']?\s*[:=]\s*(?:true|enabled|active|activated|live)"
            ),
        ),
    ),
)
PENDING_GATE_STATUSES = frozenset({"pending", "pending_autopilot_gate", "blocked", "not_run"})
FORBIDDEN_GATE_SOURCES = frozenset(
    {
        "leader",
        "self_attested",
        "self-attested",
        "manual_summary",
        "artifact_summary",
        "artifact-only",
        "artifact_only",
        "pending_autopilot_gate",
        "parent_summary",
    }
)
SOURCE_REFERENCE_PATTERN = re.compile(r"(?i)^subagent:[A-Za-z0-9_.:-]{6,}$")
SUBAGENT_REFERENCE_PATTERN = re.compile(r"(?i)^subagent:(?P<thread_id>[A-Za-z0-9_.:-]{6,})$")
STALE_FAILURE_STATE_PATTERNS = (
    (
        "current gate fails",
        re.compile(r"\bcurrent\b[^\n]{0,160}\bgate\s+fails\b", re.I),
    ),
    (
        "current checker fails",
        re.compile(r"\bcurrent\b[^\n]{0,160}\bchecker\s+fails\b", re.I),
    ),
    (
        "fails only on/while",
        re.compile(r"\bfails\s+only\s+(?:on|while|because|due\s+to)\b", re.I),
    ),
    ("expectedly red", re.compile(r"\bexpectedly\s+red\b", re.I)),
    ("blocked by absent records", re.compile(r"\bblocked\s+by\s+absent\s+records\b", re.I)),
    (
        "until those records are written",
        re.compile(r"\buntil\s+those\s+records\s+are\s+written\b", re.I),
    ),
)

SUBORDINATE_GATE_FIXTURE_PATHS = frozenset(
    {
        Path(".env.example"),
        Path("docker-compose.digest.yml"),
        Path("docker-compose.operator-dashboard-split.yml"),
        Path("docker-compose.registry-remote-postgres.yml"),
        Path("docker-compose.worker-mcp-event-bus-split.yml"),
        Path("docker-compose.yml"),
        Path("_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md"),
        Path("dashboard/static"),
        Path("packages/mtls/src/mtls/db.py"),
        Path("services/console-cli/src/console_cli"),
        Path("services/registry-api/src/registry_api/app.py"),
        Path("services/registry-state/src/registry_state/adapters/sqlite_store.py"),
        Path("services/registry-state/src/registry_state/migrations/env.py"),
        Path("services/telegram-gateway/src/telegram_gateway"),
    }
)
STALE_FAILURE_STATE_NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|reject(?:s|ed|ing)?|forbid(?:s|den)?|"
    r"must\s+not|mustn't|does\s+not|do\s+not|cannot|can't|should\s+not)\b",
    re.I,
)


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {self.message}"


def _read(root: Path, relpath: Path) -> str:
    return (root / relpath).read_text(encoding="utf-8")


def _just_recipe(text: str, recipe_name: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(recipe_name)}:\n(?P<body>(?:^[ \t].*\n?)*)")
    match = pattern.search(text)
    return match.group("body") if match else ""


def _load_json(root: Path, relpath: Path) -> dict[str, Any]:
    with (root / relpath).open(encoding="utf-8") as f:
        data: object = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must be a JSON object")
    return cast("dict[str, Any]", data)


def _get(data: Mapping[str, Any], path: Sequence[str]) -> object:
    current: object = data
    for key in path:
        current = current.get(key) if isinstance(current, Mapping) else None
    return current


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_strings(item)


def _as_string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {item for item in value if isinstance(item, str)}


def _validate_contract(root: Path) -> list[Violation]:
    try:
        data = _load_json(root, CONTRACT_PATH)
    except Exception as exc:
        return [Violation(str(CONTRACT_PATH), f"invalid JSON contract: {exc}")]

    violations: list[Violation] = []
    if missing := REQUIRED_SECTIONS - set(data):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required sections missing {sorted(missing)}")
        )

    expected = {
        ("epic",): "132",
        ("story",): "132.8",
        ("mode",): "readiness_contract_complete_not_live_activation",
        ("production_activation",): "deferred_fail_closed",
        ("epic_closure", "status"): "readiness_contract_complete_not_live_activation",
        ("epic_closure", "activation_state"): "deferred_fail_closed",
        ("epic_closure", "live_activation"): False,
        ("epic_closure", "future_activation_required"): True,
        ("readiness_checks", "checker_command"): CHECKER_COMMAND,
        ("readiness_checks", "self_test_command"): CHECKER_SELF_TEST_COMMAND,
    }
    for path, expected_value in expected.items():
        if (current := _get(data, path)) != expected_value:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must be {expected_value!r}, found {current!r}",
                )
            )

    _validate_story_evidence(root, data, violations)
    _validate_gate_wiring_contract(data, violations)
    _validate_domains(data, violations)
    _validate_fail_closed(data, violations)
    _validate_quality_gates(root, data, violations)

    if DOC_REFS - _as_string_set(data.get("docs_refs")):
        violations.append(Violation(str(CONTRACT_PATH), "docs_refs missing Story 132.8 refs"))
    if any(pattern.search(s) for pattern in SECRET_PATTERNS for s in _walk_strings(data)):
        violations.append(Violation(str(CONTRACT_PATH), "contract contains secret-like value"))
    return violations


def _validate_story_evidence(
    root: Path, data: Mapping[str, Any], violations: list[Violation]
) -> None:
    evidence = data.get("required_story_evidence")
    if not isinstance(evidence, Mapping):
        violations.append(
            Violation(str(CONTRACT_PATH), "required_story_evidence must be an object")
        )
        return
    for story, required in REQUIRED_STORIES.items():
        entry = evidence.get(story)
        if not isinstance(entry, Mapping):
            violations.append(
                Violation(str(CONTRACT_PATH), f"required_story_evidence missing {story}")
            )
            continue
        if entry.get("status") != "done":
            violations.append(
                Violation(str(CONTRACT_PATH), f"story {story} evidence status must be done")
            )
        for key, rel in required.items():
            if entry.get(key) != rel:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"story {story} {key} must reference {rel}")
                )
            if not (root / rel).exists():
                violations.append(
                    Violation(str(CONTRACT_PATH), f"story {story} {key} path missing {rel}")
                )
        refs = _as_string_set(entry.get("docs_refs"))
        if len(refs) < 2 or not any("docs/" in ref for ref in refs):
            violations.append(
                Violation(
                    str(CONTRACT_PATH), f"story {story} docs_refs require durable docs references"
                )
            )


def _validate_gate_wiring_contract(data: Mapping[str, Any], violations: list[Violation]) -> None:
    gates = data.get("required_ci_gates")
    if not isinstance(gates, Mapping):
        violations.append(Violation(str(CONTRACT_PATH), "required_ci_gates must be an object"))
        return
    commands = gates.get("commands")
    if not isinstance(commands, Mapping):
        violations.append(
            Violation(str(CONTRACT_PATH), "required_ci_gates.commands must be an object")
        )
        return
    for story, command in REQUIRED_GATE_COMMANDS.items():
        entry = commands.get(story)
        if not isinstance(entry, Mapping):
            violations.append(Violation(str(CONTRACT_PATH), f"required_ci_gates missing {story}"))
            continue
        if entry.get("checker_command") != command:
            violations.append(Violation(str(CONTRACT_PATH), f"{story} checker command missing"))
        if entry.get("self_test_command") != f"{command} --self-test":
            violations.append(Violation(str(CONTRACT_PATH), f"{story} self-test command missing"))
        if entry.get("just_wired") is not True or entry.get("ci_wired") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{story} just/CI wiring flags must be true")
            )


def _validate_domains(data: Mapping[str, Any], violations: list[Violation]) -> None:
    domains = data.get("required_readiness_domains")
    if not isinstance(domains, Mapping):
        violations.append(
            Violation(str(CONTRACT_PATH), "required_readiness_domains must be an object")
        )
        return
    if missing := REQUIRED_READINESS_DOMAINS - set(domains):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required_readiness_domains missing {sorted(missing)}")
        )
    for domain in REQUIRED_READINESS_DOMAINS & set(domains):
        entry = domains[domain]
        if not isinstance(entry, Mapping):
            violations.append(
                Violation(str(CONTRACT_PATH), f"readiness domain {domain} must be an object")
            )
            continue
        if entry.get("status") not in {"complete", "composed", "readiness_complete"}:
            violations.append(
                Violation(str(CONTRACT_PATH), f"readiness domain {domain} is not complete")
            )
        if (
            not isinstance(entry.get("evidence"), str)
            or len(cast("str", entry.get("evidence")).strip()) < 20
        ):
            violations.append(
                Violation(str(CONTRACT_PATH), f"readiness domain {domain} evidence required")
            )


def _validate_fail_closed(data: Mapping[str, Any], violations: list[Violation]) -> None:
    unsupported = _as_string_set(data.get("unsupported_until_later_activation"))
    if missing := REQUIRED_UNSUPPORTED - unsupported:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"unsupported_until_later_activation missing {sorted(missing)}"
            )
        )
    statements = _as_string_set(data.get("required_fail_closed_statements"))
    if missing := REQUIRED_FAIL_CLOSED - statements:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required_fail_closed_statements missing {sorted(missing)}"
            )
        )


def _load_quality_gate_record(root: Path, gate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    record_path = gate.get("source_record_path")
    if not isinstance(record_path, str):
        return None
    relpath = Path(record_path)
    if relpath in {CONTRACT_PATH, ARTIFACT_PATH}:
        return None
    try:
        data = _load_json(root, relpath)
    except Exception:
        return None
    ref = gate.get("source_reference")
    records = data.get("records")
    if not isinstance(ref, str) or not isinstance(records, Mapping):
        return None
    record = records.get(ref)
    return record if isinstance(record, Mapping) else None


def _is_implementation_artifact_record(gate: Mapping[str, Any]) -> bool:
    record_path = gate.get("source_record_path")
    if not isinstance(record_path, str):
        return False
    try:
        Path(record_path).relative_to(IMPLEMENTATION_ARTIFACTS_DIR)
    except ValueError:
        return False
    return True


def _load_subagent_tracker(root: Path) -> Mapping[str, Any] | None:
    try:
        return _load_json(root, SUBAGENT_TRACKING_PATH)
    except Exception:
        return None


def _agent_turn_completion_event(root: Path, thread: Mapping[str, Any]) -> Mapping[str, Any] | None:
    thread_id = thread.get("thread_id")
    turn_id = thread.get("last_turn_id")
    if not isinstance(thread_id, str) or not isinstance(turn_id, str):
        return None
    for log_path in sorted((root / SUBAGENT_TURN_LOG_DIR).glob("turns-*.jsonl")):
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            if (
                event.get("type") == "agent-turn-complete"
                and event.get("thread_id") == thread_id
                and event.get("turn_id") == turn_id
                and isinstance(event.get("timestamp"), str)
            ):
                return event
    return None


def _agent_turn_completed_at(root: Path, thread: Mapping[str, Any]) -> str | None:
    event = _agent_turn_completion_event(root, thread)
    if event is None:
        return None
    return cast("str", event["timestamp"])


def _completed_subagent_thread(
    root: Path, tracker: Mapping[str, Any] | None, thread_id: str
) -> Mapping[str, Any] | None:
    if tracker is None:
        return None
    sessions = tracker.get("sessions")
    if not isinstance(sessions, Mapping):
        return None
    for session in sessions.values():
        if not isinstance(session, Mapping):
            continue
        threads = session.get("threads")
        if not isinstance(threads, Mapping):
            continue
        thread = threads.get(thread_id)
        if not isinstance(thread, Mapping):
            continue
        if thread.get("thread_id") != thread_id or thread.get("kind") != "subagent":
            continue
        completed_at = thread.get("completed_at")
        if isinstance(completed_at, str) and completed_at.strip():
            return thread
        completed_at = _agent_turn_completed_at(root, thread)
        if completed_at is not None:
            return {**thread, "completed_at": completed_at}
    return None


def _load_committed_native_subagent_provenance(root: Path) -> Mapping[str, Any] | None:
    try:
        return _load_json(root, NATIVE_SUBAGENT_PROVENANCE_PATH)
    except Exception:
        return None


def _committed_subagent_completion_evidence(
    root: Path, thread_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    provenance = _load_committed_native_subagent_provenance(root)
    if provenance is None:
        return None
    records = provenance.get("records")
    if not isinstance(records, Mapping):
        return None
    ref = f"subagent:{thread_id}"
    record = records.get(ref)
    if not isinstance(record, Mapping):
        return None
    thread = record.get("tracker_thread")
    event = record.get("completion_event")
    if not isinstance(thread, Mapping) or not isinstance(event, Mapping):
        return None
    if record.get("source_reference") != ref or record.get("source_type") != "native_subagent":
        return None
    if record.get("thread_id") != thread_id or record.get("status") != "completed":
        return None
    if not isinstance(record.get("completed_at"), str) or not record["completed_at"].strip():
        return None
    if thread.get("thread_id") != thread_id or thread.get("kind") != "subagent":
        return None
    if not isinstance(thread.get("last_turn_id"), str) or not thread["last_turn_id"].strip():
        return None
    if (
        event.get("type") != "agent-turn-complete"
        or event.get("thread_id") != thread_id
        or event.get("turn_id") != thread.get("last_turn_id")
        or event.get("timestamp") != record.get("completed_at")
        or not isinstance(event.get("output_preview"), str)
    ):
        return None
    return ({**thread, "completed_at": record["completed_at"]}, event)


def _subagent_completion_evidence(
    root: Path, thread_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any] | None] | None:
    tracker = _load_subagent_tracker(root)
    tracker_thread = _completed_subagent_thread(root, tracker, thread_id)
    if tracker_thread is not None:
        return tracker_thread, _agent_turn_completion_event(root, tracker_thread)
    if not (root / SUBAGENT_TRACKING_PATH).exists() and not (root / SUBAGENT_TURN_LOG_DIR).exists():
        return _committed_subagent_completion_evidence(root, thread_id)
    return None


def _normalize_completed_output(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _raw_matches_logged_output(raw: str, logged_output: str) -> bool:
    return _normalize_completed_output(raw) == _normalize_completed_output(logged_output)


def _stale_failure_state_labels(text: str) -> set[str]:
    labels: set[str] = set()
    for label, pattern in STALE_FAILURE_STATE_PATTERNS:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - 96) : match.start()]
            if STALE_FAILURE_STATE_NEGATION_PATTERN.search(before) is not None:
                continue
            labels.add(label)
            break
    return labels


def _validate_no_stale_failure_state_output(
    gate_name: str,
    output_kind: str,
    output: str,
    violations: list[Violation],
) -> None:
    labels = sorted(_stale_failure_state_labels(output))
    if labels:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} {output_kind} contains stale failure-state "
                f"language: {', '.join(labels)}",
            )
        )


def _validate_native_subagent_log_output(
    gate_name: str,
    record: Mapping[str, Any],
    log_event: Mapping[str, Any] | None,
    completed_at: str,
    violations: list[Violation],
) -> None:
    if log_event is None:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} durable subagent completion output required",
            )
        )
        return
    if log_event.get("timestamp") != completed_at:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record completed_at must match subagent turn log",
            )
        )
    logged_output = log_event.get("output_preview")
    if not isinstance(logged_output, str) or len(logged_output.strip()) < 50:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} durable subagent completion output required",
            )
        )
        return
    _validate_no_stale_failure_state_output(
        gate_name, "native subagent log output", logged_output, violations
    )
    raw = record.get("raw_completed")
    if not isinstance(raw, str) or not _raw_matches_logged_output(raw, logged_output):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} raw source record must match durable subagent completion output",
            )
        )
    lowered_output = logged_output.lower()
    if gate_name == "code_review":
        if record.get("recommendation") == "APPROVE" and "approve" not in lowered_output:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    "quality_gates.code_review source record verdict is inconsistent with subagent log",
                )
            )
        if record.get("architectural_status") == "CLEAR" and "clear" not in lowered_output:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    "quality_gates.code_review source record verdict is inconsistent with subagent log",
                )
            )
        if "request_changes" in lowered_output or "request changes" in lowered_output:
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "quality_gates.code_review source record is non-clean"
                )
            )
    if gate_name == "ultraqa":
        if record.get("verdict") == "PASS" and "pass" not in lowered_output:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    "quality_gates.ultraqa source record verdict is inconsistent with subagent log",
                )
            )
        if lowered_output.startswith("fail") or "blocker found" in lowered_output:
            violations.append(
                Violation(str(CONTRACT_PATH), "quality_gates.ultraqa source record is non-clean")
            )


def _validate_native_subagent_source(
    root: Path,
    gate_name: str,
    gate: Mapping[str, Any],
    record: Mapping[str, Any],
    allowed_roles: set[str],
    violations: list[Violation],
) -> None:
    ref = gate.get("source_reference")
    match = SUBAGENT_REFERENCE_PATTERN.match(ref) if isinstance(ref, str) else None
    if match is None:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name}.source_reference must be subagent:<thread_id>",
            )
        )
        return
    thread_id = match.group("thread_id")
    if record.get("thread_id") != thread_id:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record thread_id mismatch",
            )
        )
    if record.get("source_type") != "native_subagent":
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record must be native_subagent",
            )
        )
    if record.get("agent_role") not in allowed_roles:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record must identify non-leader role",
            )
        )
    if record.get("status") != "completed":
        violations.append(
            Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} source record incomplete")
        )
    completed_at = record.get("completed_at")
    if not isinstance(completed_at, str) or not completed_at.strip():
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record completed_at required",
            )
        )
    completion_evidence = _subagent_completion_evidence(root, thread_id)
    if completion_evidence is None:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record must match completed subagent tracker evidence",
            )
        )
        return
    tracker_thread, log_event = completion_evidence
    if tracker_thread.get("completed_at") != completed_at:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record completed_at must match subagent tracker",
            )
        )
    else:
        _validate_native_subagent_log_output(gate_name, record, log_event, completed_at, violations)


def _validate_quality_gate_record(
    root: Path,
    gate_name: str,
    gate: Mapping[str, Any],
    allowed_roles: set[str],
    violations: list[Violation],
) -> None:
    record = _load_quality_gate_record(root, gate)
    if record is None:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name}.source_record_path must resolve to durable source record",
            )
        )
        return
    if _is_implementation_artifact_record(gate) and gate.get("source_type") != "native_subagent":
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} implementation-artifact source record requires externally verifiable native_subagent evidence",
            )
        )
    if record.get("source_reference") != gate.get("source_reference"):
        violations.append(
            Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} source record ref mismatch")
        )
    if record.get("agent_role") not in allowed_roles:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"quality_gates.{gate_name} source record must identify non-leader role",
            )
        )
    if record.get("status") != "completed":
        violations.append(
            Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} source record incomplete")
        )
    if gate.get("source_type") == "native_subagent":
        _validate_native_subagent_source(root, gate_name, gate, record, allowed_roles, violations)
    raw = record.get("raw_completed")
    if not isinstance(raw, str) or len(raw.strip()) < 50:
        violations.append(
            Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} raw source record required")
        )
    elif gate.get("source_type") == "native_subagent":
        _validate_no_stale_failure_state_output(gate_name, "raw source record", raw, violations)
    lowered_raw = raw.lower() if isinstance(raw, str) else ""
    if "fabricated" in lowered_raw:
        violations.append(
            Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} source record is not durable")
        )
    if gate_name == "code_review":
        if record.get("recommendation") != "APPROVE":
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "quality_gates.code_review source record must APPROVE"
                )
            )
        if record.get("architectural_status") != "CLEAR":
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "quality_gates.code_review source record must be CLEAR"
                )
            )
        if "request_changes" in lowered_raw or "architectural status: block" in lowered_raw:
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "quality_gates.code_review source record is non-clean"
                )
            )
    if gate_name == "ultraqa":
        if record.get("verdict") != "PASS" or record.get("clean") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "quality_gates.ultraqa source record must PASS clean")
            )
        if lowered_raw.startswith("fail") or "blocker found" in lowered_raw:
            violations.append(
                Violation(str(CONTRACT_PATH), "quality_gates.ultraqa source record is non-clean")
            )


def _validate_quality_gates(
    root: Path, data: Mapping[str, Any], violations: list[Violation]
) -> None:
    gates = data.get("quality_gates")
    if not isinstance(gates, Mapping):
        violations.append(Violation(str(CONTRACT_PATH), "quality_gates must be an object"))
        return
    for gate_name in ("local_checker", "code_review", "ultraqa", "ci_wiring"):
        if gate_name not in gates:
            violations.append(Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name} missing"))
    local = gates.get("local_checker")
    if isinstance(local, Mapping) and local.get("status") != "passed":
        violations.append(
            Violation(str(CONTRACT_PATH), "quality_gates.local_checker must be passed")
        )
    ci_wiring = gates.get("ci_wiring")
    if isinstance(ci_wiring, Mapping) and ci_wiring.get("status") != "passed":
        violations.append(Violation(str(CONTRACT_PATH), "quality_gates.ci_wiring must be passed"))
    for gate_name, allowed_roles in {
        "code_review": {"code-reviewer", "architect"},
        "ultraqa": {"verifier", "test-engineer", "prometheus-strict-oracle"},
    }.items():
        gate = gates.get(gate_name)
        if not isinstance(gate, Mapping):
            continue
        status = gate.get("status")
        if status in PENDING_GATE_STATUSES:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name} pending placeholder cannot satisfy final closure",
                )
            )
            continue
        if status != "passed":
            violations.append(
                Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name}.status must be passed")
            )
            continue
        source_values = {
            str(gate.get(key, "")).strip().lower().replace("-", "_")
            for key in ("source", "source_type", "source_kind", "source_reference", "agent_role")
        }
        if source_values & FORBIDDEN_GATE_SOURCES:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name} uses forbidden self-attested source",
                )
            )
        if gate.get("source_type") != "native_subagent":
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name}.source_type must be native_subagent for passed final closure",
                )
            )
        if gate.get("agent_role") not in allowed_roles:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name}.agent_role must identify non-leader source",
                )
            )
        if gate.get("reviewed_by_non_leader") is not True:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name}.reviewed_by_non_leader=true required for non-leader evidence",
                )
            )
        ref = gate.get("source_reference")
        if not isinstance(ref, str) or SOURCE_REFERENCE_PATTERN.search(ref) is None:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"quality_gates.{gate_name}.source_reference must identify native subagent record",
                )
            )
        evidence = gate.get("evidence")
        if not isinstance(evidence, str) or len(evidence.strip()) < 30:
            violations.append(
                Violation(str(CONTRACT_PATH), f"quality_gates.{gate_name}.evidence required")
            )
        _validate_quality_gate_record(root, gate_name, gate, allowed_roles, violations)
    code_review = gates.get("code_review")
    if isinstance(code_review, Mapping):
        if code_review.get("recommendation") != "APPROVE":
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "quality_gates.code_review.recommendation must be APPROVE"
                )
            )
        if code_review.get("architectural_status") != "CLEAR":
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    "quality_gates.code_review.architectural_status must be CLEAR",
                )
            )
    ultraqa = gates.get("ultraqa")
    if isinstance(ultraqa, Mapping) and ultraqa.get("clean") is not True:
        violations.append(
            Violation(str(CONTRACT_PATH), "quality_gates.ultraqa.clean=true required")
        )


def _validate_docs_wiring(root: Path) -> list[Violation]:
    required: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: [
            "Story 132.8",
            CHECKER_COMMAND,
            str(CONTRACT_PATH),
            "readiness-contract-complete_not_live_activation",
        ],
        PRODUCTION_OPS_PATH: [
            "Story 132.8",
            CHECKER_COMMAND,
            "Epic 132 closure evidence readiness",
            "durable non-leader native subagent provenance/evidence only",
        ],
        BACKUP_RESTORE_PATH: [
            "Story 132.8",
            CHECKER_COMMAND,
            "failure/load/backup/restore validation",
        ],
        FEATURE_STATUS_PATH: [
            "Story 132.8",
            str(CONTRACT_PATH),
            CHECKER_COMMAND,
            "readiness-contract-complete_not_live_activation",
        ],
        ARTIFACT_PATH: ["Story 132.8", CHECKER_COMMAND, str(CONTRACT_PATH), "quality_gates"],
        SPRINT_STATUS_PATH: REQUIRED_SPRINT_TOKENS,
        JUSTFILE_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
        CI_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
    }
    violations: list[Violation] = []
    for relpath, tokens in required.items():
        text = _read(root, relpath)
        for token in tokens:
            if token not in text:
                violations.append(Violation(str(relpath), f"missing required reference {token}"))
    for relpath in (JUSTFILE_PATH, CI_PATH):
        text = _read(root, relpath)
        check_gates = _just_recipe(text, "check-gates") if relpath == JUSTFILE_PATH else text
        self_tests = (
            _just_recipe(text, "check-gates-self-test") if relpath == JUSTFILE_PATH else text
        )
        for story, command in REQUIRED_GATE_COMMANDS.items():
            if command not in check_gates:
                violations.append(
                    Violation(str(relpath), f"missing required reference {story} {command}")
                )
            if f"{command} --self-test" not in self_tests:
                violations.append(
                    Violation(str(relpath), f"missing required reference {story} self-test")
                )
    return violations


def _validate_secrets_and_overclaims(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in SECRET_SCAN_PATHS:
        text = _read(root, relpath)
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            violations.append(Violation(str(relpath), "secret-like value is forbidden"))
        for category, patterns in FORBIDDEN_SURFACE_PATTERNS:
            for pattern in patterns:
                for match in pattern.finditer(text):
                    window = text[max(0, match.start() - 240) : min(len(text), match.end() + 240)]
                    is_explicit_setting = ":" in match.group(0) or "=" in match.group(0)
                    if not is_explicit_setting and NEGATION_PATTERN.search(window) is not None:
                        continue
                    violations.append(
                        Violation(
                            str(relpath), f"forbidden production surface is enabled: {category}"
                        )
                    )
                    break
        overclaim_text = text
        if relpath == SPRINT_STATUS_PATH:
            overclaim_text = "\n".join(
                line for line in text.splitlines() if "epic-132" in line or "132-8" in line
            )
        elif relpath in {
            OPERATOR_RUNBOOK_PATH,
            PRODUCTION_OPS_PATH,
            FEATURE_STATUS_PATH,
            BACKUP_RESTORE_PATH,
        }:
            paragraphs = re.split(r"\n\s*\n", text)
            overclaim_text = "\n\n".join(
                paragraph
                for paragraph in paragraphs
                if "Story 132.8" in paragraph
                or "Epic 132" in paragraph
                or str(CONTRACT_PATH) in paragraph
            )
        for pattern in OVERCLAIM_PATTERNS:
            for match in pattern.finditer(overclaim_text):
                window = overclaim_text[
                    max(0, match.start() - 520) : min(len(overclaim_text), match.end() + 520)
                ]
                if NEGATION_PATTERN.search(window) is None:
                    violations.append(
                        Violation(
                            str(relpath), "Epic 132 closure live activation overclaim is forbidden"
                        )
                    )
                    break
    return violations


def _gate_script_from_command(command: str) -> Path | None:
    """Return the checker script referenced by a documented gate command."""

    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for part in parts:
        if part.endswith(".py"):
            return Path(part)
    return None


def _run_subordinate_gate(
    root: Path, story: str, command: str, *, self_test: bool
) -> Violation | None:
    script = _gate_script_from_command(command)
    command_kind = "self-test" if self_test else "checker"
    if script is None:
        return Violation(
            str(CONTRACT_PATH),
            f"subordinate Story {story} {command_kind} command is not parseable: {command}",
        )
    script_path = root / script
    if not script_path.exists():
        return Violation(
            str(CONTRACT_PATH),
            f"subordinate Story {story} {command_kind} script missing: {script}",
        )
    args = [sys.executable, str(script_path)]
    if self_test:
        args.append("--self-test")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["STORY_1328_CLOSURE_SUBORDINATE_GATE"] = "1"
    try:
        result = subprocess.run(
            args,
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=SUBORDINATE_GATE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return Violation(
            str(CONTRACT_PATH),
            (
                f"subordinate Story {story} {command_kind} timed out after "
                f"{SUBORDINATE_GATE_TIMEOUT_SECONDS}s: {script}"
                + (f"\nstdout:\n{exc.stdout}" if exc.stdout else "")
                + (f"\nstderr:\n{exc.stderr}" if exc.stderr else "")
            ),
        )
    if result.returncode == 0:
        return None
    detail = "\n".join(
        part
        for part in (
            f"stdout:\n{result.stdout.strip()}" if result.stdout.strip() else "",
            f"stderr:\n{result.stderr.strip()}" if result.stderr.strip() else "",
        )
        if part
    )
    return Violation(
        str(CONTRACT_PATH),
        (
            f"subordinate Story {story} {command_kind} failed with exit "
            f"{result.returncode}: {script}" + (f"\n{detail}" if detail else "")
        ),
    )


def _validate_subordinate_gates(root: Path) -> list[Violation]:
    """Execute Story 132.1-132.7 gates so closure proves subordinate gates are green."""

    violations: list[Violation] = []
    for story in SUBORDINATE_GATE_STORIES:
        command = REQUIRED_GATE_COMMANDS[story]
        for self_test in (False, True):
            violation = _run_subordinate_gate(root, story, command, self_test=self_test)
            if violation is not None:
                violations.append(violation)
    return violations


def validate(root: Path = REPO_ROOT, *, run_subordinate_gates: bool = True) -> list[Violation]:
    missing = [
        Violation(str(path), "required file missing")
        for path in REQUIRED_FILES
        if not (root / path).exists()
    ]
    if missing:
        return missing
    violations: list[Violation] = []
    for validator in (_validate_contract, _validate_docs_wiring, _validate_secrets_and_overclaims):
        violations.extend(validator(root))
    if run_subordinate_gates:
        violations.extend(_validate_subordinate_gates(root))
    return violations


def _copy_fixture_path(src_root: Path, dst_root: Path, relpath: Path) -> None:
    src = src_root / relpath
    dst = dst_root / relpath
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_live_fixture(dst_root: Path) -> None:
    paths = set(REQUIRED_FILES) | set(SECRET_SCAN_PATHS) | {ARTIFACT_PATH}
    paths.update(SUBORDINATE_GATE_FIXTURE_PATHS)
    tracker_src = REPO_ROOT / SUBAGENT_TRACKING_PATH
    if tracker_src.exists():
        paths.add(SUBAGENT_TRACKING_PATH)
    for log_src in (REPO_ROOT / SUBAGENT_TURN_LOG_DIR).glob("turns-*.jsonl"):
        log_dst = dst_root / SUBAGENT_TURN_LOG_DIR / log_src.name
        log_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log_src, log_dst)
    for required in REQUIRED_STORIES.values():
        for rel in required.values():
            paths.add(Path(rel))
    for relpath in sorted(paths):
        _copy_fixture_path(REPO_ROOT, dst_root, relpath)


def _clean_self_test_quality_gate_output(gate_name: str) -> str:
    if gate_name == "code_review":
        return (
            "Recommendation: APPROVE; Architectural status: CLEAR; Evidence: temporary "
            "self-test native subagent fixture for Story 132.8 closure validation, with "
            "all required wiring, durable provenance, and readiness-only boundaries verified."
        )
    return (
        "PASS\n\nEvidence:\n"
        "- Temporary self-test native subagent fixture validates mutation coverage, closure "
        "semantics, CI wiring, and fail-closed readiness-only boundaries.\n"
        "- No live activation is asserted."
    )


def _replace_turn_log_output(root: Path, thread_id: str, turn_id: str, output: str) -> None:
    for log_path in sorted((root / SUBAGENT_TURN_LOG_DIR).glob("turns-*.jsonl")):
        changed = False
        rewritten_lines: list[str] = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                rewritten_lines.append(line)
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "agent-turn-complete"
                and event.get("thread_id") == thread_id
                and event.get("turn_id") == turn_id
            ):
                event["output_preview"] = output
                changed = True
            rewritten_lines.append(json.dumps(event) if isinstance(event, dict) else line)
        if changed:
            log_path.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")


def _write_clean_self_test_quality_gate_records(root: Path) -> None:
    """Make temporary copied fixtures clean without altering committed evidence."""

    contract = _load_json(root, CONTRACT_PATH)
    quality_records = _load_json(root, QUALITY_GATE_RECORD_PATH)
    provenance = _load_json(root, NATIVE_SUBAGENT_PROVENANCE_PATH)
    gates = cast("Mapping[str, Any]", contract["quality_gates"])
    records = cast("dict[str, Any]", quality_records["records"])
    provenance_records = cast("dict[str, Any]", provenance["records"])
    for gate_name in ("code_review", "ultraqa"):
        gate = cast("Mapping[str, Any]", gates[gate_name])
        ref = cast("str", gate["source_reference"])
        output = _clean_self_test_quality_gate_output(gate_name)
        record = cast("dict[str, Any]", records[ref])
        record["raw_completed"] = output
        if gate_name == "code_review":
            record["recommendation"] = "APPROVE"
            record["architectural_status"] = "CLEAR"
        else:
            record["verdict"] = "PASS"
            record["clean"] = True
        provenance_record = cast("dict[str, Any]", provenance_records[ref])
        completion_event = cast("dict[str, Any]", provenance_record["completion_event"])
        completion_event["output_preview"] = output
        thread_id = cast("str", completion_event["thread_id"])
        turn_id = cast("str", completion_event["turn_id"])
        _replace_turn_log_output(root, thread_id, turn_id, output)
    (root / QUALITY_GATE_RECORD_PATH).write_text(
        json.dumps(quality_records, indent=2) + "\n", encoding="utf-8"
    )
    (root / NATIVE_SUBAGENT_PROVENANCE_PATH).write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )


def _write_contract(root: Path, data: dict[str, Any]) -> None:
    (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="split-deployment-closure-readiness-") as tmp:
        root = Path(tmp)
        _copy_live_fixture(root)
        _write_clean_self_test_quality_gate_records(root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1
        shutil.rmtree(root / ".omx", ignore_errors=True)
        clean_without_omx = validate(root)
        if clean_without_omx:
            print("self-test clean checkout fixture without .omx failed:", file=sys.stderr)
            for violation in clean_without_omx:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1

        subordinate_contract = root / Path(REQUIRED_STORIES["132.2"]["contract"])
        subordinate_contract.write_text('{"story": "132.2", "broken": true}\n', encoding="utf-8")
        if not any("subordinate Story 132.2 checker failed" in v.message for v in validate(root)):
            print(
                "self-test failed to detect failing subordinate Story 132.2 checker",
                file=sys.stderr,
            )
            return 1
        shutil.copy2(REPO_ROOT / REQUIRED_STORIES["132.2"]["contract"], subordinate_contract)

        data = _load_json(root, CONTRACT_PATH)
        records = _load_json(root, QUALITY_GATE_RECORD_PATH)
        code_review_gate = cast("dict[str, Any]", data["quality_gates"])["code_review"]
        code_review_ref = cast("dict[str, Any]", code_review_gate)["source_reference"]
        code_review_record = cast(
            "dict[str, Any]", cast("dict[str, Any]", records["records"])[code_review_ref]
        )
        code_review_record["raw_completed"] = (
            f"{code_review_record['raw_completed']}\n"
            "Fabricated clean tail that must not be accepted after the real output_preview."
        )
        (root / QUALITY_GATE_RECORD_PATH).write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        if not any(
            "durable subagent completion output" in v.message
            for v in validate(root, run_subordinate_gates=False)
        ):
            print(
                "self-test failed to detect forged raw_completed tail on real output_preview",
                file=sys.stderr,
            )
            return 1
        shutil.copy2(REPO_ROOT / QUALITY_GATE_RECORD_PATH, root / QUALITY_GATE_RECORD_PATH)

        records = _load_json(root, QUALITY_GATE_RECORD_PATH)
        cast("dict[str, Any]", cast("dict[str, Any]", records["records"])[code_review_ref])[
            "raw_completed"
        ] = (
            "Invented clean code review text that reuses the real thread_id and completed_at "
            "but does not match the durable subagent completion output."
        )
        (root / QUALITY_GATE_RECORD_PATH).write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        if not any(
            "durable subagent completion output" in v.message
            for v in validate(root, run_subordinate_gates=False)
        ):
            print(
                "self-test failed to detect invented raw_completed on real subagent thread",
                file=sys.stderr,
            )
            return 1
        shutil.copy2(REPO_ROOT / QUALITY_GATE_RECORD_PATH, root / QUALITY_GATE_RECORD_PATH)

        data = _load_json(root, CONTRACT_PATH)
        data["production_activation"] = "active"
        _write_contract(root, data)
        if not any(
            "production_activation" in v.message
            for v in validate(root, run_subordinate_gates=False)
        ):
            print("self-test failed to detect live production_activation", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        data["production_activation"] = "deferred_fail_closed"
        cast("dict[str, Any]", data["required_story_evidence"]).pop("132.6")
        _write_contract(root, data)
        if not any("132.6" in v.message for v in validate(root, run_subordinate_gates=False)):
            print("self-test failed to detect missing Story 132.6 evidence", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        data["required_story_evidence"] = _load_json(REPO_ROOT, CONTRACT_PATH)[
            "required_story_evidence"
        ]
        cast("dict[str, Any]", cast("dict[str, Any]", data["quality_gates"])["code_review"])[
            "source"
        ] = "leader"
        _write_contract(root, data)
        if not any(
            "self-attested" in v.message for v in validate(root, run_subordinate_gates=False)
        ):
            print("self-test failed to detect leader-authored code review gate", file=sys.stderr)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run built-in mutation checks")
    parser.add_argument("--verbose", action="store_true", help="print success detail")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1
    if args.verbose:
        print("Story 132.8 Epic 132 closure readiness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
