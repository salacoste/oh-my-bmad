#!/usr/bin/env python3
"""Validate Story 134.5 combined split/remote Postgres/DB mTLS rehearsal evidence."""

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
CONTRACT_PATH = Path("docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PROJECT_OVERVIEW_PATH = Path("docs/project-overview.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "134-5-combined-split-remote-postgres-db-mtls-rehearsal-evidence-package.md"
)
CLOSURE_ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = (
    "uv run python scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py"
)
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "schema_version",
        "phase",
        "epic",
        "story",
        "title",
        "mode",
        "activation_boundary",
        "operator_gate",
        "readiness_prerequisites",
        "future_rehearsal_evidence_contract",
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
        "split_deployment_profile_ref",
        "remote_postgres_profile_ref",
        "db_mtls_profile_ref",
        "combined_rehearsal_trace_ref",
        "backup_checkpoint_ref",
        "failure_injection_report_ref",
        "rollback_owner",
        "rollback_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
        "go_no_go_signoff_ref",
    }
)
REQUIRED_DOMAINS = frozenset(
    {
        "explicit_operator_gate_enablement",
        "split_deployment_service_placement",
        "remote_postgres_endpoint_and_migration_preconditions",
        "db_mtls_server_and_client_certificate_enforcement",
        "combined_rehearsal_smoke_trace",
        "backup_restore_checkpoint",
        "no_plaintext_fallback_behavior",
        "bounded_sanitized_failure_injection",
        "rollback_emergency_disable_fail_closed_signoff",
    }
)
REQUIRED_READINESS_REFS = frozenset(
    {
        "docs/split-deployment-remote-postgres-closure-readiness.json",
        "docs/db-mtls-readiness.json",
        "docs/controlled-activation-evidence.json",
        "docs/split-deployment-activation-smoke-evidence.json",
        "docs/remote-postgres-activation-smoke-migration-evidence.json",
        "docs/registry-db-mtls-activation-smoke-failure-evidence.json",
        "_bmad-output/implementation-artifacts/132-8-closure-evidence.md",
        "_bmad-output/implementation-artifacts/133-5-db-mtls-closure-evidence.md",
        "_bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md",
        "_bmad-output/implementation-artifacts/"
        "134-2-split-deployment-activation-smoke-evidence-package.md",
        "_bmad-output/implementation-artifacts/"
        "134-3-remote-postgres-activation-smoke-migration-evidence-package.md",
        "_bmad-output/implementation-artifacts/"
        "134-4-registry-db-mtls-activation-smoke-failure-evidence-package.md",
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
        "dsn_value_rejected",
        "activation_overclaim_rejected",
        "rehearsal_overclaim_rejected",
        "plaintext_fallback_overclaim_rejected",
        "readiness_as_proof_rejected",
        "combined_rehearsal_domain_coverage_required",
        "operator_gate_required",
        "split_deployment_placement_required",
        "remote_postgres_preconditions_required",
        "db_mtls_enforcement_required",
        "backup_checkpoint_required",
        "bounded_failure_injection_required",
        "rollback_emergency_disable_fail_closed_signoff_required",
        "justfile_and_ci_wiring_required",
        "status_docs_story_134_5_done_required",
        "story_134_6_backlog_required",
        "epic_134_in_progress_required",
        "production_script_change_rejected",
        "local_static_checker_test_ci_wiring_only",
        "planning_story_forbidden_change_overclaim_rejected",
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
        r"private[_-]?key|api[_-]?key|bearer|dsn)['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
    re.compile(
        r"(?i)\b(?:cert(?:ificate)?|client[_-]?cert|ca[_-]?cert)['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
)

ACTIVATION_TARGET = (
    r"(?:split[-\s]+deployment(?:\s+activation)?|remote[-\s]+postgres(?:\s+activation)?|"
    r"(?:registry\s+)?db\s+mTLS(?:\s+production)?(?:\s+activation)?|"
    r"production\s+activation|live\s+database\s+cutover|migration\s+execution|"
    r"compose/profile\s+activation)"
)
REHEARSAL_TARGET = (
    r"(?:combined\s+rehearsal|live\s+combined\s+rehearsal|"
    r"live\s+rehearsal|rehearsal|"
    r"split\s+deployment,\s+remote\s+postgres,\s+and\s+db\s+mTLS\s+rehearsal|"
    r"combined\s+split\s+deployment\s+remote\s+postgres\s+db\s+mTLS\s+rehearsal)"
)
POSITIVE_STATE = (
    r"(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
    r"run|ran|performed|executed|serving(?:\s+traffic)?|done|provisioned|"
    r"proves?|proved|proven)"
)
CLAIM_AUXILIARY_GAP = r"(?:(?:was|were|is|are|has|have|had|been|successfully)\s+){0,5}"
ACTIVATION_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{ACTIVATION_TARGET}\b(?:\W+\w+){{0,8}}\W+\b{POSITIVE_STATE}\b", re.I),
    re.compile(rf"\b{POSITIVE_STATE}\b(?:\W+\w+){{0,8}}\W+\b{ACTIVATION_TARGET}\b", re.I),
    re.compile(
        rf"\b(?:database\s+)?migrations?\b\s+{CLAIM_AUXILIARY_GAP}"
        r"\b(?:activated|enabled|run|ran|executed|performed|completed|done|successful|succeeded)\b",
        re.I,
    ),
)
REHEARSAL_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{REHEARSAL_TARGET}\b(?:\W+\w+){{0,8}}\W+\b{POSITIVE_STATE}\b", re.I),
    re.compile(rf"\b{POSITIVE_STATE}\b(?:\W+\w+){{0,8}}\W+\b{REHEARSAL_TARGET}\b", re.I),
)
READINESS_AS_PROOF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproof\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b"
        r"(?:proves?|proved|proven)\s+.*\b(?:activation|rehearsal)\b",
        re.I,
    ),
)
PLAINTEXT_FALLBACK_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bplaintext[-\s]+fallback\b(?:\W+\w+){0,6}\W+\b(?:enabled|active|available|works|accepted|allowed)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:enabled|active|available|works|accepted|allowed)\b(?:\W+\w+){0,6}\W+\bplaintext[-\s]+fallback\b",
        re.I,
    ),
)
FORBIDDEN_PLANNING_CHANGE_TARGET = (
    r"(?:rollback\s+execution|restore\s+execution|destructive\s+operation|"
    r"production\s+host\s+mutation|production\s+host|credential\s+use|"
    r"production-state\s+change|production\s+state\s+change|production\s+state|"
    r"runtime\s+behavior\s+change|deployment\s+config\s+change|"
    r"operator/deployment/rollback/restore/migration/activation/production\s+scripts?|"
    r"operator/deployment/rollback/restore/migration/activation/production\s+script\s+change|"
    r"(?:operator|deployment|rollback|restore|migration|activation|production)\s+scripts?|"
    r"(?:operator|deployment|rollback|restore|migration|activation|production)\s+script\s+change|"
    r"script\s+change)"
)
FORBIDDEN_CHANGE_POSITIVE_STATE = (
    r"(?:activated|enabled|complete|completed|successful|succeeded|occurred|"
    r"run|ran|performed|executed|done|changed|updated|used|mutated|"
    r"proves?|proved|proven)"
)
FORBIDDEN_PLANNING_CHANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b{FORBIDDEN_PLANNING_CHANGE_TARGET}\b(?:\W+\w+){{0,8}}\W+\b{FORBIDDEN_CHANGE_POSITIVE_STATE}\b",
        re.I,
    ),
    re.compile(
        rf"\b{FORBIDDEN_CHANGE_POSITIVE_STATE}\b(?:\W+\w+){{0,8}}\W+\b{FORBIDDEN_PLANNING_CHANGE_TARGET}\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:rollbacks?|restores?)\b\s+{CLAIM_AUXILIARY_GAP}"
        r"\b(?:run|ran|executed|performed|completed|done|successful|succeeded|changed|updated)\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:production\s+)?credentials?\b\s+{CLAIM_AUXILIARY_GAP}"
        r"\b(?:used|enabled|activated|applied)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:successfully\s+)?(?:used|enabled|activated|applied)\b(?:\W+\w+){0,4}\W+\b(?:production\s+)?credentials?\b",
        re.I,
    ),
)
SELF_ATTESTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bself[-_ ](?:attestation|attested|review)\b.*\b(?:sufficient|accepted|approved)\b",
        re.I,
    ),
    re.compile(r"\bimplementer[-_ ]review\b.*\b(?:sufficient|accepted|approved)\b", re.I),
)
SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:no|without)\b.*\b(?:activation|rehearsal|live\s+combined\s+rehearsal|"
        r"rollback\s+execution|restore\s+execution|destructive\s+operation|"
        r"migration\s+execution|database\s+cutover|real\s+certificate\s+material|"
        r"private\s+key\s+material|credential\s+(?:values?|use)|provisioning|"
        r"(?:production\s+)?host\s+mutation|script\s+change|production-state\s+change|"
        r"production\s+state\s+change|compose/profile\s+activation|plaintext\s+fallback)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:not|never)\s+.*\bproof\b.*\b(?:activation|rehearsal)\b.*\boccurred\b",
        re.I,
    ),
    re.compile(r"\bnot\s+proof\b.*\b(?:activation|rehearsal)\s+occurred\b", re.I),
    re.compile(
        r"\b(?:future/operator-gated|operator[- ]gated|deferred|fail[- ]closed|planning\s+only|"
        r"evidence\s+planning|static|docs/status|checker|contract|local(?:ly)?|readiness[- ]only)\b",
        re.I,
    ),
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites).*\bnot\s+proof\b", re.I),
    re.compile(
        r"\b(?:is|are|remain|remains|stays)\s+(?:deferred|fail[- ]closed|operator[- ]gated)\b",
        re.I,
    ),
    re.compile(r"\bnot\s+live\s+(?:production\s+)?activation\b", re.I),
    re.compile(r"\bwhile\b.*\b(?:deferred|operator[- ]gated|fail[- ]closed)\b", re.I),
    re.compile(
        r"\b(?:activation|rehearsal)\b.*\b(?:credential\s+handling|runtime\s+behavior\s+change|"
        r"production-state\s+change|host\s+mutation|plaintext\s+fallback)\b",
        re.I,
    ),
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


