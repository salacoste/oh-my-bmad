#!/usr/bin/env python3
"""Validate the Epic 133 DB connection mTLS runtime-gated readiness contract."""

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
CONTRACT_PATH = Path("docs/db-mtls-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_133_1_PATH = Path(
    "_bmad-output/implementation-artifacts/133-1-db-mtls-static-readiness-contract.md"
)
CLOSURE_ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/133-5-db-mtls-closure-evidence.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
ARCHITECT_APPROVAL_PATH = Path(
    "_bmad-output/implementation-artifacts/133-db-mtls-architect-approval-cycle-3.md"
)
CRITIC_APPROVAL_PATH = Path(
    "_bmad-output/implementation-artifacts/133-db-mtls-critic-approval-cycle-3.md"
)
MTLS_RUNTIME_PATH = Path("packages/mtls/src/mtls/db.py")
REGISTRY_RUNTIME_PATH = Path("services/registry-state/src/registry_state/adapters/sqlite_store.py")
CHECKER_COMMAND = "uv run python scripts/check_db_mtls_readiness.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "ca_ownership",
        "certificate_profiles",
        "approved_secret_locations",
        "env_config_keys",
        "profile_gating_and_url_policy",
        "server_side_postgres_evidence",
        "client_side_registry_evidence",
        "rotation_revocation_drills",
        "failure_observability",
        "redaction_and_secret_hygiene",
        "rollback_disable",
        "closure_gate",
        "fail_closed_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#db-mtls-readiness-story-133",
        f"{PRODUCTION_OPS_PATH}#epic-133-db-connection-mtls-readiness",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_133_1_PATH}#summary",
        f"{CLOSURE_ARTIFACT_PATH}#summary",
    }
)
REQUIRED_STATUS_REFS = frozenset(
    {f"{SPRINT_STATUS_PATH}#development_status", f"{FEATURE_STATUS_PATH}#current-bmad-status"}
)
REQUIRED_APPROVED_PREFIXES = frozenset({"/run/secrets/", "/certs/db/"})
REQUIRED_ENV_KEYS = frozenset(
    {
        "REGISTRY_DB_MTLS_ENABLED",
        "REGISTRY_DB_MTLS_ROOT_CA",
        "REGISTRY_DB_MTLS_CLIENT_CERT",
        "REGISTRY_DB_MTLS_CLIENT_KEY",
        "REGISTRY_DB_MTLS_SERVER_HOSTNAME",
        "REGISTRY_DB_MTLS_REVOCATION_LIST",
        "REGISTRY_DATABASE_URL",
    }
)
REQUIRED_SERVER_SETTINGS = frozenset({"ssl=on", "ssl_cert_file", "ssl_key_file", "ssl_ca_file"})
REQUIRED_SERVER_EVIDENCE = frozenset(
    {
        "server_certificate_from_approved_prefix",
        "server_private_key_from_approved_prefix",
        "root_ca_from_approved_prefix",
        "client_ca_from_approved_prefix",
        "private_key_mode_and_owner_checked",
    }
)
REQUIRED_PG_HBA_TOKENS = frozenset(
    {
        "application_role_and_database",
        "hostssl",
        "clientcert",
        "cert_based_client_verification",
        "no_earlier_plaintext_host_bypass",
    }
)
REQUIRED_SERVER_FIELDS = {
    "application_database": "registry",
    "application_role": "app",
}
REQUIRED_URL_POLICIES = frozenset(
    {
        "disabled_profile_preserves_sqlite_default",
        "enabled_requires_postgresql_asyncpg",
        "enabled_rejects_sqlite",
        "enabled_rejects_non_postgres",
        "enabled_rejects_non_asyncpg_postgres",
        "enabled_rejects_sslmode_disable",
        "enabled_rejects_sslmode_allow",
        "enabled_rejects_sslmode_prefer",
        "enabled_rejects_sslmode_require",
        "enabled_rejects_sslmode_verify_ca",
        "no_plaintext_fallback",
    }
)
REQUIRED_ROTATION_ITEMS = frozenset(
    {
        "replacement_server_cert_from_approved_ca",
        "replacement_client_cert_from_approved_ca",
        "distribution_only_approved_prefixes",
        "safe_service_reconnect_after_staging",
        "old_client_cert_rejected_by_server",
        "revoked_or_old_server_cert_rejected_by_client",
        "old_serial_rejected_through_crl",
        "crl_under_approved_prefixes",
        "reload_restart_reconnect_after_rotation",
        "expiry_warning_before_hard_expiry",
        "failed_rotation_rollback_without_private_key_commit",
    }
)
REQUIRED_FAILURE_CLASSES = frozenset(
    {
        "invalid_ca",
        "expired_cert",
        "hostname_mismatch",
        "missing_client_cert",
        "wrong_permissions",
        "unreadable_material",
        "plaintext_attempt",
        "revoked_cert",
    }
)
REQUIRED_REDACTION_SURFACES = frozenset(
    {
        "db_mtls_builder_structured_logs",
        "registry_state_runtime_engine_diagnostics",
        "alembic_migration_diagnostics",
        "readiness_checker_output",
    }
)
REQUIRED_CLOSURE_FIELDS = frozenset(
    {
        "enabled_disabled_matrix",
        "rotation_revocation_drill",
        "failure_mode_observability",
        "secret_scanner",
        "docs_status_links",
        "architect_approval",
        "critic_approval",
        "code_review",
        "ultraqa",
        "ci_local_validation",
        "split_deployment_remote_postgres_notes",
        "checker",
        "scanner",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "runtime-gated registry-state DB mTLS support; no production activation without operator evidence",
        "server-side Postgres evidence is exact and required before activation",
        "approved secret prefixes enforced by canonical realpath",
        "symlink escapes fail closed",
        "no plaintext fallback or sslmode=disable bypass",
        "rotation and revocation evidence required before closure",
        "failure diagnostics are sanitized and bounded",
        "local closure evidence recorded; production activation still requires operator evidence",
        "justfile and CI checker wiring is present",
    }
)
DOC_STATUS_PATHS = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_133_1_PATH,
    CLOSURE_ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = DOC_STATUS_PATHS
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)
POSTGRES_URL_PATTERN = re.compile(r"""(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>]+""")
PLACEHOLDER_PASSWORDS = frozenset(
    {"password", "pass", "secret", "example", "placeholder", "changeme", "test", "****"}
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\bdb\s+mtls\b(?:\W+\w+){0,6}\W+(?:active|activated|live|production[- ]ready|shipped|rollout\s+complete)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:active|activated|live|production[- ]ready|shipped|rollout\s+complete)\b(?:\W+\w+){0,6}\W+\bdb\s+mtls\b",
        re.I,
    ),
)
PLAINTEXT_FALLBACK_PATTERNS = (
    re.compile(r"\bplaintext\s+fallback\s+(?:is\s+)?(?:allowed|available|enabled)\b", re.I),
    re.compile(r"\bfall\s+back\s+to\s+plaintext\b", re.I),
    re.compile(r"\bsslmode\s*=\s*disable\s+(?:is\s+)?(?:allowed|accepted)\b", re.I),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|rejects?|forbidden|fail[- ]closed|deferred)\b", re.I
)
FORBIDDEN_MACHINE_STATUSES = frozenset(
    {"active", "activated", "complete", "enabled", "live", "shipped"}
)
EXPECTED_MACHINE_STATUSES = {
    ("mode",): "runtime_gated_readiness",
    ("production_activation",): "deferred_fail_closed",
    ("db_mtls_runtime_implementation",): "runtime_gated_supported",
    ("server_side_postgres_evidence", "status"): "operator_contract_required",
    ("client_side_registry_evidence", "status"): "runtime_gated_supported",
    ("rotation_revocation_drills", "status"): "future_evidence_required",
    ("failure_observability", "status"): "runtime_gated_supported",
    ("closure_gate", "status"): "closure_evidence_recorded",
}
STATIC_ONLY_RUNTIME_DENIAL_PATTERNS = (
    re.compile(r"\bstatic/readiness-only\s+DB\s+mTLS\s+contract\b", re.I),
    re.compile(r"\bDB[-\s]+mTLS\b.*\bstatic\s+evidence\s+only\b", re.I),
    re.compile(r"\bstatic\s+evidence\s+only\b.*\bDB[-\s]+mTLS\b", re.I),
    re.compile(r"\bDB[-\s]+mTLS\b.*\bruntime[-\s]+activation[-\s]+story\b", re.I),
    re.compile(r"\bruntime[-\s]+activation[-\s]+story\b.*\bDB[-\s]+mTLS\b", re.I),
    re.compile(r"\bstatic\s+readiness\s+wiring\b", re.I),
    re.compile(r"\bdoes\s+not\s+(?:add|implement)\s+runtime\s+DB\s+mTLS\b", re.I),
    re.compile(r"\bno\s+runtime\s+DB\s+mTLS\s+(?:code|implementation)\b", re.I),
    re.compile(r"\bruntime\s+DB\s+mTLS\s+implementation\b.*\bpending\b", re.I),
    re.compile(r"\blater\s+approved\s+runtime\s+story\s+enables\s+DB\s+mTLS\b", re.I),
    re.compile(r"\bfuture\s+approved\s+story\s+enables\s+DB\s+mTLS\b", re.I),
    re.compile(r"\b133-2-postgres-server-client-mtls-runtime:\s+backlog\b", re.I),
)
FEATURE_STATUS_STALE_DB_MTLS_PATTERNS = (
    re.compile(
        r"\bDB\s+connection\s+mTLS\b.*\bplanning[- ]only\s*/\s*deferred\b.*"
        r"\bimplementation\s+stories\b",
        re.I,
    ),
    re.compile(
        r"\bDB\s+connection\s+mTLS\b.*\bremain(?:s)?\b.*\bplanning[- ]only\b.*"
        r"\bdeferred\b",
        re.I,
    ),
)
FEATURE_STATUS_CURRENT_PHASE_PATTERN = re.compile(
    r"(?m)^-\s+\*\*Current phase:\*\*\s+(?P<line>.+)$"
)
FEATURE_STATUS_STALE_CURRENT_PHASE_PATTERNS = (
    re.compile(r"\bPhase\s+48\b", re.I),
    re.compile(r"\bEpic\s+130\b", re.I),
)
FORBIDDEN_CLOSURE_PENDING_PATTERNS = (re.compile(r"\bpending\s+until\s+gates\s+run\b", re.I),)
ALLOWED_STORY_133_5_STATUSES = frozenset({"done", "closed"})
FORBIDDEN_STORY_133_5_STATUSES = frozenset({"backlog", "in_progress", "in-progress"})


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
    sanitized = re.sub(
        r"(?i)(password|passwd|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", sanitized
    )
    sanitized = re.sub(r"/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", "[redacted-path]", sanitized)
    sanitized = re.sub(
        r"\b[A-Za-z0-9_.-]+\.(?:crt|cert|cer|key|pem|crl)\b", "[redacted-file]", sanitized
    )
    sanitized = re.sub(r"\b(?:CN|SAN|subject)=\S+", "[redacted-name]", sanitized)
    return sanitized


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


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name)
    return value if isinstance(value, Mapping) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {entry for entry in value if isinstance(entry, str)}


