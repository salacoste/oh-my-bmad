#!/usr/bin/env python3
"""Validate Story 134.2 split-deployment activation smoke evidence planning."""

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
CONTRACT_PATH = Path("docs/split-deployment-activation-smoke-evidence.json")
FEATURE_STATUS_PATH = Path("docs/feature-status.md")
PROJECT_OVERVIEW_PATH = Path("docs/project-overview.md")
SPRINT_STATUS_PATH = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")
ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/"
    "134-2-split-deployment-activation-smoke-evidence-package.md"
)
CLOSURE_ARTIFACT_PATH = Path(
    "_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md"
)
JUSTFILE_PATH = Path("justfile")
CI_PATH = Path(".github/workflows/ci.yml")
CHECKER_COMMAND = "uv run python scripts/check_split_deployment_activation_smoke_evidence.py"
CHECKER_SELF_TEST_COMMAND = f"{CHECKER_COMMAND} --self-test"

REQUIRED_TOP_LEVEL_SECTIONS = frozenset(
    {
        "schema_version",
        "phase",
        "epic",
        "story",
        "mode",
        "activation_boundary",
        "operator_gate",
        "readiness_prerequisites",
        "future_smoke_evidence_contract",
        "fail_closed_checks",
        "redaction_and_secret_hygiene",
        "non_goals",
        "docs_refs",
        "status_refs",
    }
)
REQUIRED_OPERATOR_GATE_FIELDS = frozenset(
    {
        "operator_approval_ref",
        "security_approval_ref",
        "change_window_utc",
        "target_environment",
        "target_version",
        "generated_at_utc",
        "expires_at_utc",
        "rollback_owner",
        "rollback_plan_ref",
        "emergency_disable_owner",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
    }
)
REQUIRED_DOMAINS = frozenset(
    {
        "service_placement",
        "network_boundaries",
        "registry_state_single_writer_authority",
        "event_log_append_authority",
        "mcp_boundary",
        "operator_dashboard_ingress_boundary",
        "health_readiness",
        "rollback",
    }
)
REQUIRED_READINESS_REFS = frozenset(
    {
        "docs/split-deployment-topology-readiness.json",
        "docs/worker-mcp-event-bus-split-readiness.json",
        "docs/operator-dashboard-split-readiness.json",
        "docs/horizontal-scaling-readiness.json",
        "docs/split-deployment-remote-postgres-closure-readiness.json",
    }
)
REQUIRED_FAIL_CLOSED_CHECKS = frozenset(
    {
        "missing_evidence_fails_closed",
        "malformed_evidence_fails_closed",
        "stale_evidence_fails_closed",
        "self_attestation_rejected",
        "secret_like_material_rejected",
        "activation_overclaim_rejected",
        "readiness_as_proof_rejected",
        "split_deployment_domain_coverage_required",
        "operator_gate_required",
        "rollback_required",
        "justfile_and_ci_wiring_required",
        "status_docs_story_134_2_done_required",
        "epic_134_in_progress_required",
    }
)
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
STATUS_SCAN_PATHS = (
    CONTRACT_PATH,
    FEATURE_STATUS_PATH,
    PROJECT_OVERVIEW_PATH,
    SPRINT_STATUS_PATH,
    ARTIFACT_PATH,
)
SECRET_SCAN_PATHS = STATUS_SCAN_PATHS

SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    re.compile(r"(?i)\bpostgres(?:ql)?(?:\+[-A-Za-z0-9_]+)?://[^\s'\"<>]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(
        r"(?i)(?:^|[^A-Za-z0-9_-])(?:password|passwd|passphrase|secret|token|credential|"
        r"private[_-]?key|api[_-]?key|bearer)['\"]?\s*[:=]\s*['\"]?[^\s'\"<>]+"
    ),
)
ACTIVATION_OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:split[-_ ]deployment|production\s+activation|activation|cutover|go[- ]live)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:live|active|activated|enabled|complete|completed|successful|succeeded|occurred|"
        r"performed|executed|serving(?:\s+traffic)?|done)\b"
        r"(?:\W+\w+){0,8}\W+"
        r"\b(?:split[-_ ]deployment|production\s+activation|activation|cutover|go[- ]live)\b",
        re.I,
    ),
)
READINESS_AS_PROOF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproof\b", re.I),
    re.compile(
        r"\breadiness\s+(?:artifacts?|evidence|prerequisites)\b.*\bproves?\s+activation\b", re.I
    ),
)
SELF_ATTESTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bself[-_ ](?:attestation|attested|review)\b.*\b(?:sufficient|accepted|approved)\b", re.I
    ),
    re.compile(r"\bimplementer[-_ ]review\b.*\b(?:sufficient|accepted|approved)\b", re.I),
)
SAFE_CONTEXT_PATTERN = re.compile(
    r"\b(?:no|not|never|without|future|operator[- ]gated|planning|contract|evidence\s+package|"
    r"not\s+proof|no\s+live|deferred|fail[- ]closed|forbidden|rejected|not\s+activation|"
    r"complete\s+locally|local[-_]done)\b",
    re.I,
)
ALLOWED_STORY_STATUS = {"done", "closed"}


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
        raise ValueError(f"{relpath} must contain a JSON object")
    return cast("dict[str, Any]", data)


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _section(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _lower_text(value: object) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        return " ".join(_lower_text(item) for pair in value.items() for item in pair)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_lower_text(item) for item in value)
    return str(value).lower()


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_strings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from _walk_strings(child)


def _contains_secret_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS)


def _slug_heading(line: str) -> str | None:
    match = re.match(r"^#+\s+(?P<title>.+?)\s*$", line)
    if not match:
        return None
    title = match.group("title").strip().lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title)).strip("-")


def _validate_ref_target(root: Path, ref: str) -> list[Violation]:
    path_text, _, fragment = ref.partition("#")
    path = root / path_text
    if not path.exists():
        return [Violation(ref, "referenced file is missing")]
    if fragment:
        text = path.read_text(encoding="utf-8")
        slugs = {_slug_heading(line) for line in text.splitlines()}
        if fragment not in text and fragment.lower() not in slugs:
            return [Violation(ref, "referenced anchor is missing")]
    return []


def _is_safe_context(line: str) -> bool:
    return bool(SAFE_CONTEXT_PATTERN.search(line))