def _has_exact_command_line(text: str, command: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == command or stripped == f"run: {command}":
            return True
    return False


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


def _clause_bounds(line: str, start: int, end: int) -> tuple[int, int]:
    lower = line.lower()
    start_boundaries = [line.rfind(".", 0, start), line.rfind(";", 0, start)]
    for token in (" but ", " however "):
        start_boundaries.append(lower.rfind(token, 0, start))
    clause_start = max(start_boundaries) + 1

    end_candidates = [pos for pos in (line.find(".", end), line.find(";", end)) if pos != -1]
    for token in (" but ", " however "):
        pos = lower.find(token, end)
        if pos != -1:
            end_candidates.append(pos)
    clause_end = min(end_candidates) if end_candidates else len(line)
    return clause_start, clause_end


def _matched_clause(line: str, start: int, end: int) -> str:
    clause_start, clause_end = _clause_bounds(line, start, end)
    return line[clause_start:clause_end]


def _has_clause_negation(line: str, start: int, end: int) -> bool:
    clause_start, _ = _clause_bounds(line, start, end)
    prefix = line[clause_start:start]
    separator_matches = list(re.finditer(r",|:|—|–|\s+-\s+|\band\b", prefix, re.I))
    local_prefix = prefix[separator_matches[-1].end() :] if separator_matches else prefix
    negation_pattern = (
        r"\b(?:no|without|never|does\s+not|do\s+not|did\s+not|not\s+proof|"
        r"not\s+live|not\s+production)\b|\bnot\s*$"
    )
    if re.search(negation_pattern, local_prefix, re.I) is not None:
        return True
    if re.search(r"\b(?:approval|operator\s+approval)\s+exists\b", prefix, re.I) is not None:
        return False
    if re.search(r"\b(?:does\s+not|do\s+not|did\s+not)\s+(?:add|claim|perform)\b", prefix, re.I):
        return True
    if re.search(r"^\s*(?:[-*>#`| ]+)?no\b", prefix, re.I) is not None:
        return True
    return (
        re.search(negation_pattern, prefix, re.I) is not None
        and re.search(r"\b(?:exists?|available|present)\b", prefix, re.I) is None
    )


def _is_safe_overclaim_match(line: str, start: int, end: int, *, kind: str) -> bool:
    clause = _matched_clause(line, start, end)
    match_text = line[start:end]
    if re.search(
        r"\bactivation(?!\s+(?:evidence|planning|contract|package|smoke|schema))\b\s+"
        r"(?:(?:is|was|has|have|had|been|now|successfully)\s+){0,6}"
        r"(?:complete|completed|successful|succeeded|performed|executed|activated|enabled|live|active|done)\b",
        clause,
        re.I,
    ):
        return False
    if kind == "activation" and re.search(
        r"\b(?:no(?:\W+\w+){0,8}\W+activation|done\s+without\s+activation|"
        r"not\s+activation|not\s+production\s+activation|future/operator-gated|"
        r"planning-only\s+evidence\s+closure)\b",
        clause,
        re.I,
    ):
        return True
    if kind == "activation" and re.search(
        r"\bDB\s+mTLS\s+readiness\s+remains\s+complete\s+locally/runtime-gated\b",
        clause,
        re.I,
    ):
        return True
    if kind == "planning-change" and re.search(
        r"(?:^\s*(?:[-*>#| ]+)?(?:and\s+)?no\b.*\b"
        r"(?:rollback/restore\s+execution|destructive\s+operation|production\s+host\s+mutation|"
        r"credentials/certs|migration\s+execution|runtime/script/deployment\s+config\s+change|"
        r"dependency/lock\s+change|production-state\s+change)\b|"
        r"^\s*change,\s+dependency/lock\s+change,\s+or\s+production-state\s+change\s+occurred\.?)",
        clause,
        re.I,
    ):
        return True
    if _has_clause_negation(line, start, end):
        return True
    if re.search(
        r"\bcomplete\s+locally\b[^.]*\.\s*(?:production\s+)?activation\b", match_text, re.I
    ):
        return True
    if kind == "activation":
        if (
            re.search(
                r"\b(?:readiness\s+is\s+complete|evidence\s+(?:planning|package|contract)|"
                r"smoke/migration\s+evidence|smoke\s+checks?\s+without\s+performing\s+live|"
                r"docs/status/static-checker|complete\s+locally\s+as\s+future/operator-gated)\b",
                match_text,
                re.I,
            )
            is not None
            or re.search(
                r"\bcomplete\s+locally\s+and\s+merged\s+via\s+PR\s+#\d+\s+as\s+the\s+"
                r"split[- ]deployment\s+smoke\s+evidence\s+package\s+docs/status/static-checker\s+slice\b",
                clause,
                re.I,
            )
            is not None
            or re.search(
                r"\b(?:event|expiry_freshness):\s+\S*(?:split[- ]deployment|remote[- ]postgres)\S*",
                line,
                re.I,
            )
            is not None
            or re.search(r":\s*done\b.*\bdocs/status/static-checker\b", clause, re.I) is not None
        ):
            return True
        return (
            re.search(
                r"\b(?:production\s+)?activation\b.*\b(?:deferred|operator[- ]gated|future/operator-gated|fail[- ]closed|not\s+enabled)\b",
                clause,
                re.I,
            )
            is not None
            or re.search(
                r"\bactivation\b(?:\W+\w+){0,5}\W+\b(?:evidence|schema/preflight|planning|contract|package|smoke/failure|smoke)\b",
                clause,
                re.I,
            )
            is not None
        )
    if kind == "rehearsal":
        return (
            re.search(
                r"\brehearsal\b.*\b(?:deferred|operator[- ]gated|fail[- ]closed|not\s+performed)\b",
                clause,
                re.I,
            )
            is not None
            or re.search(
                r"\brehearsal\b(?:\W+\w+){0,5}\W+\b(?:evidence|planning|contract|package)\b",
                clause,
                re.I,
            )
            is not None
            or re.search(r"\brehearsal\b.*\bnot\s+proof\b", clause, re.I) is not None
        )
    if kind == "plaintext-fallback":
        return (
            re.search(
                r"\bplaintext\s+fallback\b.*\b(?:deferred|fail[- ]closed|rejected|not\s+enabled|not\s+allowed)\b",
                clause,
                re.I,
            )
            is not None
        )
    if kind == "readiness-as-proof":
        return re.search(r"\bnot\b.*\bproof\b|\bprerequisites?\s+only\b", clause, re.I) is not None
    return False


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
            for pattern in FORBIDDEN_PLANNING_CHANGE_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(
                        line, match.start(), match.end(), kind="planning-change"
                    )
                    for match in pattern.finditer(line)
                ):
                    violations.append(
                        Violation(
                            f"{relpath}:{lineno}",
                            "planning-story forbidden change overclaim",
                        )
                    )
                    break
            for pattern in ACTIVATION_OVERCLAIM_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(
                        line, match.start(), match.end(), kind="activation"
                    )
                    for match in pattern.finditer(line)
                ):
                    violations.append(Violation(f"{relpath}:{lineno}", "activation overclaim"))
                    break
            for pattern in REHEARSAL_OVERCLAIM_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(line, match.start(), match.end(), kind="rehearsal")
                    for match in pattern.finditer(line)
                ):
                    violations.append(Violation(f"{relpath}:{lineno}", "rehearsal overclaim"))
                    break
            for pattern in PLAINTEXT_FALLBACK_OVERCLAIM_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(
                        line, match.start(), match.end(), kind="plaintext-fallback"
                    )
                    for match in pattern.finditer(line)
                ):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "plaintext fallback overclaim")
                    )
                    break
            for pattern in READINESS_AS_PROOF_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(
                        line, match.start(), match.end(), kind="readiness-as-proof"
                    )
                    for match in pattern.finditer(line)
                ):
                    violations.append(
                        Violation(f"{relpath}:{lineno}", "readiness-as-proof overclaim")
                    )
                    break
            for pattern in SELF_ATTESTATION_PATTERNS:
                if any(
                    not _is_safe_overclaim_match(
                        line, match.start(), match.end(), kind="self-attestation"
                    )
                    for match in pattern.finditer(line)
                ):
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

    if data.get("schema_version") != "story-134.5/v1":
        violations.append(Violation(str(CONTRACT_PATH), "schema_version must be story-134.5/v1"))
    if data.get("phase") != "51" or data.get("epic") != "134" or data.get("story") != "134.5":
        violations.append(
            Violation(str(CONTRACT_PATH), "phase/epic/story identifiers must be 51/134/134.5")
        )

    boundary = _section(data, "activation_boundary")
    for key in ("activation_performed", "rehearsal_performed"):
        if boundary.get(key) is not False:
            violations.append(Violation(f"{CONTRACT_PATH}#{key}", f"{key} must be false"))
    required_true = (
        "no_live_rehearsal",
        "no_split_deployment_activation",
        "no_remote_postgres_activation",
        "no_db_mtls_activation",
        "no_migration_execution",
        "no_live_database_cutover",
        "no_real_certificate_material",
        "no_private_key_material",
        "no_plaintext_fallback",
        "operator_gated",
        "future_evidence_only",
        "readiness_prerequisites_are_not_activation_proof",
        "no_production_script_change",
        "allowed_local_validation_wiring_only",
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
    if prerequisites.get("semantics") != "prerequisites_only_not_activation_or_rehearsal_proof":
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#readiness_prerequisites",
                "readiness prerequisites must be prerequisites-only, not activation/rehearsal proof",
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

    contract = _section(data, "future_rehearsal_evidence_contract")
    domains = _section(contract, "required_domains")
    missing_domains = REQUIRED_DOMAINS - set(domains)
    if missing_domains:
        violations.append(
            Violation(
                f"{CONTRACT_PATH}#future_rehearsal_evidence_contract.required_domains",
                f"required rehearsal evidence domains missing: {sorted(missing_domains)}",
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
        "DSN values",
        "private key material",
        "certificate bodies",
        "plaintext secrets",
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
        "split-service placement",
        "remote postgres endpoint",
        "migration preconditions",
        "server-side tls",
        "client-certificate enforcement",
        "combined smoke traces",
        "backup/restore checkpoint",
        "plaintext fallback",
        "failure-injection diagnostics",
        "rollback",
        "emergency-disable",
        "go/no-go signoff",
        "fail-closed",
        "operator/deployment/rollback/restore/migration/activation/production script change",
        "local validation only",
    ):
        if required_text not in all_contract_text:
            violations.append(
                Violation(
                    str(CONTRACT_PATH), f"required rehearsal language missing: {required_text}"
                )
            )

    _validate_status_language(root, violations)
    _validate_secret_absence(root, violations)

    just_text = _read(root, JUSTFILE_PATH)
    if not _has_exact_command_line(just_text, CHECKER_COMMAND):
        violations.append(Violation(str(JUSTFILE_PATH), "checker command missing from justfile"))
    if not _has_exact_command_line(just_text, CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(str(JUSTFILE_PATH), "checker self-test command missing from justfile")
        )
    ci_text = _read(root, CI_PATH)
    if not _has_exact_command_line(ci_text, CHECKER_COMMAND):
        violations.append(Violation(str(CI_PATH), "checker command missing from CI"))
    if not _has_exact_command_line(ci_text, CHECKER_SELF_TEST_COMMAND):
        violations.append(Violation(str(CI_PATH), "checker self-test command missing from CI"))

    feature_text = _read(root, FEATURE_STATUS_PATH)
    project_text = _read(root, PROJECT_OVERVIEW_PATH)
    sprint_text = _read(root, SPRINT_STATUS_PATH)
    if "Story 134.5" not in feature_text or "combined" not in feature_text.lower():
        violations.append(
            Violation(str(FEATURE_STATUS_PATH), "Story 134.5 combined rehearsal status missing")
        )
    if "Story 134.5" not in project_text or "combined" not in project_text.lower():
        violations.append(
            Violation(str(PROJECT_OVERVIEW_PATH), "Story 134.5 combined rehearsal overview missing")
        )
    if "134-5-combined-split-remote-postgres-db-mtls-rehearsal: done" not in sprint_text:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.5 must be marked done"))
    story_134_6 = re.search(
        r"(?m)^\s*134-6-controlled-activation-closure-go-no-go-evidence:\s*(?P<status>\S+)",
        sprint_text,
    )
    story_134_6_status = story_134_6.group("status") if story_134_6 else None
    closure_exists = _has_story_134_6_planning_closure(root)
    if story_134_6_status != "backlog" and not (
        story_134_6_status in {"done", "closed"} and closure_exists
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Story 134.6 must remain backlog unless planning-only closure exists",
            )
        )
    epic_134 = re.search(r"(?m)^\s*epic-134:\s*(?P<status>\S+)", sprint_text)
    epic_134_status = epic_134.group("status") if epic_134 else None
    if epic_134_status != "in-progress" and not (
        epic_134_status in {"done", "closed"} and closure_exists
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Epic 134 must remain in-progress unless Story 134.6 planning-only closure exists",
            )
        )

    return violations


