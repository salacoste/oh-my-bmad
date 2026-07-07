#!/usr/bin/env python3
"""Validate the Story 132.1 split deployment topology readiness contract.

This gate is intentionally static/readiness-only. It validates the remote
Postgres/split-deployment topology contract, documentation/status wiring, CI/just
wiring, overclaim prevention, secret absence, and the absence of Story 132.1
runtime/deployment expansion surfaces. Runtime-surface checks are repo-wide and
fail-closed by design: Story 132.1 must not add activation/deployment surfaces
outside the static contract artifacts.

Usage::

    uv run python scripts/check_split_deployment_topology.py
    uv run python scripts/check_split_deployment_topology.py --self-test
"""

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
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/split-deployment-topology-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "132-1-split-deployment-remote-postgres-topology-contract.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_split_deployment_topology.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "current_default_preservation",
        "service_placement",
        "network_boundaries",
        "remote_postgres_data_authority",
        "pooling_migration_backup_prerequisites",
        "ingress",
        "secrets_handling",
        "observability",
        "unsupported_topologies",
        "rollback_fallback",
        "db_mtls_deferment",
        "core_invariants",
        "forbidden_runtime_expansion_surfaces",
        "fail_closed_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_SERVICES = frozenset(
    {
        "registry_api",
        "registry_state",
        "telegram_gateway",
        "orchestrator_adapter",
        "worker_wrapper",
        "clawhip_daemon",
    }
)
REQUIRED_CORE_INVARIANTS = frozenset(
    {
        "single_writer_state_mutation",
        "append_only_event_log_authority",
        "idempotency_and_locking",
        "capability_tiers",
    }
)
REQUIRED_FORBIDDEN_SURFACES = frozenset(
    {
        "compose profile or compose overlay activation",
        "environment activation flag for split deployment or remote Postgres",
        "new deploy target for split deployment or remote Postgres",
        "migration runner for remote Postgres",
        "service route activation for topology or database switching",
        "Dockerfile activation for split deployment",
        "remote Postgres connection code or DSN defaults",
        "external host or network command surface",
    }
)
REQUIRED_DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#split-deployment-and-remote-postgres-topology-readiness-story-1321",
        f"{PRODUCTION_OPS_PATH}#epic-132-split-deployment-and-remote-postgres-readiness",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_STATUS_REFS = frozenset(
    {
        f"{SPRINT_STATUS_PATH}#development_status",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
    }
)
REQUIRED_DEFAULT_PRESERVATION_FLAGS = (
    "single_host_default",
    "no_compose_profile_change",
    "no_env_activation_flag",
    "no_remote_postgres_connection_code",
    "future_activation_requires_new_story",
)
REQUIRED_SECTION_STATUSES = {
    "service_placement": "contract_only",
    "network_boundaries": "contract_only",
    "remote_postgres_data_authority": "deferred_fail_closed",
    "pooling_migration_backup_prerequisites": "future_required_evidence",
    "rollback_fallback": "future_required_evidence",
}
REQUIRED_INGRESS_PHRASES = (
    "public ingress",
    "approved edge services",
    "registry_api",
    "control boundary",
    "not added by this story",
)
REQUIRED_SECRETS_HANDLING_PHRASES = (
    "no real secret values",
    "future approved secret store contract",
    "embedded passwords",
    "forbidden",
    "story 131.2 credential readiness",
)
REQUIRED_OBSERVABILITY_PHRASES = (
    "per-service topology identity labels",
    "database connection pool metrics",
    "migration and backup audit evidence",
    "network-boundary health checks",
    "event-log append and idempotency-lock metrics",
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "contract remains static_readiness_only",
        "current single-host default is preserved",
        "unsupported topologies are explicitly fail-closed",
        "DB mTLS is deferred to Epic 133",
        "no live split deployment or remote Postgres support is claimed",
        "no runtime/deployment expansion surface is added by Story 132.1",
        "mandatory justfile and CI checker wiring is present",
    }
)
DOC_STATUS_PATHS = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)

SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b(?:password|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
)
OVERCLAIM_SUBJECT_PATTERN = r"(?:split[-_ ]deployment|remote[-_ ]postgres)"
OVERCLAIM_STATE_PATTERN = (
    r"(?:enabled|live|active|activated|available(?:\s+now)?|implemented|"
    r"production[-_ ]ready|ready|shipped|rollout\s+complete)"
)
OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b{OVERCLAIM_SUBJECT_PATTERN}\b"
        rf"(?P<gap>(?:\s+[-\w]+){{0,6}}?)\s+\b{OVERCLAIM_STATE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{OVERCLAIM_STATE_PATTERN}\b"
        rf"(?P<gap>(?:\s+[-\w]+){{0,6}}?)\s+\b{OVERCLAIM_SUBJECT_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\blive split[-_ ]deployment is active\b", re.IGNORECASE),
    re.compile(
        r"\bproduction remote[-_ ]postgres\b(?P<gap>(?:\s+[-\w]+){0,6}?)\s+\blive\b", re.IGNORECASE
    ),
)
OVERCLAIM_NEGATION_PATTERN = re.compile(r"\b(?:no|not|never|without)\b", re.IGNORECASE)
FORBIDDEN_MACHINE_READABLE_ACTIVATION_STATUSES = frozenset(
    {
        "active",
        "activated",
        "available",
        "available_now",
        "complete",
        "enabled",
        "implemented",
        "live",
        "production_ready",
        "ready",
        "rollout_complete",
        "shipped",
    }
)
MACHINE_READABLE_ACTIVATION_PATH_TERMS = frozenset(
    {
        "activation",
        "deployment",
        "mode",
        "postgres",
        "runtime",
        "split",
        "status",
        "topology",
    }
)
MACHINE_READABLE_BOOLEAN_ACTIVATION_TERMS = frozenset(
    {
        "active",
        "activated",
        "available",
        "enabled",
        "implemented",
        "live",
        "ready",
        "runtime",
    }
)
MACHINE_READABLE_NEGATING_PATH_TERMS = frozenset(
    {
        "deferred",
        "fail",
        "forbidden",
        "future",
        "no",
        "not",
        "preservation",
        "unsupported",
        "without",
    }
)
EXPECTED_MACHINE_READABLE_STATUSES = {
    ("mode",): "static_readiness_only",
    ("production_activation",): "deferred_fail_closed",
    ("current_default_preservation", "status"): "preserved",
    ("service_placement", "status"): "contract_only",
    ("network_boundaries", "status"): "contract_only",
    ("remote_postgres_data_authority", "status"): "deferred_fail_closed",
    ("pooling_migration_backup_prerequisites", "status"): "future_required_evidence",
    ("ingress", "status"): "contract_only",
    ("secrets_handling", "status"): "metadata_only",
    ("observability", "status"): "future_required_evidence",
    ("unsupported_topologies", "status"): "fail_closed",
    ("rollback_fallback", "status"): "future_required_evidence",
    ("db_mtls_deferment", "status"): "deferred_to_epic_133",
}
FORBIDDEN_COMPOSE_FILENAME_TOKENS = frozenset(
    {
        "split",
        "split-deployment",
        "split_deployment",
        "remote-postgres",
        "remote_postgres",
        "postgres",
    }
)
FORBIDDEN_RUNTIME_PATTERNS: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "compose profile/overlay activation",
        re.compile(r"(?i)\b(?:split[-_ ]deployment|remote[-_ ]postgres)\b"),
        ("compose-file",),
    ),
    (
        "environment activation flag",
        re.compile(
            r"(?i)\b(?:SPLIT_DEPLOYMENT(?:_ENABLED)?|ENABLE_SPLIT_DEPLOYMENT|"
            r"REMOTE_POSTGRES(?:_ENABLED)?|ENABLE_REMOTE_POSTGRES)\b"
        ),
        (
            ".env",
            "justfile",
            ".yml",
            ".yaml",
            ".toml",
            ".json",
            ".ini",
            ".cfg",
            ".sh",
            ".bash",
            ".zsh",
            ".py",
            ".ts",
            ".js",
        ),
    ),
    (
        "deploy target",
        re.compile(r"(?im)^deploy-(?:split|remote-postgres|split-deployment)\b"),
        ("justfile", ".sh", ".bash", ".zsh"),
    ),
    (
        "migration runner",
        re.compile(r"(?i)\b(?:remote_postgres_migration_runner|migrate-remote-postgres)\b"),
        ("justfile", ".py", ".sh", ".bash", ".zsh"),
    ),
    (
        "service route activation",
        re.compile(r"(?i)/(?:admin/)?(?:split-deployment|remote-postgres)/(?:enable|activate)\b"),
        (".py", ".ts", ".js"),
    ),
    (
        "Dockerfile activation",
        re.compile(r"(?i)\b(?:SPLIT_DEPLOYMENT|REMOTE_POSTGRES)\b"),
        ("Dockerfile", ".dockerfile"),
    ),
    (
        "remote Postgres connection code",
        re.compile(
            r"(?i)\b(?:REMOTE_POSTGRES_URL|REMOTE_DATABASE_URL|remote_postgres_dsn|"
            r"REMOTE_PG_DSN)\b|"
            r"\b(?:DATABASE_URL|POSTGRES_DSN|POSTGRES_URL)\b(?:\n|.){0,160}?['\"]?"
            r"postgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://"
            r"(?!(?:[^@/\s]+@)?(?:localhost|127\.0\.0\.1|::1|\[::1\])(?::|/|\?|\s|['\"}\]]|$))"
            r"[^\s'\"]+|"
            r"postgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://"
            r"(?!(?:[^@/\s]+@)?(?:localhost|127\.0\.0\.1|::1|\[::1\])(?::|/|\?|\s|['\"}\]]|$))"
            r"[^\s'\"]+(?:\n|.){0,160}?\b(?:DATABASE_URL|POSTGRES_DSN|POSTGRES_URL)\b|"
            r"\b(?:POSTGRES_HOST|PGHOST)\b[\s'\"\]]*[:=]\s*['\"]?"
            r"(?!(?:localhost|127\.0\.0\.1|::1|\[::1\])(?::|\s|['\"}\]]|$))[^\s'\"]+"
        ),
        (
            ".py",
            ".ts",
            ".js",
            ".sh",
            ".bash",
            ".zsh",
            ".env",
            ".yml",
            ".yaml",
            ".toml",
            ".json",
            ".ini",
            ".cfg",
        ),
    ),
    (
        "external host/network command surface",
        re.compile(r"(?i)\b(?:ssh\s+.*remote-postgres|tailscale\s+.*split|scp\s+.*postgres)\b"),
        ("justfile", ".sh", ".bash", ".zsh", ".md"),
    ),
)
FORBIDDEN_SCAN_EXCLUDE_PREFIXES = (
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    ".omx/",
    "docs/",
    "tests/",
    "scripts/check_split_deployment_topology.py",
    "_bmad-output/implementation-artifacts/",
)
NON_RUNTIME_EXAMPLE_PATH_TOKENS = frozenset(
    {"example", "examples", "fixture", "fixtures", "sample", "samples"}
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


POSTGRES_URL_PATTERN = re.compile(r"""(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'"<>]+""")
PLACEHOLDER_PASSWORDS = frozenset(
    {"password", "example", "changeme", "placeholder", "pass", "test", "secret", "****"}
)
LOCAL_POSTGRES_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _postgres_url_contains_secret(value: str) -> bool:
    for match in POSTGRES_URL_PATTERN.finditer(value):
        try:
            parsed = urlsplit(match.group(0))
        except ValueError:
            continue
        password = parsed.password or ""
        if password and password.lower() not in PLACEHOLDER_PASSWORDS:
            return True
    return False


def _contains_secret_value(value: str) -> bool:
    return _postgres_url_contains_secret(value) or any(
        pattern.search(value) for pattern in SECRET_VALUE_PATTERNS
    )


def _ref_path(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_anchor(ref: str) -> str:
    return ref.split("#", 1)[1] if "#" in ref else ""


def _github_heading_anchor(heading: str) -> str:
    anchor = heading.strip().lower()
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"\s", "-", anchor)
    return anchor


def _markdown_heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base_anchor = _github_heading_anchor(match.group(1))
        count = seen.get(base_anchor, 0)
        seen[base_anchor] = count + 1
        anchors.add(base_anchor if count == 0 else f"{base_anchor}-{count}")
    return anchors


def _structured_text_anchor_exists(text: str, anchor: str) -> bool:
    key_pattern = re.compile(rf"(?m)^\s*{re.escape(anchor)}\s*:")
    return bool(key_pattern.search(text))


def _validate_ref_target(root: Path, ref: str) -> list[Violation]:
    relpath = _ref_path(ref)
    anchor = _ref_anchor(ref)
    if not relpath:
        return [Violation(str(CONTRACT_PATH), f"reference missing file path: {ref!r}")]

    path = root / relpath
    if not path.exists():
        return [Violation(str(CONTRACT_PATH), f"referenced file does not exist: {relpath}")]
    if not anchor:
        return [
            Violation(
                str(CONTRACT_PATH), f"referenced docs/status entry must include an anchor: {ref}"
            )
        ]

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        if anchor not in _markdown_heading_anchors(text):
            return [
                Violation(str(CONTRACT_PATH), f"referenced markdown anchor does not exist: {ref}")
            ]
    elif not _structured_text_anchor_exists(text, anchor):
        return [
            Violation(str(CONTRACT_PATH), f"referenced structured anchor does not exist: {ref}")
        ]
    return []


def _section(data: dict[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name)
    return value if isinstance(value, Mapping) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {entry for entry in value if isinstance(entry, str)}


def _lower_text(value: object) -> str:
    return "\n".join(_walk_strings(value)).lower()


def _validate_section_status(data: dict[str, Any], name: str) -> list[Violation]:
    expected_status = REQUIRED_SECTION_STATUSES[name]
    actual_status = _section(data, name).get("status")
    if actual_status != expected_status:
        return [Violation(str(CONTRACT_PATH), f"{name} status must be {expected_status}")]
    return []


def _validate_requirements_section(
    data: dict[str, Any],
    name: str,
    *,
    expected_status: str,
    min_requirements: int,
    required_phrases: tuple[str, ...],
) -> list[Violation]:
    violations: list[Violation] = []
    section = _section(data, name)
    if section.get("status") != expected_status:
        violations.append(Violation(str(CONTRACT_PATH), f"{name} status must be {expected_status}"))
    requirements = _string_set(section.get("requirements"))
    if len(requirements) < min_requirements:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"{name} must include at least {min_requirements} requirement entries",
            )
        )
    text = _lower_text(section)
    for phrase in required_phrases:
        if phrase not in text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{name} missing required phrase {phrase!r}")
            )
    return violations


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("story") != "132.1":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 132.1"))
    if data.get("mode") != "static_readiness_only":
        violations.append(Violation(str(CONTRACT_PATH), "mode must be static_readiness_only"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    violations.extend(_validate_machine_readable_statuses(data))

    missing_sections = REQUIRED_TOP_LEVEL_SECTIONS - set(data)
    if missing_sections:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required topology sections missing {sorted(missing_sections)}"
            )
        )
    for section_name in REQUIRED_SECTION_STATUSES:
        violations.extend(_validate_section_status(data, section_name))

    current_default = _section(data, "current_default_preservation")
    if current_default.get("status") != "preserved":
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "current_default_preservation status must be preserved",
            )
        )
    for flag in REQUIRED_DEFAULT_PRESERVATION_FLAGS:
        if current_default.get(flag) is not True:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"current_default_preservation must keep {flag} true",
                )
            )
    if (
        "only supported runtime topology"
        not in str(current_default.get("required_statement", "")).lower()
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "current_default_preservation required_statement must preserve current defaults",
            )
        )

    services = _string_set(_section(data, "service_placement").get("required_services"))
    missing_services = REQUIRED_SERVICES - services
    if missing_services:
        violations.append(
            Violation(str(CONTRACT_PATH), f"service_placement missing {sorted(missing_services)}")
        )

    network_boundaries = _string_set(
        _section(data, "network_boundaries").get("required_boundaries")
    )
    if len(network_boundaries) < 4 or not any("clawhip" in item for item in network_boundaries):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "network_boundaries must cover private networks, database traffic, and clawhip_daemon",
            )
        )

    authority = _section(data, "remote_postgres_data_authority")
    authority_text = "\n".join(_walk_strings(authority)).lower()
    for phrase in ("future data authority", "local database defaults", "migration authority"):
        if phrase not in authority_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"remote_postgres_data_authority missing {phrase!r}")
            )
    data_boundaries = _string_set(authority.get("data_boundary_coverage"))
    for phrase in (
        "state mutation authority",
        "append-only event-log authority",
        "backup and restore authority",
    ):
        if phrase not in data_boundaries:
            violations.append(
                Violation(
                    str(CONTRACT_PATH), f"remote Postgres data-boundary coverage missing {phrase!r}"
                )
            )

    prereqs = _section(data, "pooling_migration_backup_prerequisites")
    for subsection in ("pooling", "migration", "backup"):
        if len(_string_set(prereqs.get(subsection))) < 2:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"pooling_migration_backup_prerequisites missing {subsection}",
                )
            )

    violations.extend(
        _validate_requirements_section(
            data,
            "ingress",
            expected_status="contract_only",
            min_requirements=3,
            required_phrases=REQUIRED_INGRESS_PHRASES,
        )
    )
    violations.extend(
        _validate_requirements_section(
            data,
            "secrets_handling",
            expected_status="metadata_only",
            min_requirements=4,
            required_phrases=REQUIRED_SECRETS_HANDLING_PHRASES,
        )
    )
    violations.extend(
        _validate_requirements_section(
            data,
            "observability",
            expected_status="future_required_evidence",
            min_requirements=5,
            required_phrases=REQUIRED_OBSERVABILITY_PHRASES,
        )
    )

    unsupported = _section(data, "unsupported_topologies")
    unsupported_entries = _lower_text(unsupported)
    if unsupported.get("status") != "fail_closed" or "multi-writer" not in unsupported_entries:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "unsupported_topologies must be fail_closed and name multi-writer",
            )
        )
    if "clawhip_daemon omitted" not in unsupported_entries:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "unsupported_topologies must fail closed when clawhip_daemon is omitted",
            )
        )

    rollback = _lower_text(_section(data, "rollback_fallback"))
    if "single-host" not in rollback or "fallback" not in rollback or "rollback" not in rollback:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "rollback_fallback must document single-host fallback and rollback",
            )
        )

    mtls = _section(data, "db_mtls_deferment")
    if mtls.get("status") != "deferred_to_epic_133" or str(mtls.get("epic")) != "133":
        violations.append(
            Violation(str(CONTRACT_PATH), "db_mtls_deferment must be deferred_to_epic_133")
        )

    invariants = _section(data, "core_invariants")
    missing_invariants = REQUIRED_CORE_INVARIANTS - set(invariants)
    if missing_invariants:
        violations.append(
            Violation(str(CONTRACT_PATH), f"core_invariants missing {sorted(missing_invariants)}")
        )
    invariant_text = "\n".join(_walk_strings(invariants)).lower()
    for phrase in ("single", "append-only", "idempotency", "lock", "capability tiers"):
        if phrase not in invariant_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"core invariant coverage missing {phrase!r}")
            )

    forbidden = _string_set(data.get("forbidden_runtime_expansion_surfaces"))
    missing_forbidden = REQUIRED_FORBIDDEN_SURFACES - forbidden
    if missing_forbidden:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"forbidden_runtime_expansion_surfaces missing {sorted(missing_forbidden)}",
            )
        )

    fail_closed_checks = _string_set(data.get("fail_closed_checks"))
    missing_fail_closed_checks = REQUIRED_FAIL_CLOSED_CHECKS - fail_closed_checks
    if missing_fail_closed_checks:
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"fail_closed_checks missing {sorted(missing_fail_closed_checks)}",
            )
        )

    non_goals = "\n".join(str(x) for x in data.get("non_goals", []))
    for phrase in (
        "no live split deployment activation",
        "no remote Postgres connection code",
        "no docker compose profile or overlay activation",
        "no deployment target or external host command",
        "no migration runner or database migration execution",
        "no service route or command surface activation",
        "no Dockerfile behavior change",
        "no DB mTLS implementation before Epic 133",
    ):
        if phrase not in non_goals:
            violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {phrase!r}"))

    doc_refs = _string_set(data.get("docs_refs"))
    missing_doc_refs = REQUIRED_DOC_REFS - doc_refs
    if missing_doc_refs:
        violations.append(
            Violation(str(CONTRACT_PATH), f"docs_refs missing {sorted(missing_doc_refs)}")
        )
    status_refs = _string_set(data.get("status_refs"))
    missing_status_refs = REQUIRED_STATUS_REFS - status_refs
    if missing_status_refs:
        violations.append(
            Violation(str(CONTRACT_PATH), f"status_refs missing {sorted(missing_status_refs)}")
        )
    for ref in doc_refs | status_refs:
        violations.extend(_validate_ref_target(root, ref))

    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract appears to contain a real credential value")
            )
            break
    return violations