def _scan_text_for_forbidden(relpath: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if (
            relpath == SPRINT_STATUS_PATH
            and "134" not in line
            and "current_phase" not in line
            and "epic-134" not in line
        ):
            continue
        if _contains_secret_value(line):
            violations.append(Violation(f"{relpath}:{line_no}", "secret-like value is not allowed"))
        for pattern in ACTIVATION_OVERCLAIM_PATTERNS:
            if pattern.search(line) and not _is_safe_context(line):
                violations.append(
                    Violation(f"{relpath}:{line_no}", "activation overclaim is not allowed")
                )
                break
        for pattern in READINESS_AS_PROOF_PATTERNS:
            if pattern.search(line) and not _is_safe_context(line):
                violations.append(
                    Violation(f"{relpath}:{line_no}", "readiness-as-proof language is not allowed")
                )
                break
        for pattern in SELF_ATTESTATION_PATTERNS:
            if pattern.search(line) and not _is_safe_context(line):
                violations.append(
                    Violation(f"{relpath}:{line_no}", "self-attestation acceptance is not allowed")
                )
                break
    return violations


def _validate_contract(root: Path, data: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    if missing := REQUIRED_TOP_LEVEL_SECTIONS - set(data):
        violations.append(
            Violation(str(CONTRACT_PATH), f"required sections missing {sorted(missing)}")
        )
    if data.get("schema_version") != "story-134.2/v1":
        violations.append(Violation(str(CONTRACT_PATH), "schema_version must be story-134.2/v1"))
    if data.get("phase") != "51" or data.get("epic") != "134" or data.get("story") != "134.2":
        violations.append(Violation(str(CONTRACT_PATH), "phase/epic/story must be 51/134/134.2"))
    if (
        data.get("mode")
        != "static_split_deployment_activation_smoke_evidence_contract_not_activation"
    ):
        violations.append(
            Violation(
                str(CONTRACT_PATH), "mode must be static split evidence contract, not activation"
            )
        )

    boundary = _section(data, "activation_boundary")
    if boundary.get("activation_performed") is not False:
        violations.append(Violation(str(CONTRACT_PATH), "activation_performed must be false"))
    boundary_text = _lower_text(boundary)
    for phrase in (
        "future/operator-gated",
        "not proof activation occurred",
        "no live split deployment activation",
        "compose/profile activation",
    ):
        if phrase not in boundary_text:
            violations.append(
                Violation(str(CONTRACT_PATH), f"activation boundary missing {phrase!r}")
            )

    gate = _section(data, "operator_gate")
    if gate.get("required") is not True:
        violations.append(Violation(str(CONTRACT_PATH), "operator gate must be required"))
    if missing := REQUIRED_OPERATOR_GATE_FIELDS - _string_set(gate.get("fields")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"operator gate fields missing {sorted(missing)}")
        )
    gate_text = _lower_text(gate)
    for phrase in ("operator-gated", "timestamped", "redacted", "change window", "rollback"):
        if phrase not in gate_text:
            violations.append(Violation(str(CONTRACT_PATH), f"operator gate missing {phrase!r}"))

    readiness = _section(data, "readiness_prerequisites")
    if readiness.get("semantics") != "prerequisites_only_not_activation_proof":
        violations.append(
            Violation(str(CONTRACT_PATH), "readiness prerequisites must not be activation proof")
        )
    if missing := REQUIRED_READINESS_REFS - _string_set(readiness.get("minimum_refs")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"readiness refs missing {sorted(missing)}")
        )

    future_contract = _section(_section(data, "future_smoke_evidence_contract"), "required_domains")
    if missing := REQUIRED_DOMAINS - set(future_contract):
        violations.append(
            Violation(
                str(CONTRACT_PATH), f"required smoke evidence domains missing {sorted(missing)}"
            )
        )
    for domain in REQUIRED_DOMAINS & set(future_contract):
        entry = _section(future_contract, domain)
        if entry.get("required") is not True:
            violations.append(Violation(str(CONTRACT_PATH), f"{domain} must be required"))
        if entry.get("not_activation_proof_by_itself") is not True:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{domain} must not be activation proof by itself")
            )
        if len(_string_set(entry.get("minimum_evidence"))) < 2:
            violations.append(
                Violation(str(CONTRACT_PATH), f"{domain} minimum evidence is incomplete")
            )

    if missing := REQUIRED_FAIL_CLOSED_CHECKS - _string_set(data.get("fail_closed_checks")):
        violations.append(
            Violation(str(CONTRACT_PATH), f"fail-closed checks missing {sorted(missing)}")
        )
    redaction_text = _lower_text(_section(data, "redaction_and_secret_hygiene"))
    for phrase in (
        "no plaintext secrets",
        "credential values",
        "private key",
        "certificate material",
        "redaction_report_ref",
    ):
        if phrase not in redaction_text:
            violations.append(Violation(str(CONTRACT_PATH), f"redaction policy missing {phrase!r}"))
    non_goals_text = _lower_text(data.get("non_goals", []))
    for phrase in (
        "no live split deployment activation",
        "external load-balancer activation",
        "host-port change",
        "production host mutation",
        "compose/profile activation",
        "no acceptance of readiness prerequisites as proof activation occurred",
    ):
        if phrase not in non_goals_text:
            violations.append(Violation(str(CONTRACT_PATH), f"non-goals missing {phrase!r}"))
    if missing := REQUIRED_DOC_REFS - _string_set(data.get("docs_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"docs refs missing {sorted(missing)}"))
    if missing := REQUIRED_STATUS_REFS - _string_set(data.get("status_refs")):
        violations.append(Violation(str(CONTRACT_PATH), f"status refs missing {sorted(missing)}"))
    for ref in sorted(_string_set(data.get("docs_refs")) | _string_set(data.get("status_refs"))):
        violations.extend(_validate_ref_target(root, ref))
    return violations


def _recipe_body(just: str, recipe: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(recipe)}:\n(?P<body>.*?)(?=^\S|\Z)", just)
    return match.group("body") if match else ""


def _ci_has_command(text: str, command: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            stripped = stripped.removeprefix("run:").strip()
        if stripped.startswith("-"):
            stripped = stripped.removeprefix("-").strip()
        if stripped == command:
            return True
    return False


def _has_story_134_6_planning_closure(root: Path) -> bool:
    closure_path = root / CLOSURE_ARTIFACT_PATH
    if not closure_path.exists():
        return False
    closure_text = _read(root, CLOSURE_ARTIFACT_PATH)
    return all(
        phrase in closure_text
        for phrase in ("planning-only/docs-status", "not activation", "future/operator-gated")
    )


def _validate_wiring(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    just = _read(root, JUSTFILE_PATH)
    ci = _read(root, CI_PATH)
    for recipe in ("lint", "check-gates"):
        if not _ci_has_command(_recipe_body(just, recipe), CHECKER_COMMAND):
            violations.append(
                Violation(str(JUSTFILE_PATH), f"{recipe} must run Story 134.2 checker")
            )
    if not _ci_has_command(_recipe_body(just, "check-gates-self-test"), CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(
                str(JUSTFILE_PATH), "check-gates-self-test must run Story 134.2 checker self-test"
            )
        )
    if not _ci_has_command(ci, CHECKER_COMMAND):
        violations.append(Violation(str(CI_PATH), "CI static checks must run Story 134.2 checker"))
    if not _ci_has_command(ci, CHECKER_SELF_TEST_COMMAND):
        violations.append(
            Violation(str(CI_PATH), "CI self-tests must run Story 134.2 checker self-test")
        )
    return violations


def _validate_status(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    sprint = _read(root, SPRINT_STATUS_PATH)
    story_134_2 = re.search(
        r"(?m)^\s*134-2-split-deployment-activation-smoke-evidence-package:\s*(?P<status>\S+)",
        sprint,
    )
    if not story_134_2 or story_134_2.group("status") not in ALLOWED_STORY_STATUS:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.2 must be done/closed"))
    story_134_1 = re.search(
        r"(?m)^\s*134-1-activation-evidence-schema-preflight-gate:\s*(?P<status>\S+)", sprint
    )
    if not story_134_1 or story_134_1.group("status") not in ALLOWED_STORY_STATUS:
        violations.append(Violation(str(SPRINT_STATUS_PATH), "Story 134.1 must remain done/closed"))
    epic_134 = re.search(r"(?m)^\s*epic-134:\s*(?P<status>\S+)", sprint)
    epic_134_status = epic_134.group("status") if epic_134 else None
    if epic_134_status != "in-progress" and not (
        epic_134_status in {"done", "closed"} and _has_story_134_6_planning_closure(root)
    ):
        violations.append(
            Violation(
                str(SPRINT_STATUS_PATH),
                "Epic 134 must remain in-progress unless Story 134.6 planning-only closure exists",
            )
        )

    feature = _read(root, FEATURE_STATUS_PATH)
    for phrase in (
        "Story 134.2",
        "complete locally",
        CHECKER_COMMAND,
        "future/operator-gated",
        "not proof activation occurred",
        "No live split deployment activation",
    ):
        if phrase not in feature:
            violations.append(
                Violation(str(FEATURE_STATUS_PATH), f"feature status missing {phrase!r}")
            )
    if not (
        "split-deployment activation smoke evidence package" in feature
        or "split-deployment smoke evidence package" in feature
    ):
        violations.append(
            Violation(
                str(FEATURE_STATUS_PATH),
                "feature status missing split-deployment smoke evidence package",
            )
        )
    overview = _read(root, PROJECT_OVERVIEW_PATH)
    for phrase in (
        "Story 134.2",
        "complete locally",
        "future/operator-gated",
        "no live activation",
    ):
        if phrase not in overview:
            violations.append(
                Violation(str(PROJECT_OVERVIEW_PATH), f"project overview missing {phrase!r}")
            )
    artifact = _read(root, ARTIFACT_PATH)
    for phrase in (
        "No live split deployment activation",
        CHECKER_COMMAND,
        CHECKER_SELF_TEST_COMMAND,
        "uv run pytest tests/scripts/test_check_split_deployment_activation_smoke_evidence.py",
        "uv run ruff check scripts/check_split_deployment_activation_smoke_evidence.py tests/scripts/test_check_split_deployment_activation_smoke_evidence.py",
    ):
        if phrase not in artifact:
            violations.append(Violation(str(ARTIFACT_PATH), f"story artifact missing {phrase!r}"))
    return violations


def validate(root: Path = REPO_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    try:
        data = _load_json(root, CONTRACT_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [Violation(str(CONTRACT_PATH), f"failed to load contract: {exc}")]
    violations.extend(_validate_contract(root, data))
    for relpath in SECRET_SCAN_PATHS:
        try:
            violations.extend(_scan_text_for_forbidden(relpath, _read(root, relpath)))
        except OSError as exc:
            violations.append(Violation(str(relpath), f"failed to read for scan: {exc}"))
    for value in _walk_strings(data):
        if _contains_secret_value(value):
            violations.append(
                Violation(str(CONTRACT_PATH), "contract contains secret-like material")
            )
            break
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
        CLOSURE_ARTIFACT_PATH,
        JUSTFILE_PATH,
        CI_PATH,
    ):
        src = root / relpath
        if relpath == CLOSURE_ARTIFACT_PATH and not src.exists():
            continue
        dst = dest / relpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="split-deployment-activation-smoke-evidence-") as tmp:
        root = Path(tmp)
        _copy_fixture(REPO_ROOT, root)
        baseline = validate(root)
        if baseline:
            for violation in baseline:
                print(violation.render(), file=sys.stderr)
            return 1
        data = _load_json(root, CONTRACT_PATH)
        domains = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", data["future_smoke_evidence_contract"])["required_domains"],
        )
        domains.pop("service_placement")
        _write_json(root / CONTRACT_PATH, data)
        if not any("required smoke evidence domains missing" in v.message for v in validate(root)):
            print("self-test failed: missing required domain was not rejected", file=sys.stderr)
            return 1
        _copy_fixture(REPO_ROOT, root)
        status_path = root / FEATURE_STATUS_PATH
        status_path.write_text(
            status_path.read_text(encoding="utf-8") + "\nSplit deployment activation is live.\n",
            encoding="utf-8",
        )
        if not any("activation overclaim" in v.message for v in validate(root)):
            print("self-test failed: activation overclaim was not rejected", file=sys.stderr)
            return 1
        _copy_fixture(REPO_ROOT, root)
        ci_path = root / CI_PATH
        ci_path.write_text(
            ci_path.read_text(encoding="utf-8").replace(CHECKER_SELF_TEST_COMMAND, "", 1),
            encoding="utf-8",
        )
        if not any("CI self-tests" in v.message for v in validate(root)):
            print("self-test failed: missing CI self-test wiring was not rejected", file=sys.stderr)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run checker fixture self-test")
    args = parser.parse_args(argv)
    violations = validate(REPO_ROOT) if not args.self_test else []
    if args.self_test and _self_test() != 0:
        return 1
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1
    if args.self_test:
        print("split deployment activation smoke evidence checker self-test passed")
    else:
        print(
            "split deployment activation smoke evidence contract/status checks passed; not activation proof"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