def _copy_live_fixture(tmpdir: Path) -> None:
    fixture_paths = {
        *STATUS_SCAN_PATHS,
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


def _normalize_expected_done_fixture(tmpdir: Path) -> None:
    feature_path = tmpdir / FEATURE_STATUS_PATH
    feature_path.write_text(
        feature_path.read_text(encoding="utf-8")
        + "\nStory 134.5 combined rehearsal evidence is complete locally as future/operator-gated planning only; combined rehearsal remains not proof activation or rehearsal occurred.\n",
        encoding="utf-8",
    )
    project_path = tmpdir / PROJECT_OVERVIEW_PATH
    project_path.write_text(
        project_path.read_text(encoding="utf-8")
        + "\nStory 134.5 combined rehearsal evidence is complete locally as future/operator-gated planning only; no live activation or combined rehearsal is performed.\n",
        encoding="utf-8",
    )
    sprint_path = tmpdir / SPRINT_STATUS_PATH
    sprint_path.write_text(
        sprint_path.read_text(encoding="utf-8").replace(
            "134-5-combined-split-remote-postgres-db-mtls-rehearsal: backlog",
            "134-5-combined-split-remote-postgres-db-mtls-rehearsal: done",
        ),
        encoding="utf-8",
    )
    just_path = tmpdir / JUSTFILE_PATH
    just_path.write_text(
        just_path.read_text(encoding="utf-8")
        + f"\n# Story 134.5 fixture wiring\n{CHECKER_COMMAND}\n{CHECKER_SELF_TEST_COMMAND}\n",
        encoding="utf-8",
    )
    ci_path = tmpdir / CI_PATH
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8")
        + f"\n# Story 134.5 fixture wiring\n{CHECKER_COMMAND}\n{CHECKER_SELF_TEST_COMMAND}\n",
        encoding="utf-8",
    )