def _lower_text(value: object) -> str:
    return "\n".join(_walk_strings(value)).lower()


def _ref_path(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_anchor(ref: str) -> str:
    return ref.split("#", 1)[1] if "#" in ref else ""


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
    relpath = _ref_path(ref)
    anchor = _ref_anchor(ref)
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
        p.search(value) for p in SECRET_VALUE_PATTERNS
    )


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


def _normalise_status(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def _validate_machine_statuses(data: Mapping[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    for path, expected in EXPECTED_MACHINE_STATUSES.items():
        actual = _lookup_path(data, path)
        if actual != expected:
            violations.append(Violation(str(CONTRACT_PATH), f"{'.'.join(path)} must be {expected}"))
        if isinstance(actual, str) and _normalise_status(actual) in FORBIDDEN_MACHINE_STATUSES:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{'.'.join(path)} must not claim runtime status")
            )
    for path, value in _iter_path_values(data):
        path_text = "_".join(path).lower()
        if path in EXPECTED_MACHINE_STATUSES or not isinstance(value, str):
            continue
        if (
            _normalise_status(value) in FORBIDDEN_MACHINE_STATUSES
            and any(t in path_text for t in ("runtime", "activation", "production", "status"))
            and not any(t in path_text for t in ("deferred", "future", "disabled", "no"))
        ):
            violations.append(
                Violation(str(CONTRACT_PATH), f"{'.'.join(path)} must not claim runtime status")
            )
    return violations


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("epic") != "133":
        violations.append(Violation(str(CONTRACT_PATH), "epic must be 133"))
    if missing := REQUIRED_TOP_LEVEL_SECTIONS - set(data):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required DB mTLS sections missing {sorted(missing)}")
        )
    violations.extend(_validate_machine_statuses(data))
    ca = _section(data, "ca_ownership")
    if ca.get("status") != "contract_required" or "security_owner" not in _lower_text(ca):
        violations.append(
            Violation(str(CONTRACT_PATH), "ca_ownership must name security_owner contract")
        )
    cert_profiles = _lower_text(_section(data, "certificate_profiles"))
    for phrase in ("server san", "client certificate", "verify-full", "postgres"):
        if phrase not in cert_profiles:
            violations.append(
                Violation(str(CONTRACT_PATH), f"certificate_profiles missing {phrase!r}")
            )
    approved = _section(data, "approved_secret_locations")
    prefixes = _string_set(approved.get("approved_prefixes"))
    if missing := REQUIRED_APPROVED_PREFIXES - prefixes:
        violations.append(
            Violation(str(CONTRACT_PATH), f"approved_secret_locations missing {sorted(missing)}")
        )
    if approved.get("canonical_resolution") != "realpath_under_approved_prefix":
        violations.append(
            Violation(
                str(CONTRACT_PATH), "approved_secret_locations must require canonical realpath"
            )
        )
    if approved.get("symlink_escape_policy") != "reject":
        violations.append(
            Violation(str(CONTRACT_PATH), "symlink/canonical path escapes must reject")
        )
    if approved.get("private_key_permission_policy") != "reject_group_or_world_readable":
        violations.append(
            Violation(
                str(CONTRACT_PATH), "private-key mode/ownership checks must reject unsafe modes"
            )
        )
    for path in _string_set(approved.get("example_paths")):
        if not any(path.startswith(prefix) for prefix in prefixes):
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "approved_secret_locations has unapproved path example"
                )
            )
    env_keys = _string_set(_section(data, "env_config_keys").get("required_keys"))
    if missing := REQUIRED_ENV_KEYS - env_keys:
        violations.append(
            Violation(str(CONTRACT_PATH), f"env_config_keys missing {sorted(missing)}")
        )
    profile = _section(data, "profile_gating_and_url_policy")
    policies = _string_set(profile.get("policies"))
    if missing := REQUIRED_URL_POLICIES - policies:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"profile_gating_and_url_policy missing {sorted(missing)}"
            )
        )
    if profile.get("disabled_profile_connect_args") != "unchanged_no_ssl_args":
        violations.append(
            Violation(str(CONTRACT_PATH), "disabled profile must preserve no SSL args")
        )
    server = _section(data, "server_side_postgres_evidence")
    for field, expected in REQUIRED_SERVER_FIELDS.items():
        if server.get(field) != expected:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"server-side Postgres evidence must identify {field}={expected}",
                )
            )
    settings = _string_set(server.get("postgresql_conf_required"))
    if missing := REQUIRED_SERVER_SETTINGS - settings:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"server-side Postgres settings missing {sorted(missing)}"
            )
        )
    if server.get("revocation_claimed") is not True:
        violations.append(
            Violation(str(CONTRACT_PATH), "server-side revocation_claimed must be true")
        )
    if "ssl_crl_file or ssl_crl_dir" not in _string_set(server.get("revocation_settings_required")):
        violations.append(
            Violation(
                str(CONTRACT_PATH), "server-side CRL setting required when revocation is claimed"
            )
        )
    if missing := REQUIRED_SERVER_EVIDENCE - _string_set(server.get("approved_secret_evidence")):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"server-side approved secret evidence missing {sorted(missing)}",
            )
        )
    if missing := REQUIRED_PG_HBA_TOKENS - _string_set(server.get("pg_hba_required")):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                f"pg_hba.conf hostssl clientcert evidence missing {sorted(missing)}",
            )
        )
    if server.get("sslmode_disable_rejection") is not True:
        violations.append(
            Violation(str(CONTRACT_PATH), "server must require explicit sslmode=disable rejection")
        )
    client_text = _lower_text(_section(data, "client_side_registry_evidence"))
    for phrase in ("asyncpg ssl", "alembic", "verify-full", "sanitized"):
        if phrase not in client_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"client-side registry evidence missing {phrase!r}")
            )
    if missing := REQUIRED_ROTATION_ITEMS - _string_set(
        _section(data, "rotation_revocation_drills").get("required_evidence")
    ):
        violations.append(
            Violation(str(CONTRACT_PATH), f"rotation/revocation drill missing {sorted(missing)}")
        )
    failure = _section(data, "failure_observability")
    if missing := REQUIRED_FAILURE_CLASSES - _string_set(failure.get("failure_classes")):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"failure_observability classes missing {sorted(missing)}"
            )
        )
    retry = _section(failure, "bounded_retry")
    if retry.get("required") is not True or not all(
        k in retry for k in ("max_attempts", "backoff", "outcome")
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH), "failure_observability must require bounded retry metadata"
            )
        )
    if retry.get("outcome") != "fail_closed":
        violations.append(Violation(str(CONTRACT_PATH), "bounded retry outcome must fail closed"))
    redaction = _section(data, "redaction_and_secret_hygiene")
    if missing := REQUIRED_REDACTION_SURFACES - _string_set(redaction.get("required_surfaces")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"redaction surfaces missing {sorted(missing)}")
        )
    forbidden_outputs = _lower_text(redaction.get("forbidden_diagnostics", []))
    for phrase in (
        "password",
        "private key",
        "pem",
        "full dsn",
        "full filesystem path",
        "cert/key filename",
        "certificate subject",
        "san hostname",
        "production hostname",
    ):
        if phrase not in forbidden_outputs:
            violations.append(
                Violation(str(CONTRACT_PATH), f"redaction forbidden diagnostics missing {phrase!r}")
            )
    closure = _section(data, "closure_gate")
    if missing := REQUIRED_CLOSURE_FIELDS - _string_set(closure.get("required_fields")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"closure gate fields missing {sorted(missing)}")
        )
    recorded_evidence = _string_set(closure.get("recorded_evidence"))
    for field in ("docs", "CI", "checker", "scanner", "code-review", "UltraQA"):
        if field not in recorded_evidence:
            violations.append(
                Violation(str(CONTRACT_PATH), f"closure gate recorded evidence missing {field}")
            )
    if missing := REQUIRED_FAIL_CLOSED_CHECKS - _string_set(data.get("fail_closed_checks")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail_closed_checks missing {sorted(missing)}")
        )
    docs_refs = _string_set(data.get("docs_refs"))
    status_refs = _string_set(data.get("status_refs"))
    if missing := REQUIRED_DOC_REFS - docs_refs:
        violations.append(Violation(str(CONTRACT_PATH), f"docs_refs missing {sorted(missing)}"))
    if missing := REQUIRED_STATUS_REFS - status_refs:
        violations.append(Violation(str(CONTRACT_PATH), f"status_refs missing {sorted(missing)}"))
    for ref in docs_refs | status_refs:
        violations.extend(_validate_ref_target(root, ref))
    if any(_contains_secret_value(value) for value in _walk_strings(data)):
        violations.append(Violation(str(CONTRACT_PATH), "contract contains secret-like material"))
    return violations