def _lookup_path(data: Mapping[str, Any], path: tuple[str, ...]) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _iter_path_values(
    value: object, path: tuple[str, ...] = ()
) -> Iterable[tuple[tuple[str, ...], object]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_path_values(child, (*path, str(key)))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            yield from _iter_path_values(child, (*path, str(index)))
    else:
        yield path, value


def _normalise_machine_status(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def _is_forbidden_machine_activation_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and _normalise_machine_status(value) in FORBIDDEN_MACHINE_READABLE_ACTIVATION_STATUSES
    )


def _is_machine_activation_claim_path(path: tuple[str, ...]) -> bool:
    path_text = "_".join(path).lower().replace("-", "_")
    return any(term in path_text for term in MACHINE_READABLE_ACTIVATION_PATH_TERMS)


def _path_tokens(path: tuple[str, ...]) -> set[str]:
    return {
        token
        for segment in path
        for token in re.split(r"[^a-z0-9]+", segment.lower().replace("-", "_"))
        if token
    }


def _is_forbidden_machine_activation_boolean(path: tuple[str, ...], value: object) -> bool:
    if value is not True:
        return False
    tokens = _path_tokens(path)
    if tokens & MACHINE_READABLE_NEGATING_PATH_TERMS:
        return False
    return bool(tokens & MACHINE_READABLE_BOOLEAN_ACTIVATION_TERMS) and bool(
        tokens & MACHINE_READABLE_ACTIVATION_PATH_TERMS
    )


def _validate_machine_readable_statuses(data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    for path, expected in EXPECTED_MACHINE_READABLE_STATUSES.items():
        actual = _lookup_path(data, path)
        if actual != expected:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} machine-readable status must be {expected}",
                )
            )
        if _is_forbidden_machine_activation_value(actual):
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must not claim activation status {actual!r}",
                )
            )
    for path, value in _iter_path_values(data):
        if path in EXPECTED_MACHINE_READABLE_STATUSES:
            continue
        if _is_machine_activation_claim_path(path) and _is_forbidden_machine_activation_value(
            value
        ):
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must not claim activation status {value!r}",
                )
            )
        if _is_forbidden_machine_activation_boolean(path, value):
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must not claim boolean activation {value!r}",
                )
            )
    return violations


