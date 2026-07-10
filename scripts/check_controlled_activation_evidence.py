#!/usr/bin/env python3
"""Validate Story 134.1 controlled activation evidence schema/preflight status."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/controlled-activation-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PROJECT_OVERVIEW_PATH = Path("docs/project-overview.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_controlled_activation_evidence.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "schema_version",
        "phase",
        "epic",
        "story",
        "mode",
        "activation_boundary",
        "evidence_package_contract",
        "staleness_policy",
        "redaction_and_secret_hygiene",
        "fail_closed_checks",
        "non_goals",
        "future_story_refs",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_EVIDENCE_FIELDS = frozenset(
    {
        "operator_approval_ref",
        "security_approval_ref",
        "change_window_utc",
        "target_environment",
        "target_service",
        "target_version",
        "readiness_prerequisites",
        "smoke_scope",
        "rollback_owner",
        "rollback_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "generated_at_utc",
        "expires_at_utc",
        "trace_correlation",
        "redaction_report_ref",
        "activation_intent",
        "evidence_retention",
        "redaction_statement",
        "independent_reviewer_ref",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "missing_evidence_fails_closed",
        "stale_evidence_fails_closed",
        "malformed_evidence_fails_closed",
        "self_attestation_rejected",
        "secret_like_material_rejected",
        "activation_overclaim_rejected",
        "readiness_as_proof_rejected",
        "plaintext_fallback_rejected",
        "status_docs_story_134_1_done_required",
        "epic_134_in_progress_required",
        "justfile_and_ci_wiring_required",
    }
)
REQUIRED_FUTURE_STORIES = frozenset({"134.2", "134.3", "134.4", "134.5", "134.6"})
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
STATUS_SCAN_PATHS = (FEATURE_STATUS_PATH, PROJECT_OVERVIEW_PATH, SPRINT_STATUS_PATH, ARTIFACT_PATH)
SECRET_SCAN_PATHS = (CONTRACT_PATH, *STATUS_SCAN_PATHS)
POSTGRES_URL_PATTERN = re.compile(r"""(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>]+""")
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(
        r"(?i)(?:^|[^A-Za-z0-9_-])(?:[A-Za-z0-9]+_)*"
        r"(?:password|passwd|passphrase|secret(?:[_-]?key)?|token|credential|"
        r"private[_-]?key|certificate|api[_-]?key|bearer)"
        r"['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
    re.compile(
        r"(?:^|[^A-Za-z0-9_`])`?"
        r"(?:[A-Z0-9]+_)*(?:PASSWORD|PASSWD|PASSPHRASE|SECRET(?:_?KEY)?|"
        r"PRIVATE_?KEY|API_?KEY)`?"
        r"\s+(?:abc|shortkey|prodpass|[A-Za-z0-9_./+=-]*[0-9][A-Za-z0-9_./+=-]{2,}|[A-Za-z0-9_./+=-]{8,})"
    ),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)
SUSPICIOUS_SECRET_KEY_PARTS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "api",
        "key",
        "apikey",
        "bearer",
        "credential",
        "credentials",
        "private",
        "certificate",
        "cert",
    }
)
BENIGN_SECRET_CONTAINER_KEY_PARTS = frozenset(
    {"hygiene", "redaction", "policy", "policies", "report", "reports", "example", "examples"}
)
HIGH_CONFIDENCE_SECRET_CONTAINER_KEY_PARTS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "token",
        "apikey",
        "bearer",
        "credential",
        "credentials",
        "private",
        "certificate",
        "cert",
        "key",
        "api",
    }
)
STRUCTURED_SECRET_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_./+=-]{16,}")
STRUCTURED_SECRET_TEXT_PATTERN = re.compile(r"(?=.*[A-Za-z]).{16,}")
SAFE_PLACEHOLDER_VALUES = frozenset(
    {"redacted", "placeholder", "example", "changeme", "test", "none", "null"}
)
WEAK_EVIDENCE_VALUES = frozenset(
    {
        "",
        "tbd",
        "todo",
        "placeholder",
        "redacted",
        "example",
        "changeme",
        "change-me",
        "test",
        "none",
        "null",
        "n/a",
    }
)
WEAK_EVIDENCE_MARKER_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:tbd|todo|placeholder|example|changeme|change-me|n/a|redacted|"
    r"self[-_ ]review|self[-_ ]attestation|self[-_ ]attested)(?:$|[^a-z0-9])"
)
DISALLOWED_REVIEW_REFERENCE_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:self[-_ ]review|self[-_ ]attestation|self[-_ ]attested|"
    r"implementer[-_ ]review|implementer[-_ ]reviewer)(?:$|[^a-z0-9])"
)
ACTIVATION_OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:production\s+)?activation\b(?:\W+\w+){0,6}\W+"
        r"(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|done)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|done)\b"
        r"(?:\W+\w+){0,6}\W+\b(?:production\s+)?activation\b",
        re.I,
    ),
    re.compile(r"\bactivation\s+proof\s+(?:accepted|recorded|complete|available)\b", re.I),
    re.compile(
        r"\b(?:production\s+)?activation\b(?:\W+\w+){0,6}\W+"
        r"(?:live|active|serving(?:\s+traffic)?|enabled)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:controlled\s+)?production\s+cutover\b(?:\W+\w+){0,6}\W+"
        r"(?:complete|completed|successful|succeeded|occurred|performed|executed|live|active|serving(?:\s+traffic)?|done)\b",
        re.I,
    ),
    re.compile(
        r"\bcutover\b(?:\W+\w+){0,4}\W+"
        r"(?:complete|completed|successful|succeeded|occurred|performed|executed|done)\b",
        re.I,
    ),
    re.compile(
        r"\bgo[- ]live\b(?:\W+\w+){0,4}\W+"
        r"(?:complete|completed|successful|succeeded|occurred|performed|executed|live|active|done)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:complete|completed|successful|succeeded|occurred|performed|executed|done)\b"
        r"(?:\W+\w+){0,4}\W+\b(?:controlled\s+production\s+cutover|production\s+cutover|cutover|go[- ]live)\b",
        re.I,
    ),
)
READINESS_AS_PROOF_PATTERNS = (
    re.compile(r"\breadiness\s+artifacts?\b(?:\W+\w+){0,8}\W+\bproof\b", re.I),
    re.compile(r"\breadiness\s+artifacts?\b(?:\W+\w+){0,8}\W+\bproves?\s+activation\b", re.I),
    re.compile(r"\breadiness\s+evidence\b(?:\W+\w+){0,8}\W+\bproves?\s+activation\b", re.I),
    re.compile(r"\bprerequisites?\b(?:\W+\w+){0,8}\W+\bproof\s+activation\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence)\b(?:\W+\w+){0,8}\W+"
        r"\b(?:certif(?:y|ies|ied)|demonstrates?|demonstrated|establish(?:es|ed)?|shows?|confirms?|validates?)\b(?:\W+\w+){0,6}\W+"
        r"\b(?:(?:production\s+)?activation|(?:production\s+)?cutover|go[- ]live)\b",
        re.I,
    ),
)
SELF_ATTESTATION_PATTERNS = (
    re.compile(
        r"\bself[-_ ]attestation\b(?:\W+\w+){0,6}\W+"
        r"\b(?:accepted|allowed|sufficient|satisfies|passes)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:accepted|allowed|sufficient|satisfies|passes)\b"
        r"(?:\W+\w+){0,6}\W+\bself[-_ ]attestation\b",
        re.I,
    ),
    re.compile(
        r"\bself[-_ ]attested\b(?:\W+\w+){0,8}\W+"
        r"\b(?:accepted|allowed|sufficient|satisfies|passes|independent\s+reviewer)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:accepted|allowed|sufficient|satisfies|passes)\b"
        r"(?:\W+\w+){0,8}\W+\bself[-_ ]attested\b",
        re.I,
    ),
    re.compile(
        r"\bself[-_ ]review\b(?:\W+\w+){0,8}\W+"
        r"\b(?:accepted|allowed|sufficient|satisfies|passes|independent\s+reviewer)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:accepted|allowed|sufficient|satisfies|passes)\b"
        r"(?:\W+\w+){0,8}\W+\bself[-_ ]review\b",
        re.I,
    ),
)
PLAINTEXT_FALLBACK_PATTERNS = (
    re.compile(
        r"\bplaintext\s+fallback\s+(?:is\s+)?(?:allowed|available|enabled|accepted|approved|permitted)\b",
        re.I,
    ),
    re.compile(r"\bplaintext\s+fallback\s+(?:may|can)\s+be\s+used\b", re.I),
    re.compile(r"\bfall\s*back\s+to\s+plaintext\b", re.I),
    re.compile(
        r"\bssl[_.\s-]*mode\s*(?:=|:)?\s*[\"'`]?"
        r"(?:disabled?|false|off|allow|prefer|require|verify[-_ ]ca)[\"'`]?\b"
        r"(?:\s+(?:(?:is\s+)?(?:allowed|accepted|available|approved|permitted)|(?:may|can)\s+be\s+used)\b)?",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|rejects?|rejected|forbid(?:s|den)?|fail[- ]closed|"
    r"deferred|operator[- ]gated|future|planning|prevents?|overclaim|"
    r"prerequisites? only|not proof)\b",
    re.I,
)
ALLOWED_STORY_STATUS = frozenset({"done", "closed"})


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {_sanitize_for_output(self.message)}"


def _sanitize_for_output(text: str) -> str:
    sanitized = POSTGRES_URL_PATTERN.sub("[redacted-dsn]", text)
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", "[redacted-pem]", sanitized
    )
    return re.sub(
        r"(?i)(password|passwd|secret|token|api[_-]?key|bearer)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        sanitized,
    )


def _read(root: Path, relpath: Path) -> str:
    return (root / relpath).read_text(encoding="utf-8")


def _load_json(root: Path, relpath: Path) -> dict[str, Any]:
    with (root / relpath).open(encoding="utf-8") as f:
        data: object = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must be a JSON object")
    return cast("dict[str, Any]", data)


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_strings(str(key))
            yield from _walk_strings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_strings(item)


def _walk_key_values(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_key_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_key_values(item)


def _walk_key_value_paths(
    value: object, path: tuple[str, ...] = ()
) -> Iterable[tuple[tuple[str, ...], object]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            yield child_path, child
            yield from _walk_key_value_paths(child, child_path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_key_value_paths(item, path)


def _walk_value_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_value_strings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk_value_strings(item)


def _is_safe_secret_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in SAFE_PLACEHOLDER_VALUES or lowered.startswith(("redacted", "placeholder"))


def _structured_secret_violation(value: object) -> bool:
    for key, child in _walk_key_values(value):
        camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
        normalized = re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")
        parts = set(filter(None, normalized.split("_")))
        suspicious = bool(parts & SUSPICIOUS_SECRET_KEY_PARTS) or any(
            normalized.endswith(suffix) for suffix in ("password", "passphrase", "secret", "token")
        )
        if not suspicious:
            continue
        if (
            not isinstance(child, str)
            and parts & BENIGN_SECRET_CONTAINER_KEY_PARTS
            and not parts & HIGH_CONFIDENCE_SECRET_CONTAINER_KEY_PARTS
        ):
            continue
        for candidate in _walk_value_strings(child):
            if candidate.strip() and not _is_safe_secret_placeholder(candidate):
                return True
    return False


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name)
    return value if isinstance(value, Mapping) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {entry for entry in value if isinstance(entry, str)}


def _lower_text(value: object) -> str:
    return "\n".join(_walk_strings(value)).lower()


def _single_line_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _github_heading_anchor(heading: str) -> str:
    anchor = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", anchor)


def _markdown_heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = _github_heading_anchor(match.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _validate_ref_target(root: Path, ref: str) -> list[Violation]:
    relpath, _, anchor = ref.partition("#")
    if not relpath:
        return [Violation(str(CONTRACT_PATH), "reference missing file path")]
    path = root / relpath
    if not path.exists():
        return [Violation(str(CONTRACT_PATH), f"referenced file does not exist: {relpath}")]
    if not anchor:
        return [Violation(str(CONTRACT_PATH), "referenced docs/status entry must include anchor")]
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md" and anchor not in _markdown_heading_anchors(text):
        return [Violation(str(CONTRACT_PATH), f"referenced markdown anchor missing: {ref}")]
    if path.suffix != ".md" and not re.search(rf"(?m)^\s*{re.escape(anchor)}\s*:", text):
        return [Violation(str(CONTRACT_PATH), f"referenced structured anchor missing: {ref}")]
    return []


def _contains_unredacted_dsn(value: str) -> bool:
    # Story 134.1 forbids full/unredacted Postgres DSNs even when they omit a
    # password: host/database topology is activation evidence and must be
    # represented by redacted references instead.
    return POSTGRES_URL_PATTERN.search(value) is not None


def _contains_secret_value(value: str) -> bool:
    return _contains_unredacted_dsn(value) or any(
        pattern.search(value) for pattern in SECRET_VALUE_PATTERNS
    )


def _scoped_sprint_lines(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    selected: set[int] = set()
    in_epic = False
    audit_start: int | None = None
    audit_block: list[tuple[int, str]] = []

    def flush_audit_block() -> None:
        if not audit_block:
            return
        block_text = "\n".join(line for _, line in audit_block)
        if (
            "story: 134-1-activation-evidence-schema-preflight-gate" in block_text
            or "epic: epic-134" in block_text
        ):
            selected.update(idx for idx, _ in audit_block)

    for idx, line in enumerate(lines, start=1):
        if re.match(r"^\s*current_phase\s*:", line):
            selected.add(idx)
        if re.match(r"^\s*# Epic 134: Controlled production activation evidence planning", line):
            in_epic = True
        elif in_epic and (
            line.startswith("audit_trail:")
            or (re.match(r"^\s*# Epic \d+:", line) and "Epic 134" not in line)
            or (re.match(r"^\s*epic-\d+:", line) and "epic-134:" not in line)
        ):
            in_epic = False
        if in_epic:
            selected.add(idx)

        if line.startswith("audit_trail:"):
            audit_start = idx
            continue
        if audit_start is None or idx <= audit_start:
            continue
        if re.match(r"^\s*- date:", line):
            flush_audit_block()
            audit_block = [(idx, line)]
        elif audit_block:
            audit_block.append((idx, line))
    flush_audit_block()
    return [(idx, lines[idx - 1]) for idx in sorted(selected)]


def _scoped_status_lines(relpath: Path, text: str) -> list[tuple[int, str]]:
    if relpath == SPRINT_STATUS_PATH:
        return _scoped_sprint_lines(text)
    if relpath == FEATURE_STATUS_PATH:
        needles = (
            "Story 134.1",
            "Epic 134",
            "Phase 51",
            "controlled-activation-evidence",
            "134-1-activation-evidence",
        )
        forbidden_patterns = (
            *ACTIVATION_OVERCLAIM_PATTERNS,
            *READINESS_AS_PROOF_PATTERNS,
            *SELF_ATTESTATION_PATTERNS,
            *PLAINTEXT_FALLBACK_PATTERNS,
        )
        return [
            (idx, line)
            for idx, line in enumerate(text.splitlines(), start=1)
            if any(needle in line for needle in needles)
            or any(pattern.search(line) for pattern in forbidden_patterns)
        ]
    return list(enumerate(text.splitlines(), start=1))


SAFE_CONTEXT_PATTERNS = {
    "activation overclaim": (
        re.compile(r"\bnot\s+(?:proof\s+)?activation\s+(?:occurred|proof)\b", re.I),
        re.compile(r"\bnot\s+activation\b", re.I),
        re.compile(r"\bno\s+live\s+activation\b", re.I),
        re.compile(
            r"\bactivation\s+remains\s+(?:operator[- ]gated|deferred|future|fail[- ]closed)\b", re.I
        ),
        re.compile(
            r"\bproduction\s+activation\s+remains\s+(?:operator[- ]gated|deferred|future|fail[- ]closed)\b",
            re.I,
        ),
        re.compile(r"\bno\s+production\s+activation\s+proof\b", re.I),
        re.compile(r"\bwithout\s+performing\s+live\s+activation\b", re.I),
        re.compile(r"\bdoes\s+not\s+perform\s+live\s+activation\b", re.I),
        re.compile(
            r"\bNo acceptance of readiness prerequisites as proof activation occurred\b", re.I
        ),
        re.compile(
            r"\bStory 134\.1\b.*\bcomplete locally\b.*\b(?:docs/status|static[- ]checker|schema/preflight)\b",
            re.I,
        ),
        re.compile(
            r"\bStory 134\.2\b.*\bcomplete locally\b.*\bsplit[- ]deployment activation smoke evidence package\b",
            re.I,
        ),
        re.compile(
            r"\bsplit[- ]deployment activation smoke evidence package\b.*\b(?:docs/status|static[- ]checker|planning|contract)\b",
            re.I,
        ),
        re.compile(
            r"\b134-2-split-deployment-activation-smoke-evidence-package:\s*(?:done|closed)\b",
            re.I,
        ),
        re.compile(
            r"\bstory-134-2-split-deployment-activation-smoke-evidence-package-local-done\b",
            re.I,
        ),
        re.compile(r"\bactivation evidence schema/preflight (?:validation|gate)\b", re.I),
        re.compile(
            r"\bdocs/status/static[- ]checker\s+activation\s+evidence\s+schema\b.*\bno[- ]live[- ]activation\s+boundary\b",
            re.I,
        ),
        re.compile(
            r"\bproduction\s+activation\b.*\bremains?\b.*\b(?:operator[- ]gated|deferred|fail[- ]closed)\b",
            re.I,
        ),
        re.compile(
            r"\bproduction\s+activation\b.*\b(?:deferred|not enabled|fail[- ]closed)\b",
            re.I,
        ),
        re.compile(
            r"\breadiness-only/deferred activation,\s+"
            r"execution policy forbidding live drill execution\b",
            re.I,
        ),
        re.compile(
            r"\bactivation\b.*\bno\s+real\s+(?:writes?|operations?|controls?)\b.*\benabled\b",
            re.I,
        ),
        re.compile(
            r"\bcomplete\s+locally\b.*\bcontrolled\s+activation\s+evidence\s+planning\b.*\bin[- ]progress\b",
            re.I,
        ),
        re.compile(
            r"\bcomplete\s+locally\b.*\bcontrolled\s+production\s+activation\s+evidence\s+schema/preflight\s+validation\b",
            re.I,
        ),
        re.compile(
            r"\bproduction\s+activation\b.*\bremain\s+fail[- ]closed/deferred\b",
            re.I,
        ),
        re.compile(
            r"\bcomplete\s+locally\b.*\b(?:production\s+)?activation\b.*\bremains?\b.*\b(?:fail[- ]closed|deferred)\b",
            re.I,
        ),
    ),
    "readiness-as-proof": (
        re.compile(r"\breadiness\s+(?:artifacts?|evidence)\b.*\bnot\s+proof\b", re.I),
        re.compile(r"\bprerequisites?\s+only\b", re.I),
        re.compile(
            r"\bNo acceptance of readiness prerequisites as proof activation occurred\b", re.I
        ),
    ),
    "self-attestation": (
        re.compile(r"\bself[- ]attestation\b.*\brejected\b", re.I),
        re.compile(r"\brejects?\s+self[- ]attestation\b", re.I),
    ),
    "plaintext fallback": (
        re.compile(
            r"\bplaintext\s+fallback\s+remains?\s+(?:deferred|forbidden|fail[- ]closed)\b", re.I
        ),
        re.compile(r"\bno\s+plaintext\s+fallback\b", re.I),
        re.compile(r"\bplaintext\s+fallback\s+(?:is\s+)?(?:rejected|forbidden)\b", re.I),
        re.compile(
            r"\bssl[_\s-]*mode\s*(?:=|:)?\s*[\"'`]?"
            r"(?:disable|disabled|false|off|allow|prefer|require|verify[-_\s]ca)[\"'`]?\b"
            r".*\breject(?:ion|ed)?\b",
            re.I,
        ),
    ),
}


MIXED_ACTIVATION_OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:no\s+live\s+activation|does\s+not\s+perform\s+live\s+activation|"
        r"without\s+performing\s+live\s+activation)\b.{0,160}"
        r"\bactivation(?!\s+evidence)\b(?:\W+\w+){0,4}\W+"
        r"(?:(?:is\s+)?(?:live|active)|complete|completed|successful|succeeded|occurred|performed|executed|done)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no\s+live\s+activation|does\s+not\s+perform\s+live\s+activation|"
        r"without\s+performing\s+live\s+activation)\b.{0,160}"
        r"\b(?:complete|completed|successful|succeeded|occurred|performed|executed|done)\b"
        r".{0,40}\bactivation(?!\s+evidence)\b",
        re.I,
    ),
    re.compile(
        r"\bactivation\s+evidence\s+schema/preflight\s+gate\b.{0,160}"
        r"(?:\bproduction\s+activation\b.{0,40}\b(?:(?:is\s+)?(?:live|active)|serving(?:\s+traffic)?|complete|completed|successful|done)\b|"
        r"\b(?:complete|completed|successful|done)\b.{0,40}\bproduction\s+activation\b)",
        re.I,
    ),
    re.compile(
        r"\bproduction\s+activation\s+remains\s+deferred\b.{0,160}"
        r"\bproduction\s+activation\b.{0,40}\b(?:(?:is\s+)?(?:live|active)|serving(?:\s+traffic)?|complete|completed|successful|done)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no\s+live\s+activation|does\s+not\s+perform\s+live\s+activation|"
        r"without\s+performing\s+live\s+activation)\b.{0,80}"
        r"\b(?:(?:production\s+activation)(?!\s+evidence|\s+stories?|\s+planning)|"
        r"(?:controlled\s+)?production\s+cutover|cutover|go[- ]live)\b"
        r"(?:\W+\w+){0,5}\W+"
        r"(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|live|active|done)\b",
        re.I,
    ),
)


def _contains_mixed_activation_overclaim(line: str) -> bool:
    for idx, pattern in enumerate(MIXED_ACTIVATION_OVERCLAIM_PATTERNS):
        match = pattern.search(line)
        if not match:
            continue
        if idx in (0, 1) and re.search(
            r"\bactivation(?!\s+(?:evidence|proof|stories?|schema|planning|smoke))\b\s+"
            r"(?:(?:is\s+)?(?:live|active)|complete|completed|successful|succeeded|occurred|performed|executed|done)\b",
            line,
            re.I,
        ):
            return True
        if idx in (2, 3):
            return True
        if not _is_safe_forbidden_context("activation overclaim", line, match):
            return True
    return False


def _clause_for_match(line: str, match: re.Match[str]) -> str:
    start_candidates = [
        pos
        for pos in (line.rfind(";", 0, match.start()), line.rfind(".", 0, match.start()))
        if pos != -1
    ]
    start = max(start_candidates) + 1 if start_candidates else 0
    end_candidates = [
        pos for pos in (line.find(";", match.end()), line.find(".", match.end())) if pos != -1
    ]
    end = min(end_candidates) if end_candidates else len(line)
    return line[start:end]


def _is_safe_forbidden_context(kind: str, line: str, match: re.Match[str]) -> bool:
    clause = _clause_for_match(line, match)
    if kind == "activation overclaim":
        matched_text = match.group(0)
        if re.search(
            r"\b(?:production\s+activation(?!\s+evidence)|(?:controlled\s+)?production\s+cutover|cutover|go[- ]live)\b"
            r".{0,120}\b"
            r"(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|done)\b",
            matched_text,
            re.I,
        ):
            return False
        if not re.search(r"\b(?:complete|done)\s+locally\b", matched_text, re.I) and re.search(
            r"\b(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|done)\b"
            r".{0,120}\b(?:production\s+activation(?!\s+evidence)|(?:controlled\s+)?production\s+cutover|cutover|go[- ]live)\b",
            matched_text,
            re.I,
        ):
            return False
        if not re.search(
            r"\b(?:no\s+live\s+activation|not\s+activation|deferred|fail[- ]closed|operator[- ]gated|"
            r"readiness-only|forbidding\s+live|not\s+enabled|no\s+real)\b",
            matched_text,
            re.I,
        ) and re.search(
            r"\b(?:production\s+activation(?!\s+evidence)|activation(?!\s+evidence))\b"
            r"\s+(?:(?:is|now)\s+)?(?:live|active|serving(?:\s+traffic)?)\b$",
            matched_text,
            re.I,
        ):
            return False
    return any(pattern.search(clause) for pattern in SAFE_CONTEXT_PATTERNS[kind])


def _scan_text_for_forbidden(relpath: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _contains_secret_value(line):
            violations.append(Violation(f"{relpath}:{line_no}", "secret-like value is not allowed"))
    scan_lines = _scoped_status_lines(relpath, text)
    multiline_patterns = (
        *ACTIVATION_OVERCLAIM_PATTERNS,
        *READINESS_AS_PROOF_PATTERNS,
        *SELF_ATTESTATION_PATTERNS,
        *PLAINTEXT_FALLBACK_PATTERNS,
    )
    lines = text.splitlines()
    split_prefix_pattern = re.compile(
        r"\b(?:(?:production\s+)?activation|cutover|go[- ]live|readiness\s+artifacts?|"
        r"readiness\s+evidence|plaintext\s+fallback|ssl[_.\s-]*mode\s*(?:=|:)?"
        r"(?:\s*(?:disable|disabled|false|off|allow|prefer|require|verify[-_ ]ca))?)\s*$",
        re.I,
    )
    for idx, (first, second) in enumerate(zip(lines, lines[1:], strict=False), start=1):
        first_fragment = first.strip()
        if len(first_fragment) > 80 or not split_prefix_pattern.search(first_fragment):
            continue
        combined = _single_line_text(f"{first} {second}")
        if any(pattern.search(combined) for pattern in multiline_patterns):
            scan_lines.append((idx, combined))
    for line_no, line in scan_lines:
        location = f"{relpath}:{line_no}"
        if _contains_mixed_activation_overclaim(line) or re.search(
            r";\s*(?:production\s+)?activation\b.*"
            r"\b(?:complete|completed|successful|succeeded|occurred|performed|executed|activated|enabled|shipped|done)\b",
            line,
            re.I,
        ):
            violations.append(Violation(location, "activation overclaim is not allowed"))
        else:
            for pattern in ACTIVATION_OVERCLAIM_PATTERNS:
                if any(
                    not _is_safe_forbidden_context("activation overclaim", line, match)
                    for match in pattern.finditer(line)
                ):
                    violations.append(Violation(location, "activation overclaim is not allowed"))
                    break
        for pattern in READINESS_AS_PROOF_PATTERNS:
            if any(
                not _is_safe_forbidden_context("readiness-as-proof", line, match)
                for match in pattern.finditer(line)
            ):
                violations.append(Violation(location, "readiness-as-proof language is not allowed"))
                break
        for pattern in SELF_ATTESTATION_PATTERNS:
            if any(
                not _is_safe_forbidden_context("self-attestation", line, match)
                for match in pattern.finditer(line)
            ):
                violations.append(Violation(location, "self-attestation acceptance is not allowed"))
                break
        for pattern in PLAINTEXT_FALLBACK_PATTERNS:
            if any(
                not _is_safe_forbidden_context("plaintext fallback", line, match)
                for match in pattern.finditer(line)
            ):
                violations.append(
                    Violation(location, "plaintext fallback allowance is not allowed")
                )
                break
    return violations


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if missing := REQUIRED_TOP_LEVEL_SECTIONS - set(data):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"required controlled activation sections missing {sorted(missing)}",
            )
        )
    if data.get("schema_version") != "story-134.1/v1":
        violations.append(Violation(str(CONTRACT_PATH), "schema_version must be story-134.1/v1"))
    if data.get("phase") != "51" or data.get("epic") != "134" or data.get("story") != "134.1":
        violations.append(Violation(str(CONTRACT_PATH), "phase/epic/story must be 51/134/134.1"))
    if data.get("mode") != "static_status_contract_not_activation":
        violations.append(
            Violation(str(CONTRACT_PATH), "mode must be static/status contract, not activation")
        )

    boundary = _section(data, "activation_boundary")
    boundary_text = _lower_text(boundary)
    for phrase in ("not activation", "no live activation", "operator-gated", "future evidence"):
        if phrase not in boundary_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"activation boundary missing {phrase!r}")
            )
    if boundary.get("activation_performed") is not False:
        violations.append(Violation(str(CONTRACT_PATH), "activation_performed must be false"))

    required_fields = _section(_section(data, "evidence_package_contract"), "required_fields")
    if missing := REQUIRED_EVIDENCE_FIELDS - set(required_fields):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required evidence fields missing {sorted(missing)}")
        )
    readiness = _section(required_fields, "readiness_prerequisites")
    if readiness.get("semantics") != "prerequisites_only_not_activation_proof":
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "readiness prerequisites must be prerequisites only, not activation proof",
            )
        )
    if _section(required_fields, "independent_reviewer_ref").get("self_attestation") != "rejected":
        violations.append(
            Violation(str(CONTRACT_PATH), "independent reviewer must reject self-attestation")
        )
    for name in (
        "operator_approval_ref",
        "security_approval_ref",
        "activation_intent",
        "evidence_retention",
        "redaction_statement",
    ):
        if _section(required_fields, name).get("required") is not True:
            violations.append(Violation(str(CONTRACT_PATH), f"{name} must be required"))
    if _section(required_fields, "change_window_utc").get("timezone") != "UTC":
        violations.append(Violation(str(CONTRACT_PATH), "change window must be UTC-bound"))
    if _section(required_fields, "expires_at_utc").get("after") != "generated_at_utc":
        violations.append(
            Violation(str(CONTRACT_PATH), "expires_at_utc must be after generated_at_utc")
        )

    staleness = _section(data, "staleness_policy")
    if staleness.get("outcome") != "fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "staleness policy outcome must fail-closed")
        )
    if staleness.get("stale_evidence") != "reject":
        violations.append(Violation(str(CONTRACT_PATH), "stale evidence must reject"))

    redaction_text = _lower_text(_section(data, "redaction_and_secret_hygiene"))
    for phrase in (
        "no plaintext secrets",
        "redacted",
        "private key",
        "credential",
        "certificate material",
    ):
        if phrase not in redaction_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"redaction/secret hygiene missing {phrase!r}")
            )

    if missing := REQUIRED_FAIL_CLOSED_CHECKS - _string_set(data.get("fail_closed_checks")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail-closed checks missing {sorted(missing)}")
        )
    if missing := REQUIRED_FUTURE_STORIES - _string_set(data.get("future_story_refs")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"future story refs missing {sorted(missing)}")
        )
    if missing := REQUIRED_DOC_REFS - _string_set(data.get("docs_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"docs refs missing {sorted(missing)}"))
    if missing := REQUIRED_STATUS_REFS - _string_set(data.get("status_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"status refs missing {sorted(missing)}"))
    for ref in sorted(_string_set(data.get("docs_refs")) | _string_set(data.get("status_refs"))):
        violations.extend(_validate_ref_target(root, ref))
    if "activation" not in _lower_text(data.get("non_goals", [])):
        violations.append(
            Violation(str(CONTRACT_PATH), "non-goals must explicitly exclude activation")
        )
    return violations


def _recipe_body(just: str, recipe: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(recipe)}:\n(?P<body>.*?)(?=^\S|\Z)", just)
    return match.group("body") if match else ""


def _ci_has_command(ci: str, command: str) -> bool:
    for line in ci.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            stripped = stripped.removeprefix("run:").strip()
        if stripped.startswith("-"):
            stripped = stripped.removeprefix("-").strip()
        if stripped == command:
            return True
    return False


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    just = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    for recipe in ("lint", "check-gates"):
        if not _ci_has_command(_recipe_body(just, recipe), CHECKER_COMMAND):
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{recipe} must run controlled activation checker")
            )
    if not _ci_has_command(_recipe_body(just, "check-gates-self-test"), CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(
                str(JUSTFILE_PATH),
                "check-gates-self-test must run controlled activation checker self-test",
            )
        )
    if not _ci_has_command(ci, CHECKER_COMMAND):
        violations.append(
            Violation(str(CI_PATH), "CI static checks must run controlled activation checker")
        )
    if not _ci_has_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(
                str(CI_PATH), "CI self-tests must run controlled activation checker self-test"
            )
        )
    return violations


def _validate_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    sprint = _read(root, SPRINT_STATUS_PATH)
    story_match = re.search(
        r"(?m)^\s*134-1-activation-evidence-schema-preflight-gate:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_match or story_match.group("status") not in ALLOWED_STORY_STATUS:
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Story 134.1 activation evidence schema/preflight gate must be done/closed",
            )
        )
    epic_match = re.search(r"(?m)^\s*epic-134:\s*(?P<status>\S+)", sprint)
    if not epic_match or epic_match.group("status") != "in-progress":
        violations.append(
            Violation(str(SPRINT_STATUS_PATH), "Epic 134 must be in-progress after Story 134.1")
        )

    feature = _read(root, FEATURE_STATUS_PATH)
    if "Story 134.1" not in feature or "complete locally" not in feature:
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH), "feature status must mark Story 134.1 complete locally"
            )
        )
    if "Epic 134" not in feature or "in progress" not in feature:
        violations.append(
            Violation(str(FEATURE_STATUS_PATH), "feature status must mark Epic 134 in progress")
        )
    for required in (
        "future/operator-gated",
        "not proof activation occurred",
        "no live activation",
    ):
        if required not in feature:
            violations.append(
                Violation(str(FEATURE_STATUS_PATH), f"feature status missing {required!r}")
            )

    overview = _read(root, PROJECT_OVERVIEW_PATH)
    if "current repository state" not in overview.lower():
        violations.append(
            Violation(
                str(PROJECT_OVERVIEW_PATH), "project overview must retain current status summary"
            )
        )
    if "Story 134.1" not in overview or "complete locally" not in overview:
        violations.append(
            Violation(
                str(PROJECT_OVERVIEW_PATH),
                "project overview must note Story 134.1 complete locally",
            )
        )

    artifact = _read(root, ARTIFACT_PATH)
    for phrase in (
        "No live activation",
        CHECKER_SELF_TEST_COMMAND,
        CHECKER_COMMAND,
        "uv run pytest tests/scripts/test_check_controlled_activation_evidence.py",
        "uv run ruff check scripts/check_controlled_activation_evidence.py tests/scripts/test_check_controlled_activation_evidence.py",
    ):
        if phrase not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"story artifact missing {phrase!r}"))
    return violations


def _strong_evidence_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return (
        bool(normalized)
        and normalized not in WEAK_EVIDENCE_VALUES
        and not WEAK_EVIDENCE_MARKER_PATTERN.search(value)
    )


def _strong_evidence_string_sequence(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and bool(value)
        and all(_strong_evidence_string(item) for item in value)
    )


def _strong_reference_string(value: object) -> bool:
    return (
        _strong_evidence_string(value)
        and isinstance(value, str)
        and not DISALLOWED_REVIEW_REFERENCE_PATTERN.search(value)
    )


def _strong_reference_sequence(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and bool(value)
        and all(_strong_reference_string(item) for item in value)
    )


def _parse_utc_timestamp(
    value: object, field: str, location: str
) -> tuple[datetime | None, list[Violation]]:
    if not isinstance(value, str) or not value.strip():
        return None, [Violation(location, f"{field} must be a non-empty UTC timestamp")]
    raw = value.strip()
    if not (raw.endswith("Z") or raw.endswith("+00:00")):
        return None, [Violation(location, f"{field} must use UTC with Z or +00:00 suffix")]
    try:
        parsed = datetime.fromisoformat(
            raw.removesuffix("Z") + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError:
        return None, [Violation(location, f"{field} must be an ISO-8601 UTC timestamp")]
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        return None, [Violation(location, f"{field} must be UTC")]
    return parsed.astimezone(UTC), []


def _validate_change_window(value: object, location: str) -> list[Violation]:
    if not isinstance(value, Mapping):
        return [
            Violation(
                location, "change_window_utc must be an object with starts_at_utc and ends_at_utc"
            )
        ]
    starts_at, start_violations = _parse_utc_timestamp(
        value.get("starts_at_utc"), "change_window_utc.starts_at_utc", location
    )
    ends_at, end_violations = _parse_utc_timestamp(
        value.get("ends_at_utc"), "change_window_utc.ends_at_utc", location
    )
    violations = [*start_violations, *end_violations]
    if starts_at is not None and ends_at is not None:
        if ends_at <= starts_at:
            violations.append(
                Violation(location, "change_window_utc.ends_at_utc must be after starts_at_utc")
            )
        now = datetime.now(UTC)
        if starts_at <= now:
            violations.append(
                Violation(location, "change_window_utc has already started and must fail closed")
            )
        if ends_at <= now:
            violations.append(
                Violation(location, "change_window_utc is stale/expired and must fail closed")
            )
    return violations


def _validate_semantic_evidence_fields(
    data: Mapping[str, object], location: str
) -> list[Violation]:
    violations: list[Violation] = []
    for key in (
        "target_environment",
        "target_service",
        "target_version",
        "smoke_scope",
        "rollback_owner",
        "emergency_disable_owner",
        "activation_intent",
        "evidence_retention",
        "redaction_statement",
    ):
        if key in data and not _strong_evidence_string(data[key]):
            violations.append(Violation(location, f"{key} must be a non-empty string"))
    readiness = data.get("readiness_prerequisites")
    if "readiness_prerequisites" in data and (
        not _strong_evidence_string_sequence(readiness)
        or len(cast("Sequence[object]", readiness)) < 3
    ):
        violations.append(
            Violation(
                location,
                "readiness_prerequisites must include at least three non-empty prerequisite refs",
            )
        )
    trace = data.get("trace_correlation")
    if "trace_correlation" in data:
        if not isinstance(trace, Mapping):
            violations.append(
                Violation(
                    location,
                    "trace_correlation must be an object with operation_id, trace_id, and audit_event_refs",
                )
            )
        else:
            for key in ("operation_id", "trace_id"):
                if not _strong_evidence_string(trace.get(key)):
                    violations.append(
                        Violation(location, f"trace_correlation.{key} must be a non-empty string")
                    )
            if not _strong_evidence_string_sequence(trace.get("audit_event_refs")):
                violations.append(
                    Violation(
                        location,
                        "trace_correlation.audit_event_refs must be a non-empty string list",
                    )
                )
    return violations


def _normalized_field_key(key: object) -> str:
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")


def _meaningful_truthy_value(value: object) -> bool:
    return value not in (False, None, "", "false", "False")


def _activation_claim_key_violation(value: object, location: str) -> list[Violation]:
    violations: list[Violation] = []
    claim_markers = (
        "status",
        "state",
        "live",
        "active",
        "activated",
        "serving",
        "traffic",
        "enabled",
        "complete",
        "completed",
        "performed",
        "occurred",
        "executed",
        "successful",
        "success",
        "succeeded",
        "proof",
        "done",
        "shipped",
    )
    claim_marker_set = set(claim_markers)
    subject_keys = {
        "activation",
        "production_activation",
        "cutover",
        "production_cutover",
        "go_live",
        "golive",
    }
    safe_key_markers = {
        "planned",
        "planning",
        "plan",
        "intent",
        "scope",
        "metadata",
        "note",
        "notes",
        "ref",
        "reference",
    }

    def string_has_claim_marker(candidate: str) -> bool:
        return bool(set(_normalized_field_key(candidate).split("_")) & claim_marker_set)

    def subject_string_is_safe(key: object, candidate: str) -> bool:
        key_parts = set(_normalized_field_key(key).split("_"))
        value_parts = set(_normalized_field_key(candidate).split("_"))
        safe_key = bool(key_parts & safe_key_markers)
        safe_explicit_value = {"future", "operator", "gated"} <= value_parts or bool(
            value_parts & {"deferred", "planned", "planning", "false", "no"}
        )
        return safe_key and safe_explicit_value

    def is_subject_key(normalized: str) -> bool:
        parts = set(normalized.split("_"))
        return (
            normalized in subject_keys
            or "activation" in parts
            or "cutover" in parts
            or normalized.startswith(("go_live", "golive"))
        )

    def subject_path(path: tuple[str, ...]) -> str:
        return ".".join(path) if path else "<root>"

    def append_violation(path: tuple[str, ...]) -> None:
        violations.append(
            Violation(location, f"activation claim field {subject_path(path)!r} is not allowed")
        )

    def visit(candidate: object, *, in_subject: bool, path: tuple[str, ...]) -> None:
        if isinstance(candidate, Mapping):
            for key, child in candidate.items():
                key_text = str(key)
                normalized = _normalized_field_key(key)
                child_path = (*path, key_text)
                child_in_subject = in_subject or is_subject_key(normalized)
                parts = set(normalized.split("_"))
                claim_parts = parts & claim_marker_set
                if normalized.startswith(("go_live", "golive")):
                    claim_parts.discard("live")
                has_subject_key = is_subject_key(normalized)
                has_claim_key = (
                    normalized not in subject_keys and has_subject_key and bool(claim_parts)
                )
                if (
                    has_claim_key
                    and _meaningful_truthy_value(child)
                    and not (isinstance(child, str) and subject_string_is_safe(key_text, child))
                ):
                    append_violation(child_path)
                if child_in_subject:
                    if isinstance(child, str):
                        if _meaningful_truthy_value(child) and not subject_string_is_safe(
                            key_text, child
                        ):
                            append_violation(child_path)
                    elif isinstance(child, (Mapping, Sequence)) and not isinstance(
                        child, (str, bytes, bytearray)
                    ):
                        if (has_subject_key and not child) or (
                            normalized not in subject_keys and _meaningful_truthy_value(child)
                        ):
                            append_violation(child_path)
                    elif _meaningful_truthy_value(child):
                        append_violation(child_path)
                visit(child, in_subject=child_in_subject, path=child_path)
        elif isinstance(candidate, str):
            key_text = path[-1] if path else ""
            if (
                in_subject
                and _meaningful_truthy_value(candidate)
                and not subject_string_is_safe(key_text, candidate)
            ):
                append_violation(path)
        elif isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
            for item in candidate:
                if (
                    in_subject
                    and not isinstance(item, (Mapping, Sequence, str, bytes, bytearray))
                    and _meaningful_truthy_value(item)
                ):
                    append_violation(path)
                visit(item, in_subject=in_subject, path=path)

    visit(value, in_subject=False, path=())
    return violations


def _plaintext_fallback_field_violation(value: object, location: str) -> list[Violation]:
    violations: list[Violation] = []
    allow_values = {
        "allowed",
        "available",
        "enabled",
        "accepted",
        "approved",
        "permitted",
        "true",
        "yes",
        "1",
    }
    allow_key_markers = {
        "allow",
        "allowed",
        "available",
        "enable",
        "enabled",
        "accept",
        "accepted",
        "approve",
        "approved",
        "permit",
        "permitted",
    }

    def value_allows(candidate: object) -> bool:
        if candidate is True:
            return True
        if isinstance(candidate, str):
            lowered = candidate.strip().lower()
            candidate_parts = _normalized_field_key(candidate).split("_")
            return (
                lowered in allow_values
                or any(marker in candidate_parts for marker in allow_values)
                or bool(re.search(r"\b(?:may|can)\s+be\s+used\b", lowered))
            )
        if isinstance(candidate, Mapping):
            for nested_key, nested_value in candidate.items():
                nested_normalized = _normalized_field_key(nested_key)
                nested_parts = nested_normalized.split("_")
                nested_key_allows = any(marker in nested_parts for marker in allow_key_markers)
                if nested_key_allows and _meaningful_truthy_value(nested_value):
                    return True
                if value_allows(nested_value):
                    return True
        elif isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
            return any(value_allows(item) for item in candidate)
        return False

    weak_sslmode_value_markers = {
        "disable",
        "disabled",
        "false",
        "off",
        "allow",
        "prefer",
        "require",
    }
    weak_sslmode_values = {"verify_ca"}

    def normalized_value_is_weak_sslmode(normalized: str) -> bool:
        return normalized in weak_sslmode_values or bool(
            weak_sslmode_value_markers & set(normalized.split("_"))
        )

    def value_disables_tls(candidate: object) -> bool:
        if candidate is False:
            return True
        if isinstance(candidate, str):
            return normalized_value_is_weak_sslmode(_normalized_field_key(candidate))
        if isinstance(candidate, Mapping):
            return any(
                normalized_value_is_weak_sslmode(_normalized_field_key(key))
                or value_disables_tls(item)
                for key, item in candidate.items()
            )
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
            return any(value_disables_tls(item) for item in candidate)
        return False

    def ssl_object_disables_plaintext(candidate: object) -> bool:
        if not isinstance(candidate, Mapping):
            return False
        for nested_key, nested_value in candidate.items():
            nested_normalized = _normalized_field_key(nested_key)
            nested_compact = nested_normalized.replace("_", "")
            if nested_compact in {"mode", "sslmode"} and value_disables_tls(nested_value):
                return True
            if nested_compact == "ssl" and ssl_object_disables_plaintext(nested_value):
                return True
        return False

    for key, child in _walk_key_values(value):
        normalized = _normalized_field_key(key)
        compact = normalized.replace("_", "")
        parts = set(normalized.split("_"))
        if "sslmode" in compact and (
            normalized_value_is_weak_sslmode(normalized) or value_disables_tls(child)
        ):
            violations.append(Violation(location, "plaintext fallback allowance is not allowed"))
        if "ssl" in parts and (value_disables_tls(child) or ssl_object_disables_plaintext(child)):
            violations.append(Violation(location, "plaintext fallback allowance is not allowed"))
        mentions_plaintext_fallback = (
            "plaintext_fallback" in normalized or "plaintextfallback" in compact
        )
        key_allows = any(marker in normalized.split("_") for marker in allow_key_markers)
        if mentions_plaintext_fallback and (
            value_allows(child) or (key_allows and _meaningful_truthy_value(child))
        ):
            violations.append(Violation(location, "plaintext fallback allowance is not allowed"))
    return violations


def _self_attestation_field_violation(value: object, location: str) -> list[Violation]:
    violations: list[Violation] = []
    for key, child in _walk_key_values(value):
        normalized = _normalized_field_key(key)
        if any(
            marker in normalized
            for marker in (
                "self_attestation",
                "self_attested",
                "self_review",
                "implementer_review",
                "implementer_reviewer",
            )
        ) and _meaningful_truthy_value(child):
            violations.append(Violation(location, "self-attestation acceptance is not allowed"))
    return violations


def _is_reference_field_key(normalized: str) -> bool:
    compact = normalized.replace("_", "")
    return normalized.endswith(("_ref", "_refs", "_reference", "_references")) or compact.endswith(
        ("ref", "refs", "reference", "references")
    )


def _reference_field_violation(value: object, location: str) -> list[Violation]:
    violations: list[Violation] = []
    for path, child in _walk_key_value_paths(value):
        key = path[-1]
        normalized = _normalized_field_key(key)
        if not _is_reference_field_key(normalized):
            continue
        if isinstance(child, str):
            valid = _strong_reference_string(child)
        elif isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
            valid = _strong_reference_sequence(child)
        else:
            valid = False
        if not valid:
            field_path = ".".join(path)
            violations.append(
                Violation(location, f"{field_path} must be a non-empty strong reference")
            )
    return violations


def validate_evidence_package(path: Path) -> list[Violation]:
    """Validate one future activation evidence package instance shape.

    This is an extension point for Stories 134.2+; it validates package shape and
    redaction hygiene only. A clean result is not activation proof.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data: object = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return [Violation(str(path), f"failed to load evidence package: {exc}")]
    if not isinstance(data, Mapping):
        return [Violation(str(path), "evidence package must be a JSON object")]
    violations: list[Violation] = []
    missing = REQUIRED_EVIDENCE_FIELDS - set(data)
    if missing:
        violations.append(
            Violation(str(path), f"future evidence package fields missing {sorted(missing)}")
        )
    if data.get("activation_performed", False) is not False:
        violations.append(
            Violation(
                str(path),
                "activation_performed must be absent or false; package validation is not activation proof",
            )
        )
    violations.extend(_activation_claim_key_violation(data, str(path)))
    violations.extend(_plaintext_fallback_field_violation(data, str(path)))
    violations.extend(_self_attestation_field_violation(data, str(path)))
    violations.extend(_reference_field_violation(data, str(path)))
    generated_at, generated_violations = _parse_utc_timestamp(
        data.get("generated_at_utc"), "generated_at_utc", str(path)
    )
    expires_at, expires_violations = _parse_utc_timestamp(
        data.get("expires_at_utc"), "expires_at_utc", str(path)
    )
    violations.extend(generated_violations)
    violations.extend(expires_violations)
    if generated_at is not None and expires_at is not None:
        if expires_at <= generated_at:
            violations.append(Violation(str(path), "expires_at_utc must be after generated_at_utc"))
        now = datetime.now(UTC)
        if (now - generated_at).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
            violations.append(
                Violation(str(path), "generated_at_utc is stale and must fail closed")
            )
        if generated_at > now:
            violations.append(Violation(str(path), "generated_at_utc must not be in the future"))
        if expires_at <= now:
            violations.append(
                Violation(
                    str(path), "future evidence package is stale/expired and must fail closed"
                )
            )
    violations.extend(_validate_change_window(data.get("change_window_utc"), str(path)))
    violations.extend(_validate_semantic_evidence_fields(data, str(path)))
    if _structured_secret_violation(data):
        violations.append(
            Violation(str(path), "future evidence package contains structured secret-like material")
        )
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(
                    str(path),
                    "future evidence package contains secret-like or unredacted DSN material",
                )
            )
            break
    for value in _walk_strings(data):
        for violation in _scan_text_for_forbidden(path, _single_line_text(value)):
            violations.append(Violation(str(path), violation.message))
    for key in (
        "operator_approval_ref",
        "security_approval_ref",
        "rollback_plan_ref",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
    ):
        if key in data and not _strong_reference_string(data[key]):
            violations.append(Violation(str(path), f"{key} must be a non-empty strong reference"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"failed to load contract: {exc}")]
    violations.extend(_validate_contract(root, data))
    if _structured_secret_violation(data):
        violations.append(
            Violation(str(CONTRACT_PATH), "structured JSON secret-like value is not allowed")
        )
    for relpath in SECRET_SCAN_PATHS:
        try:
            violations.extend(_scan_text_for_forbidden(relpath, _read(root, relpath)))
        except OSError as exc:
            violations.append(Violation(str(relpath), f"failed to read for scan: {exc}"))
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
        JUSTFILE_PATH,
        CI_PATH,
    ):
        src = root / relpath
        dst = dest / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="controlled-activation-evidence-") as tmp:
        root = Path(tmp)
        _copy_fixture(REPO_ROOT, root)
        baseline = validate(root)
        if baseline:
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        fields = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", data["evidence_package_contract"])["required_fields"],
        )
        fields.pop("operator_approval_ref")
        _write_json(root / CONTRACT_PATH, data)
        if not any("required evidence fields missing" in v.message for v in validate(root)):
            print("self-test failed: missing evidence field was not rejected", file=sys.stderr)
            return 1
        _copy_fixture(REPO_ROOT, root)
        status_path = root / FEATURE_STATUS_PATH
        status_path.write_text(
            status_path.read_text(encoding="utf-8")
            + "\nProduction activation completed successfully.\n",
            encoding="utf-8",
        )
        if not any("activation overclaim" in v.message for v in validate(root)):
            print("self-test failed: activation overclaim was not rejected", file=sys.stderr)
            return 1
        _copy_fixture(REPO_ROOT, root)
        data = _load_json(root, CONTRACT_PATH)
        cast("dict[str, Any]", data["staleness_policy"])["outcome"] = "warn_only"
        _write_json(root / CONTRACT_PATH, data)
        if not any("fail-closed" in v.message for v in validate(root)):
            print("self-test failed: non-fail-closed staleness was not rejected", file=sys.stderr)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run checker fixture self-test")
    parser.add_argument(
        "--evidence",
        type=Path,
        help="validate one future activation evidence package instance shape; this is not activation proof",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.evidence is not None:
        violations = validate_evidence_package(args.evidence)
    else:
        violations = validate(REPO_ROOT)
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1
    if args.evidence is not None:
        print("controlled activation evidence package shape checks passed; not activation proof")
    else:
        print("controlled activation evidence contract/status checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
