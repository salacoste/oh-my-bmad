#!/usr/bin/env python3
"""Validate Story 134.3 remote Postgres activation smoke and migration evidence planning."""

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
CONTRACT_PATH = Path("docs/remote-postgres-activation-smoke-migration-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PROJECT_OVERVIEW_PATH = Path("docs/project-overview.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "134-3-remote-postgres-activation-smoke-migration-evidence-package.md"
)
CLOSURE_ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = (
    "uv run python scripts/check_remote_postgres_activation_smoke_migration_evidence.py"
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
        "remote_postgres_endpoint_ref",
        "migration_runner_ref",
        "backup_restore_checkpoint_ref",
        "rollback_owner",
        "rollback_plan_ref",
        "fix_forward_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
    }
)
REQUIRED_DOMAINS = frozenset(
    {
        "migration_preconditions",
        "single_migration_runner",
        "backup_restore_checkpoint",
        "bounded_pool_settings",
        "writer_authority",
        "read_side_compatibility",
        "redacted_database_endpoint_identity",
        "rollback_fix_forward_criteria",
    }
)
REQUIRED_READINESS_REFS = frozenset(
    {
        "docs/remote-postgres-production-readiness.json",
        "docs/registry-remote-postgres-profile-readiness.json",
        "docs/split-deployment-remote-postgres-closure-readiness.json",
        "docs/db-mtls-readiness.json",
        "docs/controlled-activation-evidence.json",
        "docs/split-deployment-activation-smoke-evidence.json",
        "_bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md",
        "_bmad-output/implementation-artifacts/134-2-split-deployment-activation-smoke-evidence-package.md",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "missing_evidence_fails_closed",
        "malformed_evidence_fails_closed",
        "stale_evidence_fails_closed",
        "self_attestation_rejected",
        "secret_like_material_rejected",
        "unredacted_connection_string_rejected",
        "activation_overclaim_rejected",
        "migration_execution_overclaim_rejected",
        "cutover_overclaim_rejected",
        "readiness_as_proof_rejected",
        "remote_postgres_domain_coverage_required",
        "operator_gate_required",
        "backup_restore_checkpoint_required",
        "rollback_fix_forward_required",
        "justfile_and_ci_wiring_required",
        "status_docs_story_134_3_done_required",
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
)
ACTIVATION_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:remote\s+postgres|postgres|database|migration|cutover|go[- ]live|activation|"
        r"provisioning|compose/profile)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done|provisioned|migrated|cut\s*over|"
        r"proves?|proved|proven)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done|provisioned|migrated|cut\s*over|"
        r"proves?|proved|proven)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"\b(?:remote\s+postgres|postgres|database|migration|cutover|go[- ]live|activation|"
        r"provisioning|compose/profile)\b",
        re.I,
    ),
)
READINESS_AS_PROOF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproof\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b"
        r"(?:proves?|proved|proven)\s+"
        r"(?:(?:remote\s+postgres|postgres|database)\s+)?activation\b",
        re.I,
    ),
)
MIGRATION_EXECUTION_VERB_PHRASE = (
    r"(?:(?:has|have|had|is|are|was|were|be|been|being|did|do|does|already|"
    r"successfully|previously|now)\s+){0,8}"
    r"(?:ran|run|execute[ds]?|performed|completed|applied)"
)
MIGRATION_TOOL_ACTOR_PATTERN = (
    r"(?:alembic|tool|(?:database|migration)\s+(?:tool|runner)|migration\s+orchestrator)"
)
MIGRATION_EXECUTION_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:(?:remote\s+postgres|postgres|database|alembic)\s+){1,3}"
        r"migrations?\b(?!\s+runner\b)"
        rf"\s+{MIGRATION_EXECUTION_VERB_PHRASE}\b",
        re.I,
    ),
    re.compile(
        r"\bmigrations?\b(?!\s+runner\b)"
        rf"\s+{MIGRATION_EXECUTION_VERB_PHRASE}\b"
        r"(?:\W+\w+){0,8}\W+\b(?:remote\s+postgres|postgres|database)\b",
        re.I,
    ),
    re.compile(
        rf"\b{MIGRATION_TOOL_ACTOR_PATTERN}\b"
        rf"\s+{MIGRATION_EXECUTION_VERB_PHRASE}\b"
        r"\s+(?:(?:remote\s+postgres|postgres|database)\s+)?migrations?\b(?!\s+runner\b)"
        r"\s+(?:to|against|for)\s+(?:remote\s+postgres|postgres|database)\b",
        re.I,
    ),
)
ACTIVATION_PROOF_NOUN_STATE_PHRASE = (
    r"(?:(?:is|are|was|were|has|have|had|be|been|being|now|already|successfully|"
    r"previously)\s+){0,8}"
    r"(?:exists?|existed|present|available|submitted|uploaded|documented|recorded|"
    r"accepted|provided|supplied)"
)
ACTIVATION_PROOF_NOUN_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:remote\s+postgres\s+activation\s+(?:proof|evidence)|"
        r"activation\s+proof)\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b",
        re.I,
    ),
    re.compile(
        r"\bactivation\s+(?:proof|evidence)\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b"
        r"\s+for\s+remote\s+postgres\b",
        re.I,
    ),
    re.compile(
        r"\bactivation\s+(?:proof|evidence)\s+for\s+remote\s+postgres\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:proof|evidence)\s+(?:for|of)\s+remote\s+postgres\s+activation\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:proof|evidence)\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b"
        r"\s+for\s+remote\s+postgres\s+activation\b",
        re.I,
    ),
    re.compile(
        r"\bremote\s+postgres\s+(?:proof|evidence)\s+(?:for|of)\s+activation\b"
        rf"\s+{ACTIVATION_PROOF_NOUN_STATE_PHRASE}\b",
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
        r"\b(?:no|without)\s+(?:(?:remote\s+postgres|production|compose/profile|live)\s+)?"
        r"(?:activation|migration\s+execution|live\s+database\s+cutover|database\s+cutover|"
        r"provisioning|production\s+host\s+mutation|host\s+mutation|compose/profile\s+activation|"
        r"plaintext\s+fallback)\b",
        re.I,
    ),
    re.compile(
        r"\bno\b.*\b(?:live|production|remote\s+postgres|postgres|compose/profile)?\s*"
        r"(?:activation|migration\s+execution|database\s+cutover|provisioning|host\s+mutation)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:not|never)\s+(?:activation\s+proof|proof\s+(?:that\s+)?activation\s+occurred|"
        r"proof\s+of\s+(?:remote\s+postgres\s+)?activation)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no|not|never|without)\b.*\b(?:activation|migration\s+execution|"
        r"live\s+database\s+cutover|database\s+cutover|provisioning|"
        r"production\s+host\s+mutation|host\s+mutation|"
        r"compose/profile\s+activation|plaintext\s+fallback)\b.*\b"
        r"(?:performed|claimed|added|enabled|authorized|implemented|supplied|accepted)\b",
        re.I,
    ),
    re.compile(
        r"\bwithout\s+(?:performing|claiming|adding|enabling|authorizing|implementing|supplying|"
        r"accepting|executing)\b.*\b(?:activation|migration\s+execution|"
        r"live\s+database\s+cutover|database\s+cutover|provisioning|"
        r"production\s+host\s+mutation|host\s+mutation|compose/profile\s+activation|"
        r"plaintext\s+fallback)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:does|do|did)\s+not\s+"
        r"(?:add|implement|enable|authorize|perform|claim|include|satisfy)\b.*\b"
        r"(?:activation|migration|cutover|provisioning|host\s+mutation|runtime\s+audit)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no|without)\b.*\b(?:activation|migration\s+execution|"
        r"live\s+database\s+cutover|database\s+cutover|provisioning|host\s+mutation|"
        r"compose/profile\s+activation|plaintext\s+fallback)\b.*\b"
        r"(?:is|are)\s+(?:performed|claimed|added|enabled|authorized|implemented|supplied|accepted)\b",
        re.I,
    ),
    re.compile(
        r"\bnot\s+(?:accepted\s+as\s+)?proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\bneither\s+is\s+proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+acceptance\s+of\s+readiness\s+(?:artifacts?|evidence|prerequisites)\s+as\s+"
        r"proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b(?:are|is)\s+"
        r"(?:(?:prerequisites|prerequisites\s+only)(?:\s+and|,)?\s+)?"
        r"not\s+proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\b(?:future/operator-gated|operator[- ]gated/(?:deferred|future)|"
        r"operator[- ]gated\s+(?:and\s+)?deferred|deferred/operator[- ]gated)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:activation|migration|cutover|smoke|evidence)\b.*\b(?:planning[-/ ]only|"
        r"evidence\s+planning|future/operator-gated\s+evidence\s+only)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:complete\s+locally\s+(?:as|for)|local)\b.*\b"
        r"(?:evidence|schema/preflight|static|docs/status|checker|slice|contract|gate|planning)\b",
        re.I,
    ),
    re.compile(
        r"\breadiness-only/deferred\s+activation\b",
        re.I,
    ),
    re.compile(
        r"\bforbids\s+(?:live[- ]activation|activation|overclaim)\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+real\s+writes\s+are\s+enabled\b",
        re.I,
    ),
    re.compile(
        r"\bnot\s+live\s+(?:production\s+)?activation\b",
        re.I,
    ),
    re.compile(
        r"\b(?:remain|remains|stays|kept|is|are)\s+"
        r"(?:future/operator-gated|operator[- ]gated(?:/deferred)?|operator[- ]gated/deferred|"
        r"deferred|gated\s+by|not\s+enabled|fail[- ]closed|deferred/fail[- ]closed|fail[- ]closed/deferred)"
        r"(?:\b|/)",
        re.I,
    ),
    re.compile(
        r"\b(?:fail[- ]closed|forbidden|rejected)\s+(?:until|when|if|unless|by|on|for)\b",
        re.I,
    ),
)
DIRECT_NEGATED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:no|without)\s+(?:(?:remote\s+postgres|production|compose/profile|live)\s+)?"
        r"(?:activation|migration\s+execution|live\s+database\s+cutover|database\s+cutover|"
        r"provisioning|production\s+host\s+mutation|host\s+mutation|compose/profile\s+activation|"
        r"plaintext\s+fallback)\b"
        r"(?:(?:(?!\bbut\b)[^,;:.—–-]){0,80}\b(?:occurred|performed|executed|completed|claimed|added|"
        r"enabled|authorized|implemented|supplied|accepted))?",
        re.I,
    ),
    re.compile(
        r"\b(?:not|never)\s+(?:activation\s+proof|proof\s+(?:that\s+)?activation\s+occurred|"
        r"proof\s+of\s+(?:remote\s+postgres\s+)?activation)\b",
        re.I,
    ),
    re.compile(
        r"\bnot\s+(?:accepted\s+as\s+)?proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+acceptance\s+of\s+readiness\s+(?:artifacts?|evidence|prerequisites)\s+as\s+"
        r"proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b(?:are|is)\s+"
        r"(?:(?:prerequisites|prerequisites\s+only)(?:\s+and|,)?\s+)?"
        r"not\s+proof\s+activation\s+occurred\b",
        re.I,
    ),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b(?:are|is)\s+"
        r"not\s+(?:(?:remote\s+postgres\s+)?activation\s+)?proof\b",
        re.I,
    ),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\b"
        r"(?:does|do|did)\s+not\s+proves?\s+activation\b",
        re.I,
    ),
    re.compile(
        r"\b(?:does|do|did)\s+not\s+"
        r"(?:add|implement|enable|authorize|perform|claim|include|satisfy)\b.*\b"
        r"(?:activation|migration|cutover|provisioning|host\s+mutation|runtime\s+audit)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no|without)\b.*\b(?:activation|migration\s+execution|"
        r"live\s+database\s+cutover|database\s+cutover|provisioning|"
        r"production\s+host\s+mutation|host\s+mutation|"
        r"compose/profile\s+activation|plaintext\s+fallback)\b.*\b"
        r"(?:is|are)\s+(?:performed|claimed|added|enabled|authorized|implemented|supplied|accepted)\b",
        re.I,
    ),
    re.compile(
        r"\bnot\s+live\s+(?:production\s+)?activation\b",
        re.I,
    ),
)
CLAUSE_BOUNDARY_PATTERN = re.compile(r"[.;]|(?:\s+[—–-]\s+)")
CLAIM_CONTEXT_BOUNDARY_PATTERN = re.compile(
    r"[,.;:]|(?:\s+[—–-]\s+)|(?:\s+\b(?:but|and)\b\s+)",
    re.I,
)
DIRECT_NEGATION_BOUNDARY_PATTERN = CLAIM_CONTEXT_BOUNDARY_PATTERN
NEGATED_LIST_PREFIX_PATTERN = re.compile(r"\b(?:no|without|not|never)\b", re.I)
POSITIVE_ACTION_CLAIM_PATTERN = re.compile(
    r"\b(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
    r"performed|executed|ran|run|applied|serving|done|provisioned|migrated|cut\s+over|"
    r"proves?|proved|proven)\b",
    re.I,
)
STRONG_POSITIVE_ACTIVATION_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:remote\s+postgres|postgres|database|migration|cutover|go[- ]live|activation|"
        r"provisioning|compose/profile)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"(?:(?:(?:is|are|was|were)\s+)?live|"
        r"(?:(?:is|are|was|were)\s+)?(?:active|activated|enabled|complete|completed|"
        r"successful|succeeded|occurred|performed|executed|serving(?:\s+traffic)?|done|"
        r"provisioned|migrated|cut\s+over|proves?|proved|proven))\b",
        re.I,
    ),
)
INLINE_CODE_OR_PATH_TOKEN_PATTERN = re.compile(
    r"`[^`]*`|\S*\.(?:json|md|py|ya?ml)\b\S*|"
    r"\b[a-z0-9]+(?:[-_][a-z0-9]+){2,}\b",
    re.I,
)
SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN = re.compile(
    r"\b(?:current_phase|epic-134|story[- ]134(?:\.\d+)?|134(?:\.\d+|-[a-z0-9][a-z0-9-]*)|"
    r"Epic\s+134)\b",
    re.I,
)
SPRINT_STATUS_EPIC_134_SECTION_PATTERN = re.compile(r"^\s*#\s*Epic\s+134\b", re.I)
SPRINT_STATUS_EPIC_SECTION_PATTERN = re.compile(r"^\s*#\s*Epic\s+\d+(?:\.\d+)?\b", re.I)
SPRINT_STATUS_AUDIT_TRAIL_PATTERN = re.compile(r"^audit_trail:\s*$")
SPRINT_STATUS_AUDIT_ITEM_PATTERN = re.compile(r"^  -\s+")
SPRINT_STATUS_BLOCK_SCALAR_PATTERN = re.compile(r"^(?P<indent>\s*)[A-Za-z0-9_]+:\s*[>|]-?\s*$")
ALLOWED_PRIOR_STORY_STATUS = {"done", "closed"}


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