def _validate_docs_and_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    required_mentions = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.1", CHECKER_COMMAND, "DB mTLS", "Epic 133"],
        PRODUCTION_OPS_PATH: ["Story 132.1", str(CONTRACT_PATH), CHECKER_COMMAND],
        FEATURE_STATUS_PATH: ["Story 132.1", str(CONTRACT_PATH), str(ARTIFACT_PATH)],
        SPRINT_STATUS_PATH: ["132-1-split-deployment", "epic-132"],
        ARTIFACT_PATH: ["Story 132.1", CHECKER_COMMAND, "No runtime"],
    }
    for relpath, needles in required_mentions.items():
        path = root / relpath
        if not path.exists():
            violations.append(Violation(str(relpath), "required docs/status file is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                violations.append(Violation(str(relpath), f"missing required reference {needle!r}"))
    return violations


def _strip_shell_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if char == "#" and not in_single_quote and not in_double_quote:
            return line[:index]
    return line


def _normalise_executable_command(line: str) -> str:
    candidate = _strip_shell_comment(line).strip()
    while candidate.startswith(("@", "-")):
        candidate = candidate[1:].lstrip()
    return candidate


def _contains_exact_command(text: str, command: str) -> bool:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("run:"):
            candidate = candidate.removeprefix("run:").strip()
        candidate = _normalise_executable_command(candidate)
        if candidate == command:
            return True
    return False


def _recipe_header_matches(line: str, recipe: str) -> bool:
    if line[:1].isspace():
        return False
    stripped = _strip_shell_comment(line).strip()
    return bool(re.match(rf"^{re.escape(recipe)}(?:\s[^:]*)?:", stripped))


def _iter_just_recipe_commands(text: str, recipe: str) -> Iterable[str]:
    in_recipe = False
    for line in text.splitlines():
        if not in_recipe:
            in_recipe = _recipe_header_matches(line, recipe)
            continue
        if line.strip() and not line[:1].isspace() and not line.lstrip().startswith("#"):
            break
        command = _normalise_executable_command(line)
        if command:
            yield command


def _just_recipe_has_executable_command(text: str, recipe: str, command: str) -> bool:
    return any(candidate == command for candidate in _iter_just_recipe_commands(text, recipe))


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    justfile = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    for recipe in ("lint", "check-gates"):
        if not _just_recipe_has_executable_command(justfile, recipe, CHECKER_COMMAND):
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{recipe}: missing split topology checker")
            )
    if not _just_recipe_has_executable_command(
        justfile, "check-gates-self-test", CHECKER_SELF_TEST_COMMAND
    ):
        violations.append(
            Violation(str(JUSTFILE_PATH), "check-gates-self-test missing split topology self-test")
        )
    if not _contains_exact_command(ci, CHECKER_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI missing split topology checker step"))
    if not _contains_exact_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI missing split topology checker self-test"))
    return violations


def _is_overclaim_match(match: re.Match[str], text: str) -> bool:
    gap = match.groupdict().get("gap") or ""
    sentence_start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start()))
    prefix = text[sentence_start + 1 : match.start()]
    local_prefix = re.split(
        r"\b(?:because|but|however|although|though|while|whereas|yet|unless|except)\b|[;:]",
        prefix,
        flags=re.IGNORECASE,
    )[-1]
    prefix_negation = re.search(
        r"\b(?:no|not|never|without|does\s+not|do\s+not|did\s+not)\b",
        local_prefix,
        re.IGNORECASE,
    )
    return not OVERCLAIM_NEGATION_PATTERN.search(gap) and prefix_negation is None


