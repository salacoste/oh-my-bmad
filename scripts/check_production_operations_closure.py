#!/usr/bin/env python3
"""Validate the Story 131.6 production-operations readiness closure.

This static gate closes Epic 131 as a readiness-contract track, not as live
production activation. It verifies the prior Story 131 gates are present and CI
wired, and that status docs keep real GitHub writes, deployments, command
surfaces, runtime audit emitters, and retention jobs explicitly fail-closed.
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
CONTRACT_PATH = Path("docs/production-operations-closure-readiness.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/131-6-production-operations-readiness-closure.md"
)
CI_PATH = Path(".github/workflows/ci.yml")
JUSTFILE_PATH = Path("justfile")
REQUIRED_STORY_KEYS = frozenset({"131.1", "131.2", "131.3", "131.4", "131.5"})
REQUIRED_CI_GATES = frozenset(
    {
        "scripts/check_production_credentials.py",
        "scripts/check_github_write_activation.py",
        "scripts/check_deployment_change_readiness.py",
        "scripts/check_production_command_surface.py",
        "scripts/check_production_operations_closure.py",
    }
)
REQUIRED_FAIL_CLOSED_STATEMENTS = frozenset(
    {
        "real GitHub writes are not enabled",
        "live deployment changes are not enabled",
        "live command surfaces are not enabled",
        "runtime production audit emitters are not enabled",
        "retention jobs are not enabled",
    }
)
STORY_STATUS_KEYS = frozenset(
    {
        "131-1-production-operations-runbook-and-preflight-contract: done",
        "131-2-credential-provisioning-scoping-rotation-revocation: done",
        "131-3-github-write-activation-readiness: done",
        "131-4-deployment-change-control-readiness: done",
        "131-5-production-command-surface-readiness: done",
        "131-6-production-operations-readiness-closure: done",
    }
)
FORBIDDEN_OVERCLAIMS = (
    re.compile(r"real GitHub writes (are|were) (enabled|activated|implemented|shipped)", re.I),
    re.compile(r"live deployment changes (are|were) (enabled|activated|implemented|shipped)", re.I),
    re.compile(r"live command surfaces (are|were) (enabled|activated|implemented|shipped)", re.I),
    re.compile(
        r"runtime production audit emitters (are|were) (enabled|activated|implemented|shipped)",
        re.I,
    ),
    re.compile(r"retention jobs (are|were) (enabled|activated|implemented|shipped)", re.I),
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
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
    if data.get("story") != "131.6":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 131.6"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    if data.get("mode") != "static_readiness_closure_only":
        violations.append(
            Violation(str(CONTRACT_PATH), "mode must be static_readiness_closure_only")
        )
    if data.get("epic_closure") != "readiness_contract_complete_not_live_activation":
        violations.append(Violation(str(CONTRACT_PATH), "epic_closure overclaims activation"))
    stories = data.get("required_story_evidence")
    if not isinstance(stories, list):
        violations.append(Violation(str(CONTRACT_PATH), "required_story_evidence must be a list"))
        stories = []
    story_ids = {str(entry.get("story")) for entry in stories if isinstance(entry, dict)}
    missing_stories = REQUIRED_STORY_KEYS - story_ids
    if missing_stories:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required_story_evidence missing {sorted(missing_stories)}"
            )
        )
    for idx, entry in enumerate(stories):
        if not isinstance(entry, dict):
            violations.append(
                Violation(f"{CONTRACT_PATH}:required_story_evidence[{idx}]", "must be object")
            )
            continue
        for key in ("artifact", "checker", "contract", "docs_ref"):
            rel = entry.get(key)
            if rel is None:
                continue
            if not isinstance(rel, str) or not (root / rel).exists():
                violations.append(
                    Violation(
                        f"{CONTRACT_PATH}:required_story_evidence[{idx}]",
                        f"{key} path missing or invalid: {rel}",
                    )
                )
    gates = set(data.get("required_ci_gates", []))
    missing_gates = REQUIRED_CI_GATES - gates
    if missing_gates:
        violations.append(
            Violation(str(CONTRACT_PATH), f"required_ci_gates missing {sorted(missing_gates)}")
        )
    statements = set(data.get("required_fail_closed_statements", []))
    missing_statements = REQUIRED_FAIL_CLOSED_STATEMENTS - statements
    if missing_statements:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"required_fail_closed_statements missing {sorted(missing_statements)}",
            )
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


def _validate_gate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    ci = _read(root, CI_PATH)
    justfile = _read(root, JUSTFILE_PATH)
    for gate in sorted(REQUIRED_CI_GATES):
        if gate not in ci:
            violations.append(Violation(str(CI_PATH), f"CI missing gate {gate}"))
        if f"{gate} --self-test" not in ci:
            violations.append(Violation(str(CI_PATH), f"CI missing self-test for {gate}"))
        if gate not in justfile:
            violations.append(Violation(str(JUSTFILE_PATH), f"justfile missing gate {gate}"))
        if f"{gate} --self-test" not in justfile:
            violations.append(
                Violation(str(JUSTFILE_PATH), f"justfile missing self-test for {gate}")
            )
    return violations


def _validate_status_docs(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    sprint = _read(root, SPRINT_STATUS_PATH)
    feature = _read(root, FEATURE_STATUS_PATH)
    prod = _read(root, PRODUCTION_OPS_PATH)
    operator = _read(root, OPERATOR_RUNBOOK_PATH)
    artifact = _read(root, ARTIFACT_PATH)
    if "epic-131: done" not in sprint:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "epic-131 must be marked done"))
    for key in sorted(STORY_STATUS_KEYS):
        if key not in sprint:
            violations.append(Violation(str(SPRINT_STATUS_PATH), f"missing status key {key}"))
    feature_lower = feature.lower()
    for needle in (
        "Story 131.6",
        "readiness closure",
        "not live production activation",
    ):
        if needle not in feature:
            violations.append(Violation(str(FEATURE_STATUS_PATH), f"missing {needle!r}"))
    for needle in (
        "real github writes are not enabled",
        "live deployment changes are not enabled",
        "live command surfaces are not enabled",
        "runtime production audit emitters are not enabled",
        "retention jobs are not enabled",
    ):
        if needle not in feature_lower:
            violations.append(Violation(str(FEATURE_STATUS_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 131.6",
        "docs/production-operations-closure-readiness.json",
        "scripts/check_production_operations_closure.py",
        "not live production activation",
    ):
        if needle not in prod:
            violations.append(Violation(str(PRODUCTION_OPS_PATH), f"missing {needle!r}"))
    for needle in (
        "Production operations readiness closure (Story 131.6)",
        "uv run python scripts/check_production_operations_closure.py",
        "readiness closure only",
    ):
        if needle not in operator:
            violations.append(Violation(str(OPERATOR_RUNBOOK_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 131.6",
        "scripts/check_production_operations_closure.py",
        "static/readiness closure only",
    ):
        if needle not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"missing {needle!r}"))
    return violations


def _validate_no_overclaim(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in (
        FEATURE_STATUS_PATH,
        PRODUCTION_OPS_PATH,
        OPERATOR_RUNBOOK_PATH,
        SPRINT_STATUS_PATH,
    ):
        text = _read(root, relpath)
        for pattern in FORBIDDEN_OVERCLAIMS:
            match = pattern.search(text)
            if match:
                violations.append(
                    Violation(str(relpath), f"live production overclaim: {match.group(0)!r}")
                )
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_json(root, CONTRACT_PATH)
    return [
        *_validate_contract(root, data),
        *_validate_gate_wiring(root),
        *_validate_status_docs(root),
        *_validate_no_overclaim(root),
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
        FEATURE_STATUS_PATH,
        PRODUCTION_OPS_PATH,
        OPERATOR_RUNBOOK_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        CI_PATH,
        JUSTFILE_PATH,
        Path("docs/production-credential-inventory.json"),
        Path("docs/github-write-activation-readiness.json"),
        Path("docs/deployment-change-readiness.json"),
        Path("docs/production-command-surface-readiness.json"),
        Path("scripts/check_production_credentials.py"),
        Path("scripts/check_github_write_activation.py"),
        Path("scripts/check_deployment_change_readiness.py"),
        Path("scripts/check_production_command_surface.py"),
        Path(
            "_bmad-output/implementation-artifacts/131-1-production-operations-runbook-preflight-contract.md"
        ),
        Path(
            "_bmad-output/implementation-artifacts/131-2-credential-provisioning-scoping-rotation-revocation.md"
        ),
        Path("_bmad-output/implementation-artifacts/131-3-github-write-activation-readiness.md"),
        Path("_bmad-output/implementation-artifacts/131-4-deployment-change-control-readiness.md"),
        Path("_bmad-output/implementation-artifacts/131-5-production-command-surface-readiness.md"),
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
        feature = root / FEATURE_STATUS_PATH
        feature.write_text(
            feature.read_text(encoding="utf-8")
            + "\nReal GitHub writes are enabled for production.\n",
            encoding="utf-8",
        )
        bad = validate(root)
        if not any("overclaim" in v.message for v in bad):
            print("self-test overclaim fixture did not fail", file=sys.stderr)
            return 1
    print("✓ check_production_operations_closure.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_production_operations_closure.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_production_operations_closure.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_json(REPO_ROOT, CONTRACT_PATH)
        print(
            "✓ production operations closure readiness OK "
            f"({len(data.get('required_story_evidence', []))} prerequisite story record(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
