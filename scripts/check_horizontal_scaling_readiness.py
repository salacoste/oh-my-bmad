#!/usr/bin/env python3
"""Validate Story 132.6 horizontal-scaling readiness."""

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
CONTRACT_PATH = Path("docs/horizontal-scaling-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path("_bmad-output/implementation-artifacts/132-6-horizontal-scaling.md")
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_horizontal_scaling_readiness.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_FILES = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
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
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
REQUIRED_SECTIONS = frozenset(
    {
        "default_preservation",
        "scale_safety_matrix",
        "singleton_authorities",
        "coordination_boundaries",
        "load_balancer_readiness",
        "unsupported_scaling_modes",
        "rollback_and_observability",
        "readiness_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_SERVICE_CLASSES = {
    "registry-api": "stateless_scalable",
    "registry-state": "singleton_required",
    "telegram-gateway": "profile_gated_internal",
    "orchestrator-adapter": "profile_gated_internal",
    "worker-wrapper": "profile_gated_internal",
    "clawhip-daemon": "singleton_required",
}
ALLOWED_SERVICE_CLASSES = frozenset(
    {"stateless_scalable", "singleton_required", "profile_gated_internal"}
)
REQUIRED_SINGLETON_AUTHORITIES = frozenset(
    {
        "mutable_registry_state_writer_materializer",
        "alembic_migration_runner",
        "retention_apply_destructive_lifecycle_runner",
        "event_append_authority",
        "clawhip_bridge_authority",
    }
)
REQUIRED_COORDINATION_BOUNDARIES = frozenset(
    {
        "idempotency_shared_storage",
        "worktree_lock_ownership",
        "task_session_registry_consistency",
        "event_ordering_replay",
        "capability_tier_preservation",
        "bounded_db_pool_composition",
    }
)
REQUIRED_UNSUPPORTED_MODES = frozenset(
    {
        "external_worker_pool",
        "multi_writer_registry_state",
        "multi_runner_migration",
        "multi_clawhip_appenders",
        "dashboard_live_scaling",
        "runtime_audit_emitter_activation",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no live horizontal scaling activation",
        "no external worker pool",
        "no external load balancer",
        "no host port publishing",
        "no multi-writer registry-state",
        "no multi-runner migration",
        "no multi-clawhip appenders",
        "no dashboard live scaling",
        "no production credentials or token values",
        "no production host mutation",
        "no provisioning",
        "no live load generation",
        "no live restore execution",
        "no runtime production audit emitter",
    }
)
DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#horizontal-scaling-readiness-story-1326",
        f"{PRODUCTION_OPS_PATH}#story-1326-horizontal-scaling-readiness",
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
        r"\b(?:horizontal[- ]scaling|external[- ]worker|external[- ]load[- ]balancer|"
        r"multi[- ]writer|multi[- ]runner|multi[- ]clawhip|dashboard[- ]live[- ]scaling)\b"
        r"[^\n.]{0,160}\b(?:live|activated|enabled|deployed|production[- ]ready|shipped)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|enabled|deployed|production[- ]ready|shipped)\b"
        r"[^\n.]{0,160}\b(?:horizontal[- ]scaling|external[- ]worker|"
        r"external[- ]load[- ]balancer|multi[- ]writer|multi[- ]runner|multi[- ]clawhip|"
        r"dashboard[- ]live[- ]scaling)\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|"
    r"is\s+not|remains\s+deferred|future|readiness[- ]only|unsupported)\b",
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
        "external hosts/ports",
        (
            re.compile(
                r"(?i)\b(?:external_(?:host|hosts|host_added|hosts_added|"
                r"load_balancer|load_balancer_added)|host_ports(?:_published)?)\b"
                r"[\"']?\s*[:=]\s*true"
            ),
            re.compile(
                r"(?i)\b(?:add|adds|added|configure|configured|enable|enabled|publish|"
                r"publishes|published)\s+(?:external\s+(?:hosts?|load\s+balancers?)|"
                r"host\s+ports?)\b"
            ),
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
        "live load",
        (
            re.compile(
                r"(?i)\b(?:live_load|live_load_generation|load_generation|"
                r"live_load_drill)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|runs|ran|execute|executes|executed|generate|"
                r"generates|generated)\s+(?:live\s+)?load\b"
            ),
        ),
    ),
    (
        "live restore",
        (
            re.compile(
                r"(?i)\b(?:live_restore|live_restore_execution|restore_execution|"
                r"backup_restore_drill)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|ran|executed)"
            ),
            re.compile(
                r"(?i)\b(?:run|runs|ran|execute|executes|executed)\s+"
                r"(?:live\s+)?(?:restore|backup/restore|backup\s+restore)\b"
            ),
        ),
    ),
    (
        "live scaling activation",
        (
            re.compile(
                r"(?i)\b(?:live_scaling_activation|horizontal_scaling_activation|"
                r"scaling_activation|dashboard_live_scaling)\b[\"']?\s*[:=]\s*"
                r"(?:true|enabled|active|activated|live)"
            ),
            re.compile(
                r"(?i)\b(?:activate|activates|activated|enable|enabled|make\s+live|"
                r"made\s+live)\s+(?:live\s+)?(?:horizontal\s+)?scaling\b"
            ),
        ),
    ),
    (
        "credential rendering",
        (re.compile(r"(?i)\bcredential_rendering_forbidden\b[\"']?\s*[:=]\s*false"),),
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
        ("story",): "132.6",
        ("mode",): "readiness_only",
        ("production_activation",): "deferred",
        ("default_preservation", "status"): "preserved",
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

    default = data.get("default_preservation", {})
    if isinstance(default, Mapping):
        for flag in ("single_host_sqlite_default", "root_compose_default_unchanged"):
            if default.get(flag) is not True:
                violations.append(Violation(str(CONTRACT_PATH), f"{flag}=true required"))
        if default.get("no_live_scaling_activation") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "no_live_scaling_activation=true required")
            )

    matrix = data.get("scale_safety_matrix", {})
    if not isinstance(matrix, Mapping):
        violations.append(Violation(str(CONTRACT_PATH), "scale_safety_matrix must be an object"))
    else:
        for service, expected_class in REQUIRED_SERVICE_CLASSES.items():
            entry = matrix.get(service)
            if not isinstance(entry, Mapping):
                violations.append(
                    Violation(str(CONTRACT_PATH), f"scale_safety_matrix missing {service}")
                )
                continue
            service_class = entry.get("class")
            if service_class != expected_class or service_class not in ALLOWED_SERVICE_CLASSES:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"{service} class must be {expected_class}")
                )
            for key in ("reason", "limit_note"):
                if (
                    not isinstance(entry.get(key), str)
                    or len(cast("str", entry.get(key)).strip()) < 20
                ):
                    violations.append(Violation(str(CONTRACT_PATH), f"{service}.{key} is required"))

    authorities = data.get("singleton_authorities", {})
    if not isinstance(authorities, Mapping):
        violations.append(Violation(str(CONTRACT_PATH), "singleton_authorities must be an object"))
    else:
        missing = REQUIRED_SINGLETON_AUTHORITIES - set(authorities)
        if missing:
            violations.append(
                Violation(str(CONTRACT_PATH), f"singleton authorities missing {sorted(missing)}")
            )
        for key in REQUIRED_SINGLETON_AUTHORITIES & set(authorities):
            entry = authorities[key]
            if not isinstance(entry, Mapping):
                violations.append(Violation(str(CONTRACT_PATH), f"{key} must be an object"))
                continue
            cardinality = entry.get("cardinality")
            if cardinality not in {"exactly_one", "exactly_one_per_event_log"}:
                violations.append(Violation(str(CONTRACT_PATH), f"{key} must be singleton"))
            reason = cast("str", entry.get("reason", "")).lower()
            if not isinstance(entry.get("reason"), str) or not (
                "one" in reason or "single" in reason
            ):
                violations.append(Violation(str(CONTRACT_PATH), f"{key} singleton reason required"))

    boundaries = _list_ids(data.get("coordination_boundaries"))
    if missing := REQUIRED_COORDINATION_BOUNDARIES - boundaries:
        violations.append(
            Violation(str(CONTRACT_PATH), f"coordination boundaries missing {sorted(missing)}")
        )

    lb = data.get("load_balancer_readiness", {})
    if isinstance(lb, Mapping):
        endpoints = lb.get("health_readiness_endpoints")
        if not isinstance(endpoints, Sequence) or isinstance(endpoints, (str, bytes, bytearray)):
            violations.append(Violation(str(CONTRACT_PATH), "health/readiness endpoints required"))
        elif len([item for item in endpoints if isinstance(item, str)]) < 3:
            violations.append(
                Violation(str(CONTRACT_PATH), "health/readiness endpoints incomplete")
            )
        for key in (
            "trace_propagation",
            "auth_header_preservation",
            "rate_limit_behavior",
            "sticky_sessions",
        ):
            if not isinstance(lb.get(key), str) or len(cast("str", lb.get(key)).strip()) < 10:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"load_balancer_readiness.{key} required")
                )
        if lb.get("host_ports_published") is not False or lb.get("no_host_ports") is not True:
            violations.append(Violation(str(CONTRACT_PATH), "load balancer must add no host ports"))
        if (
            lb.get("external_load_balancer_added") is not False
            or lb.get("no_external_load_balancer") is not True
        ):
            violations.append(
                Violation(str(CONTRACT_PATH), "external load balancer must not be added")
            )
    else:
        violations.append(
            Violation(str(CONTRACT_PATH), "load_balancer_readiness must be an object")
        )

    unsupported = _list_ids(data.get("unsupported_scaling_modes"))
    if missing := REQUIRED_UNSUPPORTED_MODES - unsupported:
        violations.append(
            Violation(str(CONTRACT_PATH), f"unsupported scaling modes missing {sorted(missing)}")
        )
    for item in data.get("unsupported_scaling_modes", []):
        if isinstance(item, Mapping) and item.get("unsupported") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{item.get('id')} must be unsupported=true")
            )

    rollback = data.get("rollback_and_observability", {})
    if isinstance(rollback, Mapping):
        for key in ("scale_down_fallback", "version_skew", "lag_backpressure", "pool_saturation"):
            if (
                not isinstance(rollback.get(key), str)
                or len(cast("str", rollback.get(key)).strip()) < 10
            ):
                violations.append(
                    Violation(str(CONTRACT_PATH), f"rollback_and_observability.{key} required")
                )
        if rollback.get("credential_rendering_forbidden") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "credential rendering must be forbidden")
            )
        if rollback.get("production_host_mutation") is not False:
            violations.append(
                Violation(str(CONTRACT_PATH), "production_host_mutation=false required")
            )
    else:
        violations.append(
            Violation(str(CONTRACT_PATH), "rollback_and_observability must be an object")
        )

    if missing := REQUIRED_NON_GOALS - set(cast("Iterable[str]", data.get("non_goals", []))):
        violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing)}"))
    if DOC_REFS - set(cast("Iterable[str]", data.get("docs_refs", []))):
        violations.append(Violation(str(CONTRACT_PATH), "docs_refs missing Story 132.6 refs"))
    if any(pattern.search(s) for pattern in SECRET_PATTERNS for s in _walk_strings(data)):
        violations.append(Violation(str(CONTRACT_PATH), "contract contains secret-like value"))
    return violations