def _self_test() -> int:
    scenarios: list[tuple[str, str, str]] = [
        (
            "Remote Postgres activation completed successfully.",
            "activation overclaim",
            "activation",
        ),
        (
            "Remote Postgres was activated successfully.",
            "activation overclaim",
            "remote postgres activated",
        ),
        (
            "Remote-Postgres activation completed successfully.",
            "activation overclaim",
            "remote postgres hyphen activation",
        ),
        (
            "DB mTLS is enabled in production.",
            "activation overclaim",
            "db mtls enabled",
        ),
        ("split deployment is live.", "activation overclaim", "split deployment live"),
        ("Migration was executed successfully.", "activation overclaim", "migration executed"),
        ("Migration ran successfully.", "activation overclaim", "migration ran"),
        ("Migration successfully completed.", "activation overclaim", "migration completed"),
        ("Migration successfully executed.", "activation overclaim", "migration executed adverb"),
        ("Migration was run successfully.", "activation overclaim", "migration was run"),
        (
            "Migration has successfully been executed.",
            "activation overclaim",
            "migration has successfully been executed",
        ),
        (
            "Migration has successfully been completed.",
            "activation overclaim",
            "migration has successfully been completed",
        ),
        (
            "Migrations have successfully been executed.",
            "activation overclaim",
            "plural migrations executed",
        ),
        (
            "Database migrations have successfully been completed.",
            "activation overclaim",
            "database migrations completed",
        ),
        ("Combined rehearsal completed successfully.", "rehearsal overclaim", "rehearsal"),
        ("The live combined rehearsal occurred.", "rehearsal overclaim", "live rehearsal"),
        (
            "Live rehearsal completed successfully.",
            "rehearsal overclaim",
            "generic live rehearsal",
        ),
        (
            "The rehearsal was performed successfully.",
            "rehearsal overclaim",
            "generic rehearsal performed",
        ),
        (
            "Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
            "plaintext fallback",
        ),
        (
            "Plaintext-fallback was accepted.",
            "plaintext fallback overclaim",
            "plaintext fallback hyphen",
        ),
        (
            "Readiness artifacts prove DB mTLS activation and rehearsal.",
            "readiness-as-proof",
            "readiness proof",
        ),
        (
            "rollback execution completed successfully.",
            "planning-story forbidden change overclaim",
            "rollback execution",
        ),
        (
            "rollback was executed successfully.",
            "planning-story forbidden change overclaim",
            "rollback executed",
        ),
        (
            "Rollback ran successfully.",
            "planning-story forbidden change overclaim",
            "rollback ran",
        ),
        (
            "Rollback has been run successfully.",
            "planning-story forbidden change overclaim",
            "rollback has been run",
        ),
        (
            "Rollback successfully completed.",
            "planning-story forbidden change overclaim",
            "rollback completed adverb",
        ),
        (
            "Rollback successfully ran.",
            "planning-story forbidden change overclaim",
            "rollback ran adverb",
        ),
        (
            "Rollback has successfully been run.",
            "planning-story forbidden change overclaim",
            "rollback has successfully been run",
        ),
        (
            "Rollbacks have successfully been run.",
            "planning-story forbidden change overclaim",
            "plural rollbacks run",
        ),
        (
            "restore execution completed successfully.",
            "planning-story forbidden change overclaim",
            "restore execution",
        ),
        (
            "Restore ran successfully.",
            "planning-story forbidden change overclaim",
            "restore ran",
        ),
        (
            "Restore has been run successfully.",
            "planning-story forbidden change overclaim",
            "restore has been run",
        ),
        (
            "Restore successfully completed.",
            "planning-story forbidden change overclaim",
            "restore completed adverb",
        ),
        (
            "Restore has successfully been completed.",
            "planning-story forbidden change overclaim",
            "restore has successfully been completed",
        ),
        (
            "Restores have successfully been completed.",
            "planning-story forbidden change overclaim",
            "plural restores completed",
        ),
        (
            "destructive operation completed successfully.",
            "planning-story forbidden change overclaim",
            "destructive operation",
        ),
        (
            "production host mutation completed successfully.",
            "planning-story forbidden change overclaim",
            "production host mutation",
        ),
        (
            "credential use completed successfully.",
            "planning-story forbidden change overclaim",
            "credential use",
        ),
        (
            "credential was used.",
            "planning-story forbidden change overclaim",
            "credential used",
        ),
        (
            "production credentials were used successfully.",
            "planning-story forbidden change overclaim",
            "production credentials used",
        ),
        (
            "Production credentials were applied successfully.",
            "planning-story forbidden change overclaim",
            "production credentials applied",
        ),
        (
            "Production credentials have been applied successfully.",
            "planning-story forbidden change overclaim",
            "production credentials have been applied",
        ),
        (
            "Production credentials successfully applied.",
            "planning-story forbidden change overclaim",
            "production credentials applied adverb",
        ),
        (
            "Production credentials have successfully been applied.",
            "planning-story forbidden change overclaim",
            "production credentials have successfully been applied",
        ),
        (
            "Production credentials have successfully been used.",
            "planning-story forbidden change overclaim",
            "production credentials have successfully been used",
        ),
        (
            "production-state change completed successfully.",
            "planning-story forbidden change overclaim",
            "production-state change",
        ),
        (
            "Runtime behavior change completed successfully.",
            "planning-story forbidden change overclaim",
            "runtime behavior change",
        ),
        (
            "Deployment config change completed successfully.",
            "planning-story forbidden change overclaim",
            "deployment config change",
        ),
        (
            "production state was changed.",
            "planning-story forbidden change overclaim",
            "production state changed",
        ),
        (
            "operator/deployment/rollback/restore/migration/activation/production script change completed successfully.",
            "planning-story forbidden change overclaim",
            "production script change",
        ),
        (
            "operator/deployment/rollback/restore/migration/activation/production scripts updated successfully.",
            "planning-story forbidden change overclaim",
            "production scripts updated",
        ),
        (
            "deployment script was changed.",
            "planning-story forbidden change overclaim",
            "deployment script changed",
        ),
        (
            "self-attestation is approved as sufficient evidence.",
            "self-attestation",
            "self-attestation",
        ),
        (
            "No operator approval exists, Remote Postgres activation completed successfully.",
            "activation overclaim",
            "unrelated no activation",
        ),
        (
            "No operator approval exists — Combined rehearsal completed successfully.",
            "rehearsal overclaim",
            "unrelated no rehearsal",
        ),
        (
            "No operator approval exists: Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
            "unrelated no plaintext fallback",
        ),
        ("-----BEGIN PRIVATE KEY-----", "secret-like", "private key"),
        ("postgresql://user:password@example.com/db", "secret-like", "DSN"),
        ("client_cert=/tmp/client.crt", "secret-like", "certificate value"),
    ]
    with tempfile.TemporaryDirectory(prefix="story-134-5-combined-rehearsal-evidence-") as raw_tmp:
        tmpdir = Path(raw_tmp)
        _copy_live_fixture(tmpdir)
        _normalize_expected_done_fixture(tmpdir)
        baseline = validate(tmpdir)
        if baseline:
            print("self-test baseline unexpectedly failed:", file=sys.stderr)
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1

        for unsafe_text, expected, label in scenarios:
            _copy_live_fixture(tmpdir)
            _normalize_expected_done_fixture(tmpdir)
            target = tmpdir / FEATURE_STATUS_PATH
            target.write_text(
                target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8"
            )
            violations = validate(tmpdir)
            if not any(expected in violation.message for violation in violations):
                print(f"self-test failed: {label} was not rejected", file=sys.stderr)
                for violation in violations:
                    print(violation.render(), file=sys.stderr)
                return 1

        _copy_live_fixture(tmpdir)
        _normalize_expected_done_fixture(tmpdir)
        contract_data = _load_json(tmpdir, CONTRACT_PATH)
        future_contract = _section(contract_data, "future_rehearsal_evidence_contract")
        domains = _section(future_contract, "required_domains")
        domains.pop("combined_rehearsal_smoke_trace", None)
        (tmpdir / CONTRACT_PATH).write_text(
            json.dumps(contract_data, indent=2) + "\n", encoding="utf-8"
        )
        if not any(
            "required rehearsal evidence domains missing" in violation.message
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
    print("Story 134.5 combined split/remote Postgres/DB mTLS rehearsal evidence contract OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