def _lower_text(value: object) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        return " ".join(_lower_text(item) for pair in value.items() for item in pair)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_lower_text(item) for item in value)
    return str(value).lower()


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


def _matched_clause_bounds(line: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for boundary in CLAUSE_BOUNDARY_PATTERN.finditer(line):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            return left, boundary.start()
    return left, len(line)


def _matched_clause(line: str, start: int, end: int) -> str:
    left, right = _matched_clause_bounds(line, start, end)
    return line[left:right]


def _matched_claim_context_bounds(line: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for boundary in CLAIM_CONTEXT_BOUNDARY_PATTERN.finditer(line):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            return left, boundary.start()
    return left, len(line)


def _matched_claim_context(line: str, start: int, end: int) -> str:
    left, right = _matched_claim_context_bounds(line, start, end)
    return line[left:right]


def _claim_context_ranges(line: str, start: int, end: int) -> Iterable[tuple[int, int]]:
    left = start
    for boundary in CLAIM_CONTEXT_BOUNDARY_PATTERN.finditer(line):
        if boundary.end() <= start:
            continue
        if boundary.start() >= end:
            break
        if left < boundary.start():
            yield left, boundary.start()
        left = boundary.end()
    if left < end:
        yield left, end


def _has_claim_context_boundary(line: str, start: int, end: int) -> bool:
    return any(
        boundary.start() > start and boundary.end() < end
        for boundary in CLAIM_CONTEXT_BOUNDARY_PATTERN.finditer(line)
    )


def _direct_negation_segment_start(line: str, start: int) -> int:
    left = 0
    for boundary in DIRECT_NEGATION_BOUNDARY_PATTERN.finditer(line):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= start:
            break
    return left


def _ranges_overlap(first_start: int, first_end: int, second_start: int, second_end: int) -> bool:
    return first_start < second_end and second_start < first_end


def _is_directly_negated_claim(line: str, start: int, end: int) -> bool:
    context_start, context_end = _matched_claim_context_bounds(line, start, end)
    context = line[context_start:context_end]
    relative_start = start - context_start
    relative_end = end - context_start
    if any(
        match.start() <= relative_start and match.end() >= relative_end
        for pattern in DIRECT_NEGATED_CLAIM_PATTERNS
        for match in pattern.finditer(context)
    ):
        return True
    claim_text = line[start:end]
    if POSITIVE_ACTION_CLAIM_PATTERN.search(claim_text):
        return False
    segment_start = _direct_negation_segment_start(line, start)
    return NEGATED_LIST_PREFIX_PATTERN.search(line[segment_start:start]) is not None


def _is_forbidden_activation_claim(line: str, start: int, end: int) -> bool:
    if _has_claim_context_boundary(line, start, end):
        for segment_start, segment_end in _claim_context_ranges(line, start, end):
            segment = line[segment_start:segment_end]
            if not POSITIVE_ACTION_CLAIM_PATTERN.search(segment):
                continue
            if any(
                _is_forbidden_activation_claim(
                    line,
                    segment_start + match.start(),
                    segment_start + match.end(),
                )
                for pattern in ACTIVATION_OVERCLAIM_PATTERNS
                for match in pattern.finditer(segment)
            ):
                return True
        return False
    if _is_directly_negated_claim(line, start, end):
        return False
    context = _matched_claim_context(line, start, end)
    if any(
        pattern.search(line[start:end]) for pattern in STRONG_POSITIVE_ACTIVATION_CLAIM_PATTERNS
    ):
        return not _is_safe_context(context)
    return not (_is_safe_context(context) or _is_safe_context(_matched_clause(line, start, end)))


def _is_forbidden_direct_activation_claim(line: str, start: int, end: int) -> bool:
    return not _is_directly_negated_claim(line, start, end)


def _claim_scan_line(line: str) -> str:
    return INLINE_CODE_OR_PATH_TOKEN_PATTERN.sub(" ", line)


def _sprint_status_audit_item_ranges(lines: Sequence[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    in_audit_trail = False
    item_start: int | None = None
    for index, line in enumerate(lines):
        if not in_audit_trail:
            in_audit_trail = SPRINT_STATUS_AUDIT_TRAIL_PATTERN.match(line) is not None
            continue

        if line and not line[0].isspace():
            if item_start is not None:
                ranges.append((item_start, index))
            item_start = None
            in_audit_trail = SPRINT_STATUS_AUDIT_TRAIL_PATTERN.match(line) is not None
            continue
        if SPRINT_STATUS_AUDIT_ITEM_PATTERN.match(line):
            if item_start is not None:
                ranges.append((item_start, index))
            item_start = index
    if item_start is not None:
        ranges.append((item_start, len(lines)))
    return ranges


def _add_sprint_status_audit_item_scan_units(
    scan_units: list[tuple[int, str]], lines: Sequence[str], start: int, end: int
) -> set[int]:
    if not any(
        SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN.search(lines[index]) for index in range(start, end)
    ):
        return set()

    consumed: set[int] = set()
    index = start
    while index < end:
        match = SPRINT_STATUS_BLOCK_SCALAR_PATTERN.match(lines[index])
        if not match:
            index += 1
            continue

        indent = len(match.group("indent"))
        scalar_lines: list[str] = []
        scalar_indexes: list[int] = [index]
        cursor = index + 1
        while cursor < end:
            child = lines[cursor]
            if child and len(child) - len(child.lstrip()) <= indent:
                break
            scalar_indexes.append(cursor)
            stripped = child.strip()
            if stripped:
                scalar_lines.append(stripped)
            cursor += 1
        consumed.update(scalar_indexes)
        if scalar_lines:
            scan_units.append((index + 1, " ".join(scalar_lines)))
        index = cursor

    for index in range(start, end):
        if index not in consumed and lines[index].strip():
            scan_units.append((index + 1, lines[index]))
    return set(range(start + 1, end + 1))


def _sprint_status_claim_scan_units(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    scan_line_numbers = {
        line_no
        for line_no, line in enumerate(lines, start=1)
        if SPRINT_STATUS_DIRECT_RELEVANCE_PATTERN.search(line)
    }

    in_epic_134_section = False
    for line_no, line in enumerate(lines, start=1):
        if in_epic_134_section and line and not line.startswith("  "):
            in_epic_134_section = False
        if SPRINT_STATUS_EPIC_134_SECTION_PATTERN.search(line):
            in_epic_134_section = True
        elif in_epic_134_section and SPRINT_STATUS_EPIC_SECTION_PATTERN.search(line):
            in_epic_134_section = False
        if in_epic_134_section:
            scan_line_numbers.add(line_no)

    scan_units: list[tuple[int, str]] = []
    audit_line_numbers: set[int] = set()
    for item_start, item_end in _sprint_status_audit_item_ranges(lines):
        audit_line_numbers.update(
            _add_sprint_status_audit_item_scan_units(scan_units, lines, item_start, item_end)
        )
    for line_no in sorted(scan_line_numbers - audit_line_numbers):
        scan_units.append((line_no, lines[line_no - 1]))
    return scan_units


def _scan_claims_for_forbidden(relpath: Path, line_no: int, line: str) -> list[Violation]:
    violations: list[Violation] = []
    claim_line = _claim_scan_line(line)
    for pattern in ACTIVATION_OVERCLAIM_PATTERNS:
        if any(
            _is_forbidden_activation_claim(claim_line, match.start(), match.end())
            for match in pattern.finditer(claim_line)
        ):
            violations.append(
                Violation(f"{relpath}:{line_no}", "activation overclaim is not allowed")
            )
            break
    for pattern in MIGRATION_EXECUTION_CLAIM_PATTERNS:
        if any(
            _is_forbidden_direct_activation_claim(claim_line, match.start(), match.end())
            for match in pattern.finditer(claim_line)
        ):
            violations.append(
                Violation(f"{relpath}:{line_no}", "activation overclaim is not allowed")
            )
            break
    for pattern in ACTIVATION_PROOF_NOUN_CLAIM_PATTERNS:
        if any(
            _is_forbidden_direct_activation_claim(claim_line, match.start(), match.end())
            for match in pattern.finditer(claim_line)
        ):
            violations.append(
                Violation(f"{relpath}:{line_no}", "activation overclaim is not allowed")
            )
            break
    for pattern in READINESS_AS_PROOF_PATTERNS:
        if any(
            not _is_directly_negated_claim(claim_line, match.start(), match.end())
            for match in pattern.finditer(claim_line)
        ):
            violations.append(
                Violation(f"{relpath}:{line_no}", "readiness-as-proof language is not allowed")
            )
            break
    for pattern in SELF_ATTESTATION_PATTERNS:
        if any(
            not _is_safe_context(_matched_claim_context(claim_line, match.start(), match.end()))
            for match in pattern.finditer(claim_line)
        ):
            violations.append(
                Violation(f"{relpath}:{line_no}", "self-attestation acceptance is not allowed")
            )
            break
    return violations


def _scan_text_for_forbidden(relpath: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _contains_secret_value(line):
            violations.append(Violation(f"{relpath}:{line_no}", "secret-like value is not allowed"))

    if relpath == SPRINT_STATUS_PATH:
        scan_units = _sprint_status_claim_scan_units(text)
    else:
        scan_units = list(enumerate(text.splitlines(), start=1))
    for line_no, line in scan_units:
        violations.extend(_scan_claims_for_forbidden(relpath, line_no, line))
    return violations


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if missing := REQUIRED_TOP_LEVEL_SECTIONS - set(data):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required sections missing {sorted(missing)}")
        )
    if data.get("schema_version") != "story-134.3/v1":
        violations.append(Violation(str(CONTRACT_PATH), "schema_version must be story-134.3/v1"))
    if data.get("phase") != "51" or data.get("epic") != "134" or data.get("story") != "134.3":
        violations.append(Violation(str(CONTRACT_PATH), "phase/epic/story must be 51/134/134.3"))
    if (
        data.get("mode")
        != "static_remote_postgres_activation_smoke_migration_evidence_contract_not_activation"
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "mode must be remote Postgres activation smoke/migration evidence contract, "
                "not activation",
            )
        )

    boundary = _section(data, "activation_boundary")
    if boundary.get("activation_performed") is not False:
        violations.append(Violation(str(CONTRACT_PATH), "activation_performed must be false"))
    boundary_text = _lower_text(boundary)
    for phrase in (
        "future/operator-gated",
        "not proof activation occurred",
        "no remote postgres activation",
        "no migration execution",
        "no live database cutover",
        "compose/profile activation",
        "plaintext fallback",
    ):
        if phrase not in boundary_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"activation boundary missing {phrase!r}")
            )

    gate = _section(data, "operator_gate")
    if gate.get("required") is not True:
        violations.append(Violation(str(CONTRACT_PATH), "operator gate must be required"))
    if missing := REQUIRED_OPERATOR_GATE_FIELDS - _string_set(gate.get("fields")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"operator gate fields missing {sorted(missing)}")
        )
    gate_text = _lower_text(gate)
    for phrase in (
        "operator-gated",
        "timestamped",
        "redacted",
        "change window",
        "rollback",
        "fix-forward",
        "remote postgres endpoint",
        "migration runner",
        "backup/restore",
    ):
        if phrase not in gate_text:
            violations.append(Violation(str(CONTRACT_PATH), f"operator gate missing {phrase!r}"))

    readiness = _section(data, "readiness_prerequisites")
    if readiness.get("semantics") != "prerequisites_only_not_activation_proof":
        violations.append(
            Violation(str(CONTRACT_PATH), "readiness prerequisites must not be activation proof")
        )
    if missing := REQUIRED_READINESS_REFS - _string_set(readiness.get("minimum_refs")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"readiness refs missing {sorted(missing)}")
        )

    future_contract = _section(_section(data, "future_smoke_evidence_contract"), "required_domains")
    if missing := REQUIRED_DOMAINS - set(future_contract):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required smoke evidence domains missing {sorted(missing)}"
            )
        )
    for domain in REQUIRED_DOMAINS & set(future_contract):
        entry = _section(future_contract, domain)
        if entry.get("required") is not True:
            violations.append(Violation(str(CONTRACT_PATH), f"{domain} must be required"))
        if entry.get("not_activation_proof_by_itself") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{domain} must not be activation proof by itself")
            )
        if len(_string_set(entry.get("minimum_evidence"))) < 2:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{domain} minimum evidence is incomplete")
            )

    if missing := REQUIRED_FAIL_CLOSED_CHECKS - _string_set(data.get("fail_closed_checks")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail-closed checks missing {sorted(missing)}")
        )
    redaction_text = _lower_text(_section(data, "redaction_and_secret_hygiene"))
    for phrase in (
        "no plaintext secrets",
        "credential values",
        "unredacted connection strings",
        "database endpoint identity",
        "private key",
        "certificate material",
        "redaction_report_ref",
    ):
        if phrase not in redaction_text:
            violations.append(Violation(str(CONTRACT_PATH), f"redaction policy missing {phrase!r}"))
    non_goals_text = _lower_text(data.get("non_goals", []))
    for phrase in (
        "no remote postgres activation",
        "no provisioning",
        "migration execution",
        "production host mutation",
        "live database cutover",
        "compose/profile activation",
        "plaintext fallback",
        "runtime behavior change",
        "dependency change",
        "lockfile change",
        "production-state change",
        "no acceptance of readiness prerequisites as proof activation occurred",
    ):
        if phrase not in non_goals_text:
            violations.append(Violation(str(CONTRACT_PATH), f"non-goals missing {phrase!r}"))
    if missing := REQUIRED_DOC_REFS - _string_set(data.get("docs_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"docs refs missing {sorted(missing)}"))
    if missing := REQUIRED_STATUS_REFS - _string_set(data.get("status_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"status refs missing {sorted(missing)}"))
    for ref in sorted(_string_set(data.get("docs_refs")) | _string_set(data.get("status_refs"))):
        violations.extend(_validate_ref_target(root, ref))
    return violations


def _recipe_body(just: str, recipe: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(recipe)}:\n(?P<body>.*?)(?=^\S|\Z)", just)
    return match.group("body") if match else ""


def _ci_has_command(text: str, command: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            stripped = stripped.removeprefix("run:").strip()
        if stripped.startswith("-"):
            stripped = stripped.removeprefix("-").strip()
        if stripped == command:
            return True
    return False


def _has_story_134_6_planning_closure(root: Path) -> bool:
    closure_path = root / CLOSURE_ARTIFACT_PATH
    if not closure_path.exists():
        return False
    closure_text = _read(root, CLOSURE_ARTIFACT_PATH)
    return all(
        phrase in closure_text
        for phrase in ("planning-only/docs-status", "not activation", "future/operator-gated")
    )


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    just = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    for recipe in ("lint", "check-gates"):
        if not _ci_has_command(_recipe_body(just, recipe), CHECKER_COMMAND):
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{recipe} must run Story 134.3 checker")
            )
    if not _ci_has_command(_recipe_body(just, "check-gates-self-test"), CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(
                str(JUSTFILE_PATH), "check-gates-self-test must run Story 134.3 checker self-test"
            )
        )
    if not _ci_has_command(ci, CHECKER_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI static checks must run Story 134.3 checker"))
    if not _ci_has_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(str(CI_PATH), "CI self-tests must run Story 134.3 checker self-test")
        )
    return violations


def _validate_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    sprint = _read(root, SPRINT_STATUS_PATH)
    story_134_3 = re.search(
        r"(?m)^\s*134-3-remote-postgres-activation-smoke-migration-evidence-package:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_134_3 or story_134_3.group("status") != "done":
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.3 must be done"))
    story_134_1 = re.search(
        r"(?m)^\s*134-1-activation-evidence-schema-preflight-gate:\s*(?P<status>\S+)", sprint
    )
    if not story_134_1 or story_134_1.group("status") not in ALLOWED_PRIOR_STORY_STATUS:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.1 must remain done/closed"))
    story_134_2 = re.search(
        r"(?m)^\s*134-2-split-deployment-activation-smoke-evidence-package:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_134_2 or story_134_2.group("status") not in ALLOWED_PRIOR_STORY_STATUS:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.2 must remain done/closed"))
    epic_134 = re.search(r"(?m)^\s*epic-134:\s*(?P<status>\S+)", sprint)
    epic_134_status = epic_134.group("status") if epic_134 else None
    closure_exists = _has_story_134_6_planning_closure(root)
    if epic_134_status != "in-progress" and not (
        epic_134_status in {"done", "closed"} and closure_exists
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Epic 134 must remain in-progress unless Story 134.6 planning-only closure exists",
            )
        )

    story_134_4 = re.search(
        r"(?m)^\s*134-4-registry-db-mtls-activation-smoke-failure-evidence-package:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_134_4 or story_134_4.group("status") not in ALLOWED_PRIOR_STORY_STATUS:
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Story 134.4 must remain done/closed once DB mTLS evidence planning lands",
            )
        )

    story_134_5 = re.search(
        r"(?m)^\s*134-5-combined-split-remote-postgres-db-mtls-rehearsal:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_134_5 or story_134_5.group("status") not in ALLOWED_PRIOR_STORY_STATUS:
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Story 134.5 must remain done/closed once combined rehearsal evidence planning lands",
            )
        )

    story_134_6 = re.search(
        r"(?m)^\s*134-6-controlled-activation-closure-go-no-go-evidence:\s*(?P<status>\S+)",
        sprint,
    )
    story_134_6_status = story_134_6.group("status") if story_134_6 else None
    if story_134_6_status != "backlog" and not (
        story_134_6_status in {"done", "closed"} and closure_exists
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "134-6-controlled-activation-closure-go-no-go-evidence must remain backlog unless planning-only closure exists",
            )
        )

    feature = _read(root, FEATURE_STATUS_PATH)
    for phrase in (
        "Story 134.3",
        "complete locally",
        CHECKER_COMMAND,
        "future/operator-gated",
        "not proof activation occurred",
        "No remote Postgres activation",
        "No migration execution",
        "No live database cutover",
    ):
        if phrase not in feature:
            violations.append(
                Violation(str(FEATURE_STATUS_PATH), f"feature status missing {phrase!r}")
            )
    if not (
        "remote Postgres activation smoke and migration evidence package" in feature
        or "remote Postgres smoke/migration evidence" in feature
    ):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "feature status missing remote Postgres smoke/migration evidence package",
            )
        )
    overview = _read(root, PROJECT_OVERVIEW_PATH)
    for phrase in (
        "Story 134.3",
        "complete locally",
        "future/operator-gated",
        "no live activation",
        "no live database cutover",
    ):
        if phrase not in overview:
            violations.append(
                Violation(str(PROJECT_OVERVIEW_PATH), f"project overview missing {phrase!r}")
            )
    artifact = _read(root, ARTIFACT_PATH)
    for phrase in (
        "No remote Postgres activation",
        CHECKER_COMMAND,
        CHECKER_SELF_TEST_COMMAND,
        "uv run pytest tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py",
        "uv run ruff check scripts/check_remote_postgres_activation_smoke_migration_evidence.py tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py",
    ):
        if phrase not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"story artifact missing {phrase!r}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"failed to load contract: {exc}")]
    violations.extend(_validate_contract(root, data))
    for relpath in SECRET_SCAN_PATHS:
        try:
            violations.extend(_scan_text_for_forbidden(relpath, _read(root, relpath)))
        except OSError as exc:
            violations.append(Violation(str(relpath), f"failed to read for scan: {exc}"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract contains secret-like material")
            )
            break
    violations.extend(_validate_wiring(root))
    violations.extend(_validate_status(root))
    return violations


def _copy_fixture(root: Path, dest: Path) -> None:
    for relpath in (
        CONTRACT_PATH,
        FEATURE_STATUS_PATH,
        PROJECT_OVERVIEW_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        CLOSURE_ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    ):
        src = root / relpath
        if relpath == CLOSURE_ARTIFACT_PATH and not src.exists():
            continue
        dst = dest / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _self_test() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-postgres-activation-smoke-migration-evidence-"
    ) as tmp:
        root = Path(tmp)
        _copy_fixture(REPO_ROOT, root)
        baseline = validate(root)
        if baseline:
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        domains = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", data["future_smoke_evidence_contract"])["required_domains"],
        )
        domains.pop("migration_preconditions")
        _write_json(root / CONTRACT_PATH, data)
        if not any("required smoke evidence domains missing" in v.message for v in validate(root)):
            print("self-test failed: missing required domain was not rejected", file=sys.stderr)
            return 1
        _copy_fixture(REPO_ROOT, root)
        status_path = root / FEATURE_STATUS_PATH
        status_path.write_text(
            status_path.read_text(encoding="utf-8")
            + "\nRemote Postgres cutover completed successfully.\n",
            encoding="utf-8",
        )
        if not any("activation overclaim" in v.message for v in validate(root)):
            print("self-test failed: activation overclaim was not rejected", file=sys.stderr)
            return 1
        for unsafe_text in (
            "No remote Postgres activation is performed and remote Postgres activation is enabled.",
            "Future/operator-gated planning only and remote Postgres activation occurred.",
            "Remote Postgres activation proven.",
            "Database proved activation.",
            "Remote Postgres migration ran.",
            "Remote Postgres migrations have been run.",
            "Remote Postgres migration has run.",
            "Remote Postgres migration was run.",
            "Remote Postgres migration has been run.",
            "Remote Postgres migration has successfully been run.",
            "Remote Postgres migration is run.",
            "Remote Postgres migration was successfully run.",
            "Remote Postgres migration did run.",
            "Remote Postgres migrations were applied.",
            "Alembic applied migrations against remote Postgres.",
            "Alembic has applied migrations to remote Postgres.",
            "Alembic previously applied database migrations to remote Postgres.",
            "Migration tool ran migrations for remote Postgres.",
            "Database tool executed migrations against remote Postgres.",
            "Migration ran for remote Postgres.",
            "Migration already ran for remote Postgres.",
            "Migration has been run for remote Postgres.",
            "Alembic migrations ran for remote Postgres.",
            "Database migrations ran against remote Postgres.",
            "Alembic migration did execute against remote Postgres.",
            "Future/operator-gated planning only Remote Postgres migration has been run.",
            "Remote Postgres activation proof exists.",
            "Remote Postgres activation proof was submitted.",
            "Remote Postgres activation proof was present.",
            "Remote Postgres activation proof available.",
            "Remote Postgres activation proof is now present.",
            "Remote Postgres activation evidence exists.",
            "Activation evidence for remote Postgres was submitted.",
            "Activation proof for remote Postgres was uploaded.",
            "Activation evidence for remote Postgres exists.",
            "Activation evidence for remote Postgres is now available.",
            "Activation evidence was submitted for remote Postgres.",
            "Activation proof was uploaded for remote Postgres.",
            "Activation evidence has been documented for remote Postgres.",
            "Activation proof is available for remote Postgres.",
            "Proof of remote Postgres activation exists.",
            "Proof of remote Postgres activation was uploaded.",
            "Proof of remote Postgres activation was available.",
            "Proof of remote Postgres activation has now been accepted.",
            "Proof was submitted for remote Postgres activation.",
            "Proof was accepted for remote Postgres activation.",
            "Evidence of remote Postgres activation was present.",
            "Evidence was recorded for remote Postgres activation.",
            "Evidence has been provided for remote Postgres activation.",
            "Evidence for remote Postgres activation was present.",
            "Evidence of remote Postgres activation has been recorded.",
            "Remote Postgres evidence of activation was available.",
            "Remote Postgres proof of activation exists.",
            "Future/operator-gated planning only Remote Postgres activation proof was present.",
        ):
            _copy_fixture(REPO_ROOT, root)
            status_path = root / FEATURE_STATUS_PATH
            status_path.write_text(
                status_path.read_text(encoding="utf-8") + f"\n{unsafe_text}\n",
                encoding="utf-8",
            )
            if not any("activation overclaim" in v.message for v in validate(root)):
                print(
                    "self-test failed: conjunction-masked activation overclaim was not rejected",
                    file=sys.stderr,
                )
                return 1
        for unsafe_text in (
            "Readiness artifacts prove remote Postgres activation.",
            "Readiness artifacts are not proof activation occurred, but readiness artifacts prove remote Postgres activation.",
        ):
            _copy_fixture(REPO_ROOT, root)
            status_path = root / FEATURE_STATUS_PATH
            status_path.write_text(
                status_path.read_text(encoding="utf-8") + f"\n{unsafe_text}\n",
                encoding="utf-8",
            )
            if not any("readiness-as-proof" in v.message for v in validate(root)):
                print(
                    "self-test failed: readiness proof overclaim was not rejected",
                    file=sys.stderr,
                )
                return 1
        _copy_fixture(REPO_ROOT, root)
        sprint_status_path = root / SPRINT_STATUS_PATH
        sprint_status_path.write_text(
            sprint_status_path.read_text(encoding="utf-8").replace(
                "      remote Postgres activation smoke and migration evidence only. It performs no remote",
                "      Postgres activation proof was submitted for remote Postgres activation.",
                1,
            ),
            encoding="utf-8",
        )
        if not any("activation overclaim" in v.message for v in validate(root)):
            print(
                "self-test failed: Story 134.3 sprint-status audit continuation overclaim was not rejected",
                file=sys.stderr,
            )
            return 1
        _copy_fixture(REPO_ROOT, root)
        sprint_status_path = root / SPRINT_STATUS_PATH
        sprint_status_path.write_text(
            sprint_status_path.read_text(encoding="utf-8")
            + "\npr_note: PR #134 remote Postgres activation proof was submitted\n",
            encoding="utf-8",
        )
        if any("activation overclaim" in v.message for v in validate(root)):
            print(
                "self-test failed: unrelated sprint-status PR #134 note was scanned "
                "as Story/Epic 134",
                file=sys.stderr,
            )
            return 1
        for safe_text in (
            "migration runner",
            "single migration runner",
            "migration preconditions",
            "not activation proof",
            "not proof of activation",
            "Readiness artifacts are not activation proof.",
            "Activation proof was not submitted for remote Postgres.",
            "Evidence was not recorded for remote Postgres activation.",
        ):
            _copy_fixture(REPO_ROOT, root)
            status_path = root / FEATURE_STATUS_PATH
            status_path.write_text(
                status_path.read_text(encoding="utf-8") + f"\n{safe_text}\n",
                encoding="utf-8",
            )
            if validate(root):
                print(
                    "self-test failed: negated readiness proof wording was rejected",
                    file=sys.stderr,
                )
                return 1
        _copy_fixture(REPO_ROOT, root)
        ci_path = root / CI_PATH
        ci_path.write_text(
            ci_path.read_text(encoding="utf-8").replace(CHECKER_SELF_TEST_COMMAND, "", 1),
            encoding="utf-8",
        )
        if not any("CI self-tests" in v.message for v in validate(root)):
            print("self-test failed: missing CI self-test wiring was not rejected", file=sys.stderr)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run checker fixture self-test")
    args = parser.parse_args(argv)
    violations = validate(REPO_ROOT) if not args.self_test else []
    if args.self_test and _self_test() != 0:
        return 1
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1
    if args.self_test:
        print("remote Postgres activation smoke and migration evidence checker self-test passed")
    else:
        print(
            "remote Postgres activation smoke and migration evidence contract/status checks passed; not activation proof"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