def _strip_shell_comment(line: str) -> str:
    in_single = in_double = escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _normalise_executable_command(line: str) -> str:
    candidate = _strip_shell_comment(line).strip()
    while candidate.startswith(("@", "-")):
        candidate = candidate[1:].lstrip()
    return candidate


def _recipe_header_matches(line: str, recipe: str) -> bool:
    return not line[:1].isspace() and bool(
        re.match(rf"^{re.escape(recipe)}(?:\s[^:]*)?:", _strip_shell_comment(line).strip())
    )


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


def _just_recipe_has_command(text: str, recipe: str, command: str) -> bool:
    return any(candidate == command for candidate in _iter_just_recipe_commands(text, recipe))


def _contains_exact_command(text: str, command: str) -> bool:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("run:"):
            candidate = candidate.removeprefix("run:").strip()
        if _normalise_executable_command(candidate) == command:
            return True
    return False


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    justfile = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    for recipe in ("lint", "check-gates"):
        if not _just_recipe_has_command(justfile, recipe, CHECKER_COMMAND):
            violations.append(Violation(str(JUSTFILE_PATH), f"{recipe}: missing DB mTLS checker"))
    if not _just_recipe_has_command(justfile, "check-gates-self-test", CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(str(JUSTFILE_PATH), "check-gates-self-test missing DB mTLS self-test")
        )
    if not _contains_exact_command(ci, CHECKER_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI missing DB mTLS checker step"))
    if not _contains_exact_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI missing DB mTLS checker self-test"))
    return violations


def _validate_docs_and_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    required_mentions = {
        OPERATOR_RUNBOOK_PATH: [
            "Story 133",
            CHECKER_COMMAND,
            "sslmode=disable",
            "hostssl",
            "runtime-gated registry-state DB mTLS support",
        ],
        PRODUCTION_OPS_PATH: ["Epic 133", str(CONTRACT_PATH), CHECKER_COMMAND],
        FEATURE_STATUS_PATH: ["Epic 133", str(CONTRACT_PATH), str(CLOSURE_ARTIFACT_PATH)],
        SPRINT_STATUS_PATH: ["epic-133", "133-1-db-mtls-static-readiness-contract"],
        ARTIFACT_133_1_PATH: ["Story 133.1", CHECKER_COMMAND, "runtime-gated"],
        CLOSURE_ARTIFACT_PATH: [
            "Story 133.5",
            "Architect APPROVE/CLEAR",
            "Critic APPROVE/CLEAR",
            "closure rework",
            "runtime-gated registry-state DB mTLS support",
        ],
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


def _validate_sprint_closure_status(root: Path) -> list[Violation]:
    path = root / SPRINT_STATUS_PATH
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*133-5-db-mtls-closure-evidence:\s*([A-Za-z0-9_-]+)\b", text)
    if not match:
        return [Violation(str(SPRINT_STATUS_PATH), "133.5 closure status entry is missing")]
    status = _normalise_status(match.group(1))
    if status in FORBIDDEN_STORY_133_5_STATUSES or status not in ALLOWED_STORY_133_5_STATUSES:
        return [
            Violation(
                str(SPRINT_STATUS_PATH),
                "133.5 DB mTLS closure evidence must be done/closed, not in-progress/backlog",
            )
        ]
    return []


def _epic_133_closed(root: Path) -> bool:
    path = root / SPRINT_STATUS_PATH
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return bool(re.search(r"(?m)^\s*epic-133:\s*(?:done|closed)\b", text, re.I))


def _validate_feature_status_epic_133_closure(root: Path) -> list[Violation]:
    if not _epic_133_closed(root):
        return []
    path = root / FEATURE_STATUS_PATH
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    if any(pattern.search(text) for pattern in FEATURE_STATUS_STALE_DB_MTLS_PATTERNS):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "feature status must not claim DB connection mTLS is planning-only/deferred "
                "after Epic 133 closure",
            )
        )
    current_phase = FEATURE_STATUS_CURRENT_PHASE_PATTERN.search(text)
    if not current_phase:
        violations.append(Violation(str(FEATURE_STATUS_PATH), "Current phase bullet is missing"))
        return violations
    current_phase_line = current_phase.group("line")
    if any(
        pattern.search(current_phase_line)
        for pattern in FEATURE_STATUS_STALE_CURRENT_PHASE_PATTERNS
    ):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "Current phase must reflect Phase 50 / Epic 133 closure, not Phase 48 / Epic 130",
            )
        )
    if not re.search(r"\bPhase\s+50\b", current_phase_line, re.I) or not re.search(
        r"\bEpic\s+133\b", current_phase_line, re.I
    ):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "Current phase must name Phase 50 / Epic 133 after Epic 133 closure",
            )
        )
    return violations


