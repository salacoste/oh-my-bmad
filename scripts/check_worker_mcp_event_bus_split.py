#!/usr/bin/env python3
"""Validate Story 132.4 worker/MCP/event-bus split-profile readiness."""

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
CONTRACT_PATH = Path("docs/worker-mcp-event-bus-split-readiness.json")
OVERLAY_PATH = Path("docker-compose.worker-mcp-event-bus-split.yml")
ROOT_COMPOSE_PATH = Path("docker-compose.yml")
ENV_EXAMPLE_PATH = Path(".env.example")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/132-4-worker-mcp-event-bus-split-profile.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_worker_mcp_event_bus_split.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"
PROFILE = "worker-mcp-event-bus-split"

MCP_URL_ENV = frozenset(
    {
        "TASK_REGISTRY_URL",
        "SESSION_REGISTRY_URL",
        "GIT_URL",
        "GITHUB_URL",
        "VERIFICATION_URL",
        "MEMORY_URL",
        "ARTIFACT_URL",
        "BROWSER_URL",
        "CLAWHIP_BRIDGE_URL",
    }
)
MCP_SERVICES = frozenset(
    {
        "task-registry-mcp",
        "session-registry-mcp",
        "git-mcp",
        "github-mcp",
        "verification-mcp",
        "memory-mcp",
        "artifact-mcp",
        "browser-mcp",
        "clawhip-bridge-mcp",
    }
)
REQUIRED_SECTIONS = frozenset(
    {
        "profile_artifact",
        "default_preservation",
        "spawner_remote_mcp_wiring",
        "mcp_server_profile",
        "event_bus_boundary",
        "security_policy_composition",
        "readiness_checks",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_NON_GOALS = frozenset(
    {
        "no live split deployment activation",
        "no external worker host",
        "no external MCP host",
        "no external event-bus broker",
        "no host port publishing",
        "no production credentials or token values",
        "no production host mutation",
        "no registry authority change",
        "no runtime production audit emitter",
    }
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)\b(?:jwt_secret_key|mcp_auth_token|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{32,}"
    ),
)
OVERCLAIM_PATTERNS = (
    re.compile(
        r"\b(?:worker|MCP|event[- ]bus|split deployment)\b[^\n.]{0,160}\b(?:live|activated|production[- ]ready|deployed)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|activated|production[- ]ready|deployed)\b[^\n.]{0,160}\b(?:worker|MCP|event[- ]bus|split deployment)\b",
        re.I,
    ),
)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deferred|fail[- ]closed|does\s+not|do\s+not|is\s+not|remains\s+deferred)\b",
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
        ("story",): "132.4",
        ("mode",): "opt_in_profile_fail_closed",
        ("production_activation",): "deferred_operator_evidence_required",
        ("profile_artifact", "compose_file"): str(OVERLAY_PATH),
        ("profile_artifact", "profile_name"): PROFILE,
        ("readiness_checks", "checker_command"): CHECKER_COMMAND,
        ("readiness_checks", "self_test_command"): CHECKER_SELF_TEST_COMMAND,
        ("security_policy_composition", "required_auth_env"): None,
    }
    expected.pop(("security_policy_composition", "required_auth_env"))
    for path, expected_value in expected.items():
        current: object = data
        for key in path:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current != expected_value:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{'.'.join(path)} must be {expected_value!r}")
            )
    profile = data.get("profile_artifact", {})
    if isinstance(profile, Mapping):
        for flag in (
            "root_compose_default_unchanged",
            "no_external_ports",
            "no_external_broker",
            "no_live_host_mutation",
        ):
            if profile.get(flag) is not True:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"profile_artifact.{flag}=true required")
                )
    wiring = data.get("spawner_remote_mcp_wiring", {})
    if isinstance(wiring, Mapping):
        if wiring.get("required_auth_env") != "MCP_AUTH_TOKEN":
            violations.append(Violation(str(CONTRACT_PATH), "MCP_AUTH_TOKEN required for spawners"))
        missing_urls = MCP_URL_ENV - set(cast("Iterable[str]", wiring.get("required_urls", [])))
        if missing_urls:
            violations.append(
                Violation(str(CONTRACT_PATH), f"required_urls missing {sorted(missing_urls)}")
            )
        if wiring.get("no_stdio_fallback_when_profile_is_active") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "profile must not silently fall back to stdio")
            )
    servers = data.get("mcp_server_profile", {})
    if isinstance(servers, Mapping):
        if servers.get("required_server_auth_env") != "JWT_SECRET_KEY":
            violations.append(
                Violation(str(CONTRACT_PATH), "JWT_SECRET_KEY required for MCP servers")
            )
        missing_services = MCP_SERVICES - set(cast("Iterable[str]", servers.get("services", [])))
        if missing_services:
            violations.append(
                Violation(str(CONTRACT_PATH), f"MCP services missing {sorted(missing_services)}")
            )
        if servers.get("host_ports_published") is not False:
            violations.append(Violation(str(CONTRACT_PATH), "host_ports_published=false required"))
    event_bus = data.get("event_bus_boundary", {})
    if isinstance(event_bus, Mapping):
        for key, value in {
            "writer_service": "clawhip-bridge-mcp",
            "writer_volume_mode": "rw",
        }.items():
            if event_bus.get(key) != value:
                violations.append(
                    Violation(str(CONTRACT_PATH), f"event_bus_boundary.{key} must be {value}")
                )
        if event_bus.get("no_external_event_bus_broker") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), "no_external_event_bus_broker=true required")
            )
    missing_non_goals = REQUIRED_NON_GOALS - set(cast("Iterable[str]", data.get("non_goals", [])))
    if missing_non_goals:
        violations.append(
            Violation(str(CONTRACT_PATH), f"non_goals missing {sorted(missing_non_goals)}")
        )
    return violations