def _validate_overclaims_and_secrets(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in DOC_STATUS_PATHS:
        path = root / relpath
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in OVERCLAIM_PATTERNS:
            if any(_is_overclaim_match(match, text) for match in pattern.finditer(text)):
                violations.append(
                    Violation(
                        str(relpath), f"overclaim forbidden by Story 132.1: {pattern.pattern}"
                    )
                )
                break
    for relpath in SECRET_SCAN_PATHS:
        path = root / relpath
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if _contains_secret_value(text):
            violations.append(Violation(str(relpath), "secret-like value is forbidden"))
    return violations


def _should_scan_runtime_file(relpath: Path) -> bool:
    rel = relpath.as_posix()
    if any(rel.startswith(prefix) for prefix in FORBIDDEN_SCAN_EXCLUDE_PREFIXES):
        return False
    return _is_relevant_runtime_text_file(relpath)


def _is_test_fixture_file(relpath: Path) -> bool:
    name = relpath.name.lower()
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.js"))
        or any(part in {"tests", "__tests__"} for part in relpath.parts)
    )


def _is_relevant_runtime_text_file(relpath: Path) -> bool:
    name = relpath.name
    lower_name = name.lower()
    if name in {"justfile", "Justfile", "pyproject.toml"}:
        return True
    if lower_name.startswith(("dockerfile", ".env")):
        return True
    if lower_name in {"compose.yaml", "compose.yml"}:
        return True
    return relpath.suffix in {
        ".bash",
        ".cfg",
        ".env",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".yaml",
        ".yml",
        ".zsh",
    }


