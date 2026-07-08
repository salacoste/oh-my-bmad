#!/usr/bin/env python3
"""Validate Story 132.5 operator/dashboard split-profile readiness."""

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
CONTRACT_PATH = Path("docs/operator-dashboard-split-readiness.json")
OVERLAY_PATH = Path("docker-compose.operator-dashboard-split.yml")
ROOT_COMPOSE_PATH = Path("docker-compose.yml")
DIGEST_COMPOSE_PATH = Path("docker-compose.digest.yml")
ENV_EXAMPLE_PATH = Path(".env.example")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/132-5-operator-dashboard-split-profile.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
DASHBOARD_STATIC_DIR = Path("dashboard/static")
TELEGRAM_SRC_DIR = Path("services/telegram-gateway/src/telegram_gateway")
CONSOLE_SRC_DIR = Path("services/console-cli/src/console_cli")
TELEGRAM_HEALTH_PATH = Path("services/telegram-gateway/src/telegram_gateway/app/webhook.py")
TELEGRAM_MAIN_PATH = Path("services/telegram-gateway/src/telegram_gateway/app/main.py")
TELEGRAM_MIDDLEWARE_PATH = Path("services/telegram-gateway/src/telegram_gateway/app/middleware.py")
TELEGRAM_INIT_PATH = Path("services/telegram-gateway/src/telegram_gateway/__init__.py")
CONSOLE_INIT_PATH = Path("services/console-cli/src/console_cli/__init__.py")
DASHBOARD_HEALTH_PATH = Path("dashboard/static/health-readiness.js")
DASHBOARD_CONTRACT_PATH = Path("dashboard/static/replay-lifecycle-contract.json")
PRD_PATH = Path("_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md")
PREREQ_1323_CHECKER = Path("scripts/check_registry_remote_postgres_profile.py")
PREREQ_1324_CHECKER = Path("scripts/check_worker_mcp_event_bus_split.py")
PREREQ_1323_CONTRACT = Path("docs/registry-remote-postgres-profile-readiness.json")
PREREQ_1324_CONTRACT = Path("docs/worker-mcp-event-bus-split-readiness.json")
PREREQ_1323_OVERLAY = Path("docker-compose.registry-remote-postgres.yml")
PREREQ_1324_OVERLAY = Path("docker-compose.worker-mcp-event-bus-split.yml")
CHECKER_COMMAND = "uv run python scripts/check_operator_dashboard_split.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"
PROFILE = "operator-dashboard-split"
AUTH_ENV = "OPERATOR_DASHBOARD_AUTH_TOKEN"

