#!/usr/bin/env python3
"""Validate Story 134.4 registry DB mTLS activation smoke/failure evidence planning."""

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
CONTRACT_PATH = Path("docs/registry-db-mtls-activation-smoke-failure-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PROJECT_OVERVIEW_PATH = Path("docs/project-overview.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "134-4-registry-db-mtls-activation-smoke-failure-evidence-package.md"
)
CLOSURE_ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = (
    "uv run python scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py"
)
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "schema_version",
        "phase",
        "epic",
        "story",
        "mode",
        "activation_boundary",
        "operator_gate",
        "readiness_prerequisites",
        "future_smoke_evidence_contract",
        "fail_closed_checks",
        "redaction_and_secret_hygiene",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_OPERATOR_GATE_FIELDS = frozenset(
    {
        "operator_approval_ref",
        "security_approval_ref",
        "change_window_utc",
        "target_environment",
        "target_version",
        "generated_at_utc",
        "expires_at_utc",
        "registry_db_mtls_profile_ref",
        "server_tls_enforcement_ref",
        "client_certificate_enforcement_ref",
        "approved_secret_location_refs",
        "certificate_metadata_ref",
        "failure_diagnostics_ref",
        "rollback_owner",
        "rollback_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
    }
)
REQUIRED_DOMAINS = frozenset(
    {
        "explicit_operator_gate_enablement",
        "server_tls_enforcement",
        "client_certificate_enforcement",
        "approved_secret_location_identifiers",
        "certificate_expiry_revocation_hostname_metadata",
        "no_plaintext_fallback_behavior",
        "bounded_sanitized_failure_diagnostics",
        "rollback_fail_closed_criteria",
    }
)
REQUIRED_READINESS_REFS = frozenset(
    {
        "docs/db-mtls-readiness.json",
        "docs/controlled-activation-evidence.json",
        "docs/split-deployment-activation-smoke-evidence.json",
        "docs/remote-postgres-activation-smoke-migration-evidence.json",
        "_bmad-output/implementation-artifacts/133-5-db-mtls-closure-evidence.md",
        "_bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md",
        "_bmad-output/implementation-artifacts/134-2-split-deployment-activation-smoke-evidence-package.md",
        "_bmad-output/implementation-artifacts/134-3-remote-postgres-activation-smoke-migration-evidence-package.md",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "missing_evidence_fails_closed",
        "malformed_evidence_fails_closed",
        "stale_evidence_fails_closed",
        "self_attestation_rejected",
        "secret_like_material_rejected",
        "private_key_material_rejected",
        "certificate_material_rejected",
        "credential_value_rejected",
        "activation_overclaim_rejected",
        "plaintext_fallback_overclaim_rejected",
        "readiness_as_proof_rejected",
        "registry_db_mtls_domain_coverage_required",
        "operator_gate_required",
        "server_tls_enforcement_required",
        "client_certificate_enforcement_required",
        "failure_diagnostics_required",
        "rollback_fail_closed_required",
        "justfile_and_ci_wiring_required",
        "status_docs_story_134_4_done_required",
        "epic_134_in_progress_required",
    }
)
REQUIRED_DOC_REFS = frozenset(
    {
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{PROJECT_OVERVIEW_PATH}#status",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_STATUS_REFS = frozenset(
    {f"{SPRINT_STATUS_PATH}#development_status", f"{FEATURE_STATUS_PATH}#current-bmad-status"}
)
STATUS_SCAN_PATHS = (
    CONTRACT_PATH,
    FEATURE_STATUS_PATH,
    PROJECT_OVERVIEW_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = STATUS_SCAN_PATHS

SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
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
        r"(?i)\b(?:cert(?:ificate)?|client[_-]?cert|ca[_-]?cert)['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
)

DB_MTLS_TARGET = (
    r"(?:registry\s+db\s+mTLS|db\s+mTLS|database\s+mTLS|mTLS\s+activation|"
    r"client\s+cert(?:ificate)?\s+enforcement|certificate\s+enforcement|"
    r"server[- ]side\s+TLS\s+(?:enforcement|activation)|TLS/client[- ]cert(?:ificate)?\s+enforcement|"
    r"plaintext\s+fallback|production\s+activation|compose/profile\s+activation)"
)
POSITIVE_STATE = (
    r"(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
    r"performed|executed|serving(?:\s+traffic)?|done|provisioned|proves?|proved|proven)"
)
ACTIVATION_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{DB_MTLS_TARGET}\b(?:\W+\w+){{0,8}}\W+\b{POSITIVE_STATE}\b", re.I),
)
READINESS_AS_PROOF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproof\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b"
        r"(?:proves?|proved|proven)\s+(?:(?:registry\s+)?db\s+mTLS\s+)?activation\b",
        re.I,
    ),
)
PLAINTEXT_FALLBACK_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bplaintext\s+fallback\b(?:\W+\w+){0,6}\W+\b(?:enabled|active|available|works|accepted|allowed)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:enabled|active|available|works|accepted|allowed)\b(?:\W+\w+){0,6}\W+\bplaintext\s+fallback\b",
        re.I,
    ),
)
SELF_ATTESTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bself[-_ ](?:attestation|attested|review)\b.*\b(?:sufficient|accepted|approved)\b", re.I
    ),
    re.compile(r"\bimplementer[-_ ]review\b.*\b(?:sufficient|accepted|approved)\b", re.I),
)
SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:no|without)\b.*\b(?:activation|production\s+activation|real\s+certificate\s+material|"
        r"private\s+key\s+material|credential\s+values?|provisioning|host\s+mutation|"
        r"compose/profile\s+activation|plaintext\s+fallback)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:not|never)\s+(?:proof\s+(?:that\s+)?activation\s+occurred|"
        r"proof\s+of\s+(?:registry\s+db\s+mTLS\s+)?activation)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:future/operator-gated|operator[- ]gated|deferred|fail[- ]closed|planning\s+only|"
        r"evidence\s+planning|static|docs/status|checker|contract|local(?:ly)?|readiness[- ]only)\b",
        re.I,
    ),
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites).*\bnot\s+proof\b", re.I),
    re.compile(
        r"\b(?:is|are|remain|remains|stays)\s+(?:deferred|fail[- ]closed|operator[- ]gated)\b", re.I
    ),
    re.compile(r"\bnot\s+live\s+(?:production\s+)?activation\b", re.I),
    re.compile(r"\bwhile\b.*\b(?:deferred|operator[- ]gated|fail[- ]closed)\b", re.I),
)
SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN = re.compile(
    r"\b(?:current_phase|epic-134|story[- ]134(?:\.\d+)?|134(?:\.\d+|-[a-z0-9][a-z0-9-]*)|Epic\s+134)\b",
    re.I,
)
SPRINT_STATUS_EPIC_134_SECTION_PATTERN = re.compile(r"^\s*#\s*Epic\s+134\b", re.I)
SPRINT_STATUS_EPIC_SECTION_PATTERN = re.compile(r"^\s*#\s*Epic\s+\d+(?:\.\d+)?\b", re.I)
SPRINT_STATUS_AUDIT_TRAIL_PATTERN = re.compile(r"^audit_trail:\s*$")
SPRINT_STATUS_AUDIT_ITEM_PATTERN = re.compile(r"^  -\s+")
SPRINT_STATUS_BLOCK_SCALAR_PATTERN = re.compile(r"^(?P<indent>\s*)[A-Za-z0-9_]+:\s*[>|]-?\s*$")


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
        raise ValueError(f"{relpath} must contain a JSON object")
    return cast("dict[str, Any]", data)


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _section(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_strings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from _walk_strings(child)


def _contains_secret_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS)