def _validate_overlay(root: Path) -> list[Violation]:
    text = _read(root, OVERLAY_PATH)
    violations: list[Violation] = []
    if f'profiles: ["{PROFILE}"]' not in text:
        violations.append(
            Violation(str(OVERLAY_PATH), "overlay must use worker-mcp-event-bus-split profile")
        )
    for token in (
        "${MCP_AUTH_TOKEN:?",
        "${JWT_SECRET_KEY:?",
        "orchestrator-adapter:",
        "worker-wrapper:",
        "clawhip-bridge-mcp:",
        "oh-my-bmad-data:/var/lib/oh-my-bmad",
    ):
        if token not in text:
            violations.append(Violation(str(OVERLAY_PATH), f"overlay missing {token}"))
    missing_urls = _missing(text, MCP_URL_ENV)
    if missing_urls:
        violations.append(
            Violation(str(OVERLAY_PATH), f"overlay missing URL env {sorted(missing_urls)}")
        )
    missing_services = _missing(text, {f"{service}:" for service in MCP_SERVICES})
    if missing_services:
        violations.append(
            Violation(str(OVERLAY_PATH), f"overlay missing MCP services {sorted(missing_services)}")
        )
    for service in MCP_SERVICES:
        expected_host = f"http://omb-{service}:"
        if service != "clawhip-bridge-mcp" and service not in {
            "task-registry-mcp",
            "session-registry-mcp",
        }:
            pass
        if (
            service
            in {
                "task-registry-mcp",
                "session-registry-mcp",
                "git-mcp",
                "github-mcp",
                "verification-mcp",
                "memory-mcp",
                "artifact-mcp",
                "browser-mcp",
                "clawhip-bridge-mcp",
            }
            and expected_host not in text
        ):
            violations.append(Violation(str(OVERLAY_PATH), f"missing internal URL for {service}"))
    if re.search(r"(?m)^\s*ports\s*:", text):
        violations.append(Violation(str(OVERLAY_PATH), "overlay must not publish host ports"))
    return violations


def _validate_default_preservation(root: Path) -> list[Violation]:
    root_compose = _read(root, ROOT_COMPOSE_PATH)
    env_example = _read(root, ENV_EXAMPLE_PATH)
    violations: list[Violation] = []
    for token in (
        "TASK_REGISTRY_URL: ${TASK_REGISTRY_URL:-}",
        "MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:-}",
        'profiles: ["remote-mcp"]',
    ):
        if token not in root_compose:
            violations.append(
                Violation(str(ROOT_COMPOSE_PATH), f"root default token missing {token}")
            )
    for token in (
        "#MCP_AUTH_TOKEN=",
        "#JWT_SECRET_KEY=",
        "#TASK_REGISTRY_URL=http://omb-task-registry-mcp:8081/mcp",
    ):
        if token not in env_example:
            violations.append(
                Violation(str(ENV_EXAMPLE_PATH), f"remote MCP example token missing {token}")
            )
    return violations