REQUIRED_SECTIONS = frozenset(
    {
        "profile_artifact",
        "default_preservation",
        "core_split_prerequisites",
        "telegram_ingress",
        "console_boundary",
        "dashboard_boundary",
        "auth_policy",
        "health_readiness",
        "trace_propagation",
        "version_compatibility",
        "network_boundary",
        "secret_hygiene",
        "readiness_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no live split deployment activation",
        "no live operator/dashboard production activation",
        "no runtime auth enforcement added",
        "no dashboard compose service",
        "no console-cli compose service",
        "no host port publishing",
        "no external operator host",
        "no external dashboard host",
        "no reverse-proxy or tunnel activation",
        "no production credentials or token values",
        "no browser payload secrets",
        "no log secret material",
        "no production host mutation",
        "no registry worker MCP or event-bus authority change",
        "no runtime production audit emitter",
    }
)
AC_DIMENSIONS = frozenset(
    {
        "ingress",
        "auth",
        "health/readiness",
        "trace propagation",
        "version compatibility",
        "secret hygiene",
    }
)
DOC_REFS = frozenset(
    {
        f"{OPERATOR_RUNBOOK_PATH}#operatordashboard-split-profile-story-1325",
        f"{PRODUCTION_OPS_PATH}#story-1325-operatordashboard-split-profile",
        f"{FEATURE_STATUS_PATH}#current-bmad-status",
        f"{ARTIFACT_PATH}#summary",
    }
)
REQUIRED_FILES = (
    CONTRACT_PATH,
    OVERLAY_PATH,
    ROOT_COMPOSE_PATH,
    DIGEST_COMPOSE_PATH,
    ENV_EXAMPLE_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
    JUSTFILE_PATH,
    CI_PATH,
    DASHBOARD_STATIC_DIR,
    TELEGRAM_SRC_DIR,
    CONSOLE_SRC_DIR,
    TELEGRAM_HEALTH_PATH,
    TELEGRAM_MAIN_PATH,
    TELEGRAM_MIDDLEWARE_PATH,
    TELEGRAM_INIT_PATH,
    CONSOLE_INIT_PATH,
    DASHBOARD_HEALTH_PATH,
    DASHBOARD_CONTRACT_PATH,
    PRD_PATH,
    PREREQ_1323_CHECKER,
    PREREQ_1324_CHECKER,
    PREREQ_1323_CONTRACT,
    PREREQ_1324_CONTRACT,
    PREREQ_1323_OVERLAY,
    PREREQ_1324_OVERLAY,
)
SECRET_SCAN_PATHS = (
    CONTRACT_PATH,
    OVERLAY_PATH,
    ENV_EXAMPLE_PATH,
    ARTIFACT_PATH,
    OPERATOR_RUNBOOK_PATH,
    PRODUCTION_OPS_PATH,
    FEATURE_STATUS_PATH,
    SPRINT_STATUS_PATH,
    DASHBOARD_STATIC_DIR,
    TELEGRAM_SRC_DIR,
    CONSOLE_SRC_DIR,
)
TEXT_SUFFIXES = frozenset({".py", ".js", ".html", ".json", ".md", ".yml", ".yaml", ".txt"})
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:operator/dashboard|operator[- ]surface|dashboard|Telegram|console)\b"
        r"[^\n.]{0,180}\b(?:live|activated|production[- ]ready|deployed|docker compose up)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|production[- ]ready|deployed|docker compose up)\b"
        r"[^\n.]{0,180}\b(?:operator/dashboard|operator[- ]surface|dashboard|Telegram|console)\b",
        re.I,
    ),
    re.compile(r"https?://(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?", re.I),
    re.compile(
        r"\b(?:cloudflared|ngrok|reverse proxy|tunnel)\b[^\n.]{0,120}\b(?:enabled|activated|deployed|configured)\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|is\s+not|"
    r"remains\s+deferred|future|readiness[- ]only|placeholder[- ]only)\b",
    re.I,
)
RUNTIME_AUTH_OVERCLAIM = re.compile(
    rf"{AUTH_ENV}[^\n.{{}}]{{0,160}}\b(?:enforces|authenticates|authorizes|protects|secures)\b|"
    rf"\b(?:enforces|authenticates|authorizes|protects|secures)\b[^\n.{{}}]{{0,160}}{AUTH_ENV}",
    re.I,
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


def _missing(text: str, tokens: Iterable[str]) -> set[str]:
    return {token for token in tokens if token not in text}


def _iter_text_files(root: Path, relpath: Path) -> Iterable[Path]:
    target = root / relpath
    if target.is_file():
        yield relpath
        return
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in {"__pycache__", ".mypy_cache"} for part in path.parts):
            continue
        yield path.relative_to(root)


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _walk_strings(item)


def _get(data: Mapping[str, Any], path: Sequence[str]) -> object:
    current: object = data
    for key in path:
        current = current.get(key) if isinstance(current, Mapping) else None
    return current


def _overlay_service_names(text: str) -> set[str]:
    """Return top-level service names from this small compose overlay.

    The checker intentionally avoids a YAML dependency. The overlay is a
    repository-owned static profile file, so a conservative indentation parser is
    enough for the service-scope gate: after the top-level ``services:`` mapping,
    collect two-space keys until the next top-level section.
    """
    services: set[str] = set()
    in_services = False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services and re.match(r"^[A-Za-z0-9_-]+:\s*$", line):
            break
        if not in_services:
            continue
        match = re.match(r"^  ([A-Za-z0-9_-]+):(?:\s+.*)?$", line)
        if match:
            services.add(match.group(1))
    return services


