#!/usr/bin/env python3
"""Validate Story 135.1 operator-gated activation-smoke fail-closed evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/operator-gated-activation-smoke-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "135-1-operator-gated-split-remote-postgres-db-mtls-activation-smoke.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_operator_gated_activation_smoke.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"
REQUIRED_TOP = frozenset(
    {
        "schema_version",
        "phase",
        "epic",
        "story",
        "mode",
        "activation_boundary",
        "operator_gate",
        "readiness_prerequisites",
        "blocked_smoke_evidence_contract",
        "redaction_and_secret_hygiene",
        "fail_closed_checks",
        "non_goals",
        "status_semantics",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_GATE = frozenset(
    {
        "operator_approval_ref",
        "security_approval_ref",
        "target_environment",
        "target_version",
        "change_window_utc",
        "rollback_owner",
        "rollback_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "independent_reviewer_ref",
        "evidence_retention",
        "evidence_freshness",
        "redaction_statement",
        "redaction_report_ref",
        "approved_secret_location_refs",
        "approved_certificate_location_refs",
    }
)
REQUIRED_REFS = frozenset(
    {
        "_bmad-output/planning-artifacts/phase-51-controlled-activation-epics.md",
        "_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md",
        "docs/controlled-activation-evidence.json",
        "docs/split-deployment-activation-smoke-evidence.json",
        "docs/remote-postgres-activation-smoke-migration-evidence.json",
        "docs/registry-db-mtls-activation-smoke-failure-evidence.json",
        "docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json",
        "docs/split-deployment-remote-postgres-closure-readiness.json",
        "docs/db-mtls-readiness.json",
    }
)
REQUIRED_SPLIT = frozenset(
    {
        "service_placement",
        "network_boundary",
        "registry_state_single_writer_authority",
        "event_log_append_authority",
        "mcp_boundary",
        "operator_dashboard_ingress",
        "health_readiness",
        "rollback_trigger",
        "post_smoke_go_no_go_decision",
    }
)
REQUIRED_REMOTE = frozenset(
    {
        "backup_checkpoint",
        "single_migration_runner",
        "bounded_pool_settings",
        "migration_rollback_fix_forward_decision_points",
        "read_side_compatibility",
        "writer_authority",
        "redacted_endpoint_identity",
        "no_plaintext_fallback",
    }
)
REQUIRED_MTLS = frozenset(
    {
        "registry_db_mtls_enabled_gate_state",
        "server_side_tls_enforcement",
        "client_certificate_enforcement",
        "approved_secret_references_by_identifier_only",
        "certificate_expiry_revocation_hostname_metadata",
        "bounded_failure_diagnostics",
        "no_plaintext_fallback",
    }
)
REQUIRED_CHECKS = frozenset(
    {
        "operator_gate_missing_records_blocked_no_go",
        "missing_evidence_fails_closed",
        "stale_or_ambiguous_evidence_fails_closed",
        "unredacted_evidence_rejected",
        "self_attestation_rejected",
        "secret_like_material_rejected",
        "activation_overclaim_rejected",
        "readiness_as_proof_rejected",
        "migration_execution_overclaim_rejected",
        "plaintext_fallback_rejected",
        "split_deployment_domain_coverage_required",
        "remote_postgres_domain_coverage_required",
        "db_mtls_domain_coverage_required",
        "status_docs_story_135_1_done_fail_closed_required",
        "epic_135_done_fail_closed_required",
        "justfile_and_ci_wiring_required",
    }
)
STATUS_SCAN_PATHS = (CONTRACT_PATH, FEATURE_STATUS_PATH, SPRINT_STATUS_PATH, ARTIFACT_PATH)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(
        r"(?i)(?:^|[^A-Za-z0-9_-])(?:password|passwd|passphrase|secret|token|credential|"
        r"private[_-]?key|api[_-]?key|bearer)['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
    re.compile(
        r"(?i)\b(?:client[_-]?cert|ca[_-]?cert|cert(?:ificate)?|private[_-]?key)"
        r"['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
)
ACTIVATION_OVERCLAIM = (
    re.compile(
        r"\b(?:split[-_ ]deployment|remote\s+postgres|registry\s+db\s+mTLS|db\s+mTLS|"
        r"production\s+activation|activation|cutover|go[- ]live|compose/profile)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done|provisioned|cut\s*over)",
        re.I,
    ),
    re.compile(
        r"\b(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done|provisioned|cut\s*over)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"\b(?:split[-_ ]deployment|remote\s+postgres|registry\s+db\s+mTLS|db\s+mTLS|"
        r"production\s+activation|activation|cutover|go[- ]live|compose/profile)\b",
        re.I,
    ),
)
MIGRATION_OVERCLAIM = (
    re.compile(
        r"\b(?:migration|migrations|database\s+migration)\b(?:\W+\w+){0,6}\W+"
        r"(?:ran|run|executed|performed|completed|applied|successful|succeeded)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:ran|run|executed|performed|completed|applied)\b(?:\W+\w+){0,6}\W+"
        r"\b(?:migration|migrations|database\s+migration)\b",
        re.I,
    ),
)
READINESS_AS_PROOF = (
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproof\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b"
        r"(?:proves?|proved|proven|confirms?|validates?)\s+(?:production\s+)?activation\b",
        re.I,
    ),
)
PLAINTEXT_OVERCLAIM = (
    re.compile(
        r"\bplaintext\s+fallback\b(?:\W+\w+){0,6}\W+"
        r"(?:enabled|active|available|works|accepted|allowed)",
        re.I,
    ),
)
SAFE_CONTEXT = (
    re.compile(
        r"\b(?:no|not|never|without|blocked|no-go|fail[- ]closed|operator[- ]gated|"
        r"deferred|prerequisites only|not proof|not activation|not run|not attempted|"
        r"not claimed|forbidden|rejected|missing|unavailable|absent|remains blocked)\b",
        re.I,
    ),
    re.compile(r"\bstory done means\b.*\brepo_local_fail_closed_no_go\b", re.I),
    re.compile(r"\bneither\b.*\bproof\s+(?:production\s+)?activation\b", re.I),
)


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {self.message}"


def _load_json(root: Path, relpath: Path) -> dict[str, Any]:
    with (root / relpath).open(encoding="utf-8") as f:
        data: object = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must contain a JSON object")
    return cast("dict[str, Any]", data)


def _section(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _slug_heading(line: str) -> str | None:
    match = re.match(r"^#+\s+(.+?)\s*$", line)
    if not match:
        return None
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", match.group(1).lower())).strip("-")


def _validate_ref(root: Path, ref: str) -> list[Violation]:
    path_text, _, fragment = ref.partition("#")
    path = root / path_text
    if not path.exists():
        return [Violation(ref, "referenced file is missing")]
    if fragment:
        text = path.read_text(encoding="utf-8")
        slugs = {_slug_heading(line) for line in text.splitlines()}
        if fragment not in text and fragment.lower() not in slugs:
            return [Violation(ref, "referenced anchor is missing")]
    return []


CONTRAST_PATTERN = re.compile(r"\b(?:but|however|yet|nevertheless|though|although)\b", re.I)
CLAIM_SUCCESS_PATTERN = re.compile(
    r"\b(?:activated|enabled|complete|completed|successful|succeeded|occurred|"
    r"performed|executed|done|provisioned|ran|run|applied|available|works|"
    r"accepted|allowed)\b"
    r"|\b(?:is|was|were|been|becomes?|now)\s+(?:live|active)\b"
    r"|\bserving(?:\s+traffic)?\b",
    re.I,
)


def _match_clause(line: str, match: re.Match[str]) -> tuple[str, int]:
    start = max(line.rfind(".", 0, match.start()), line.rfind(";", 0, match.start())) + 1
    end_candidates = [
        pos for pos in (line.find(".", match.end()), line.find(";", match.end())) if pos != -1
    ]
    end = min(end_candidates) if end_candidates else len(line)
    return line[start:end], start


def _match_is_safe(line: str, match: re.Match[str]) -> bool:
    """Return true only when a specific forbidden-looking match is negated.

    Broad status lines often contain safe boundary language such as "blocked" or
    "no live activation". That safe language must not mask a later
    contradictory clause like "blocked, but activation completed".
    """

    clause, clause_start = _match_clause(line, match)
    before = clause[: max(0, match.start() - clause_start)]
    if CONTRAST_PATTERN.search(before):
        return False
    return any(pattern.search(clause) for pattern in SAFE_CONTEXT)


def _scan_clauses(line: str) -> list[str]:
    clauses: list[str] = []
    for sentence in re.split(r"[.;]", line):
        carry_negative_list = False
        for raw_part in re.split(r"[:,]|\s+-\s+|[–—]", sentence):
            part = raw_part.strip()
            if not part:
                continue
            carries_unsafe_success = CLAIM_SUCCESS_PATTERN.search(part) is not None
            clauses.append(
                f"No {part}" if carry_negative_list and not carries_unsafe_success else part
            )
            if CONTRAST_PATTERN.search(part):
                carry_negative_list = False
            elif re.search(r"\b(?:no|not|never|without)\b", part, re.I):
                carry_negative_list = True
    return clauses


def _has_unsafe_match(line: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    # Scan punctuation-delimited clauses independently so safe negation in one
    # clause cannot mask a contradictory overclaim in the next clause. Negative
    # comma-list items inherit a leading "No" only while they do not contain
    # success/completion/performed wording themselves.
    for clause in _scan_clauses(line):
        for pattern in patterns:
            for match in pattern.finditer(clause):
                if not _match_is_safe(clause, match):
                    return True
    return False


def _scan(relpath: Path, text: str) -> list[Violation]:
    out: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if relpath in (FEATURE_STATUS_PATH, SPRINT_STATUS_PATH) and not re.search(
            r"(?:135|Epic 135|Story 135|operator-gated activation-smoke)", line, re.I
        ):
            continue
        loc = f"{relpath}:{line_no}"
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            out.append(Violation(loc, "secret-like material is forbidden"))
        if _has_unsafe_match(line, ACTIVATION_OVERCLAIM):
            out.append(Violation(loc, "activation overclaim is forbidden"))
        if _has_unsafe_match(line, MIGRATION_OVERCLAIM):
            out.append(Violation(loc, "migration execution overclaim is forbidden"))
        if _has_unsafe_match(line, READINESS_AS_PROOF):
            out.append(Violation(loc, "readiness-as-proof claim is forbidden"))
        if _has_unsafe_match(line, PLAINTEXT_OVERCLAIM):
            out.append(Violation(loc, "plaintext fallback overclaim is forbidden"))
    return out


def _blocked_fail_closed(text: str) -> bool:
    lowered = text.lower()
    return ("fail-closed" in lowered or "fail_closed" in lowered) and (
        "no-go" in lowered or "blocked" in lowered
    )


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"cannot load contract: {exc}")]
    missing_top = REQUIRED_TOP - frozenset(data)
    if missing_top:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required top-level sections missing: {sorted(missing_top)}"
            )
        )
    if (
        data.get("schema_version") != "story-135.1/v1"
        or data.get("phase") != "52"
        or data.get("epic") != "135"
        or data.get("story") != "135.1"
    ):
        violations.append(
            Violation(str(CONTRACT_PATH), "schema/phase/epic/story identifiers are incorrect")
        )
    if "fail_closed" not in str(data.get("mode", "")) or "not_activation" not in str(
        data.get("mode", "")
    ):
        violations.append(
            Violation(str(CONTRACT_PATH), "mode must be fail-closed and not-activation")
        )
    boundary = _section(data, "activation_boundary")
    for flag in (
        "activation_attempted",
        "activation_performed",
        "split_deployment_smoke_performed",
        "remote_postgres_smoke_performed",
        "db_mtls_smoke_performed",
        "migration_execution_performed",
        "provisioning_performed",
        "production_state_changed",
        "runtime_or_deployment_config_changed",
        "dependency_or_lockfile_changed",
        "credential_or_certificate_handling_performed",
        "compose_profile_activation_performed",
        "plaintext_fallback_allowed",
    ):
        if boundary.get(flag) is not False:
            violations.append(
                Violation(f"{CONTRACT_PATH}:activation_boundary.{flag}", "must be false")
            )
    if (
        boundary.get("operator_gated") is not True
        or boundary.get("outcome") != "blocked_no_go_fail_closed"
    ):
        violations.append(
            Violation(
                f"{CONTRACT_PATH}:activation_boundary",
                "must be operator-gated blocked_no_go_fail_closed",
            )
        )
    gate = _section(data, "operator_gate")
    if (
        gate.get("required") is not True
        or gate.get("status") != "missing_blocked_no_go_fail_closed"
    ):
        violations.append(
            Violation(
                f"{CONTRACT_PATH}:operator_gate",
                "must be required and missing_blocked_no_go_fail_closed",
            )
        )
    missing_gate = REQUIRED_GATE - _string_set(gate.get("fields"))
    if missing_gate:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}:operator_gate.fields",
                f"required gate fields missing: {sorted(missing_gate)}",
            )
        )
    prereqs = _section(data, "readiness_prerequisites")
    refs = _string_set(prereqs.get("minimum_refs"))
    missing_refs = REQUIRED_REFS - refs
    if prereqs.get("semantics") != "prerequisites_only_not_activation_proof" or missing_refs:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}:readiness_prerequisites",
                f"prerequisite semantics/refs invalid; missing {sorted(missing_refs)}",
            )
        )
    for ref in refs:
        violations.extend(_validate_ref(root, ref))
    smoke = _section(data, "blocked_smoke_evidence_contract")
    for section_name, required in (
        ("split_deployment_domains", REQUIRED_SPLIT),
        ("remote_postgres_domains", REQUIRED_REMOTE),
        ("db_mtls_domains", REQUIRED_MTLS),
    ):
        domains = _section(smoke, section_name)
        missing = required - frozenset(domains)
        if missing:
            violations.append(
                Violation(
                    f"{CONTRACT_PATH}:{section_name}",
                    f"required domains missing: {sorted(missing)}",
                )
            )
        for domain_name, domain in domains.items():
            if not isinstance(domain, Mapping):
                violations.append(
                    Violation(
                        f"{CONTRACT_PATH}:{section_name}.{domain_name}",
                        "domain must be an object",
                    )
                )
                continue
            if domain.get("required") is not True:
                violations.append(
                    Violation(
                        f"{CONTRACT_PATH}:{section_name}.{domain_name}",
                        "domain required must be true",
                    )
                )
            if domain.get("status") != "blocked_not_run_until_operator_gate":
                violations.append(
                    Violation(
                        f"{CONTRACT_PATH}:{section_name}.{domain_name}",
                        "domain status must be blocked_not_run_until_operator_gate",
                    )
                )
    missing_checks = REQUIRED_CHECKS - _string_set(data.get("fail_closed_checks"))
    if missing_checks:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required fail-closed checks missing: {sorted(missing_checks)}"
            )
        )
    for ref in _string_set(data.get("docs_refs")) | _string_set(data.get("status_refs")):
        violations.extend(_validate_ref(root, ref))
    for relpath in STATUS_SCAN_PATHS:
        try:
            violations.extend(_scan(relpath, (root / relpath).read_text(encoding="utf-8")))
        except OSError as exc:
            violations.append(Violation(str(relpath), f"cannot read status path: {exc}"))
    story_text = (root / ARTIFACT_PATH).read_text(encoding="utf-8")
    sprint_text = (root / SPRINT_STATUS_PATH).read_text(encoding="utf-8")
    feature_text = (root / FEATURE_STATUS_PATH).read_text(encoding="utf-8")
    if "Status: done" not in story_text or not _blocked_fail_closed(story_text):
        violations.append(
            Violation(
                str(ARTIFACT_PATH),
                "Story artifact must be Status: done and state blocked/no-go/fail-closed outcome",
            )
        )
    if (
        "epic-135: done" not in sprint_text
        or "135-1-operator-gated-split-remote-postgres-db-mtls-activation-smoke: done"
        not in sprint_text
        or not _blocked_fail_closed(sprint_text)
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Epic/Story 135 status must be done with blocked/no-go/fail-closed semantics",
            )
        )
    if "Story 135.1" not in feature_text or not _blocked_fail_closed(feature_text):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "feature status must summarize Story 135.1 fail-closed outcome",
            )
        )
    just_text = (root / JUSTFILE_PATH).read_text(encoding="utf-8")
    ci_text = (root / CI_PATH).read_text(encoding="utf-8")
    if CHECKER_COMMAND not in just_text or CHECKER_SELF_TEST_COMMAND not in just_text:
        violations.append(Violation(str(JUSTFILE_PATH), "justfile must wire checker and self-test"))
    if CHECKER_COMMAND not in ci_text or CHECKER_SELF_TEST_COMMAND not in ci_text:
        violations.append(Violation(str(CI_PATH), "CI must wire checker and self-test"))
    return violations


def _copy_live_fixture(tmpdir: Path) -> None:
    for relpath in {
        CONTRACT_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    }:
        target = tmpdir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relpath, target)
    for ref in REQUIRED_REFS:
        target = tmpdir / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / ref, target)


def _self_test() -> int:
    scenarios = (
        (
            "Remote Postgres activation completed successfully.",
            "activation overclaim",
            "activation",
        ),
        (
            "blocked pending approval, but remote Postgres activation completed successfully.",
            "activation overclaim",
            "mixed safe/unsafe activation",
        ),
        (
            "No live activation. Remote Postgres activation completed successfully.",
            "activation overclaim",
            "period-separated activation overclaim",
        ),
        (
            "No live activation; remote Postgres activation completed successfully.",
            "activation overclaim",
            "semicolon-separated activation overclaim",
        ),
        (
            "No live activation, remote Postgres activation completed successfully.",
            "activation overclaim",
            "comma-separated activation overclaim",
        ),
        (
            "No live activation: remote Postgres activation completed successfully.",
            "activation overclaim",
            "colon-separated activation overclaim",
        ),
        (
            "No live activation - remote Postgres activation completed successfully.",
            "activation overclaim",
            "dash-separated activation overclaim",
        ),
        (
            "Remote Postgres activation completed successfully, no live activation claimed.",
            "activation overclaim",
            "trailing-disclaimer activation overclaim",
        ),
        ("database migration executed successfully.", "migration execution overclaim", "migration"),
        ("readiness artifacts are proof of activation.", "readiness-as-proof", "readiness proof"),
        (
            "Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
            "plaintext",
        ),
        ("-----BEGIN PRIVATE KEY-----", "secret-like", "private key"),
        ("postgresql://user:password@example.com/db", "secret-like", "DSN"),
        ("client_cert=/tmp/client.crt", "secret-like", "cert path"),
    )
    with tempfile.TemporaryDirectory(prefix="story-135-1-activation-smoke-") as raw_tmp:
        tmpdir = Path(raw_tmp)
        _copy_live_fixture(tmpdir)
        baseline = validate(tmpdir)
        if baseline:
            print("self-test baseline unexpectedly failed:", file=sys.stderr)
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1
        for unsafe_text, expected, label in scenarios:
            _copy_live_fixture(tmpdir)
            target = tmpdir / FEATURE_STATUS_PATH
            target.write_text(
                target.read_text(encoding="utf-8")
                + f"\nStory 135.1 adversarial check: {unsafe_text}\n",
                encoding="utf-8",
            )
            violations = validate(tmpdir)
            if not any(expected in violation.message for violation in violations):
                print(f"self-test failed: {label} was not rejected", file=sys.stderr)
                return 1
        mutations = (
            (
                lambda d: d["activation_boundary"].__setitem__("activation_performed", True),
                "must be false",
            ),
            (
                lambda d: d["operator_gate"]["fields"].remove("operator_approval_ref"),
                "required gate fields missing",
            ),
            (
                lambda d: d["blocked_smoke_evidence_contract"]["split_deployment_domains"].pop(
                    "service_placement"
                ),
                "required domains missing",
            ),
            (
                lambda d: d["blocked_smoke_evidence_contract"]["split_deployment_domains"][
                    "service_placement"
                ].__setitem__("required", False),
                "domain required must be true",
            ),
        )
        for mutate, expected in mutations:
            _copy_live_fixture(tmpdir)
            data = _load_json(tmpdir, CONTRACT_PATH)
            mutate(data)
            (tmpdir / CONTRACT_PATH).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            if not any(expected in violation.message for violation in validate(tmpdir)):
                print(f"self-test failed: mutation not rejected for {expected}", file=sys.stderr)
                return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run checker self-tests")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1
    print("Story 135.1 operator-gated activation-smoke fail-closed evidence OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