def _slug_heading(line: str) -> str | None:
    match = re.match(r"^#+\s+(?P<title>.+?)\s*$", line)
    if not match:
        return None
    title = match.group("title").strip().lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title)).strip("-")


def _validate_ref_target(root: Path, ref: str) -> list[Violation]:
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


def _is_safe_context(line: str) -> bool:
    return any(pattern.search(line) for pattern in SAFE_CONTEXT_PATTERNS)


def _is_relevant_sprint_status_line(line: str, *, in_epic_134_section: bool) -> bool:
    return in_epic_134_section or bool(SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN.search(line))


def _iter_relevant_status_lines(path: Path, text: str) -> Iterable[tuple[int, str]]:
    if path != SPRINT_STATUS_PATH:
        for lineno, line in enumerate(text.splitlines(), start=1):
            yield lineno, line
        return

    in_epic_134_section = False
    in_audit_trail = False
    audit_item_relevant = False
    block_scalar_indent: int | None = None
    block_scalar_relevant = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SPRINT_STATUS_EPIC_134_SECTION_PATTERN.search(line):
            in_epic_134_section = True
        elif SPRINT_STATUS_EPIC_SECTION_PATTERN.search(
            line
        ) and not SPRINT_STATUS_EPIC_134_SECTION_PATTERN.search(line):
            in_epic_134_section = False
        if SPRINT_STATUS_AUDIT_TRAIL_PATTERN.match(line):
            in_audit_trail = True
            audit_item_relevant = False
            block_scalar_indent = None
            block_scalar_relevant = False
        if in_audit_trail and SPRINT_STATUS_AUDIT_ITEM_PATTERN.match(line):
            audit_item_relevant = bool(SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN.search(line))
            block_scalar_indent = None
            block_scalar_relevant = False
        block_match = SPRINT_STATUS_BLOCK_SCALAR_PATTERN.match(line)
        if block_match and (in_epic_134_section or audit_item_relevant):
            block_scalar_indent = len(block_match.group("indent"))
            block_scalar_relevant = (
                bool(SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN.search(line)) or audit_item_relevant
            )
        elif block_scalar_indent is not None:
            current_indent = len(line) - len(line.lstrip(" "))
            if line.strip() and current_indent <= block_scalar_indent:
                block_scalar_indent = None
                block_scalar_relevant = False
        relevant = _is_relevant_sprint_status_line(line, in_epic_134_section=in_epic_134_section)
        relevant = relevant or audit_item_relevant or block_scalar_relevant
        if relevant:
            yield lineno, line