def _validate_contract(root: Path) -> list[Violation]:
    try:
        data = _load_json(root, CONTRACT_PATH)
    except Exception as exc:
        return [Violation(str(CONTRACT_PATH), f"invalid JSON contract: {exc}")]
    violations: list[Violation] = []
    if missing := REQUIRED_SECTIONS - set(data):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required sections missing {sorted(missing)}")
        )

    expected = {
        ("story",): "132.5",
        ("mode",): "opt_in_profile_fail_closed",
        ("production_activation",): "deferred_operator_evidence_required",
        ("profile_artifact", "compose_file"): str(OVERLAY_PATH),
        ("profile_artifact", "profile_name"): PROFILE,
        ("profile_artifact", "status"): "present_opt_in_only",
        ("auth_policy", "required_ingress_auth_env"): AUTH_ENV,
        ("auth_policy", "status"): "placeholder_only_no_runtime_enforcement_added",
        ("dashboard_boundary", "status"): "static_browser_assets_future_ingress_only",
        ("console_boundary", "status"): "host_side_cli_not_compose_service",
        ("readiness_checks", "checker_command"): CHECKER_COMMAND,
        ("readiness_checks", "self_test_command"): CHECKER_SELF_TEST_COMMAND,
    }
    for path, expected_value in expected.items():
        if (current := _get(data, path)) != expected_value:
            violations.append(
                Violation(
                    str(CONTRACT_PATH),
                    f"{'.'.join(path)} must be {expected_value!r}, found {current!r}",
                )
            )

    profile = data.get("profile_artifact", {})
    if isinstance(profile, Mapping):
        for flag in (
            "root_compose_default_unchanged",
            "no_external_ports",
            "no_live_host_mutation",
            "no_runtime_auth_enforcement_added",
        ):
            if profile.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"profile_artifact.{flag}=true required")
                )
        if set(cast("Iterable[str]", profile.get("compose_services", []))) != {
            "telegram-gateway",
            "clawhip-daemon",
        }:
            violations.append(
                Violation(str(CONTRACT_PATH), "compose_services must be telegram/clawhip only")
            )
        forbidden = set(cast("Iterable[str]", profile.get("forbidden_compose_services", [])))
        if {"console-cli", "dashboard"} - forbidden:
            violations.append(
                Violation(str(CONTRACT_PATH), "forbidden console/dashboard services required")
            )

    prereq = data.get("core_split_prerequisites", {})
    if isinstance(prereq, Mapping):
        required_checkers = {str(PREREQ_1323_CHECKER), str(PREREQ_1324_CHECKER)}
        required_contracts = {str(PREREQ_1323_CONTRACT), str(PREREQ_1324_CONTRACT)}
        required_overlays = {str(PREREQ_1323_OVERLAY), str(PREREQ_1324_OVERLAY)}
        checks = set(cast("Iterable[str]", prereq.get("required_checkers", [])))
        contracts = set(cast("Iterable[str]", prereq.get("required_contracts", [])))
        overlays = set(cast("Iterable[str]", prereq.get("required_overlays", [])))
        if (
            required_checkers - checks
            or required_contracts - contracts
            or required_overlays - overlays
        ):
            violations.append(
                Violation(str(CONTRACT_PATH), "132.3/132.4 prerequisite artifacts missing")
            )

    auth = data.get("auth_policy", {})
    if isinstance(auth, Mapping) and auth.get("runtime_auth_enforcement_added") is not False:
        violations.append(
            Violation(str(CONTRACT_PATH), "runtime_auth_enforcement_added=false required")
        )
    dashboard = data.get("dashboard_boundary", {})
    if isinstance(dashboard, Mapping):
        if dashboard.get("not_metrics_subscriber") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "dashboard must not use metrics-subscriber proof")
            )
        if dashboard.get("host_ports_published") is not False:
            violations.append(
                Violation(str(CONTRACT_PATH), "dashboard host_ports_published=false required")
            )
    network = data.get("network_boundary", {})
    if isinstance(network, Mapping):
        if network.get("host_ports_published") is not False:
            violations.append(
                Violation(str(CONTRACT_PATH), "network host_ports_published=false required")
            )
        if network.get("external_hostnames_committed") is not False:
            violations.append(
                Violation(str(CONTRACT_PATH), "external_hostnames_committed=false required")
            )

    readiness = " ".join(
        cast("Iterable[str]", data.get("readiness_checks", {}).get("validates", []))
        if isinstance(data.get("readiness_checks"), Mapping)
        else []
    )
    for token in AC_DIMENSIONS:
        if token not in readiness:
            violations.append(
                Violation(str(CONTRACT_PATH), f"readiness_checks.validates missing {token}")
            )
    missing_non_goals = REQUIRED_NON_GOALS - set(cast("Iterable[str]", data.get("non_goals", [])))
    if missing_non_goals:
        violations.append(
            Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing_non_goals)}")
        )
    if DOC_REFS - set(cast("Iterable[str]", data.get("docs_refs", []))):
        violations.append(Violation(str(CONTRACT_PATH), "docs_refs missing Story 132.5 refs"))
    if any(
        SECRET_PATTERNS[0].search(s) or SECRET_PATTERNS[1].search(s) for s in _walk_strings(data)
    ):
        violations.append(Violation(str(CONTRACT_PATH), "contract contains secret-like value"))
    return violations