def _validate_docs_wiring(root: Path) -> list[Violation]:
    required: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.6", CHECKER_COMMAND, str(CONTRACT_PATH)],
        PRODUCTION_OPS_PATH: ["Story 132.6", CHECKER_COMMAND, "horizontal scaling readiness"],
        FEATURE_STATUS_PATH: ["Story 132.6", str(CONTRACT_PATH), CHECKER_COMMAND],
        ARTIFACT_PATH: ["Story 132.6", CHECKER_COMMAND, str(CONTRACT_PATH)],
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
                line for line in text.splitlines() if "epic-132" in line or "132-6" in line
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
                            "horizontal scaling live activation overclaim is forbidden",
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
    with tempfile.TemporaryDirectory(prefix="horizontal-scaling-readiness-") as tmp:
        root = Path(tmp)
        _copy_live_fixture(root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        data["production_activation"] = "active"
        _write_contract(root, data)
        if not any("production_activation" in v.message for v in validate(root)):
            print("self-test failed to detect live activation", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        data["production_activation"] = "deferred"
        cast("dict[str, Any]", data["scale_safety_matrix"]).pop("registry-state")
        _write_contract(root, data)
        if not any("registry-state" in v.message for v in validate(root)):
            print("self-test failed to detect missing registry-state service", file=sys.stderr)
            return 1

        data = _load_json(root, CONTRACT_PATH)
        cast("dict[str, Any]", data["scale_safety_matrix"])["registry-state"] = {
            "class": "singleton_required",
            "reason": "One mutable registry-state writer/materializer remains single-owner.",
            "limit_note": "Maximum one mutable writer/materializer and one Alembic migration runner.",
        }
        data["non_goals"] = ["no live horizontal scaling activation"]
        _write_contract(root, data)
        if not any("non_goals" in v.message for v in validate(root)):
            print("self-test failed to detect missing non-goals", file=sys.stderr)
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
        print("Story 132.6 horizontal scaling readiness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