def _iter_repo_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(root)
        if _should_scan_runtime_file(relpath):
            yield relpath


def _is_compose_file(relpath: Path) -> bool:
    name = relpath.name.lower()
    if relpath.suffix.lower() not in {".yaml", ".yml"}:
        return False
    return name in {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"} or (
        name.startswith(("compose.", "compose-", "docker-compose.", "docker-compose-"))
    )


def _is_compose_overlay_file(relpath: Path) -> bool:
    name = relpath.name.lower()
    if name in {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}:
        return False
    return _is_compose_file(relpath)


def _has_forbidden_compose_path_token(relpath: Path) -> bool:
    token_source = relpath.as_posix().lower()
    tokens = [token for token in re.split(r"[/._-]+", token_source) if token]
    token_pairs = {f"{left}-{right}" for left, right in zip(tokens, tokens[1:], strict=False)}
    token_pairs.update(f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False))
    return bool((set(tokens) | token_pairs) & FORBIDDEN_COMPOSE_FILENAME_TOKENS)


def _has_compose_like_yaml_shape(text: str) -> bool:
    compose_service_keys = r"(?i)\b(?:profiles?|image|build)\s*:"
    return bool(re.search(r"(?im)^services\s*:", text) and re.search(compose_service_keys, text))


def _suffix_matches(rel: str, suffixes: tuple[str, ...]) -> bool:
    name = Path(rel).name
    if "compose-file" in suffixes and _is_compose_file(Path(rel)):
        return True
    return any(rel.endswith(suffix) or name == suffix or suffix in rel for suffix in suffixes)


