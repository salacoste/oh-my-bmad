#!/usr/bin/env python3
"""Validate Story 132.2 remote Postgres production-readiness contract.

This checker is intentionally a readiness/static guard. It validates the
contract shape, docs/status references and anchors, just/CI wiring, redaction,
overclaim prevention, and a small set of existing runtime strings where they are
reasonable to assert without activating or provisioning remote Postgres.
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
CONTRACT_PATH = Path("docs/remote-postgres-production-readiness.json")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
BACKUP_RESTORE_PATH = Path("docs/backup-restore.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/132-2-remote-postgres-production-mode.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
REGISTRY_STATE_ENGINE_PATH = Path(
    "services/registry-state/src/registry_state/adapters/sqlite_store.py"
)
REGISTRY_STATE_MIGRATIONS_PATH = Path(
    "services/registry-state/src/registry_state/migrations/env.py"
)
REGISTRY_API_APP_PATH = Path("services/registry-api/src/registry_api/app.py")
MTLS_RUNTIME_PATH = Path("packages/mtls/src/mtls/db.py")
CHECKER_COMMAND = "uv run python scripts/check_remote_postgres_readiness.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "current_default_preservation",
        "opt_in_remote_postgres_runtime_support",
        "bounded_pool_contract",
        "migration_and_backup_gate",
        "redaction_and_secret_hygiene",
        "db_mtls_composition",
        "backup_restore_drill",
        "registry_api_read_side_support",
        "non_goals",
        "fail_closed_checks",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#remote-postgres-production-readiness-story-1322",
        f"{PRODUCTION_OPS_PATH}#story-1322-remote-postgres-production-readiness",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{BACKUP_RESTORE_PATH}#remote-postgres-backuprestore-drill-readiness-story-1322",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_STATUS_REFS = frozenset(
    {f"{SPRINT_STATUS_PATH}#development_status", f"{FEATURE_STATUS_PATH}#current-bmad-status"}
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "SQLite default is preserved",
        "remote Postgres remains explicit opt-in only",
        "production activation is deferred/fail-closed",
        "bounded pool contract is exact: pool_size formula, max_overflow 5, pool_timeout 30, pool_recycle 1800, pre_ping true",
        "Alembic has a single migration runner with pre-migration backup evidence",
        "backup/restore drill requires checksum, integrity, and rollback/fix-forward evidence",
        "Epic 133 REGISTRY_DB_MTLS_ENABLED composition is documented",
        "registry-api read-side support is documented without creating a second migration/materialization writer",
        "redaction forbids DSNs, passwords, private keys, paths, and hostnames",
        "no live activation, provisioning, credentials, or overclaims are present",
        "justfile and CI checker wiring is present",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no live remote Postgres production activation",
        "no live Postgres provisioning",
        "no production credentials or DSN values",
        "no compose profile or overlay activation",
        "no deployment target mutation",
        "no automatic migration execution",
        "no multiple migration runners",
        "no runtime production audit emitter",
        "no DB mTLS production activation",
    }
)
REQUIRED_REDACTION_FORBIDDEN = frozenset(
    {
        "password",
        "secret",
        "token",
        "private key",
        "full DSN",
        "full filesystem path",
        "production hostname",
        "certificate subject",
        "SAN hostname",
    }
)
REQUIRED_MIGRATION_EVIDENCE = frozenset(
    {
        "single migration runner identity and lock/election evidence",
        "pre-migration backup artifact with checksum",
        "Alembic head/current revision evidence before and after migration",
        "no registry-api or worker migration runner",
        "rollback or fix-forward decision record for failed migrations",
    }
)
REQUIRED_BACKUP_EVIDENCE = frozenset(
    {
        "pg_dump or managed snapshot reference",
        "sha256 checksum for backup artifact or provider snapshot identity",
        "restored scratch target identity",
        "Postgres integrity check or logical consistency query evidence",
        "Alembic revision parity after restore",
        "event-log/materialized-state reconciliation evidence",
        "rollback or fix-forward decision record",
    }
)
DOC_STATUS_PATHS = (
    CONTRACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    BACKUP_RESTORE_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = DOC_STATUS_PATHS
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"),
)
POSTGRES_URL_PATTERN = re.compile(r"""(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>]+""")
PLACEHOLDER_PASSWORDS = frozenset(
    {"password", "pass", "secret", "example", "placeholder", "changeme", "test", "****", "redacted"}
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\bremote\s+Postgres\b[^\n.]{0,160}\b(?:live|active|activated|production[- ]ready|shipped|provisioned)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|active|activated|production[- ]ready|shipped|provisioned)\b[^\n.]{0,160}\bremote\s+Postgres\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|is\s+not|remains\s+deferred)\b",
    re.I,
)
FORBIDDEN_MACHINE_STATUSES = frozenset(
    {"active", "activated", "complete", "enabled", "live", "production_ready", "ready", "shipped"}
)
EXPECTED_MACHINE_STATUSES = {
    ("mode",): "readiness_contract_runtime_opt_in",
    ("production_activation",): "deferred_fail_closed",
    ("current_default_preservation", "status"): "preserved",
    ("opt_in_remote_postgres_runtime_support", "status"): "opt_in_supported_local_only",
    ("bounded_pool_contract", "status"): "required_before_production_activation",
    ("migration_and_backup_gate", "status"): "operator_contract_required",
    ("redaction_and_secret_hygiene", "status"): "required",
    ("db_mtls_composition", "status"): "compose_with_epic_133_runtime_gate",
    ("backup_restore_drill", "status"): "required_before_production_activation",
    ("registry_api_read_side_support", "status"): "read_side_contract_required",
}


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {_sanitize_for_output(self.message)}"


def _sanitize_for_output(text: str) -> str:
    sanitized = POSTGRES_URL_PATTERN.sub("[redacted-dsn]", text)
    sanitized = re.sub(
        r"(?i)(password|passwd|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", sanitized
    )
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", "[redacted-pem]", sanitized
    )
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
            yield str(key)
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
                Violation(
                    str(CONTRACT_PATH), f"{'.'.join(path)} must not claim production activation"
                )
            )
    for path, value in _iter_path_values(data):
        path_text = "_".join(path).lower()
        if path in EXPECTED_MACHINE_STATUSES or not isinstance(value, str):
            continue
        if _normalise_status(value) in FORBIDDEN_MACHINE_STATUSES and any(
            token in path_text
            for token in ("activation", "production", "postgres", "status", "runtime")
        ):
            violations.append(
                Violation(str(CONTRACT_PATH), f"{'.'.join(path)} must not claim activation status")
            )
    return violations


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
        pattern.search(value) for pattern in SECRET_VALUE_PATTERNS
    )


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("epic") != "132":
        violations.append(Violation(str(CONTRACT_PATH), "epic must be 132"))
    if data.get("story") != "132.2":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 132.2"))
    if missing := REQUIRED_TOP_LEVEL_SECTIONS - set(data):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required remote Postgres sections missing {sorted(missing)}"
            )
        )
    violations.extend(_validate_machine_statuses(data))

    current = _section(data, "current_default_preservation")
    for flag in (
        "sqlite_default",
        "remote_postgres_requires_explicit_database_url",
        "no_default_remote_dsn",
        "no_compose_profile_activation",
        "no_production_credentials",
    ):
        if current.get(flag) is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"current_default_preservation must keep {flag}=true")
            )
    if "sqlite remains the default" not in _lower_text(current):
        violations.append(
            Violation(
                str(CONTRACT_PATH), "current_default_preservation must state SQLite remains default"
            )
        )

    opt_in = _section(data, "opt_in_remote_postgres_runtime_support")
    if (
        opt_in.get("activation_key") != "REGISTRY_DATABASE_URL"
        or opt_in.get("required_scheme") != "postgresql+asyncpg://"
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "opt-in remote Postgres must require REGISTRY_DATABASE_URL with postgresql+asyncpg://",
            )
        )
    if "remote postgres is never selected by default" not in _string_set(
        opt_in.get("fail_closed_rules")
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "opt-in fail_closed_rules must deny default remote Postgres selection",
            )
        )

    pool = _section(data, "bounded_pool_contract")
    expected_pool = {
        "pool_size_formula": "5 + 2 * num_workers",
        "max_overflow": 5,
        "pool_timeout_seconds": 30,
        "pool_recycle_seconds": 1800,
        "pool_pre_ping": True,
    }
    for key, expected in expected_pool.items():
        if pool.get(key) != expected:
            violations.append(
                Violation(str(CONTRACT_PATH), f"bounded_pool_contract {key} must be {expected!r}")
            )

    migration = _section(data, "migration_and_backup_gate")
    for key in (
        "alembic_required",
        "single_migration_runner_required",
        "pre_migration_backup_required",
    ):
        if migration.get(key) is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"migration_and_backup_gate must keep {key}=true")
            )
    if missing := REQUIRED_MIGRATION_EVIDENCE - _string_set(migration.get("required_evidence")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"migration evidence missing {sorted(missing)}")
        )

    redaction = _section(data, "redaction_and_secret_hygiene")
    if missing := REQUIRED_REDACTION_FORBIDDEN - _string_set(
        redaction.get("forbidden_diagnostics")
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"redaction forbidden diagnostics missing {sorted(missing)}"
            )
        )

    mtls = _section(data, "db_mtls_composition")
    if mtls.get(
        "env_key"
    ) != "REGISTRY_DB_MTLS_ENABLED" or "no plaintext fallback" not in _lower_text(mtls):
        violations.append(
            Violation(
                str(CONTRACT_PATH),
                "DB mTLS composition must bind REGISTRY_DB_MTLS_ENABLED and no plaintext fallback",
            )
        )

    backup = _section(data, "backup_restore_drill")
    if missing := REQUIRED_BACKUP_EVIDENCE - _string_set(backup.get("required_evidence")):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"backup/restore drill evidence missing {sorted(missing)}"
            )
        )
    backup_text = _lower_text(backup)
    for phrase in ("checksum", "integrity", "rollback/fix-forward"):
        if phrase not in backup_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"backup_restore_drill missing {phrase!r}")
            )

    read_side = _lower_text(_section(data, "registry_api_read_side_support"))
    for phrase in (
        "registry-api",
        "read",
        "shared sqlalchemy session factory",
        "must not run alembic",
        "second state materializer",
    ):
        if phrase not in read_side:
            violations.append(
                Violation(str(CONTRACT_PATH), f"registry_api_read_side_support missing {phrase!r}")
            )

    if missing := REQUIRED_NON_GOALS - _string_set(data.get("non_goals")):
        violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing)}"))
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
            violations.append(
                Violation(
                    str(JUSTFILE_PATH), f"{recipe}: missing remote Postgres readiness checker"
                )
            )
    if not _just_recipe_has_command(justfile, "check-gates-self-test", CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(
                str(JUSTFILE_PATH),
                "check-gates-self-test missing remote Postgres readiness self-test",
            )
        )
    if not _contains_exact_command(ci, CHECKER_COMMAND):
        violations.append(
            Violation(str(CI_PATH), "CI missing remote Postgres readiness checker step")
        )
    if not _contains_exact_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI missing remote Postgres readiness self-test"))
    return violations


def _validate_docs_and_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    required_mentions = {
        OPERATOR_RUNBOOK_PATH: [
            "Story 132.2",
            CHECKER_COMMAND,
            "REGISTRY_DATABASE_URL",
            "REGISTRY_DB_MTLS_ENABLED",
        ],
        PRODUCTION_OPS_PATH: ["Story 132.2", str(CONTRACT_PATH), CHECKER_COMMAND],
        FEATURE_STATUS_PATH: ["Story 132.2", str(CONTRACT_PATH), str(ARTIFACT_PATH)],
        BACKUP_RESTORE_PATH: [
            "Story 132.2",
            "checksum",
            "integrity",
            "rollback/fix-forward",
        ],
        SPRINT_STATUS_PATH: ["132-2-remote-postgres-production-mode", "epic-132"],
        ARTIFACT_PATH: ["Story 132.2", CHECKER_COMMAND, "No live activation"],
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


def _is_overclaim_match(match: re.Match[str], text: str) -> bool:
    # Use human sentence boundaries, but do not split on story-number decimals
    # such as "132.3" immediately before a heading/title.
    before = text[: match.start()]
    starts = [m.end() for m in re.finditer(r"[.!?](?=\s+[A-Z#])", before)]
    starts.extend(m.end() for m in re.finditer(r"\n(?=\s*(?:[-*#]|$))", before))
    sentence_start = max(starts, default=0)
    after = text[match.end() :]
    end_match = re.search(r"[.!?](?=\s|$)", after)
    sentence_end = match.end() + end_match.end() if end_match else len(text)
    sentence = text[sentence_start:sentence_end]
    if len(sentence) > 400:
        line_start = text.rfind("\n", 0, match.start())
        line_end = text.find("\n", match.end())
        sentence = text[line_start + 1 : line_end if line_end != -1 else len(text)]
    return NEGATION_PATTERN.search(sentence) is None


def _validate_overclaims_and_secrets(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in DOC_STATUS_PATHS:
        path = root / relpath
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if any(_is_overclaim_match(m, text) for p in OVERCLAIM_PATTERNS for m in p.finditer(text)):
            violations.append(
                Violation(str(relpath), "remote Postgres production overclaim is forbidden")
            )
    for relpath in SECRET_SCAN_PATHS:
        path = root / relpath
        if path.exists() and _contains_secret_value(path.read_text(encoding="utf-8")):
            violations.append(Violation(str(relpath), "secret-like value is forbidden"))
    return violations


def _validate_runtime_strings(root: Path) -> list[Violation]:
    """Validate stable local runtime strings without treating this story as activation."""
    violations: list[Violation] = []
    engine_path = root / REGISTRY_STATE_ENGINE_PATH
    if not engine_path.exists():
        return [
            Violation(str(REGISTRY_STATE_ENGINE_PATH), "registry-state engine factory is missing")
        ]
    engine = engine_path.read_text(encoding="utf-8")
    required_engine_needles = [
        "postgresql+asyncpg://",
        "safe_worker_count = _validate_worker_count(worker_count)",
        "pool_size = 5 + 2 * safe_worker_count",
        "max_overflow=5",
        "pool_timeout=30",
        "pool_recycle=1800",
        "pool_pre_ping=True",
        "build_db_mtls_connect_args(url)",
    ]
    for needle in required_engine_needles:
        if needle not in engine:
            violations.append(
                Violation(str(REGISTRY_STATE_ENGINE_PATH), f"missing runtime string {needle!r}")
            )
    migrations_path = root / REGISTRY_STATE_MIGRATIONS_PATH
    if migrations_path.exists():
        migrations = migrations_path.read_text(encoding="utf-8")
        for needle in (
            "REGISTRY_DATABASE_URL",
            "REGISTRY_STATE_DB_URL",
            "build_db_mtls_connect_args",
        ):
            if needle not in migrations:
                violations.append(
                    Violation(
                        str(REGISTRY_STATE_MIGRATIONS_PATH),
                        f"missing migration runtime string {needle!r}",
                    )
                )
    else:
        violations.append(
            Violation(str(REGISTRY_STATE_MIGRATIONS_PATH), "Alembic env.py is missing")
        )

    api_path = root / REGISTRY_API_APP_PATH
    if api_path.exists():
        api = api_path.read_text(encoding="utf-8")
        for needle in ("create_engine", "get_session", "db_url", "session_maker"):
            if needle not in api:
                violations.append(
                    Violation(
                        str(REGISTRY_API_APP_PATH),
                        f"missing registry-api read-side string {needle!r}",
                    )
                )
    else:
        violations.append(Violation(str(REGISTRY_API_APP_PATH), "registry-api app is missing"))

    mtls_path = root / MTLS_RUNTIME_PATH
    if mtls_path.exists() and "REGISTRY_DB_MTLS_ENABLED" not in mtls_path.read_text(
        encoding="utf-8"
    ):
        violations.append(
            Violation(str(MTLS_RUNTIME_PATH), "missing REGISTRY_DB_MTLS_ENABLED runtime gate")
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
    violations.extend(_validate_runtime_strings(root))
    return violations


def _copy_self_test_fixture(src_root: Path, dst_root: Path) -> None:
    for relpath in (
        CONTRACT_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        BACKUP_RESTORE_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
        REGISTRY_STATE_ENGINE_PATH,
        REGISTRY_STATE_MIGRATIONS_PATH,
        REGISTRY_API_APP_PATH,
        MTLS_RUNTIME_PATH,
    ):
        src = src_root / relpath
        dst = dst_root / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
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
        pool = data["bounded_pool_contract"]
        if not isinstance(pool, dict):
            print("self-test fixture malformed", file=sys.stderr)
            return 1
        pool["max_overflow"] = 99
        (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not any("max_overflow" in v.message for v in validate(root)):
            print("self-test failed to detect bad pool overflow", file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        data["notes"] = "remote Postgres production is live for customers"
        (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not any("overclaim" in v.message for v in validate(root)):
            print("self-test failed to detect overclaim", file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        data["notes"] = "-----BEGIN " + "PRIVATE KEY-----"
        (root / CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not any("secret-like" in v.message for v in validate(root)):
            print("self-test failed to detect secret-like material", file=sys.stderr)
            return 1
    print("remote Postgres readiness self-test passed")
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
        print("remote Postgres readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("remote Postgres readiness check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