def _has_story_134_6_planning_closure(root: Path) -> bool:
    closure_path = root / CLOSURE_ARTIFACT_PATH
    if not closure_path.exists():
        return False
    closure_text = _read(root, CLOSURE_ARTIFACT_PATH)
    return all(
        phrase in closure_text
        for phrase in ("planning-only/docs-status", "not activation", "future/operator-gated")
    )


def _validate_status_language(root: Path, violations: list[Violation]) -> None:
    for relpath in STATUS_SCAN_PATHS:
        text = _read(root, relpath)
        for lineno, line in _iter_relevant_status_lines(relpath, text):
            if _is_safe_context(line):
                continue
            for pattern in ACTIVATION_OVERCLAIM_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "registry DB mTLS activation overclaim")
                    )
                    break
            for pattern in PLAINTEXT_FALLBACK_OVERCLAIM_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "plaintext fallback overclaim")
                    )
                    break
            for pattern in READINESS_AS_PROOF_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "readiness-as-proof overclaim")
                    )
                    break
            for pattern in SELF_ATTESTATION_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "self-attestation cannot satisfy evidence")
                    )
                    break


def _validate_secret_absence(root: Path, violations: list[Violation]) -> None:
    for relpath in SECRET_SCAN_PATHS:
        text = _read(root, relpath)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _contains_secret_value(line):
                violations.append(
                    Violation(
                        f"{relpath}:{lineno}",
                        "secret-like or certificate value material is forbidden",
                    )
                )
                break


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"malformed or missing contract: {exc}")]

    missing_top = REQUIRED_TOP_LEVEL_SECTIONS - set(data)
    if missing_top:
        violations.append(
            Violation(str(CONTRACT_PATH), f"top-level sections missing: {sorted(missing_top)}")
        )

    if data.get("schema_version") != "story-134.4/v1":
        violations.append(Violation(str(CONTRACT_PATH), "schema_version must be story-134.4/v1"))
    if data.get("phase") != "51" or data.get("epic") != "134" or data.get("story") != "134.4":
        violations.append(
            Violation(str(CONTRACT_PATH), "phase/epic/story identifiers must be 51/134/134.4")
        )

    boundary = _section(data, "activation_boundary")
    required_false = ("activation_performed",)
    for key in required_false:
        if boundary.get(key) is not False:
            violations.append(Violation(f"{CONTRACT_PATH}#{key}", f"{key} must be false"))
    required_true = (
        "no_registry_db_mtls_activation",
        "no_real_certificate_material",
        "no_private_key_material",
        "no_plaintext_fallback",
        "operator_gated",
        "future_evidence_only",
        "readiness_prerequisites_are_not_activation_proof",
    )
    for key in required_true:
        if boundary.get(key) is not True:
            violations.append(Violation(f"{CONTRACT_PATH}#{key}", f"{key} must be true"))

    operator_gate = _section(data, "operator_gate")
    if operator_gate.get("required") is not True:
        violations.append(
            Violation(f"{CONTRACT_PATH}#operator_gate", "operator gate must be required")
        )
    missing_gate_fields = REQUIRED_OPERATOR_GATE_FIELDS - _string_set(operator_gate.get("fields"))
    if missing_gate_fields:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#operator_gate.fields",
                f"operator gate fields missing: {sorted(missing_gate_fields)}",
            )
        )

    prerequisites = _section(data, "readiness_prerequisites")
    if prerequisites.get("semantics") != "prerequisites_only_not_activation_proof":
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#readiness_prerequisites",
                "readiness prerequisites must be marked prerequisites-only",
            )
        )
    missing_prereqs = REQUIRED_READINESS_REFS - _string_set(prerequisites.get("minimum_refs"))
    if missing_prereqs:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#readiness_prerequisites.minimum_refs",
                f"readiness refs missing: {sorted(missing_prereqs)}",
            )
        )

    contract = _section(data, "future_smoke_evidence_contract")
    domains = _section(contract, "required_domains")
    missing_domains = REQUIRED_DOMAINS - set(domains)
    if missing_domains:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#future_smoke_evidence_contract.required_domains",
                f"required smoke evidence domains missing: {sorted(missing_domains)}",
            )
        )
    for domain in REQUIRED_DOMAINS & set(domains):
        value = _section(domains, domain)
        if value.get("required") is not True:
            violations.append(Violation(f"{CONTRACT_PATH}#{domain}", "domain must be required"))
        if value.get("not_activation_proof_by_itself") is not True:
            violations.append(
                Violation(
                    f"{CONTRACT_PATH}#{domain}", "domain must not be activation proof by itself"
                )
            )
        minimum_evidence = value.get("minimum_evidence")
        if (
            not isinstance(minimum_evidence, Sequence)
            or isinstance(minimum_evidence, (str, bytes, bytearray))
            or len(minimum_evidence) < 3
        ):
            violations.append(
                Violation(
                    f"{CONTRACT_PATH}#{domain}",
                    "domain needs at least three minimum evidence items",
                )
            )

    missing_checks = REQUIRED_FAIL_CLOSED_CHECKS - _string_set(data.get("fail_closed_checks"))
    if missing_checks:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#fail_closed_checks",
                f"fail-closed checks missing: {sorted(missing_checks)}",
            )
        )

    missing_doc_refs = REQUIRED_DOC_REFS - _string_set(data.get("docs_refs"))
    missing_status_refs = REQUIRED_STATUS_REFS - _string_set(data.get("status_refs"))
    if missing_doc_refs:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#docs_refs", f"docs refs missing: {sorted(missing_doc_refs)}"
            )
        )
    if missing_status_refs:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#status_refs",
                f"status refs missing: {sorted(missing_status_refs)}",
            )
        )
    for ref in sorted(
        _string_set(data.get("docs_refs"))
        | _string_set(data.get("status_refs"))
        | _string_set(prerequisites.get("minimum_refs"))
    ):
        violations.extend(_validate_ref_target(root, ref))

    forbidden_material = _string_set(
        _section(data, "redaction_and_secret_hygiene").get("forbidden_material")
    )
    for required in (
        "credential values",
        "private key material",
        "certificate bodies",
        "plaintext fallback evidence",
    ):
        if required not in forbidden_material:
            violations.append(
                Violation(
                    f"{CONTRACT_PATH}#redaction_and_secret_hygiene",
                    f"forbidden material missing: {required}",
                )
            )

    all_contract_text = "\n".join(_walk_strings(data)).lower()
    for required_text in (
        "server-side tls",
        "client certificate",
        "expiry",
        "revocation",
        "hostname",
        "no-plaintext fallback",
        "bounded sanitized failure diagnostics",
        "rollback",
        "fail-closed",
    ):
        if required_text not in all_contract_text:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"required DB mTLS evidence language missing: {required_text}",
                )
            )

    _validate_status_language(root, violations)
    _validate_secret_absence(root, violations)

    just_text = _read(root, JUSTFILE_PATH)
    if CHECKER_COMMAND not in just_text:
        violations.append(Violation(str(JUSTFILE_PATH), "checker command missing from justfile"))
    if CHECKER_SELF_TEST_COMMAND not in just_text:
        violations.append(
            Violation(str(JUSTFILE_PATH), "checker self-test command missing from justfile")
        )
    ci_text = _read(root, CI_PATH)
    if CHECKER_COMMAND not in ci_text:
        violations.append(Violation(str(CI_PATH), "checker command missing from CI"))
    if CHECKER_SELF_TEST_COMMAND not in ci_text:
        violations.append(Violation(str(CI_PATH), "checker self-test command missing from CI"))

    feature_text = _read(root, FEATURE_STATUS_PATH)
    project_text = _read(root, PROJECT_OVERVIEW_PATH)
    sprint_text = _read(root, SPRINT_STATUS_PATH)
    if "Story 134.4" not in feature_text or "registry DB mTLS" not in feature_text:
        violations.append(
            Violation(str(FEATURE_STATUS_PATH), "Story 134.4 DB mTLS status summary missing")
        )
    if "Story 134.4" not in project_text or "registry DB mTLS" not in project_text:
        violations.append(
            Violation(str(PROJECT_OVERVIEW_PATH), "Story 134.4 project overview summary missing")
        )
    if "134-4-registry-db-mtls-activation-smoke-failure-evidence-package: done" not in sprint_text:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.4 must be marked done"))
    epic_134 = re.search(r"(?m)^\s*epic-134:\s*(?P<status>\S+)", sprint_text)
    epic_134_status = epic_134.group("status") if epic_134 else None
    if epic_134_status != "in-progress" and not (
        epic_134_status in {"done", "closed"} and _has_story_134_6_planning_closure(root)
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Epic 134 must remain in-progress unless Story 134.6 planning-only closure exists",
            )
        )
    if (
        "story-134-4-registry-db-mtls-activation-smoke-failure-evidence-package-local-finished"
        not in sprint_text
    ):
        violations.append(
            Violation(str(SPRINT_STATUS_PATH), "Story 134.4 audit trail entry missing")
        )

    return violations