def _compose_has_split_or_remote_postgres_surface(relpath: Path, text: str) -> bool:
    if relpath.suffix.lower() not in {".yaml", ".yml"}:
        return False
    is_named_compose_file = _is_compose_file(relpath)
    is_compose_like_yaml = _has_compose_like_yaml_shape(text)
    if not is_named_compose_file and not is_compose_like_yaml:
        return False
    if re.search(r"(?i)\b(?:split[-_ ]deployment|remote[-_ ]postgres)\b", text):
        return True
    path_suggests_split_or_postgres = _has_forbidden_compose_path_token(relpath)
    split_profile = bool(
        re.search(r"(?im)\bprofiles?\s*:\s*(?:\[[^\]]*\bsplit\b|[^\n]*\bsplit\b)", text)
    )
    generic_profile = bool(re.search(r"(?im)\bprofiles?\s*:", text))
    if is_compose_like_yaml and generic_profile and _is_compose_overlay_file(relpath):
        return True
    if is_compose_like_yaml and split_profile:
        return True
    postgres_service = bool(re.search(r"(?i)\bpostgres(?:ql)?\b", text))
    return (is_compose_like_yaml and path_suggests_split_or_postgres) or (
        is_named_compose_file
        and (path_suggests_split_or_postgres or (split_profile and postgres_service))
    )


