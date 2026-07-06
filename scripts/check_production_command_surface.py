#!/usr/bin/env python3
"""Validate the Story 131.5 production command-surface readiness contract.

This is a static/readiness gate. It distinguishes existing task lifecycle
approve/reject/stop/retry surfaces from future production-operation controls and
proves the latter remain absent/fail-closed until a separately approved live
implementation story.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = Path("docs/production-command-surface-readiness.json")
PRODUCTION_OPS_PATH = Path("docs/production-operations.md")
OPERATOR_RUNBOOK_PATH = Path("docs/operator-runbook.md")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
CREDENTIAL_CONTRACT_PATH = Path("docs/production-credential-inventory.json")
DEPLOYMENT_CONTRACT_PATH = Path("docs/deployment-change-readiness.json")
CONSOLE_MAIN_PATH = Path("services/console-cli/src/console_cli/app/main.py")
CONSOLE_COMMANDS_DIR = Path("services/console-cli/src/console_cli/commands")
TELEGRAM_LIFESPAN_PATH = Path("services/telegram-gateway/src/telegram_gateway/app/lifespan.py")
TELEGRAM_HANDLERS_DIR = Path("services/telegram-gateway/src/telegram_gateway/handlers")
DASHBOARD_STATIC_DIR = Path("dashboard/static")
REGISTRY_ROUTES_DIR = Path("services/registry-api/src/registry_api/routes")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/131-5-production-command-surface-readiness.md"
)
REQUIRED_EVIDENCE = frozenset(
    {
        "read-only production operation inspection model before any control",
        "approval actor and authority source captured before apply",
        "stop disable rollback controls require explicit operator approval",
        "credentials are never rendered in console Telegram dashboard events or artifacts",
        "audit records include operation id status trace id request id and actor metadata",
        "controls fail closed when preflight approval evidence is missing stale denied or expired",
        "dashboard controls are inert unless backed by approved operation state",
        "emergency disable and revocation path is documented before activation",
        "Story 131.2 credential readiness and Story 131.4 deployment readiness remain prerequisites",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "no production operation command modules are registered in console-cli",
        "no production operation Telegram handlers are registered",
        "dashboard static assets do not expose production operation approve stop disable controls",
        "registry API routes do not add production operation apply approve stop disable endpoints",
        "contract contains no credential or private-key values",
        "docs keep command surfaces deferred until a later approved implementation story",
    }
)
EXPECTED_CONSOLE_COMMANDS = frozenset(
    {
        "agent",
        "approve",
        "events",
        "key_status",
        "logs",
        "ping",
        "reject",
        "retry",
        "status",
        "stop",
        "task",
        "trace",
    }
)
EXPECTED_TELEGRAM_HANDLERS = frozenset(
    {
        "agent_command.py",
        "approvals_command.py",
        "approve_command.py",
        "key_status_command.py",
        "logs_command.py",
        "ping_command.py",
        "reject_command.py",
        "retry_command.py",
        "status_command.py",
        "stop_command.py",
        "task_command.py",
        "trace_command.py",
    }
)
DEFAULT_FORBIDDEN_RUNTIME_TOKENS = frozenset(
    {
        "production-operation",
        "production_operation",
        "prod-operation",
        "prod_operation",
        "prodops",
        "deploy-approve",
        "deploy-stop",
        "deploy-disable",
        "deployment-approve",
        "deployment-stop",
        "deployment-disable",
        "operation-approve",
        "operation-stop",
        "operation-disable",
    }
)
RUNTIME_SCAN_DIRS = (
    CONSOLE_COMMANDS_DIR,
    TELEGRAM_HANDLERS_DIR,
    DASHBOARD_STATIC_DIR,
    REGISTRY_ROUTES_DIR,
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
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
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must be a JSON object")
    return data


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


def _contains_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _registered_console_commands(root: Path) -> set[str]:
    tree = ast.parse(_read(root, CONSOLE_MAIN_PATH), filename=str(CONSOLE_MAIN_PATH))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Call) or _call_name(node.func.func) != "app.command":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Attribute):
            out.add(arg.attr)
        elif isinstance(arg, ast.Name):
            out.add(arg.id)
    return out


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("story") != "131.5":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 131.5"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    if data.get("mode") != "static_readiness_only":
        violations.append(Violation(str(CONTRACT_PATH), "mode must be static_readiness_only"))
    if data.get("operation_class") != "production_command_surface":
        violations.append(
            Violation(str(CONTRACT_PATH), "operation_class must be production_command_surface")
        )
    target = data.get("target_boundary") if isinstance(data.get("target_boundary"), dict) else {}
    expected_refs = {
        "operation_contract_ref": PRODUCTION_OPS_PATH,
        "credential_contract_ref": CREDENTIAL_CONTRACT_PATH,
        "deployment_contract_ref": DEPLOYMENT_CONTRACT_PATH,
    }
    for key, relpath in expected_refs.items():
        if target.get(key) != str(relpath):
            violations.append(Violation(str(CONTRACT_PATH), f"target_boundary {key} mismatch"))
        if not (root / relpath).exists():
            violations.append(
                Violation(str(relpath), "referenced prerequisite contract is missing")
            )
    evidence = set(data.get("required_evidence", []))
    missing_evidence = REQUIRED_EVIDENCE - evidence
    if missing_evidence:
        violations.append(
            Violation(str(CONTRACT_PATH), f"required_evidence missing {sorted(missing_evidence)}")
        )
    checks = set(data.get("fail_closed_checks", []))
    missing_checks = REQUIRED_FAIL_CLOSED_CHECKS - checks
    if missing_checks:
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail_closed_checks missing {sorted(missing_checks)}")
        )
    forbidden = set(data.get("forbidden_runtime_tokens", []))
    missing_forbidden = DEFAULT_FORBIDDEN_RUNTIME_TOKENS - forbidden
    if missing_forbidden:
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"forbidden_runtime_tokens missing {sorted(missing_forbidden)}"
            )
        )
    for ref in data.get("docs_refs", []):
        if not isinstance(ref, str):
            continue
        path_part = ref.split("#", 1)[0]
        if path_part and not (root / path_part).exists():
            violations.append(Violation(str(CONTRACT_PATH), f"docs_ref does not exist: {ref}"))
    non_goals = "\n".join(str(x) for x in data.get("non_goals", []))
    for phrase in (
        "no console production operation commands",
        "no Telegram production operation commands",
        "no dashboard production operation controls",
        "no registry API production operation mutation endpoints",
    ):
        if phrase not in non_goals:
            violations.append(Violation(str(CONTRACT_PATH), f"non_goals missing {phrase!r}"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract appears to contain a real credential value")
            )
            break
    return violations


def _validate_console_surface(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    registered = _registered_console_commands(root)
    if registered != EXPECTED_CONSOLE_COMMANDS:
        violations.append(
            Violation(
                str(CONSOLE_MAIN_PATH),
                f"registered console commands drifted: {sorted(registered)}",
            )
        )
    modules = {
        path.stem
        for path in (root / CONSOLE_COMMANDS_DIR).glob("*.py")
        if path.name != "__init__.py"
    }
    unexpected = modules - EXPECTED_CONSOLE_COMMANDS
    if unexpected:
        violations.append(
            Violation(
                str(CONSOLE_COMMANDS_DIR),
                f"unexpected console command modules {sorted(unexpected)}",
            )
        )
    return violations


def _validate_telegram_surface(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    handlers = {
        path.name
        for path in (root / TELEGRAM_HANDLERS_DIR).glob("*command.py")
        if not path.name.startswith("test_")
    }
    unexpected = handlers - EXPECTED_TELEGRAM_HANDLERS
    missing = EXPECTED_TELEGRAM_HANDLERS - handlers
    if unexpected:
        violations.append(
            Violation(
                str(TELEGRAM_HANDLERS_DIR),
                f"unexpected Telegram command handlers {sorted(unexpected)}",
            )
        )
    if missing:
        violations.append(
            Violation(
                str(TELEGRAM_HANDLERS_DIR), f"missing expected Telegram handlers {sorted(missing)}"
            )
        )
    lifespan = _read(root, TELEGRAM_LIFESPAN_PATH)
    for needle in ("make_approve_router", "make_stop_router", "make_reject_router"):
        if needle not in lifespan:
            violations.append(Violation(str(TELEGRAM_LIFESPAN_PATH), f"missing {needle!r}"))
    return violations


def _validate_forbidden_runtime_tokens(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    forbidden = {str(x) for x in data.get("forbidden_runtime_tokens", []) if isinstance(x, str)}
    for base in RUNTIME_SCAN_DIRS:
        abs_base = root / base
        if not abs_base.exists():
            continue
        for path in abs_base.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".js", ".html", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for token in forbidden:
                if token.lower() in lower:
                    violations.append(
                        Violation(str(path.relative_to(root)), f"forbidden runtime token {token!r}")
                    )
    return violations


def _validate_docs(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    prod = _read(root, PRODUCTION_OPS_PATH)
    operator = _read(root, OPERATOR_RUNBOOK_PATH)
    feature = _read(root, FEATURE_STATUS_PATH)
    artifact = _read(root, ARTIFACT_PATH)
    for needle in (
        "Story 131.5",
        "docs/production-command-surface-readiness.json",
        "scripts/check_production_command_surface.py",
        "command surfaces remain fail-closed/deferred",
    ):
        if needle not in prod:
            violations.append(Violation(str(PRODUCTION_OPS_PATH), f"missing {needle!r}"))
    for needle in (
        "Production command surface readiness (Story 131.5)",
        "uv run python scripts/check_production_command_surface.py",
        "no console production operation commands",
        "no dashboard production operation controls",
    ):
        if needle not in operator:
            violations.append(Violation(str(OPERATOR_RUNBOOK_PATH), f"missing {needle!r}"))
    for needle in ("Story 131.5", "Production command surface readiness"):
        if needle not in feature:
            violations.append(Violation(str(FEATURE_STATUS_PATH), f"missing {needle!r}"))
    for needle in (
        "Story 131.5",
        "scripts/check_production_command_surface.py",
        "static/readiness-only",
        "does not add console",
    ):
        if needle not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"missing {needle!r}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_json(root, CONTRACT_PATH)
    return [
        *_validate_contract(root, data),
        *_validate_console_surface(root),
        *_validate_telegram_surface(root),
        *_validate_forbidden_runtime_tokens(root, data),
        *_validate_docs(root),
    ]


def _copy_fixture(root: Path, relpaths: Sequence[Path]) -> None:
    for rel in relpaths:
        src = REPO_ROOT / rel
        dst = root / rel
        if src.is_dir():
            for child in src.rglob("*"):
                if child.is_dir() or "__pycache__" in child.parts:
                    continue
                if child.suffix not in {".py", ".js", ".html", ".json"}:
                    continue
                child_dst = root / child.relative_to(REPO_ROOT)
                child_dst.parent.mkdir(parents=True, exist_ok=True)
                child_dst.write_text(child.read_text(encoding="utf-8"), encoding="utf-8")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _self_test() -> int:
    relpaths = [
        CONTRACT_PATH,
        PRODUCTION_OPS_PATH,
        OPERATOR_RUNBOOK_PATH,
        FEATURE_STATUS_PATH,
        CREDENTIAL_CONTRACT_PATH,
        DEPLOYMENT_CONTRACT_PATH,
        CONSOLE_MAIN_PATH,
        CONSOLE_COMMANDS_DIR,
        TELEGRAM_LIFESPAN_PATH,
        TELEGRAM_HANDLERS_DIR,
        DASHBOARD_STATIC_DIR,
        REGISTRY_ROUTES_DIR,
        ARTIFACT_PATH,
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _copy_fixture(root, relpaths)
        clean = validate(root)
        if clean:
            print("self-test clean fixture unexpectedly failed:", file=sys.stderr)
            for violation in clean:
                print(violation.render(), file=sys.stderr)
            return 1
        bad_console = root / CONSOLE_COMMANDS_DIR / "prod_operation.py"
        bad_console.write_text(
            "def apply():\n    return 'production-operation'\n", encoding="utf-8"
        )
        bad = validate(root)
        if not any("unexpected console command modules" in v.message for v in bad):
            print("self-test console prod command fixture did not fail", file=sys.stderr)
            return 1
        if not any("forbidden runtime token" in v.message for v in bad):
            print("self-test forbidden token fixture did not fail", file=sys.stderr)
            return 1
    print("✓ check_production_command_surface.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_production_command_surface.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_production_command_surface.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_json(REPO_ROOT, CONTRACT_PATH)
        print(
            "✓ production command surface readiness OK "
            f"({len(data.get('allowed_existing_surfaces', []))} surface(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