def _validate_overlay(root: Path) -> list[Violation]:
    text = _read(root, OVERLAY_PATH)
    violations: list[Violation] = []
    service_names = _overlay_service_names(text)
    expected_services = {"telegram-gateway", "clawhip-daemon"}
    if service_names != expected_services:
        violations.append(
            Violation(
                str(OVERLAY_PATH),
                f"overlay services must be exactly {sorted(expected_services)}, found {sorted(service_names)}",
            )
        )
    for token in (
        f'profiles: ["{PROFILE}"]',
        f"${{{AUTH_ENV}:?",
        "telegram-gateway:",
        "clawhip-daemon:",
        "OPERATOR_DASHBOARD_INGRESS_MODE: internal-only-readiness",
    ):
        if token not in text:
            violations.append(Violation(str(OVERLAY_PATH), f"overlay missing {token}"))
    for forbidden in ("console-cli:", "dashboard:", "metrics-subscriber:"):
        if re.search(rf"(?m)^\s{{2}}{re.escape(forbidden)}\s*$", text):
            violations.append(Violation(str(OVERLAY_PATH), f"overlay must not target {forbidden}"))
    if re.search(r"(?m)^\s*ports\s*:", text):
        violations.append(Violation(str(OVERLAY_PATH), "overlay must not publish host ports"))
    return violations