def _validate_docs_wiring(root: Path) -> list[Violation]:
    required: dict[Path, Sequence[str]] = {
        OPERATOR_RUNBOOK_PATH: ["Story 132.4", CHECKER_COMMAND, str(OVERLAY_PATH)],
        PRODUCTION_OPS_PATH: ["Story 132.4", CHECKER_COMMAND, "worker/MCP/event-bus split profile"],
        FEATURE_STATUS_PATH: ["Story 132.4", str(CONTRACT_PATH), str(OVERLAY_PATH)],
        ARTIFACT_PATH: ["Story 132.4", CHECKER_COMMAND, str(OVERLAY_PATH)],
        SPRINT_STATUS_PATH: ["132-4-worker-mcp-event-bus-split-profile: done"],
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
    if not any(
        token in sprint
        for token in (
            "132-5-operator-dashboard-split-profile: backlog",
            "132-5-operator-dashboard-split-profile: done",
        )
    ):
        violations.append(
            Violation(str(SPRINT_STATUS_PATH), "132.5 must remain tracked after 132.4")
        )
    return violations


def _validate_secrets_and_overclaims(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relpath in (CONTRACT_PATH, OVERLAY_PATH, ARTIFACT_PATH, ENV_EXAMPLE_PATH):
        text = _read(root, relpath)
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            violations.append(Violation(str(relpath), "secret-like value is forbidden"))
    for relpath in (
        CONTRACT_PATH,
        OPERATOR_RUNBOOK_PATH,
        PRODUCTION_OPS_PATH,
        FEATURE_STATUS_PATH,
        SPRINT_STATUS_PATH,
        ARTIFACT_PATH,
    ):
        text = _read(root, relpath)
        for pattern in OVERCLAIM_PATTERNS:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 220) : min(len(text), match.end() + 220)]
                if not any(
                    token in window for token in ("132.4", "132-4", PROFILE, "worker/MCP/event-bus")
                ):
                    continue
                if NEGATION_PATTERN.search(window) is None:
                    violations.append(
                        Violation(
                            str(relpath),
                            "worker/MCP/event-bus split activation overclaim is forbidden",
                        )
                    )
                    break
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
    missing_files = [
        Violation(str(path), "required file missing")
        for path in required_files
        if not (root / path).exists()
    ]
    if missing_files:
        return missing_files
    violations: list[Violation] = []
    for validator in (
        _validate_contract,
        _validate_overlay,
        _validate_default_preservation,
        _validate_docs_wiring,
        _validate_secrets_and_overclaims,
    ):
        violations.extend(validator(root))
    return violations


def _copy_live_fixture(dst_root: Path) -> None:
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
        dst = dst_root / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="worker-mcp-event-bus-split-") as tmp:
        root = Path(tmp)
        _copy_live_fixture(root)
        clean = validate(root)
        if clean:
            print("self-test clean fixture failed:", file=sys.stderr)
            for violation in clean:
                print(f"  - {violation.render()}", file=sys.stderr)
            return 1
        overlay = root / OVERLAY_PATH
        original = overlay.read_text(encoding="utf-8")
        overlay.write_text(
            original.replace("${MCP_AUTH_TOKEN:?", "${OPTIONAL_TOKEN:-"), encoding="utf-8"
        )
        if not any("MCP_AUTH_TOKEN" in v.message for v in validate(root)):
            print("self-test failed to detect missing MCP_AUTH_TOKEN requirement", file=sys.stderr)
            return 1
        overlay.write_text(original, encoding="utf-8")
        contract = _load_json(root, CONTRACT_PATH)
        contract["non_goals"] = []
        (root / CONTRACT_PATH).write_text(json.dumps(contract, indent=2), encoding="utf-8")
        if not any("non_goals" in v.message for v in validate(root)):
            print("self-test failed to detect missing non-goals", file=sys.stderr)
            return 1
    print("worker/MCP/event-bus split readiness self-test passed")
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
        print("worker/MCP/event-bus split readiness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        print("worker/MCP/event-bus split readiness check passed")
        print(f"  contract: {CONTRACT_PATH}")
        print(f"  overlay: {OVERLAY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