def _validate_feature_status_postgres_mtls_row(root: Path) -> list[Violation]:
    path = root / FEATURE_STATUS_PATH
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\|\s*Postgres connection mTLS\s*\|\s*([^|]+)\|([^\n]+)$", text)
    if not match:
        return [Violation(str(FEATURE_STATUS_PATH), "Postgres connection mTLS status row missing")]
    status_cell = match.group(1).strip().lower()
    row = match.group(0).lower()
    if "not implemented" in status_cell or re.search(r"\bdeferred\s*/\s*not implemented\b", row):
        return [
            Violation(
                str(FEATURE_STATUS_PATH),
                "Postgres connection mTLS row must not say deferred/not implemented after runtime-gated support",
            )
        ]
    if "database connection mtls remains future work" in row:
        return [
            Violation(
                str(FEATURE_STATUS_PATH),
                "Postgres connection mTLS row must describe runtime-gated support, not future work",
            )
        ]
    return []


def _runtime_code_present(root: Path) -> bool:
    return (root / MTLS_RUNTIME_PATH).exists() and (root / REGISTRY_RUNTIME_PATH).exists()


def _validate_runtime_status_parity(root: Path) -> list[Violation]:
    if not _runtime_code_present(root):
        return []
    violations: list[Violation] = []
    for relpath in DOC_STATUS_PATHS:
        path = root / relpath
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in STATIC_ONLY_RUNTIME_DENIAL_PATTERNS):
            violations.append(
                Violation(
                    str(relpath),
                    "Epic 133 docs/status must describe runtime-gated support, not static-only/no-runtime/backlog",
                )
            )
    return violations


