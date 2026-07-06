#!/usr/bin/env python3
"""Validate the Story 131.3 GitHub write activation readiness contract.

This gate is intentionally static/readiness-only. It proves the current repo still
fails closed for real GitHub writes while documenting and checking the code seams
that a future approved activation story must satisfy. It must not read secrets,
call GitHub, or enable production writes.

Usage::

    uv run python scripts/check_github_write_activation.py
    uv run python scripts/check_github_write_activation.py --self-test
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
CONTRACT_PATH = Path("docs/github-write-activation-readiness.json")
CREDENTIAL_CONTRACT_PATH = Path("docs/production-credential-inventory.json")
GITHUB_REST_PATH = Path("mcp-servers/github/src/github_mcp/adapters/github_rest.py")
GITHUB_SERVER_PATH = Path("mcp-servers/github/src/github_mcp/server.py")
GITHUB_TOOLS_PATH = Path("mcp-servers/github/src/github_mcp/handlers/tools.py")
RUNTIME_SCAN_PATHS = (
    Path("mcp-servers/github/src/github_mcp"),
    Path("services/worker-wrapper/src/worker_wrapper"),
    Path("services/orchestrator-adapter/src/orchestrator_adapter"),
)
ACTIVATION_FLAG = "GITHUB_MCP_WRITE_ENABLED"
REQUIRED_EVIDENCE = frozenset(
    {
        "scoped credential readiness from Story 131.2",
        "repo owner approval for exactly one repository",
        "security/operator approval at L3 GitHub write activation",
        "simulation parity for every write tool",
        "real-write smoke test plan and cleanup/rollback target",
        "rate-limit and transient failure handling evidence",
        "metadata-only audit event evidence",
        "emergency disable or revoke path",
        "out-of-scope owner/repo fail-closed evidence",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "GitHubWriteClient simulate default remains true",
        "build_server does not pass simulate=false",
        "runtime code does not read GITHUB_MCP_WRITE_ENABLED",
        "all write tools are Tier.THREE",
        "all write handlers call validate_caller_trace_id before check_tier_with_approval",
        "all write handlers bind approval_lookup=approval_lookup",
        "all write handlers validate owner/repo before invoking the write client",
        "rate-limit handling retries 429 responses without leaking tokens",
        "write results and audit descriptors never include credential values",
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


def _load_json(root: Path, relpath: Path) -> dict[str, Any]:
    with (root / relpath).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{relpath} must be a JSON object")
    return data


def _parse(root: Path, relpath: Path) -> ast.Module:
    return ast.parse((root / relpath).read_text(encoding="utf-8"), filename=str(relpath))


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


def _calls_named(fn: ast.AST, name: str) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _call_name(node.func) == name:
            calls.append(node)
    return calls


def _call_positions(fn: ast.AST, name: str) -> list[int]:
    return [call.lineno for call in _calls_named(fn, name)]


def _string_constants(fn: ast.AST) -> set[str]:
    return {
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _find_function(
    tree_or_node: ast.AST, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree_or_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _tier_map(tree: ast.Module) -> dict[str, str]:
    for node in tree.body:
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TIER_MAP" for t in node.targets)
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "TIER_MAP"
            )
        ):
            value = node.value
        if value is None:
            continue
        if not isinstance(value, ast.Dict):
            return {}
        out: dict[str, str] = {}
        for key, item in zip(value.keys, value.values, strict=False):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if isinstance(item, ast.Attribute):
                    out[key.value] = item.attr
                elif isinstance(item, ast.Constant):
                    out[key.value] = str(item.value)
        return out
    return {}


def _tool_decorator_name(fn: ast.AsyncFunctionDef) -> str | None:
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        if _call_name(deco.func) != "mcp.tool":
            continue
        for keyword in deco.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return None


def _keyword_is_name(call: ast.Call, keyword: str, expected_name: str) -> bool:
    for kw in call.keywords:
        if kw.arg == keyword and isinstance(kw.value, ast.Name) and kw.value.id == expected_name:
            return True
    return False


def _call_has_tier_map_key(call: ast.Call, tool: str) -> bool:
    for arg in call.args:
        if (
            isinstance(arg, ast.Subscript)
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "TIER_MAP"
            and isinstance(arg.slice, ast.Constant)
            and arg.slice.value == tool
        ):
            return True
    return False


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if data.get("version") != 1:
        violations.append(Violation(str(CONTRACT_PATH), "version must be 1"))
    if data.get("story") != "131.3":
        violations.append(Violation(str(CONTRACT_PATH), "story must be 131.3"))
    if data.get("production_activation") != "deferred_fail_closed":
        violations.append(
            Violation(str(CONTRACT_PATH), "production_activation must be deferred_fail_closed")
        )
    if data.get("mode") != "static_readiness_only":
        violations.append(Violation(str(CONTRACT_PATH), "mode must be static_readiness_only"))
    flag = data.get("activation_flag") if isinstance(data.get("activation_flag"), dict) else {}
    if flag.get("name") != ACTIVATION_FLAG:
        violations.append(
            Violation(str(CONTRACT_PATH), f"activation_flag.name must be {ACTIVATION_FLAG}")
        )
    if flag.get("runtime_read_allowed") is not False:
        violations.append(
            Violation(str(CONTRACT_PATH), "activation flag runtime_read_allowed must be false")
        )
    target = data.get("target_boundary") if isinstance(data.get("target_boundary"), dict) else {}
    if target.get("credential_contract_ref") != str(CREDENTIAL_CONTRACT_PATH):
        violations.append(
            Violation(
                str(CONTRACT_PATH), "target_boundary must reference Story 131.2 credential contract"
            )
        )
    if not (root / CREDENTIAL_CONTRACT_PATH).exists():
        violations.append(
            Violation(str(CREDENTIAL_CONTRACT_PATH), "credential readiness contract is missing")
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
    tools = data.get("write_tools")
    if not isinstance(tools, list) or not tools:
        violations.append(Violation(str(CONTRACT_PATH), "write_tools must be a non-empty list"))
    else:
        seen: set[str] = set()
        for idx, entry in enumerate(tools):
            loc = f"{CONTRACT_PATH}:write_tools[{idx}]"
            if not isinstance(entry, dict):
                violations.append(Violation(loc, "write tool entry must be an object"))
                continue
            for key in ("tool", "handler", "event", "tier", "requires_approval_lookup"):
                if key not in entry:
                    violations.append(Violation(loc, f"missing {key}"))
            tool = entry.get("tool")
            if isinstance(tool, str):
                seen.add(tool)
            if entry.get("tier") != "THREE":
                violations.append(Violation(loc, "tier must be THREE"))
            if entry.get("requires_approval_lookup") is not True:
                violations.append(Violation(loc, "requires_approval_lookup must be true"))
        if len(seen) != len(tools):
            violations.append(
                Violation(str(CONTRACT_PATH), "write_tools must not contain duplicate tool ids")
            )
    for ref in data.get("docs_refs", []):
        if not isinstance(ref, str):
            continue
        path_part = ref.split("#", 1)[0]
        if path_part and not (root / path_part).exists():
            violations.append(Violation(str(CONTRACT_PATH), f"docs_ref does not exist: {ref}"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract appears to contain a real credential value")
            )
            break
    return violations


def _validate_write_client(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    rest_tree = _parse(root, GITHUB_REST_PATH)
    cls = _find_class(rest_tree, "GitHubWriteClient")
    if cls is None:
        return [Violation(str(GITHUB_REST_PATH), "GitHubWriteClient class not found")]
    init = _find_function(cls, "__init__")
    if init is None:
        violations.append(Violation(str(GITHUB_REST_PATH), "GitHubWriteClient.__init__ not found"))
    else:
        args = init.args
        kw_names = [arg.arg for arg in args.kwonlyargs]
        if "simulate" not in kw_names:
            violations.append(Violation(str(GITHUB_REST_PATH), "simulate kw-only arg is missing"))
        else:
            idx = kw_names.index("simulate")
            default = args.kw_defaults[idx]
            if not (isinstance(default, ast.Constant) and default.value is True):
                violations.append(
                    Violation(
                        str(GITHUB_REST_PATH), "GitHubWriteClient simulate default must stay True"
                    )
                )
    write_fn = _find_function(cls, "_write")
    if write_fn is None:
        violations.append(Violation(str(GITHUB_REST_PATH), "GitHubWriteClient._write not found"))
    else:
        text = (
            ast.get_source_segment((root / GITHUB_REST_PATH).read_text(encoding="utf-8"), write_fn)
            or ""
        )
        if "self._simulate" not in text:
            violations.append(
                Violation(str(GITHUB_REST_PATH), "_write must branch on self._simulate")
            )
        if "self._request" not in text:
            violations.append(
                Violation(str(GITHUB_REST_PATH), "_write must keep the real HTTP seam explicit")
            )
    rest_text = (root / GITHUB_REST_PATH).read_text(encoding="utf-8")
    for needle in ("resp.status == 429", "github_rate_limited", "_make_retry"):
        if needle not in rest_text:
            violations.append(
                Violation(str(GITHUB_REST_PATH), f"rate-limit/retry evidence missing {needle!r}")
            )
    server_tree = _parse(root, GITHUB_SERVER_PATH)
    for node in ast.walk(server_tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "GitHubWriteClient":
            continue
        for kw in node.keywords:
            if (
                kw.arg == "simulate"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                violations.append(
                    Violation(str(GITHUB_SERVER_PATH), "build_server must not pass simulate=False")
                )
    return violations


def _validate_runtime_flag_absent(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for base in RUNTIME_SCAN_PATHS:
        abs_base = root / base
        if not abs_base.exists():
            continue
        for path in abs_base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if ACTIVATION_FLAG in text:
                violations.append(
                    Violation(
                        str(path.relative_to(root)),
                        f"runtime must not read or mention {ACTIVATION_FLAG} in Story 131.3",
                    )
                )
    return violations


def _validate_tool_bindings(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    tools_tree = _parse(root, GITHUB_TOOLS_PATH)
    tiers = _tier_map(tools_tree)
    tool_entries = [entry for entry in data.get("write_tools", []) if isinstance(entry, dict)]
    expected_tools = {str(entry.get("tool")) for entry in tool_entries if entry.get("tool")}
    for tool in sorted(expected_tools):
        if tiers.get(tool) != "THREE":
            violations.append(Violation(str(GITHUB_TOOLS_PATH), f"{tool} must be Tier.THREE"))
    for tool, tier in tiers.items():
        if tool in expected_tools:
            continue
        if any(verb in tool for verb in ("create", "update", "request")) and tier != "THREE":
            violations.append(
                Violation(str(GITHUB_TOOLS_PATH), f"write-like tool {tool} is not Tier.THREE")
            )
    for entry in tool_entries:
        tool = str(entry.get("tool"))
        handler = str(entry.get("handler"))
        event = str(entry.get("event"))
        fn = _find_function(tools_tree, handler)
        loc = f"{GITHUB_TOOLS_PATH}:{handler}"
        if not isinstance(fn, ast.AsyncFunctionDef):
            violations.append(Violation(loc, "handler async function not found"))
            continue
        if _tool_decorator_name(fn) != tool:
            violations.append(Violation(loc, f"handler must be decorated as mcp.tool {tool}"))
        trace_positions = _call_positions(fn, "validate_caller_trace_id")
        approval_calls = _calls_named(fn, "check_tier_with_approval")
        if not trace_positions:
            violations.append(Violation(loc, "handler must validate caller_trace_id"))
        if not approval_calls:
            violations.append(Violation(loc, "handler must call check_tier_with_approval"))
        else:
            first_approval_line = min(call.lineno for call in approval_calls)
            if trace_positions and min(trace_positions) > first_approval_line:
                violations.append(
                    Violation(loc, "caller_trace_id must be validated before approval gate")
                )
            if not any(
                _keyword_is_name(call, "approval_lookup", "approval_lookup")
                for call in approval_calls
            ):
                violations.append(
                    Violation(loc, "approval gate must bind approval_lookup=approval_lookup")
                )
            if not any(_call_has_tier_map_key(call, tool) for call in approval_calls):
                violations.append(Violation(loc, f"approval gate must use TIER_MAP[{tool!r}]"))
        guard_positions = _call_positions(fn, "_owner_repo_guard")
        write_positions = _call_positions(fn, "write_client_factory")
        if not guard_positions:
            violations.append(Violation(loc, "handler must call _owner_repo_guard before writing"))
        elif write_positions and min(guard_positions) > min(write_positions):
            violations.append(
                Violation(loc, "owner/repo guard must run before write_client_factory")
            )
        if event not in _string_constants(fn):
            violations.append(Violation(loc, f"handler must emit/result-describe {event}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    data = _load_json(root, CONTRACT_PATH)
    return [
        *_validate_contract(root, data),
        *_validate_write_client(root),
        *_validate_runtime_flag_absent(root),
        *_validate_tool_bindings(root, data),
    ]


def _copy_fixture(root: Path, relpaths: Sequence[Path]) -> None:
    for rel in relpaths:
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _self_test() -> int:
    relpaths = [
        CONTRACT_PATH,
        CREDENTIAL_CONTRACT_PATH,
        GITHUB_REST_PATH,
        GITHUB_SERVER_PATH,
        GITHUB_TOOLS_PATH,
        Path("docs/production-operations.md"),
        Path("docs/operator-runbook.md"),
        Path("docs/feature-status.md"),
        Path("_bmad-output/implementation-artifacts/131-3-github-write-activation-readiness.md"),
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
        server = root / GITHUB_SERVER_PATH
        server.write_text(
            server.read_text(encoding="utf-8").replace(
                "GitHubWriteClient(scoped_token=scoped_token)",
                "GitHubWriteClient(scoped_token=scoped_token, simulate=False)",
                1,
            ),
            encoding="utf-8",
        )
        bad = validate(root)
        if not any("simulate=False" in v.message for v in bad):
            print("self-test simulate=False fixture did not fail as expected", file=sys.stderr)
            return 1
    print("✓ check_github_write_activation.py self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_github_write_activation.py")
    parser.add_argument("--self-test", action="store_true", help="run internal fixture tests")
    parser.add_argument("--verbose", action="store_true", help="print success details")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    violations = validate(REPO_ROOT)
    if violations:
        print("check_github_write_activation.py FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.render()}", file=sys.stderr)
        return 1
    if args.verbose:
        data = _load_json(REPO_ROOT, CONTRACT_PATH)
        print(
            f"✓ GitHub write activation readiness OK ({len(data.get('write_tools', []))} write tool(s))"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