def _copy_live_fixture(tmpdir: Path) -> None:
    fixture_paths = {
        *STATUS_SCAN_PATHS,
        CLOSURE_ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
        *(Path(ref) for ref in REQUIRED_READINESS_REFS),
    }
    for relpath in fixture_paths:
        src = REPO_ROOT / relpath
        if relpath == CLOSURE_ARTIFACT_PATH and not src.exists():
            continue
        dst = tmpdir / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _self_test() -> int:
    scenarios: list[tuple[str, str | None, str]] = []
    with tempfile.TemporaryDirectory(prefix="story-134-4-db-mtls-evidence-") as raw_tmp:
        tmpdir = Path(raw_tmp)
        _copy_live_fixture(tmpdir)
        baseline = validate(tmpdir)
        if baseline:
            print("self-test baseline unexpectedly failed:", file=sys.stderr)
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1

        scenarios.append(
            (
                "db mTLS production activation enabled.",
                "activation overclaim",
                "activation overclaim",
            )
        )
        scenarios.append(
            (
                "Registry DB mTLS activation completed successfully.",
                "activation overclaim",
                "completion overclaim",
            )
        )
        scenarios.append(
            (
                "Client certificate enforcement is active in production.",
                "activation overclaim",
                "client cert overclaim",
            )
        )
        scenarios.append(
            (
                "Plaintext fallback is enabled for emergencies.",
                "plaintext fallback overclaim",
                "plaintext fallback overclaim",
            )
        )
        scenarios.append(
            (
                "Readiness artifacts prove DB mTLS activation.",
                "readiness-as-proof",
                "readiness proof overclaim",
            )
        )
        scenarios.append(
            (
                "self-attestation is approved as sufficient evidence.",
                "self-attestation",
                "self-attestation rejection",
            )
        )
        scenarios.append(("-----BEGIN PRIVATE KEY-----", "secret-like", "private key rejection"))
        scenarios.append(
            ("client_cert=/tmp/client.crt", "secret-like", "certificate value rejection")
        )

        for unsafe_text, expected, label in scenarios:
            _copy_live_fixture(tmpdir)
            target = tmpdir / FEATURE_STATUS_PATH
            target.write_text(
                target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8"
            )
            violations = validate(tmpdir)
            if expected is None:
                if violations:
                    print(f"self-test failed: {label} should be clean", file=sys.stderr)
                    for violation in violations:
                        print(violation.render(), file=sys.stderr)
                    return 1
            elif not any(expected in violation.message for violation in violations):
                print(f"self-test failed: {label} was not rejected", file=sys.stderr)
                for violation in violations:
                    print(violation.render(), file=sys.stderr)
                return 1

        _copy_live_fixture(tmpdir)
        contract_data = _load_json(tmpdir, CONTRACT_PATH)
        future_contract = _section(contract_data, "future_smoke_evidence_contract")
        domains = _section(future_contract, "required_domains")
        domains.pop("server_tls_enforcement", None)
        (tmpdir / CONTRACT_PATH).write_text(
            json.dumps(contract_data, indent=2) + "\n", encoding="utf-8"
        )
        if not any(
            "required smoke evidence domains missing" in violation.message
            for violation in validate(tmpdir)
        ):
            print("self-test failed: missing domain was not rejected", file=sys.stderr)
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
    print("Story 134.4 registry DB mTLS activation smoke/failure evidence contract OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
