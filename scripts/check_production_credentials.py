#!/usr/bin/env python3
"""Validate the Story 131.2 production credential readiness contract.

This is a static/readiness gate, not a secret loader. It verifies that the
repo's credential inventory records scope, env location, subprocess allowlist,
rotation, revocation, scanner coverage, and metadata-only ``secret.accessed``
behavior for provisioned production credentials. It also pins that broad tokens
remain absent from first-party subprocess allowlists.

Usage::

    uv run python scripts/check_production_credentials.py
    uv run python scripts/check_production_credentials.py --self-test
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
INVENTORY_PATH = Path("docs/production-credential-inventory.json")

WORKER_MCP_CLIENTS = Path("services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py")
ORCH_MCP_CLIENTS = Path(
    "services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py"
)
WORKER_CLAUDE_RUNNER = Path(
    "services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py"
)
WORKER_CODEX_RUNNER = Path("services/worker-wrapper/src/worker_wrapper/adapters/codex_runner.py")
WORKER_GEMINI_RUNNER = Path("services/worker-wrapper/src/worker_wrapper/adapters/gemini_runner.py")
ORCH_OMC_RUNNER = Path(
    "services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py"
)

FORBIDDEN_BROAD_ENV_VARS = frozenset(
    {
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "OPERATOR_HMAC_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    }
)
REQUIRED_OUTPUT_SURFACES = frozenset(
    {
        "unauthorized subprocesses",
        "logs",
        "events",
        "snapshots",
        "dashboard payloads",
        "artifacts",
    }
)
REQUIRED_CREDENTIAL_FIELDS = frozenset(
    {
        "id",
        "env_var",
        "status",
        "scope",
        "env_location",
        "authorized_subprocesses",
        "allowlist_sources",
        "server_required_env_source",
        "forbidden_broad_env_vars",
        "rotation",
        "revocation",
        "scanner_coverage",
        "audit_event",
        "docs_refs",
    }
)
REQUIRED_SCANNERS = frozenset(
    {
        "secret-hygiene-precommit",
        "scripts/check_no_secrets.py",
        "scripts/check_production_credentials.py",
    }
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class Violation:
    location: str
    message: str

    def render(self) -> str:
        return f"{self.location}: {self.message}"


def _literal_strings(node: ast.AST) -> set[str]:
    """Return every string literal contained in a simple literal expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values: set[str] = set()
        for elt in node.elts:
            values |= _literal_strings(elt)
        return values
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
    ):
        if not node.args:
            return set()
        return _literal_strings(node.args[0])
    return set()


def _literal_string_dict(node: ast.AST) -> dict[str, set[str]]:
    if not isinstance(node, ast.Dict):
        return {}
    out: dict[str, set[str]] = {}
    for key, value in zip(node.keys, node.values, strict=False):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out[key.value] = _literal_strings(value)
    return out


def _assignment_value(path: Path, name: str) -> ast.AST | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node.value
    return None


def _assignment_strings(root: Path, relpath: Path, name: str) -> set[str]:
    value = _assignment_value(root / relpath, name)
    if value is None:
        return set()
    return _literal_strings(value)


def _assignment_string_dict(root: Path, relpath: Path, name: str) -> dict[str, set[str]]:
    value = _assignment_value(root / relpath, name)
    if value is None:
        return {}
    return _literal_string_dict(value)


def _load_inventory(root: Path) -> dict[str, Any]:
    with (root / INVENTORY_PATH).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{INVENTORY_PATH} must be a JSON object")
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