def _is_non_runtime_example_file(relpath: Path) -> bool:
    path_tokens = {token for token in re.split(r"[/._-]+", relpath.as_posix().lower()) if token}
    return bool(path_tokens & NON_RUNTIME_EXAMPLE_PATH_TOKENS)


def _validate_forbidden_runtime_surfaces(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in _iter_repo_files(root):
        rel = relpath.as_posix()
        try:
            text = (root / relpath).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if not _is_test_fixture_file(relpath) and _postgres_url_contains_secret(text):
            violations.append(Violation(rel, "secret-like Postgres URL value is forbidden"))
            continue
        if _compose_has_split_or_remote_postgres_surface(relpath, text):
            violations.append(
                Violation(
                    rel,
                    "forbidden runtime/deployment expansion surface detected: "
                    "compose profile/overlay activation",
                )
            )
            continue
        skip_example_remote_dsn = _is_non_runtime_example_file(relpath)
        for surface, pattern, suffixes in FORBIDDEN_RUNTIME_PATTERNS:
            if skip_example_remote_dsn and surface == "remote Postgres connection code":
                continue
            if _suffix_matches(rel, suffixes) and pattern.search(text):
                violations.append(
                    Violation(
                        rel, f"forbidden runtime/deployment expansion surface detected: {surface}"
                    )
                )
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"failed to load contract: {exc}")]
    violations.extend(_validate_contract(root, data))
    violations.extend(_validate_docs_and_status(root))
    violations.extend(_validate_wiring(root))
    violations.extend(_validate_overclaims_and_secrets(root))
    violations.extend(_validate_forbidden_runtime_surfaces(root))
    return violations


def _copy_self_test_fixture(src_root: Path, dst_root: Path) -> None:
    for relpath in (
        CONTRACT_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    ):
        src = src_root / relpath
        dst = dst_root / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_self_test_fixture(REPO_ROOT, root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1

        contract = root / CONTRACT_PATH
        data = _load_json(root, CONTRACT_PATH)
        data["core_invariants"].pop("single_writer_state_mutation")
        contract.write_text(json.dumps(data, indent=2), encoding="utf-8")
        violations = validate(root)
        if not any("core_invariants missing" in violation.message for violation in violations):
            print("self-test failed to detect missing invariant", file=sys.stderr)
            return 1
    print("split deployment topology readiness self-test passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="run the checker's self-test fixture"
    )
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    violations = validate(REPO_ROOT)
    if violations:
        print("split deployment topology readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("split deployment topology readiness check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