def _validate_default_and_prereqs(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    root_compose = _read(root, ROOT_COMPOSE_PATH)
    if AUTH_ENV in root_compose:
        violations.append(
            Violation(str(ROOT_COMPOSE_PATH), f"root compose must not set {AUTH_ENV}")
        )
    digest = _read(root, DIGEST_COMPOSE_PATH)
    if (
        "console-cli — not compose services" not in digest
        and "console-cli is not a compose service" not in digest
    ):
        violations.append(
            Violation(str(DIGEST_COMPOSE_PATH), "console-cli non-compose evidence required")
        )
    sprint = _read(root, SPRINT_STATUS_PATH)
    for token in (
        "132-3-registry-remote-postgres-deployment-profile: done",
        "132-4-worker-mcp-event-bus-split-profile: done",
    ):
        if token not in sprint:
            violations.append(
                Violation(str(SPRINT_STATUS_PATH), f"prerequisite status missing {token}")
            )
    for relpath in (
        PREREQ_1323_CHECKER,
        PREREQ_1324_CHECKER,
        PREREQ_1323_CONTRACT,
        PREREQ_1324_CONTRACT,
        PREREQ_1323_OVERLAY,
        PREREQ_1324_OVERLAY,
    ):
        if not (root / relpath).exists():
            violations.append(Violation(str(relpath), "132.3/132.4 prerequisite file missing"))
    env = _read(root, ENV_EXAMPLE_PATH)
    for token in (
        "Operator/dashboard split profile (Story 132.5; opt-in)",
        f"#{AUTH_ENV}=",
        "runtime auth enforcement",
    ):
        if token not in env:
            violations.append(
                Violation(str(ENV_EXAMPLE_PATH), f"missing env example token {token}")
            )
    return violations


def _validate_surface_evidence(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    required_tokens = {
        TELEGRAM_HEALTH_PATH: ["health", "version"],
        TELEGRAM_MAIN_PATH: ["/v1/health", "health"],
        TELEGRAM_MIDDLEWARE_PATH: ["trace_id", "tg:"],
        TELEGRAM_INIT_PATH: ["__version__"],
        CONSOLE_INIT_PATH: ["__version__"],
        DASHBOARD_HEALTH_PATH: ["version", "/v1/health"],
        DASHBOARD_CONTRACT_PATH: ['"version"'],
        PRD_PATH: ["Story 132.5", "trace propagation", "version compatibility"],
    }
    for relpath, tokens in required_tokens.items():
        text = _read(root, relpath)
        for token in tokens:
            if token not in text:
                violations.append(Violation(str(relpath), f"surface evidence missing {token}"))
    return violations


def _validate_docs_wiring(root: Path) -> list[Violation]:
    required: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.5", CHECKER_COMMAND, str(OVERLAY_PATH)],
        PRODUCTION_OPS_PATH: ["Story 132.5", CHECKER_COMMAND, "operator/dashboard split profile"],
        FEATURE_STATUS_PATH: ["Story 132.5", str(CONTRACT_PATH), str(OVERLAY_PATH)],
        ARTIFACT_PATH: ["Story 132.5", CHECKER_COMMAND, str(OVERLAY_PATH)],
        SPRINT_STATUS_PATH: ["132-5-operator-dashboard-split-profile: done"],
        JUSTFILE_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
        CI_PATH: [CHECKER_COMMAND, CHECKER_SELF_TEST_COMMAND],
    }
    violations: list[Violation] = []
    for relpath, tokens in required.items():
        text = _read(root, relpath)
        for token in tokens:
            if token not in text:
                violations.append(Violation(str(relpath), f"missing required reference {token}"))
    sprint = _read(root, SPRINT_STATUS_PATH)
    for token in (
        "132-6-horizontal-scaling: done",
        "132-7-failure-load-backup-restore-validation: done",
        "132-8-closure-evidence: done",
    ):
        if token not in sprint:
            violations.append(
                Violation(str(SPRINT_STATUS_PATH), f"closure story status missing {token}")
            )
    return violations


def _validate_secrets_and_overclaims(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for scan_path in SECRET_SCAN_PATHS:
        for relpath in _iter_text_files(root, scan_path):
            text = _read(root, relpath)
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                violations.append(Violation(str(relpath), "secret-like value is forbidden"))
    story_paths = (
        CONTRACT_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
        OVERLAY_PATH,
    )
    for relpath in story_paths:
        text = _read(root, relpath)
        for match in RUNTIME_AUTH_OVERCLAIM.finditer(text):
            window = text[max(0, match.start() - 180) : min(len(text), match.end() + 180)]
            if NEGATION_PATTERN.search(window) is None:
                violations.append(
                    Violation(str(relpath), "runtime auth enforcement overclaim is forbidden")
                )
                break
        for pattern in OVERCLAIM_PATTERNS:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 220) : min(len(text), match.end() + 220)]
                if not any(
                    token in window for token in ("132.5", "132-5", PROFILE, "operator/dashboard")
                ):
                    continue
                if NEGATION_PATTERN.search(window) is None:
                    violations.append(
                        Violation(
                            str(relpath),
                            "operator/dashboard live activation or host overclaim is forbidden",
                        )
                    )
                    break
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    missing = [
        Violation(str(path), "required file missing")
        for path in REQUIRED_FILES
        if not (root / path).exists()
    ]
    if missing:
        return missing
    violations: list[Violation] = []
    for validator in (
        _validate_contract,
        _validate_overlay,
        _validate_default_and_prereqs,
        _validate_surface_evidence,
        _validate_docs_wiring,
        _validate_secrets_and_overclaims,
    ):
        violations.extend(validator(root))
    return violations


