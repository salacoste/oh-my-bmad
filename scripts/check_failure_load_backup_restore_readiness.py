#!/usr/bin/env python3
"""Validate Story 132.7 failure/load/backup/restore readiness."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/failure-load-backup-restore-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
BACKUP_RESTORE_PATH = Path("docs/backup-restore.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/132-7-failure-load-backup-restore-validation.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_failure_load_backup_restore_readiness.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_FILES = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    BACKUP_RESTORE_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
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
)
REQUIRED_SECTIONS = frozenset(
    {
        "execution_policy",
        "failure_scenarios",
        "load_validation",
        "backup_restore_validation",
        "observability_and_audit",
        "safety_boundaries",
        "readiness_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_FAILURE_SCENARIOS = frozenset(
    {
        "database_outage",
        "network_partition",
        "pool_exhaustion",
        "worker_crash",
        "orchestrator_crash",
        "registry_restart",
        "mcp_service_unavailable",
        "event_log_append_failure",
        "migration_failure_rollback",
        "backup_restore_failure",
    }
)
REQUIRED_LOAD_TARGET_SURFACES = frozenset(
    {
        "registry-api",
        "registry-state",
        "worker-wrapper",
        "orchestrator-adapter",
        "MCP",
        "operator dashboard",
    }
)
REQUIRED_LOAD_METRICS = frozenset({"latency", "error", "backpressure"})
REQUIRED_NON_GOALS = frozenset(
    {
        "no live drill execution",
        "no live destructive operation",
        "no destructive restore",
        "no load generation",
        "no external production load",
        "no backup pruning",
        "no production restore",
        "no production mutation",
        "no production host mutation",
        "no credential values",
        "no runtime audit emitter",
        "no production activation",
        "no provisioning",
        "no external host mutation",
    }
)
DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#failure-load-backup-restore-validation-readiness-story-1327",
        f"{PRODUCTION_OPS_PATH}#story-1327-failure-load-backup-restore-validation-readiness",
        f"{BACKUP_RESTORE_PATH}#failure-load-backuprestore-validation-readiness-story-1327",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_PATH}#summary",
    }
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
        r"\b(?:failure[- ]drill|live[- ]drill|destructive[- ]restore|backup[- ]restore|"
        r"load[- ]generation|external[- ]production[- ]load|production[- ]restore)\b"
        r"[^\n.]{0,160}\b(?:live|activated|enabled|executed|ran|production[- ]ready|shipped)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|enabled|executed|ran|production[- ]ready|shipped)\b"
        r"[^\n.]{0,160}\b(?:failure[- ]drill|live[- ]drill|destructive[- ]restore|"
        r"backup[- ]restore|load[- ]generation|external[- ]production[- ]load|production[- ]restore)\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|"
    r"is\s+not|remains\s+deferred|future|readiness[- ]only|unsupported|forbidden)\b",
    re.I,
)
FORBIDDEN_SURFACE_PATTERNS = (
    (
        "production host mutation",
        (
            re.compile(r"(?i)\bproduction_host_mutation\b[\"']?\s*[:=]\s*true"),
            re.compile(r"(?i)\b(?:mutate|mutates|mutated|mutating)\s+production\s+hosts?\b"),
            re.compile(r"(?i)\bproduction\s+hosts?\s+(?:mutated|changed|modified|updated)\b"),
        ),
    ),
    (
        "provisioning",
        (
            re.compile(
                r"(?i)\b(?:provisioning|provisioning_enabled|host_provisioning|"
                r"hosts_provisioning|live_postgres_provisioning)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active)"
            ),
            re.compile(
                r"(?i)\b(?:provision|provisions|provisioned|provisioning)\s+"
                r"(?:hosts?|live\s+postgres|production\s+(?:hosts?|infrastructure))\b"
            ),
        ),
    ),
    (
        "runtime audit emitters",
        (
            re.compile(
                r"(?i)\b(?:runtime_(?:production_)?audit_emitter(?:s)?_"
                r"(?:enabled|live|activated|activation)|runtime_audit_emitters?)\b"
                r"[\"']?\s*[:=]\s*(?:true|enabled|active|activated|live)"
            ),
            re.compile(
                r"(?i)\b(?:activate|activates|activated|enable|enabled|make\s+live|"
                r"made\s+live)\s+runtime\s+(?:production\s+)?audit\s+emitters?\b"
            ),
        ),
    ),
    (
        "live drill execution",
        (
            re.compile(
                r"(?i)\b(?:live_drill_execution|failure_drill_execution)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|ran|executed)"
            ),
            re.compile(r"(?i)\b(?:run|runs|ran|execute|executes|executed)\s+live\s+drills?\b"),
        ),
    ),
    (
        "live load",
        (
            re.compile(
                r"(?i)\b(?:live_load|live_load_generation|load_generation|"
                r"external_production_load)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|runs|ran|execute|executes|executed|generate|"
                r"generates|generated)\s+(?:live\s+|external\s+production\s+)?load\b"
            ),
        ),
    ),
    (
        "live restore",
        (
            re.compile(
                r"(?i)\b(?:live_restore|live_restore_execution|restore_execution|"
                r"production_restore)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|runs|ran|execute|executes|executed)\s+"
                r"(?:live\s+|production\s+)?(?:restore|backup/restore|backup\s+restore)\b"
            ),
        ),
    ),
    (
        "destructive restore",
        (
            re.compile(
                r"(?i)\b(?:destructive_restore|destructive_restore_execution)\b"
                r"[\"']?\s*[:=]\s*(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|runs|ran|execute|executes|executed)\s+"
                r"(?:a\s+)?destructive\s+restore\b"
            ),
        ),
    ),
    (
        "backup pruning",
        (
            re.compile(r"(?i)\bbackup_pruning\b[\"']?\s*[:=]\s*(?:true|enabled|active)"),
            re.compile(r"(?i)\b(?:prune|prunes|pruned|pruning)\s+(?:backup|backups)\b"),
        ),
    ),
    (
        "credential values",
        (re.compile(r"(?i)\bcredential_values?\b[\"']?\s*[:=]\s*(?:true|present|included)"),),
    ),
)


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {self.message}"


def _read(root: Path, relpath: Path) -> str:
    return (root / relpath).read_text(encoding="utf-8")


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


def _list_ids(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    ids: set[str] = set()
    for item in value:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            ids.add(item["id"])
        elif isinstance(item, str):
            ids.add(item)
    return ids


def _has_surface(surfaces: Sequence[object], needle: str) -> bool:
    return any(isinstance(item, str) and needle.lower() in item.lower() for item in surfaces)


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
        ("story",): "132.7",
        ("mode",): "readiness_only",
        ("production_activation",): "deferred",
        ("execution_policy", "status"): "readiness_only_static_contract",
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

    policy = data.get("execution_policy", {})
    if isinstance(policy, Mapping):
        forbidden_flags = (
            "live_drill_execution",
            "destructive_restore",
            "load_generation",
            "production_mutation",
        )
        for flag in forbidden_flags:
            if policy.get(flag) is not False:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"execution_policy.{flag}=false required")
                )
        if policy.get("production_activation_deferred") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "production_activation_deferred=true required")
            )
    else:
        violations.append(Violation(str(CONTRACT_PATH), "execution_policy must be an object"))

    scenarios = _list_ids(data.get("failure_scenarios"))
    if missing := REQUIRED_FAILURE_SCENARIOS - scenarios:
        violations.append(
            Violation(str(CONTRACT_PATH), f"failure_scenarios missing {sorted(missing)}")
        )

    load = data.get("load_validation", {})
    if isinstance(load, Mapping):
        surfaces = load.get("target_surfaces")
        if not isinstance(surfaces, Sequence) or isinstance(surfaces, (str, bytes, bytearray)):
            violations.append(
                Violation(str(CONTRACT_PATH), "load_validation.target_surfaces required")
            )
        else:
            for required in REQUIRED_LOAD_TARGET_SURFACES:
                if not _has_surface(surfaces, required):
                    violations.append(
                        Violation(str(CONTRACT_PATH), f"load target surface missing {required}")
                    )
        for flag in ("bounded_synthetic_load_only", "no_external_production_load"):
            if load.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"load_validation.{flag}=true required")
                )
        if load.get("external_production_load") is not False:
            violations.append(
                Violation(str(CONTRACT_PATH), "external_production_load=false required")
            )
        metrics = load.get("metrics")
        if not isinstance(metrics, Mapping):
            violations.append(
                Violation(str(CONTRACT_PATH), "load_validation.metrics object required")
            )
        else:
            missing_metrics = REQUIRED_LOAD_METRICS - set(metrics)
            if missing_metrics:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"load metrics missing {sorted(missing_metrics)}")
                )
            for metric in REQUIRED_LOAD_METRICS & set(metrics):
                value = metrics[metric]
                if not isinstance(value, str) or len(value.strip()) < 15:
                    violations.append(
                        Violation(str(CONTRACT_PATH), f"load metric {metric} evidence required")
                    )
        for key in (
            "pool_saturation_thresholds",
            "rate_limit_preservation",
            "trace_correlation",
        ):
            if not isinstance(load.get(key), str) or len(cast("str", load.get(key)).strip()) < 15:
                violations.append(Violation(str(CONTRACT_PATH), f"load_validation.{key} required"))
    else:
        violations.append(Violation(str(CONTRACT_PATH), "load_validation must be an object"))

    backup = data.get("backup_restore_validation", {})
    if isinstance(backup, Mapping):
        for key in (
            "pre_migration_backup",
            "checksum_manifest_validation",
            "isolated_restore",
            "schema_version_compatibility",
            "point_in_time_freshness",
            "rollback_fix_forward_decision",
            "destructive_restore_confirmation",
        ):
            if (
                not isinstance(backup.get(key), str)
                or len(cast("str", backup.get(key)).strip()) < 15
            ):
                violations.append(
                    Violation(str(CONTRACT_PATH), f"backup_restore_validation.{key} required")
                )
        if backup.get("production_restore") is not False:
            violations.append(Violation(str(CONTRACT_PATH), "production_restore=false required"))
        if backup.get("backup_pruning") is not False:
            violations.append(Violation(str(CONTRACT_PATH), "backup_pruning=false required"))
    else:
        violations.append(
            Violation(str(CONTRACT_PATH), "backup_restore_validation must be an object")
        )

    obs = data.get("observability_and_audit", {})
    if isinstance(obs, Mapping):
        for key in (
            "health_readiness_signals",
            "sanitized_logs",
            "trace_ids",
            "recovery_timeline",
        ):
            if not isinstance(obs.get(key), str) or len(cast("str", obs.get(key)).strip()) < 15:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"observability_and_audit.{key} required")
                )
        for flag in ("audit_metadata_only", "no_secret_material"):
            if obs.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"observability_and_audit.{flag}=true required")
                )
        if obs.get("runtime_audit_emitter") is not False:
            violations.append(Violation(str(CONTRACT_PATH), "runtime_audit_emitter=false required"))
    else:
        violations.append(
            Violation(str(CONTRACT_PATH), "observability_and_audit must be an object")
        )

    safety = data.get("safety_boundaries", {})
    if isinstance(safety, Mapping):
        for flag in (
            "no_live_destructive_operation",
            "no_backup_pruning",
            "no_production_restore",
            "no_host_mutation",
            "no_credential_values",
            "no_runtime_audit_emitter",
            "local_single_host_sqlite_default_preserved",
        ):
            if safety.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"safety_boundaries.{flag}=true required")
                )
    else:
        violations.append(Violation(str(CONTRACT_PATH), "safety_boundaries must be an object"))

    if missing := REQUIRED_NON_GOALS - set(cast("Iterable[str]", data.get("non_goals", []))):
        violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing)}"))
    if DOC_REFS - set(cast("Iterable[str]", data.get("docs_refs", []))):
        violations.append(Violation(str(CONTRACT_PATH), "docs_refs missing Story 132.7 refs"))
    if any(pattern.search(s) for pattern in SECRET_PATTERNS for s in _walk_strings(data)):
        violations.append(Violation(str(CONTRACT_PATH), "contract contains secret-like value"))
    return violations


def _validate_docs_wiring(root: Path) -> list[Violation]:
    required: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.7", CHECKER_COMMAND, str(CONTRACT_PATH)],
        PRODUCTION_OPS_PATH: [
            "Story 132.7",
            CHECKER_COMMAND,
            "failure/load/backup/restore validation readiness",
        ],
        BACKUP_RESTORE_PATH: ["Story 132.7", CHECKER_COMMAND, "isolated restore"],
        FEATURE_STATUS_PATH: ["Story 132.7", str(CONTRACT_PATH), CHECKER_COMMAND],
        ARTIFACT_PATH: ["Story 132.7", CHECKER_COMMAND, str(CONTRACT_PATH)],
        SPRINT_STATUS_PATH: [
            "epic-132: done",
            "132-6-horizontal-scaling: done",
            "132-7-failure-load-backup-restore-validation: done",
            "132-8-closure-evidence: done",
        ],
        JUSTFILE_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
        CI_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
    }
    violations: list[Violation] = []
    for relpath, tokens in required.items():
        text = _read(root, relpath)
        for token in tokens:
            if token not in text:
                violations.append(Violation(str(relpath), f"missing required reference {token}"))
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
                    window = text[max(0, match.start() - 520) : min(len(text), match.end() + 520)]
                    is_explicit_setting = ":" in match.group(0) or "=" in match.group(0)
                    if not is_explicit_setting and NEGATION_PATTERN.search(window) is not None:
                        continue
                    violations.append(
                        Violation(
                            str(relpath),
                            f"forbidden production surface is enabled: {category}",
                        )
                    )
                    break
        overclaim_text = text
        if relpath == SPRINT_STATUS_PATH:
            overclaim_text = "\n".join(
                line for line in text.splitlines() if "epic-132" in line or "132-7" in line
            )
        for pattern in OVERCLAIM_PATTERNS:
            for match in pattern.finditer(overclaim_text):
                window = overclaim_text[
                    max(0, match.start() - 520) : min(len(overclaim_text), match.end() + 520)
                ]
                if NEGATION_PATTERN.search(window) is None:
                    violations.append(
                        Violation(
                            str(relpath),
                            "failure/load/backup/restore live activation overclaim is forbidden",
                        )
                    )
                    break
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
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
    return violations


def _copy_live_fixture(dst_root: Path) -> None:
    for relpath in set(REQUIRED_FILES) | set(SECRET_SCAN_PATHS) | {ARTIFACT_PATH}:
        src = REPO_ROOT / relpath
        dst = dst_root / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_contract(root: Path, data: dict[str, Any]) -> None:
    (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="failure-load-backup-restore-readiness-") as tmp:
        root = Path(tmp)
        _copy_live_fixture(root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        cast("dict[str, Any]", data["execution_policy"])["live_drill_execution"] = True
        _write_contract(root, data)
        if not any("live_drill_execution" in v.message for v in validate(root)):
            print("self-test failed to detect live drill execution", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        cast("dict[str, Any]", data["execution_policy"])["live_drill_execution"] = False
        data["failure_scenarios"] = [
            item
            for item in cast("list[dict[str, Any]]", data["failure_scenarios"])
            if item.get("id") != "database_outage"
        ]
        _write_contract(root, data)
        if not any("database_outage" in v.message for v in validate(root)):
            print("self-test failed to detect missing database_outage scenario", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        cast("list[dict[str, Any]]", data["failure_scenarios"]).append(
            {
                "id": "database_outage",
                "status": "future_validation_required",
                "expected_evidence": "Health/readiness degradation, retry/backoff, recovery notes, and no production mutation.",
            }
        )
        cast("dict[str, Any]", cast("dict[str, Any]", data["load_validation"])["metrics"]).pop(
            "latency"
        )
        _write_contract(root, data)
        if not any("latency" in v.message for v in validate(root)):
            print("self-test failed to detect missing latency metric", file=sys.stderr)
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
        print("Story 132.7 failure/load/backup/restore readiness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
