#!/usr/bin/env python3
"""Validate Story 132.3 registry remote Postgres deployment-profile readiness.

This is a static/readiness gate. It proves the opt-in compose overlay/profile,
root SQLite default preservation, DB mTLS composition, migration/backup hooks,
docs/status wiring, and secret hygiene without provisioning or contacting a live
Postgres server.
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
CONTRACT_PATH = Path("docs/registry-remote-postgres-profile-readiness.json")
OVERLAY_PATH = Path("docker-compose.registry-remote-postgres.yml")
ROOT_COMPOSE_PATH = Path("docker-compose.yml")
ENV_EXAMPLE_PATH = Path(".env.example")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/132-3-registry-remote-postgres-deployment-profile.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_registry_remote_postgres_profile.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "profile_artifact",
        "sqlite_default_preservation",
        "registry_service_wiring",
        "migration_strategy",
        "db_mtls_policy_composition",
        "backup_restore_hooks",
        "readiness_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no live remote Postgres production activation",
        "no live Postgres provisioning",
        "no production credentials or DSN values",
        "no production host mutation",
        "no automatic migration execution",
        "no backup or restore execution",
        "no registry-api migration runner",
        "no DB mTLS production activation",
        "no runtime production audit emitter",
    }
)
REQUIRED_DSN_ENV = frozenset(
    {
        "REGISTRY_DATABASE_URL",
        "REGISTRY_STATE_DB_URL",
        "REGISTRY_API_DB_URL",
        "REGISTRY_API_IDEMPOTENCY_DB_URL",
    }
)
REQUIRED_MTLS_ENV = frozenset(
    {
        "REGISTRY_DB_MTLS_ENABLED",
        "REGISTRY_DB_MTLS_SSLMODE",
        "REGISTRY_DB_MTLS_ROOT_CA",
        "REGISTRY_DB_MTLS_CLIENT_CERT",
        "REGISTRY_DB_MTLS_CLIENT_KEY",
        "REGISTRY_DB_MTLS_SERVER_HOSTNAME",
        "REGISTRY_DB_MTLS_REVOCATION_LIST",
        "REGISTRY_DB_MTLS_APPROVED_PREFIXES",
    }
)
REQUIRED_DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#registry-remote-postgres-deployment-profile-story-1323",
        f"{PRODUCTION_OPS_PATH}#story-1323-registry-remote-postgres-deployment-profile",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_STATUS_REFS = frozenset(
    {f"{SPRINT_STATUS_PATH}#development_status", f"{FEATURE_STATUS_PATH}#current-bmad-status"}
)
DOC_STATUS_PATHS = (
    CONTRACT_PATH,
    OVERLAY_PATH,
    ENV_EXAMPLE_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = (CONTRACT_PATH, OVERLAY_PATH, ENV_EXAMPLE_PATH, ARTIFACT_PATH)
POSTGRES_URL_PATTERN = re.compile(r"""(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>`]+""")
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"),
)
PLACEHOLDER_PASSWORDS = frozenset(
    {"password", "pass", "secret", "example", "placeholder", "changeme", "test", "redacted", "****"}
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:registry\s+)?remote\s+Postgres\b(?:\W+\w+){0,8}\W+(?:live|activated|production[- ]ready|provisioned|deployed)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|production[- ]ready|provisioned|deployed)\b(?:\W+\w+){0,8}\W+\b(?:registry\s+)?remote\s+Postgres\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|remains\s+deferred|until operator evidence)\b",
    re.I,
)


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


def _missing_tokens(text: str, required: Iterable[str]) -> set[str]:
    return {token for token in required if token not in text}


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _walk_strings(item)


def _is_placeholder_postgres_url(url: str) -> bool:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return True
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "postgres", "omb-postgres", "example", "example.invalid"}:
        return True
    password = parsed.password or ""
    return password.lower() in PLACEHOLDER_PASSWORDS


def _validate_contract(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except Exception as exc:
        return [Violation(str(CONTRACT_PATH), f"invalid JSON contract: {exc}")]

    missing = REQUIRED_TOP_LEVEL_SECTIONS - set(data)
    if missing:
        violations.append(
            Violation(str(CONTRACT_PATH), f"required 132.3 sections missing {sorted(missing)}")
        )

    expected = {
        ("story",): "132.3",
        ("mode",): "opt_in_profile_fail_closed",
        ("production_activation",): "deferred_operator_evidence_required",
        ("profile_artifact", "status"): "present_opt_in_only",
        ("profile_artifact", "compose_file"): str(OVERLAY_PATH),
        ("profile_artifact", "profile_name"): "registry-remote-postgres",
        ("sqlite_default_preservation", "status"): "preserved",
        ("registry_service_wiring", "status"): "shared_remote_postgres_dsn_required",
        ("migration_strategy", "status"): "operator_gated_single_runner",
        ("db_mtls_policy_composition", "status"): "composes_with_epic_133_runtime_gate",
        ("backup_restore_hooks", "status"): "operator_evidence_required_before_activation",
        ("readiness_checks", "checker_command"): CHECKER_COMMAND,
        ("readiness_checks", "self_test_command"): CHECKER_SELF_TEST_COMMAND,
    }
    for path, expected_value in expected.items():
        current: object = data
        for key in path:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current != expected_value:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must be {expected_value!r}, found {current!r}",
                )
            )

    profile = data.get("profile_artifact", {})
    if not isinstance(profile, Mapping):
        violations.append(Violation(str(CONTRACT_PATH), "profile_artifact must be an object"))
    else:
        for flag in (
            "root_compose_default_unchanged",
            "no_embedded_dsn",
            "no_postgres_provisioning",
            "no_live_host_mutation",
        ):
            if profile.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"profile_artifact.{flag}=true required")
                )

    wiring = data.get("registry_service_wiring", {})
    if isinstance(wiring, Mapping):
        required_env = set(cast("Iterable[str]", wiring.get("required_env", [])))
        missing_env = REQUIRED_DSN_ENV - required_env
        if missing_env:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"registry service required_env missing {sorted(missing_env)}",
                )
            )
        if wiring.get("idempotency_uses_same_remote_dsn") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "idempotency_uses_same_remote_dsn=true required")
            )
        if wiring.get("registry_api_not_migration_runner") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "registry_api_not_migration_runner=true required")
            )

    migration = data.get("migration_strategy", {})
    if isinstance(migration, Mapping):
        if migration.get("startup_schema_creation_disabled") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "startup_schema_creation_disabled=true required")
            )
        evidence = set(
            cast("Iterable[str]", migration.get("required_evidence_before_activation", []))
        )
        for token in ("pre-migration backup", "single migration runner", "Alembic", "rollback"):
            if not any(token.lower() in item.lower() for item in evidence):
                violations.append(
                    Violation(str(CONTRACT_PATH), f"migration evidence must include {token}")
                )

    mtls = data.get("db_mtls_policy_composition", {})
    if isinstance(mtls, Mapping):
        if mtls.get("enabled_key") != "REGISTRY_DB_MTLS_ENABLED":
            violations.append(
                Violation(
                    str(CONTRACT_PATH), "DB mTLS composition must bind REGISTRY_DB_MTLS_ENABLED"
                )
            )
        required_mtls = set(cast("Iterable[str]", mtls.get("required_when_enabled", [])))
        mtls_text = "\n".join(required_mtls)
        missing_mtls = _missing_tokens(mtls_text, REQUIRED_MTLS_ENV - {"REGISTRY_DB_MTLS_ENABLED"})
        if missing_mtls:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"DB mTLS required_when_enabled missing {sorted(missing_mtls)}",
                )
            )
        if "no plaintext fallback" not in " ".join(_walk_strings(mtls)).lower():
            violations.append(
                Violation(str(CONTRACT_PATH), "DB mTLS must require no plaintext fallback")
            )

    non_goals = set(cast("Iterable[str]", data.get("non_goals", [])))
    missing_non_goals = REQUIRED_NON_GOALS - non_goals
    if missing_non_goals:
        violations.append(
            Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing_non_goals)}")
        )

    docs_refs = set(cast("Iterable[str]", data.get("docs_refs", [])))
    missing_doc_refs = REQUIRED_DOC_REFS - docs_refs
    if missing_doc_refs:
        violations.append(
            Violation(str(CONTRACT_PATH), f"docs_refs missing {sorted(missing_doc_refs)}")
        )
    status_refs = set(cast("Iterable[str]", data.get("status_refs", [])))
    missing_status_refs = REQUIRED_STATUS_REFS - status_refs
    if missing_status_refs:
        violations.append(
            Violation(str(CONTRACT_PATH), f"status_refs missing {sorted(missing_status_refs)}")
        )

    for string in _walk_strings(data):
        for match in POSTGRES_URL_PATTERN.finditer(string):
            if not _is_placeholder_postgres_url(match.group(0)):
                violations.append(
                    Violation(str(CONTRACT_PATH), "contract contains a non-placeholder DSN")
                )
    return violations


def _validate_overlay(root: Path) -> list[Violation]:
    text = _read(root, OVERLAY_PATH)
    violations: list[Violation] = []
    if 'profiles: ["registry-remote-postgres"]' not in text:
        violations.append(Violation(str(OVERLAY_PATH), "overlay must profile-gate services"))
    if text.count("${REGISTRY_DATABASE_URL:?") < 4:
        violations.append(
            Violation(str(OVERLAY_PATH), "overlay must fail-loud require REGISTRY_DATABASE_URL")
        )
    missing_dsn = _missing_tokens(text, REQUIRED_DSN_ENV)
    if missing_dsn:
        violations.append(
            Violation(str(OVERLAY_PATH), f"overlay missing DSN env {sorted(missing_dsn)}")
        )
    missing_mtls = _missing_tokens(text, REQUIRED_MTLS_ENV)
    if missing_mtls:
        violations.append(
            Violation(str(OVERLAY_PATH), f"overlay missing DB mTLS env {sorted(missing_mtls)}")
        )
    for token in (
        "registry-state:",
        "registry-api:",
        'REGISTRY_STATE_AUTO_CREATE_SCHEMA: "0"',
        'REGISTRY_API_AUTO_CREATE_IDEMPOTENCY_SCHEMA: "0"',
        "verify-full",
        "/run/secrets/:/certs/db/",
    ):
        if token not in text:
            violations.append(
                Violation(str(OVERLAY_PATH), f"overlay missing required token {token}")
            )
    for _match in POSTGRES_URL_PATTERN.finditer(text):
        violations.append(Violation(str(OVERLAY_PATH), "overlay must not embed any Postgres DSN"))
    return violations


def _validate_default_preservation(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    root_compose = _read(root, ROOT_COMPOSE_PATH)
    env_example = _read(root, ENV_EXAMPLE_PATH)
    if "REGISTRY_DATABASE_URL" in root_compose:
        violations.append(
            Violation(str(ROOT_COMPOSE_PATH), "root compose must not set REGISTRY_DATABASE_URL")
        )
    if "sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3" not in root_compose:
        violations.append(Violation(str(ROOT_COMPOSE_PATH), "root compose SQLite default missing"))
    if "REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3" not in env_example:
        violations.append(
            Violation(str(ENV_EXAMPLE_PATH), "SQLite REGISTRY_DB_PATH default missing")
        )
    if "REGISTRY_DATABASE_URL=" not in env_example:
        violations.append(
            Violation(str(ENV_EXAMPLE_PATH), "REGISTRY_DATABASE_URL placeholder missing")
        )
    if re.search(r"(?m)^REGISTRY_DATABASE_URL=\S+", env_example):
        violations.append(
            Violation(str(ENV_EXAMPLE_PATH), "REGISTRY_DATABASE_URL must be blank in .env.example")
        )
    for token in REQUIRED_MTLS_ENV:
        if token not in env_example:
            violations.append(Violation(str(ENV_EXAMPLE_PATH), f".env.example missing {token}"))
    return violations


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    justfile = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    if CHECKER_COMMAND not in justfile:
        violations.append(Violation(str(JUSTFILE_PATH), "missing registry remote Postgres checker"))
    if CHECKER_SELF_TEST_COMMAND not in justfile:
        violations.append(
            Violation(str(JUSTFILE_PATH), "missing registry remote Postgres checker self-test")
        )
    if CHECKER_COMMAND not in ci:
        violations.append(Violation(str(CI_PATH), "CI missing registry remote Postgres checker"))
    if CHECKER_SELF_TEST_COMMAND not in ci:
        violations.append(
            Violation(str(CI_PATH), "CI missing registry remote Postgres checker self-test")
        )
    return violations


def _validate_docs_status(root: Path) -> list[Violation]:
    required_refs: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.3", CHECKER_COMMAND, str(OVERLAY_PATH)],
        PRODUCTION_OPS_PATH: [
            "Story 132.3",
            CHECKER_COMMAND,
            "registry remote Postgres deployment profile",
        ],
        FEATURE_STATUS_PATH: ["Story 132.3", str(CONTRACT_PATH), str(OVERLAY_PATH)],
        ARTIFACT_PATH: ["Story 132.3", CHECKER_COMMAND, str(OVERLAY_PATH)],
        SPRINT_STATUS_PATH: ["132-3-registry-remote-postgres-deployment-profile: done"],
    }
    violations: list[Violation] = []
    for relpath, tokens in required_refs.items():
        text = _read(root, relpath)
        for token in tokens:
            if token not in text:
                violations.append(Violation(str(relpath), f"missing required reference {token}"))
    sprint = _read(root, SPRINT_STATUS_PATH)
    if "132-4-worker-mcp-event-bus-split-profile:" not in sprint:
        violations.append(
            Violation(str(SPRINT_STATUS_PATH), "132.4 sprint status entry missing after 132.3")
        )
    return violations


def _validate_secret_hygiene(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in SECRET_SCAN_PATHS:
        text = _read(root, relpath)
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(text):
                violations.append(Violation(str(relpath), "secret-like value is forbidden"))
        for match in POSTGRES_URL_PATTERN.finditer(text):
            if not _is_placeholder_postgres_url(match.group(0)):
                violations.append(
                    Violation(str(relpath), "non-placeholder Postgres DSN is forbidden")
                )
    return violations


def _validate_overclaims(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in DOC_STATUS_PATHS:
        text = _read(root, relpath)
        for pattern in OVERCLAIM_PATTERNS:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 250) : min(len(text), match.end() + 250)]
                if not NEGATION_PATTERN.search(window):
                    violations.append(
                        Violation(
                            str(relpath),
                            "registry remote Postgres activation overclaim is forbidden",
                        )
                    )
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    required_files = (
        CONTRACT_PATH,
        OVERLAY_PATH,
        ROOT_COMPOSE_PATH,
        ENV_EXAMPLE_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    )
    violations = [
        Violation(str(relpath), "required file missing")
        for relpath in required_files
        if not (root / relpath).exists()
    ]
    if violations:
        return violations
    validators = (
        _validate_contract,
        _validate_overlay,
        _validate_default_preservation,
        _validate_wiring,
        _validate_docs_status,
        _validate_secret_hygiene,
        _validate_overclaims,
    )
    for validator in validators:
        violations.extend(validator(root))
    return violations


def _copy_live_fixture(tmp_path: Path) -> None:
    for relpath in (
        CONTRACT_PATH,
        OVERLAY_PATH,
        ROOT_COMPOSE_PATH,
        ENV_EXAMPLE_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    ):
        src = REPO_ROOT / relpath
        dst = tmp_path / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="registry-remote-postgres-profile-") as tmp:
        root = Path(tmp)
        _copy_live_fixture(root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1

        overlay = root / OVERLAY_PATH
        original_overlay = overlay.read_text(encoding="utf-8")
        overlay.write_text(
            original_overlay.replace("${REGISTRY_DATABASE_URL:?", "${OTHER_URL:?"), encoding="utf-8"
        )
        if not any("REGISTRY_DATABASE_URL" in item.message for item in validate(root)):
            print("self-test expected missing REGISTRY_DATABASE_URL violation", file=sys.stderr)
            return 1
        overlay.write_text(original_overlay, encoding="utf-8")

        env_example = root / ENV_EXAMPLE_PATH
        env_example.write_text(
            env_example.read_text(encoding="utf-8").replace(
                "REGISTRY_DATABASE_URL=",
                "REGISTRY_DATABASE_URL=postgresql+asyncpg://app:password@example.invalid/db",
            ),
            encoding="utf-8",
        )
        if not any(
            "REGISTRY_DATABASE_URL must be blank" in item.message for item in validate(root)
        ):
            print("self-test expected non-blank env placeholder violation", file=sys.stderr)
            return 1
        _copy_live_fixture(root)

        justfile = root / JUSTFILE_PATH
        justfile.write_text(
            justfile.read_text(encoding="utf-8").replace(
                CHECKER_COMMAND, "uv run python scripts/other.py"
            ),
            encoding="utf-8",
        )
        if not any("checker" in item.message for item in validate(root)):
            print("self-test expected checker wiring violation", file=sys.stderr)
            return 1
    print("registry remote Postgres profile readiness self-test passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run checker fixture self-tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("registry remote Postgres profile readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("registry remote Postgres profile readiness check passed")
        print(f"  contract: {CONTRACT_PATH}")
        print(f"  overlay: {OVERLAY_PATH}")
        print(f"  checker: {CHECKER_COMMAND}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