def _copy_live_fixture(dst_root: Path) -> None:
    paths = set(REQUIRED_FILES) | set(SECRET_SCAN_PATHS) | {ARTIFACT_PATH}
    for relpath in paths:
        src = REPO_ROOT / relpath
        dst = dst_root / relpath
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="operator-dashboard-split-") as tmp:
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
            original_overlay.replace(f"${{{AUTH_ENV}:?", f"${{{AUTH_ENV}:-"), encoding="utf-8"
        )
        if not any(AUTH_ENV in v.message for v in validate(root)):
            print("self-test failed to detect optional auth placeholder", file=sys.stderr)
            return 1
        overlay.write_text(original_overlay + "\n    ports:\n      - 8080:8080\n", encoding="utf-8")
        if not any("host ports" in v.message for v in validate(root)):
            print("self-test failed to detect forbidden host ports", file=sys.stderr)
            return 1
        overlay.write_text(
            original_overlay + '\n  console-cli:\n    profiles: ["operator-dashboard-split"]\n',
            encoding="utf-8",
        )
        if not any("console-cli" in v.message for v in validate(root)):
            print("self-test failed to detect console-cli compose target", file=sys.stderr)
            return 1
        overlay.write_text(
            original_overlay + '\n  registry-api:\n    profiles: ["operator-dashboard-split"]\n',
            encoding="utf-8",
        )
        if not any("overlay services must be exactly" in v.message for v in validate(root)):
            print("self-test failed to detect arbitrary extra overlay service", file=sys.stderr)
            return 1
        overlay.write_text(original_overlay + "\n  registry-api: {}\n", encoding="utf-8")
        if not any("overlay services must be exactly" in v.message for v in validate(root)):
            print("self-test failed to detect inline extra overlay service", file=sys.stderr)
            return 1
        overlay.write_text(original_overlay, encoding="utf-8")

        contract = _load_json(root, CONTRACT_PATH)
        contract["dashboard_boundary"]["status"] = "metrics_subscriber_live_dashboard"
        (root / CONTRACT_PATH).write_text(json.dumps(contract, indent=2), encoding="utf-8")
        if not any("dashboard_boundary.status" in v.message for v in validate(root)):
            print("self-test failed to detect bad dashboard boundary", file=sys.stderr)
            return 1
        contract = _load_json(root, CONTRACT_PATH)
        contract["auth_policy"]["runtime_auth_enforcement_added"] = True
        (root / CONTRACT_PATH).write_text(json.dumps(contract, indent=2), encoding="utf-8")
        if not any("runtime_auth_enforcement_added" in v.message for v in validate(root)):
            print("self-test failed to detect runtime auth overclaim", file=sys.stderr)
            return 1
        contract = _load_json(root, CONTRACT_PATH)
        contract["core_split_prerequisites"]["required_checkers"] = []
        (root / CONTRACT_PATH).write_text(json.dumps(contract, indent=2), encoding="utf-8")
        if not any("prerequisite" in v.message for v in validate(root)):
            print("self-test failed to detect missing prerequisites", file=sys.stderr)
            return 1

        static_file = root / DASHBOARD_STATIC_DIR / "story-132-5-secret-fixture.js"
        static_file.write_text(
            "const token = '" + "ghp_" + "abcdefghijklmnopqrstuvwx" + "';\n", encoding="utf-8"
        )
        if not any("secret-like" in v.message for v in validate(root)):
            print("self-test failed to detect dashboard static secret", file=sys.stderr)
            return 1
        static_file.unlink()

        runbook = root / OPERATOR_RUNBOOK_PATH
        original_runbook = runbook.read_text(encoding="utf-8")
        runbook.write_text(
            original_runbook + "\npassword=abcdefghijklmnopqrstuvwx123456\n",
            encoding="utf-8",
        )
        if not any("secret-like" in v.message for v in validate(root)):
            print("self-test failed to detect docs secret", file=sys.stderr)
            return 1
        runbook.write_text(original_runbook, encoding="utf-8")

        artifact = root / ARTIFACT_PATH
        original_artifact = artifact.read_text(encoding="utf-8")
        artifact.write_text(
            original_artifact
            + "\nStory 132.5 operator/dashboard split is production-ready and deployed.\n",
            encoding="utf-8",
        )
        if not any("overclaim" in v.message for v in validate(root)):
            print("self-test failed to detect production-ready overclaim", file=sys.stderr)
            return 1
    print("operator/dashboard split readiness self-test passed")
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
        print("operator/dashboard split readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("operator/dashboard split readiness check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