def _validate_inventory_shape(data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(INVENTORY_PATH), "version must be 1"))
    if data.get("story") != "131.2":
        violations.append(Violation(str(INVENTORY_PATH), "story must be 131.2"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(INVENTORY_PATH), "production_activation must be deferred_fail_closed")
        )
    surfaces = set(data.get("forbidden_output_surfaces", []))
    missing_surfaces = REQUIRED_OUTPUT_SURFACES - surfaces
    if missing_surfaces:
        violations.append(
            Violation(
                str(INVENTORY_PATH),
                f"forbidden_output_surfaces missing {sorted(missing_surfaces)}",
            )
        )
    creds = data.get("credentials")
    if not isinstance(creds, list) or not creds:
        violations.append(Violation(str(INVENTORY_PATH), "credentials must be a non-empty list"))
        return violations
    for idx, cred in enumerate(creds):
        loc = f"{INVENTORY_PATH}:credentials[{idx}]"
        if not isinstance(cred, dict):
            violations.append(Violation(loc, "credential entry must be an object"))
            continue
        missing = REQUIRED_CREDENTIAL_FIELDS - set(cred)
        if missing:
            violations.append(Violation(loc, f"missing required fields {sorted(missing)}"))
        env_var = cred.get("env_var")
        if not isinstance(env_var, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", env_var):
            violations.append(
                Violation(loc, f"env_var must be an uppercase env name, got {env_var!r}")
            )
        if env_var in FORBIDDEN_BROAD_ENV_VARS:
            violations.append(Violation(loc, f"env_var {env_var} is a forbidden broad secret"))
        authorized = cred.get("authorized_subprocesses")
        if (
            not isinstance(authorized, list)
            or not authorized
            or not all(isinstance(x, str) for x in authorized)
        ):
            violations.append(
                Violation(loc, "authorized_subprocesses must be a non-empty string list")
            )
        for section in ("rotation", "revocation", "audit_event"):
            if not isinstance(cred.get(section), dict):
                violations.append(Violation(loc, f"{section} must be an object"))
        rotation = cred.get("rotation") if isinstance(cred.get("rotation"), dict) else {}
        if (
            not isinstance(rotation.get("max_age_days"), int)
            or rotation.get("max_age_days", 0) <= 0
        ):
            violations.append(Violation(loc, "rotation.max_age_days must be a positive integer"))
        if not rotation.get("procedure_ref") or not rotation.get("verification"):
            violations.append(Violation(loc, "rotation requires procedure_ref and verification"))
        if not isinstance(rotation.get("trigger_events"), list) or not rotation.get(
            "trigger_events"
        ):
            violations.append(Violation(loc, "rotation.trigger_events must be non-empty"))
        revocation = cred.get("revocation") if isinstance(cred.get("revocation"), dict) else {}
        if (
            not isinstance(revocation.get("max_revocation_minutes"), int)
            or revocation.get("max_revocation_minutes", 0) <= 0
        ):
            violations.append(
                Violation(loc, "revocation.max_revocation_minutes must be a positive integer")
            )
        for key in ("procedure_ref", "emergency_disable_ref", "verification"):
            if not revocation.get(key):
                violations.append(Violation(loc, f"revocation requires {key}"))
        scanner_text = "\n".join(str(x) for x in cred.get("scanner_coverage", []))
        missing_scanners = [s for s in REQUIRED_SCANNERS if s not in scanner_text]
        if missing_scanners:
            violations.append(Violation(loc, f"scanner_coverage missing {missing_scanners}"))
        audit = cred.get("audit_event") if isinstance(cred.get("audit_event"), dict) else {}
        if audit.get("type") != "secret.accessed":
            violations.append(Violation(loc, "audit_event.type must be secret.accessed"))
        behavior = str(audit.get("behavior", "")).lower()
        if "never" not in behavior or "credential values" not in behavior:
            violations.append(
                Violation(loc, "audit_event.behavior must forbid credential values in events")
            )
        forbidden = set(cred.get("forbidden_broad_env_vars", []))
        missing_forbidden = FORBIDDEN_BROAD_ENV_VARS - forbidden
        if missing_forbidden:
            violations.append(
                Violation(loc, f"forbidden_broad_env_vars missing {sorted(missing_forbidden)}")
            )
        docs_refs = cred.get("docs_refs")
        if not isinstance(docs_refs, list) or not docs_refs:
            violations.append(Violation(loc, "docs_refs must be non-empty"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(
                    str(INVENTORY_PATH), "inventory appears to contain a real credential value"
                )
            )
            break
    return violations


def _validate_repo_bindings(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    worker_allowlist = _assignment_strings(root, WORKER_MCP_CLIENTS, "_ENV_ALLOWLIST")
    orch_allowlist = _assignment_strings(root, ORCH_MCP_CLIENTS, "_ENV_ALLOWLIST")
    worker_server_env = _assignment_string_dict(root, WORKER_MCP_CLIENTS, "_SERVER_REQUIRED_ENV")
    child_allowlists = {
        "worker claude": _assignment_strings(root, WORKER_CLAUDE_RUNNER, "_CHILD_ENV_ALLOWLIST"),
        "worker codex": _assignment_strings(root, WORKER_CODEX_RUNNER, "_CODEX_ENV_ALLOWLIST"),
        "worker gemini": _assignment_strings(root, WORKER_GEMINI_RUNNER, "_GEMINI_ENV_ALLOWLIST"),
        "orchestrator omc": _assignment_strings(root, ORCH_OMC_RUNNER, "_CHILD_ENV_ALLOWLIST"),
    }
    for name, allowlist in {
        "worker mcp": worker_allowlist,
        "orchestrator mcp": orch_allowlist,
    }.items():
        leaked = FORBIDDEN_BROAD_ENV_VARS & allowlist
        if leaked:
            violations.append(
                Violation(
                    name, f"production broad secret env vars are MCP-allowlisted: {sorted(leaked)}"
                )
            )

    # Existing agent/runtime adapters may intentionally receive their own LLM
    # provider key. Story 131.2 pins the production/GitHub boundary: the broad
    # operator GitHub token and the scoped github-mcp token must not leak into
    # generic agent subprocesses.
    for name, allowlist in child_allowlists.items():
        leaked = {"GITHUB_TOKEN", "GITHUB_MCP_SCOPED_TOKEN"} & allowlist
        if leaked:
            violations.append(
                Violation(
                    name,
                    f"GitHub production credential env vars are agent-allowlisted: {sorted(leaked)}",
                )
            )
    for idx, cred in enumerate(data.get("credentials", [])):
        if not isinstance(cred, dict):
            continue
        loc = f"{INVENTORY_PATH}:credentials[{idx}]"
        env_var = cred.get("env_var")
        if not isinstance(env_var, str):
            continue
        if env_var not in worker_allowlist:
            violations.append(
                Violation(loc, f"{env_var} missing from worker-wrapper MCP allowlist")
            )
        if env_var not in orch_allowlist:
            violations.append(
                Violation(loc, f"{env_var} missing from orchestrator-adapter MCP allowlist")
            )
        authorized = set(cred.get("authorized_subprocesses", []))
        for server, server_vars in worker_server_env.items():
            if server in authorized:
                if env_var not in server_vars:
                    violations.append(
                        Violation(loc, f"{env_var} missing from _SERVER_REQUIRED_ENV[{server!r}]")
                    )
            elif env_var in server_vars:
                violations.append(
                    Violation(
                        loc, f"{env_var} unexpectedly reaches non-authorized server {server!r}"
                    )
                )
        for child_name, allowlist in child_allowlists.items():
            if env_var in allowlist:
                violations.append(
                    Violation(
                        loc, f"{env_var} unexpectedly reaches agent/runtime allowlist {child_name}"
                    )
                )
        for ref in cred.get("docs_refs", []):
            if not isinstance(ref, str):
                continue
            path_part = ref.split("#", 1)[0]
            if path_part and not (root / path_part).exists():
                violations.append(Violation(loc, f"docs_ref does not exist: {ref}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_inventory(root)
    return [*_validate_inventory_shape(data), *_validate_repo_bindings(root, data)]


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in [
            INVENTORY_PATH,
            WORKER_MCP_CLIENTS,
            ORCH_MCP_CLIENTS,
            WORKER_CLAUDE_RUNNER,
            WORKER_CODEX_RUNNER,
            WORKER_GEMINI_RUNNER,
            ORCH_OMC_RUNNER,
            Path("docs/operator-runbook.md"),
            Path("docs/production-operations.md"),
            Path("docs/feature-status.md"),
        ]:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
        allowlist_py = """\n_ENV_ALLOWLIST = frozenset({"GITHUB_MCP_ACTOR_KIND", "GITHUB_MCP_ACTOR_ID", "GITHUB_MCP_SCOPED_TOKEN"})\n_SERVER_REQUIRED_ENV = {"github": frozenset({"GITHUB_MCP_ACTOR_KIND", "GITHUB_MCP_ACTOR_ID", "GITHUB_MCP_SCOPED_TOKEN"}), "git": frozenset({"GIT_MCP_WORKTREE_ROOT"})}\n"""
        (root / WORKER_MCP_CLIENTS).write_text(allowlist_py, encoding="utf-8")
        (root / ORCH_MCP_CLIENTS).write_text(
            '_ENV_ALLOWLIST = frozenset({"GITHUB_MCP_ACTOR_KIND", "GITHUB_MCP_ACTOR_ID", "GITHUB_MCP_SCOPED_TOKEN"})\n',
            encoding="utf-8",
        )
        for rel, name in [
            (WORKER_CLAUDE_RUNNER, "_CHILD_ENV_ALLOWLIST"),
            (WORKER_CODEX_RUNNER, "_CODEX_ENV_ALLOWLIST"),
            (WORKER_GEMINI_RUNNER, "_GEMINI_ENV_ALLOWLIST"),
            (ORCH_OMC_RUNNER, "_CHILD_ENV_ALLOWLIST"),
        ]:
            (root / rel).write_text(f'{name} = frozenset({{"PATH"}})\n', encoding="utf-8")
        for rel in [
            Path("docs/operator-runbook.md"),
            Path("docs/production-operations.md"),
            Path("docs/feature-status.md"),
        ]:
            (root / rel).write_text("ok\n", encoding="utf-8")
        data = json.loads((REPO_ROOT / INVENTORY_PATH).read_text(encoding="utf-8"))
        (root / INVENTORY_PATH).write_text(json.dumps(data), encoding="utf-8")
        clean = validate(root)
        if clean:
            print("self-test clean fixture unexpectedly failed:", file=sys.stderr)
            for violation in clean:
                print(violation.render(), file=sys.stderr)
            return 1
        broken = json.loads((root / INVENTORY_PATH).read_text(encoding="utf-8"))
        broken["credentials"][0]["env_var"] = "GITHUB_TOKEN"
        (root / INVENTORY_PATH).write_text(json.dumps(broken), encoding="utf-8")
        bad = validate(root)
        if not any("forbidden broad secret" in v.message for v in bad):
            print("self-test broad-token fixture did not fail as expected", file=sys.stderr)
            return 1
    print("✓ check_production_credentials.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_production_credentials.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_production_credentials.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_inventory(REPO_ROOT)
        print(
            f"✓ production credential readiness OK ({len(data.get('credentials', []))} credential contract(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