def _is_overclaim_match(match: re.Match[str], text: str) -> bool:
    sentence_start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start()))
    sentence_end = text.find(".", match.end())
    sentence = text[sentence_start + 1 : sentence_end if sentence_end != -1 else len(text)]
    return NEGATION_PATTERN.search(sentence) is None


def _plaintext_claim_is_forbidden(match: re.Match[str], text: str) -> bool:
    sentence_start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start()))
    sentence_end = text.find(".", match.end())
    sentence = text[sentence_start + 1 : sentence_end if sentence_end != -1 else len(text)]
    return NEGATION_PATTERN.search(sentence) is None


def _validate_overclaims_and_secrets(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in DOC_STATUS_PATHS:
        path = root / relpath
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if any(_is_overclaim_match(m, text) for p in OVERCLAIM_PATTERNS for m in p.finditer(text)):
            violations.append(Violation(str(relpath), "DB mTLS runtime overclaim is forbidden"))
        if any(
            _plaintext_claim_is_forbidden(m, text)
            for p in PLAINTEXT_FALLBACK_PATTERNS
            for m in p.finditer(text)
        ):
            violations.append(Violation(str(relpath), "plaintext fallback claim is forbidden"))
    for relpath in SECRET_SCAN_PATHS:
        path = root / relpath
        if path.exists() and _contains_secret_value(path.read_text(encoding="utf-8")):
            violations.append(Violation(str(relpath), "secret-like value is forbidden"))
    return violations


def _approval_file_has_clear(root: Path, relpath: Path, role: str) -> bool:
    path = root / relpath
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return role in text and "Verdict: APPROVE/CLEAR" in text


def _validate_closure_artifact(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    if not _approval_file_has_clear(root, ARCHITECT_APPROVAL_PATH, "architect"):
        violations.append(
            Violation(str(CLOSURE_ARTIFACT_PATH), "Architect cycle-3 approval missing")
        )
    if not _approval_file_has_clear(root, CRITIC_APPROVAL_PATH, "critic"):
        violations.append(Violation(str(CLOSURE_ARTIFACT_PATH), "Critic cycle-3 approval missing"))
    path = root / CLOSURE_ARTIFACT_PATH
    if not path.exists():
        return violations
    text = path.read_text(encoding="utf-8")
    if any(pattern.search(text) for pattern in FORBIDDEN_CLOSURE_PENDING_PATTERNS):
        violations.append(
            Violation(
                str(CLOSURE_ARTIFACT_PATH),
                "completed closure evidence must not contain unresolved pending-until-gates placeholders",
            )
        )
    required_phrases = (
        "enabled/disabled DB mTLS matrix",
        "rotation/revocation drill evidence",
        "failure-mode and observability evidence",
        "secret-scanner evidence",
        "docs/status links",
        "code-review",
        "UltraQA",
        "command-output",
        "split-deployment/remote Postgres composition",
        str(ARCHITECT_APPROVAL_PATH),
        str(CRITIC_APPROVAL_PATH),
    )
    for phrase in required_phrases:
        if phrase not in text:
            violations.append(
                Violation(str(CLOSURE_ARTIFACT_PATH), f"closure evidence missing {phrase!r}")
            )
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"failed to load contract: {exc}")]
    violations: list[Violation] = []
    violations.extend(_validate_contract(root, data))
    violations.extend(_validate_docs_and_status(root))
    violations.extend(_validate_wiring(root))
    violations.extend(_validate_overclaims_and_secrets(root))
    violations.extend(_validate_runtime_status_parity(root))
    violations.extend(_validate_sprint_closure_status(root))
    violations.extend(_validate_feature_status_postgres_mtls_row(root))
    violations.extend(_validate_feature_status_epic_133_closure(root))
    violations.extend(_validate_closure_artifact(root))
    return violations


def _copy_self_test_fixture(src_root: Path, dst_root: Path) -> None:
    for relpath in (
        CONTRACT_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_133_1_PATH,
        CLOSURE_ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
        ARCHITECT_APPROVAL_PATH,
        CRITIC_APPROVAL_PATH,
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
        data = _load_json(root, CONTRACT_PATH)
        server = data["server_side_postgres_evidence"]
        if not isinstance(server, dict):
            print("self-test fixture malformed", file=sys.stderr)
            return 1
        server["postgresql_conf_required"] = ["ssl_cert_file"]
        (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not any("server-side Postgres settings missing" in v.message for v in validate(root)):
            print("self-test failed to detect missing server-side settings", file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        data["notes"] = "-----BEGIN " + "PRIVATE KEY-----"
        (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not any("secret-like" in v.message for v in validate(root)):
            print("self-test failed to detect secret-like material", file=sys.stderr)
            return 1
    print("DB mTLS readiness self-test passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("DB mTLS readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("DB mTLS readiness check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
