#!/usr/bin/env python3
"""Validate the Story 130.1 retention policy/object-storage readiness contract.

This is a static/readiness gate. It verifies that retention policy and adapter
semantics are explicit before any scheduled job, object-storage delete/transition,
backup pruning, archive mutation, credential loading, or runtime audit emitter is
introduced.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/retention-policy-object-storage-readiness.json")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
PLANNING_PATH = Path("_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/130-1-retention-policy-object-storage-adapter-contract.md"
)
CI_PATH = Path(".github/workflows/ci.yml")
JUSTFILE_PATH = Path("justfile")

REQUIRED_POLICY_FIELDS = frozenset(
    {
        "policy_id",
        "policy_version",
        "owner",
        "authority_source",
        "object_domain",
        "object_identity_schema",
        "retention_window",
        "hold_and_exclusion_rules",
        "dry_run_required",
        "apply_requires_future_story",
        "rollback_or_recovery_reference",
        "clock_semantics",
        "eventual_consistency_semantics",
        "audit_event_shape",
    }
)
REQUIRED_IDENTITY_FIELDS = frozenset(
    {
        "domain",
        "manifest_ref",
        "object_key",
        "version_or_generation",
        "etag_or_checksum",
        "size_bytes",
        "created_at_utc",
        "last_modified_at_utc",
        "storage_class",
        "hold_refs",
    }
)
REQUIRED_EVIDENCE = frozenset(
    {
        "retention windows are explicit per object domain and default to fail closed when missing",
        "legal hold and operator exclusion rules override deletion or transition",
        "object identity is manifest-backed and includes checksum etag size and version or generation evidence",
        "dry-run is mandatory before any apply story can consume retention evidence",
        "apply mode requires future Story 130.2 through 130.4 evidence and approval",
        "clock semantics use UTC timestamps and reject ambiguous stale or future-dated evidence",
        "eventual consistency ambiguity blocks mutation until fresh head/list evidence is available",
        "adapter responses are metadata-only and never contain credentials or secret values",
        "scheduled jobs remain disabled until a later implementation story adds lock and idempotency evidence",
    }
)
REQUIRED_FAIL_CLOSED = frozenset(
    {
        "missing or unknown retention policy id",
        "missing data owner or authority source",
        "missing manifest reference or checksum evidence",
        "duplicate overlapping or prefix-only object identity",
        "active legal hold operator exclusion or backup coverage gap",
        "stale dry-run evidence or clock skew outside the documented tolerance",
        "eventual-consistency stale-read ambiguity",
        "adapter lacks checksum etag version generation or idempotency evidence",
        "missing rollback recovery or restore reference",
        "attempted apply delete transition backup pruning archive mutation or scheduled job activation in Story 130.1",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no scheduled retention job runner",
        "no object storage deletion or transition",
        "no archive or lifecycle manifest mutation",
        "no backup pruning",
        "no external object storage calls",
        "no production credential loading",
        "no runtime audit emitter",
        "no dashboard command surface or registry mutation endpoint",
    }
)
REQUIRED_ADAPTER_CAPABILITIES = frozenset(
    {
        "list by manifest identity with bounded page size",
        "head object with checksum or etag verification",
        "dry-run transition/delete plan without mutation",
        "idempotency key support or deterministic duplicate-safe response handling",
        "explicit eventual-consistency stale-read classification",
        "metadata-only audit response without credential material",
    }
)
FORBIDDEN_NEW_PATHS = (
    Path("scripts/run_retention_job.py"),
    Path("scripts/apply_object_storage_retention.py"),
    Path("scripts/prune_object_storage.py"),
    Path("services/registry-api/src/registry_api/routes/retention.py"),
    Path("services/registry-api/src/registry_api/routes/object_storage_lifecycle.py"),
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
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
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must be a JSON object")
    return data


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _walk_strings(k)
            yield from _walk_strings(v)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_strings(item)


def _contains_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS)


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("story") != "130.1":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 130.1"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    if data.get("mode") != "static_policy_contract_only":
        violations.append(Violation(str(CONTRACT_PATH), "mode must be static_policy_contract_only"))
    if data.get("operation_class") != "lifecycle_retention":
        violations.append(
            Violation(str(CONTRACT_PATH), "operation_class must be lifecycle_retention")
        )
    if data.get("epic") != "130":
        violations.append(Violation(str(CONTRACT_PATH), "epic must be 130"))

    policy_fields = set(data.get("policy_required_fields", []))
    missing_policy = REQUIRED_POLICY_FIELDS - policy_fields
    if missing_policy:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"policy_required_fields missing {sorted(missing_policy)}"
            )
        )

    identity = data.get("object_identity_schema")
    if not isinstance(identity, dict):
        violations.append(Violation(str(CONTRACT_PATH), "object_identity_schema must be an object"))
    else:
        identity_fields = set(identity.get("required_identity_fields", []))
        missing_identity = REQUIRED_IDENTITY_FIELDS - identity_fields
        if missing_identity:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"required_identity_fields missing {sorted(missing_identity)}",
                )
            )
        forbidden_sources = "\n".join(
            str(x) for x in identity.get("forbidden_identity_sources", [])
        )
        for phrase in ("wildcard bucket deletion", "prefix-only deletion", "credential"):
            if phrase not in forbidden_sources:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"forbidden_identity_sources missing {phrase!r}")
                )

    adapter = data.get("adapter_contract")
    if not isinstance(adapter, dict):
        violations.append(Violation(str(CONTRACT_PATH), "adapter_contract must be an object"))
    else:
        caps = set(adapter.get("required_capabilities_before_apply", []))
        missing_caps = REQUIRED_ADAPTER_CAPABILITIES - caps
        if missing_caps:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"required_capabilities_before_apply missing {sorted(missing_caps)}",
                )
            )
        modes = set(adapter.get("supported_modes_in_this_story", []))
        if modes != {"static_contract", "dry_run_contract_only"}:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    "supported_modes_in_this_story must be static_contract and dry_run_contract_only",
                )
            )
        unsupported = "\n".join(str(x) for x in adapter.get("unsupported_modes_in_this_story", []))
        for phrase in (
            "scheduled retention job runner",
            "object storage delete",
            "external storage credential loading",
        ):
            if phrase not in unsupported:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"unsupported_modes missing {phrase!r}")
                )

    evidence = set(data.get("required_evidence", []))
    missing_evidence = REQUIRED_EVIDENCE - evidence
    if missing_evidence:
        violations.append(
            Violation(str(CONTRACT_PATH), f"required_evidence missing {sorted(missing_evidence)}")
        )
    fail_closed = set(data.get("fail_closed_conditions", []))
    missing_fail = REQUIRED_FAIL_CLOSED - fail_closed
    if missing_fail:
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail_closed_conditions missing {sorted(missing_fail)}")
        )
    non_goals = set(data.get("non_goals", []))
    missing_non_goals = REQUIRED_NON_GOALS - non_goals
    if missing_non_goals:
        violations.append(
            Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing_non_goals)}")
        )

    for ref in data.get("docs_refs", []):
        if not isinstance(ref, str):
            continue
        path_part = ref.split("#", 1)[0]
        if path_part and not (root / path_part).exists():
            violations.append(Violation(str(CONTRACT_PATH), f"docs_ref does not exist: {ref}"))

    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract appears to contain a real credential value")
            )
            break
    return violations


def _validate_docs(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    prod = _read(root, PRODUCTION_OPS_PATH)
    operator = _read(root, OPERATOR_RUNBOOK_PATH)
    feature = _read(root, FEATURE_STATUS_PATH)
    sprint = _read(root, SPRINT_STATUS_PATH)
    artifact = _read(root, ARTIFACT_PATH)
    planning = _read(root, PLANNING_PATH)

    for needle in (
        "Story 130.1",
        "docs/retention-policy-object-storage-readiness.json",
        "scripts/check_retention_policy_readiness.py",
        "Scheduled retention jobs remain disabled",
        "no object-storage deletion or transition",
    ):
        if needle not in prod:
            violations.append(Violation(str(PRODUCTION_OPS_PATH), f"missing {needle!r}"))
    for needle in (
        "Retention policy and object-storage adapter contract (Story 130.1)",
        "uv run python scripts/check_retention_policy_readiness.py",
        "no scheduled retention job runner",
        "no object-storage deletion or transition",
        "no external object storage calls",
    ):
        if needle not in operator:
            violations.append(Violation(str(OPERATOR_RUNBOOK_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 130.1",
        "Retention policy and object-storage adapter contract",
        "Epic 130",
    ):
        if needle not in feature:
            violations.append(Violation(str(FEATURE_STATUS_PATH), f"missing {needle!r}"))
    for needle in (
        "epic-130: in-progress",
        "130-1-retention-policy-and-object-storage-adapter-contract: done",
    ):
        if needle not in sprint:
            violations.append(Violation(str(SPRINT_STATUS_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 130.1",
        "static/readiness-only",
        "scripts/check_retention_policy_readiness.py",
        "does not add a scheduler",
    ):
        if needle not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"missing {needle!r}"))
    if "Story 130.1: Retention Policy and Object-Storage Adapter Contract" not in planning:
        violations.append(Violation(str(PLANNING_PATH), "Story 130.1 planning source missing"))
    return violations


def _validate_gate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    ci = _read(root, CI_PATH)
    justfile = _read(root, JUSTFILE_PATH)
    script = "scripts/check_retention_policy_readiness.py"
    for needle in (script, f"{script} --self-test"):
        if needle not in ci:
            violations.append(Violation(str(CI_PATH), f"missing {needle!r}"))
    if justfile.count(script) < 3:
        violations.append(
            Violation(str(JUSTFILE_PATH), f"expected lint/check/self-test wiring for {script}")
        )
    return violations


def _validate_absent_runtime_activation(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in FORBIDDEN_NEW_PATHS:
        if (root / relpath).exists():
            violations.append(
                Violation(
                    str(relpath),
                    "Story 130.1 must not add retention job runner or mutation endpoint files",
                )
            )
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_json(root, CONTRACT_PATH)
    return [
        *_validate_contract(root, data),
        *_validate_docs(root),
        *_validate_gate_wiring(root),
        *_validate_absent_runtime_activation(root),
    ]


def _copy_fixture(root: Path, relpaths: Sequence[Path]) -> None:
    for rel in relpaths:
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _self_test() -> int:
    relpaths = [
        CONTRACT_PATH,
        PRODUCTION_OPS_PATH,
        OPERATOR_RUNBOOK_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        PLANNING_PATH,
        ARTIFACT_PATH,
        CI_PATH,
        JUSTFILE_PATH,
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _copy_fixture(root, relpaths)
        clean = validate(root)
        if clean:
            print("self-test clean fixture unexpectedly failed:", file=sys.stderr)
            for violation in clean:
                print(violation.render(), file=sys.stderr)
            return 1
        contract = root / CONTRACT_PATH
        data = json.loads(contract.read_text(encoding="utf-8"))
        data["policy_required_fields"] = [
            x for x in data["policy_required_fields"] if x != "hold_and_exclusion_rules"
        ]
        contract.write_text(json.dumps(data), encoding="utf-8")
        bad_policy = validate(root)
        if not any("policy_required_fields missing" in v.message for v in bad_policy):
            print("self-test missing policy field fixture did not fail", file=sys.stderr)
            return 1
        data["policy_required_fields"].append("hold_and_exclusion_rules")
        contract.write_text(json.dumps(data), encoding="utf-8")
        forbidden = root / FORBIDDEN_NEW_PATHS[0]
        forbidden.parent.mkdir(parents=True, exist_ok=True)
        forbidden.write_text("print('retention job runner')\n", encoding="utf-8")
        bad_runtime = validate(root)
        if not any("retention job runner" in v.message for v in bad_runtime):
            print("self-test forbidden runtime fixture did not fail", file=sys.stderr)
            return 1
    print("✓ check_retention_policy_readiness.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_retention_policy_readiness.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_retention_policy_readiness.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_json(REPO_ROOT, CONTRACT_PATH)
        print(
            "✓ retention policy/object-storage readiness OK "
            f"({len(data.get('fail_closed_conditions', []))} fail-closed condition(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
