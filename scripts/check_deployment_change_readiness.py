#!/usr/bin/env python3
"""Validate the Story 131.4 deployment change readiness contract.

This gate is intentionally static/readiness-only. It checks that deployment
change control is documented, profile-gated, rollback-aware, and still
fail-closed for live mutation. It must not run docker compose, migrate a
production database, read secrets, or contact remote hosts.

Usage::

    uv run python scripts/check_deployment_change_readiness.py
    uv run python scripts/check_deployment_change_readiness.py --self-test
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
CONTRACT_PATH = Path("docs/deployment-change-readiness.json")
CREDENTIAL_CONTRACT_PATH = Path("docs/production-credential-inventory.json")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
DEPLOYMENT_GUIDE_PATH = Path("docs/deployment-guide.md")
BACKUP_RESTORE_PATH = Path("docs/backup-restore.md")
JUSTFILE_PATH = Path("justfile")
DIGEST_COMPOSE_PATH = Path("docker-compose.digest.yml")
BASE_COMPOSE_PATH = Path("docker-compose.yml")
MACOS_COMPOSE_PATH = Path("docker-compose.macos.yml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/131-4-deployment-change-control-readiness.md"
)
REQUIRED_EVIDENCE = frozenset(
    {
        "release owner approval for the exact deployment profile",
        "environment and configuration diff reviewed before apply",
        "image tags and immutable digests recorded for every deployed first-party service",
        "cosign signature SLSA provenance and SBOM verification evidence",
        "database migration compatibility and backup evidence",
        "secret scope and scanner evidence from Story 131.2",
        "pre-deploy readiness and dependency health checks",
        "rollback profile with previous image digests and config reference",
        "post-deploy health evidence and smoke-test result",
        "freeze-window or emergency-disable decision recorded",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "digest deployment targets depend on verify-images",
        "digest compose overlay fails loud on unset OMB_IMAGE_DIGEST variables",
        "tag-based deploy targets are documented as deprecated for production",
        "rollback documentation names previous digests and health criteria",
        "backup and restore documentation is linked from deployment guidance",
        "restore-from-litestream requires typed destructive confirmation",
        "deployment readiness remains static and does not add a new runtime command surface",
        "contract contains no credential or private-key values",
    }
)
REQUIRED_PROFILES = {
    "vps_digest": {
        "just_target": "deploy-vps-digest",
        "compose_files": {"docker-compose.yml", "docker-compose.digest.yml"},
    },
    "macos_digest": {
        "just_target": "deploy-macos-digest",
        "compose_files": {
            "docker-compose.yml",
            "docker-compose.digest.yml",
            "docker-compose.macos.yml",
        },
    },
}
REQUIRED_DIGEST_SERVICES = frozenset(
    {
        "registry_api",
        "registry_state",
        "telegram_gateway",
        "orchestrator_adapter",
        "worker_wrapper",
        "clawhip_daemon",
    }
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


def _recipe_header_pattern(target: str, dependency: str | None = None) -> re.Pattern[str]:
    if dependency is None:
        return re.compile(rf"^{re.escape(target)}(?::|\s)", re.MULTILINE)
    return re.compile(rf"^{re.escape(target)}:\s+.*\b{re.escape(dependency)}\b", re.MULTILINE)


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("story") != "131.4":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 131.4"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    if data.get("mode") != "static_readiness_only":
        violations.append(Violation(str(CONTRACT_PATH), "mode must be static_readiness_only"))
    if data.get("operation_class") != "deployment_change":
        violations.append(
            Violation(str(CONTRACT_PATH), "operation_class must be deployment_change")
        )

    target = data.get("target_boundary") if isinstance(data.get("target_boundary"), dict) else {}
    if target.get("credential_contract_ref") != str(CREDENTIAL_CONTRACT_PATH):
        violations.append(
            Violation(str(CONTRACT_PATH), "target_boundary must reference Story 131.2 credentials")
        )
    if not (root / CREDENTIAL_CONTRACT_PATH).exists():
        violations.append(
            Violation(str(CREDENTIAL_CONTRACT_PATH), "credential contract is missing")
        )
    if target.get("operation_contract_ref") != str(PRODUCTION_OPS_PATH):
        violations.append(
            Violation(str(CONTRACT_PATH), "target_boundary must reference production operations")
        )

    evidence = set(data.get("required_evidence", []))
    missing_evidence = REQUIRED_EVIDENCE - evidence
    if missing_evidence:
        violations.append(
            Violation(str(CONTRACT_PATH), f"required_evidence missing {sorted(missing_evidence)}")
        )
    checks = set(data.get("fail_closed_checks", []))
    missing_checks = REQUIRED_FAIL_CLOSED_CHECKS - checks
    if missing_checks:
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail_closed_checks missing {sorted(missing_checks)}")
        )

    services = set(data.get("first_party_digest_services", []))
    missing_services = REQUIRED_DIGEST_SERVICES - services
    if missing_services:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"first_party_digest_services missing {sorted(missing_services)}",
            )
        )

    profile_entries = data.get("profiles")
    if not isinstance(profile_entries, list):
        violations.append(Violation(str(CONTRACT_PATH), "profiles must be a list"))
    else:
        seen: set[str] = set()
        for idx, entry in enumerate(profile_entries):
            loc = f"{CONTRACT_PATH}:profiles[{idx}]"
            if not isinstance(entry, dict):
                violations.append(Violation(loc, "profile entry must be an object"))
                continue
            profile_id = entry.get("id")
            if not isinstance(profile_id, str):
                violations.append(Violation(loc, "profile id must be a string"))
                continue
            seen.add(profile_id)
            expected = REQUIRED_PROFILES.get(profile_id)
            if expected is None:
                violations.append(Violation(loc, f"unexpected deployment profile {profile_id!r}"))
                continue
            if entry.get("just_target") != expected["just_target"]:
                violations.append(Violation(loc, "profile just_target mismatch"))
            compose_files = set(entry.get("compose_files", []))
            if compose_files != expected["compose_files"]:
                violations.append(Violation(loc, "profile compose_files mismatch"))
            required_targets = set(entry.get("required_preflight_targets", []))
            for target_name in ("verify-images", "backup", "migrate"):
                if target_name not in required_targets:
                    violations.append(Violation(loc, f"missing preflight target {target_name}"))
            rollback_refs = entry.get("rollback_refs", [])
            if not rollback_refs:
                violations.append(Violation(loc, "rollback_refs must not be empty"))
        if seen != set(REQUIRED_PROFILES):
            violations.append(
                Violation(
                    str(CONTRACT_PATH), f"profiles must be exactly {sorted(REQUIRED_PROFILES)}"
                )
            )

    for ref in data.get("docs_refs", []):
        if not isinstance(ref, str):
            continue
        path_part = ref.split("#", 1)[0]
        if path_part and not (root / path_part).exists():
            violations.append(Violation(str(CONTRACT_PATH), f"docs_ref does not exist: {ref}"))
    non_goals = "\n".join(str(x) for x in data.get("non_goals", []))
    for phrase in (
        "no live docker compose invocation",
        "no migration execution",
        "no new deployment command surface",
    ):
        if phrase not in non_goals:
            violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {phrase!r}"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract appears to contain a real credential value")
            )
            break
    return violations


def _validate_justfile(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    text = _read(root, JUSTFILE_PATH)
    for target in ("deploy-vps-digest", "deploy-macos-digest"):
        if not _recipe_header_pattern(target, "verify-images").search(text):
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{target} must depend on verify-images")
            )
    for target in ("deploy-vps", "deploy-macos"):
        if not _recipe_header_pattern(target).search(text):
            violations.append(Violation(str(JUSTFILE_PATH), f"{target} recipe is missing"))
        if f"tag-based {target} is DEPRECATED for production" not in text:
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{target} must warn it is deprecated for production")
            )
    for target in ("verify-images", "backup", "migrate", "restore-from-litestream"):
        if not _recipe_header_pattern(target).search(text):
            violations.append(Violation(str(JUSTFILE_PATH), f"{target} recipe is missing"))
    for needle in (
        "command -v cosign",
        "[ ! -f .env ]",
        "digest_re='^sha256:[a-f0-9]{64}$'",
        "OMB_GHCR_OWNER",
        "cosign verify",
        "verify-attestation",
    ):
        if needle not in text:
            violations.append(Violation(str(JUSTFILE_PATH), f"verify-images missing {needle!r}"))
    for needle in ("OMB_RESTORE_CONFIRM", "yes-restore", "WARNING: this DESTROYS"):
        if needle not in text:
            violations.append(
                Violation(str(JUSTFILE_PATH), f"restore-from-litestream missing {needle!r}")
            )
    return violations


def _validate_digest_compose(root: Path, services: set[str]) -> list[Violation]:
    violations: list[Violation] = []
    text = _read(root, DIGEST_COMPOSE_PATH)
    digest_expansions = re.findall(r"\$\{(OMB_IMAGE_DIGEST_[A-Za-z0-9_]+)(:[^}]*)?\}", text)
    for env_var, operator in digest_expansions:
        if operator is not None and operator.startswith(":-"):
            violations.append(
                Violation(
                    str(DIGEST_COMPOSE_PATH),
                    f"{env_var} digest ref must not use default fallback ':-'",
                )
            )
    for service in sorted(services):
        env_var = f"OMB_IMAGE_DIGEST_{service}"
        if env_var not in text:
            violations.append(Violation(str(DIGEST_COMPOSE_PATH), f"missing {env_var}"))
        if f"@${{{env_var}:?" not in text:
            violations.append(
                Violation(str(DIGEST_COMPOSE_PATH), f"{env_var} must use fail-loud :? expansion")
            )
    for needle in ("build: !reset null", "docker-compose.digest.yml", "deploy-vps-digest"):
        if needle not in text:
            violations.append(Violation(str(DIGEST_COMPOSE_PATH), f"missing {needle!r}"))
    return violations


def _validate_docs(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    prod = _read(root, PRODUCTION_OPS_PATH)
    operator = _read(root, OPERATOR_RUNBOOK_PATH)
    deployment = _read(root, DEPLOYMENT_GUIDE_PATH)
    backup = _read(root, BACKUP_RESTORE_PATH)
    artifact = _read(root, ARTIFACT_PATH)
    for needle in (
        "Story 131.4",
        "docs/deployment-change-readiness.json",
        "scripts/check_deployment_change_readiness.py",
        "deployment mutations remain fail-closed/deferred",
    ):
        if needle not in prod:
            violations.append(Violation(str(PRODUCTION_OPS_PATH), f"missing {needle!r}"))
    for needle in (
        "Deployment change control readiness (Story 131.4)",
        "uv run python scripts/check_deployment_change_readiness.py",
        "deploy-vps-digest",
        "deploy-macos-digest",
    ):
        if needle not in operator:
            violations.append(Violation(str(OPERATOR_RUNBOOK_PATH), f"missing {needle!r}"))
    if re.search(r"no live docker\s+compose", operator) is None:
        violations.append(Violation(str(OPERATOR_RUNBOOK_PATH), "missing 'no live docker compose'"))
    for needle in ("deploy-vps-digest", "deploy-macos-digest", "Rollback", "backup"):
        if needle not in deployment:
            violations.append(Violation(str(DEPLOYMENT_GUIDE_PATH), f"missing {needle!r}"))
    for needle in ("just backup", "restore", "just deploy-vps-digest", "just deploy-macos-digest"):
        if needle not in backup:
            violations.append(Violation(str(BACKUP_RESTORE_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 131.4",
        "scripts/check_deployment_change_readiness.py",
        "static/readiness-only",
        "does not run docker compose",
    ):
        if needle not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"missing {needle!r}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_json(root, CONTRACT_PATH)
    services = set(data.get("first_party_digest_services", []))
    return [
        *_validate_contract(root, data),
        *_validate_justfile(root),
        *_validate_digest_compose(root, services),
        *_validate_docs(root),
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
        CREDENTIAL_CONTRACT_PATH,
        PRODUCTION_OPS_PATH,
        OPERATOR_RUNBOOK_PATH,
        DEPLOYMENT_GUIDE_PATH,
        BACKUP_RESTORE_PATH,
        JUSTFILE_PATH,
        DIGEST_COMPOSE_PATH,
        BASE_COMPOSE_PATH,
        MACOS_COMPOSE_PATH,
        ARTIFACT_PATH,
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
        digest_compose = root / DIGEST_COMPOSE_PATH
        digest_compose.write_text(
            digest_compose.read_text(encoding="utf-8").replace(
                "${OMB_IMAGE_DIGEST_registry_api:?set OMB_IMAGE_DIGEST_registry_api",
                "${OMB_IMAGE_DIGEST_registry_api:-sha256:0000000000000000000000000000000000000000000000000000000000000000",
                1,
            ),
            encoding="utf-8",
        )
        bad = validate(root)
        if not any("fail-loud" in v.message or "default fallback" in v.message for v in bad):
            print("self-test digest fallback fixture did not fail as expected", file=sys.stderr)
            return 1
    print("✓ check_deployment_change_readiness.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_deployment_change_readiness.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_deployment_change_readiness.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_json(REPO_ROOT, CONTRACT_PATH)
        print(f"✓ deployment change readiness OK ({len(data.get('profiles', []))} profile(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
