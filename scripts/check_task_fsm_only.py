#!/usr/bin/env python3
"""P6-I3 discipline gate — no task.status mutations outside task_fsm.py.

Enforces the Phase-6 invariant that ALL task state transitions go through
the formal FSM (``domain/task_fsm.py``). Direct ``Task.status = ...``
assignments, ``task.status = ...`` patterns, and raw SQL ``UPDATE tasks SET
status`` are flagged unless they appear in the allowlisted exemption sites.

Usage::

    python scripts/check_task_fsm_only.py              # exit 0 = clean
    python scripts/check_task_fsm_only.py --self-test   # run built-in fixtures

Exit codes:
    0   — no violations
    1   — violations found (CI gate fail)
    2   — self-test failure (bug in this script)

Story 31.3 / P6-I3 / ADR-0018.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Add scripts/ to path for _common
sys.path.insert(0, str(Path(__file__).parent / "checks"))

from checks._common import Violation, has_noqa, walk_python_files  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that are ALLOWED to mutate task.status directly.
# - task_fsm.py: the FSM itself is the authority
# - test_task_fsm*.py: unit tests for the FSM
# - conftest.py: test fixtures may set initial state
# - migrations/: Alembic migrations may set status columns
# - snapshots.py / materializer.py: the materializer applies FSM-validated transitions
# - handlers.py: event handlers call FSM then persist
# - __init__.py in domain: re-exports
ALLOWED_PATH_SUFFIXES: tuple[str, ...] = (
    "domain/task_fsm.py",
    "domain/task_fsm_test.py",
    "test_task_fsm",
    "conftest.py",
    "migrations/",
    "domain/snapshots.py",
    "domain/materializer.py",
    "domain/handlers.py",
    "domain/__init__.py",
    "domain/event_types.py",
    "domain/failure_detection.py",
    # ATDD test files that deliberately test status mutation patterns
    "test_worker_pool_atdd.py",
    "test_task_fsm_atdd.py",
    "domain/test_handlers.py",
)

# Patterns that indicate a direct task status mutation.
# Each tuple is (rule_id, pattern, description).
_STATUS_MUTATION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "FSM001",
        re.compile(r"\.status\s*=\s*['\"]"),
        "direct .status = '...' assignment (use FSM transition)",
    ),
    (
        "FSM002",
        re.compile(r"UPDATE\s+tasks\s+SET\s+status\s*="),
        "raw SQL UPDATE tasks SET status (use FSM transition)",
    ),
]

# The noqa tag for suppressing FSM violations.
_NOQA_TAG = "FSM001"


def _is_allowed(path: Path) -> bool:
    """Check if a file path is in the allowlist."""
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        return False
    return any(rel.endswith(suffix) or suffix in rel for suffix in ALLOWED_PATH_SUFFIXES)


def check_file(path: Path) -> list[Violation]:
    """Scan a single Python file for task.status mutation violations."""
    violations: list[Violation] = []
    if _is_allowed(path):
        return violations

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    for lineno_0, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        # Skip comment-only and docstring lines
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        for rule_id, pattern, description in _STATUS_MUTATION_PATTERNS:
            if pattern.search(line):
                # Check for noqa suppression
                reason = has_noqa(line, _NOQA_TAG)
                if reason is not None:
                    continue  # suppressed with reason
                violations.append(
                    Violation(
                        file=path,
                        lineno=lineno_0,
                        rule=rule_id,
                        message=description,
                    )
                )
    return violations


def check_paths(paths: list[Path]) -> list[Violation]:
    """Scan all Python files under the given paths."""
    violations: list[Violation] = []
    for py_file in walk_python_files(paths):
        violations.extend(check_file(py_file))
    return violations


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------

_SELF_TEST_DIR = Path(__file__).parent / "_self_test_fsm"


def _write_fixture(name: str, content: str) -> Path:
    path = _SELF_TEST_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _self_test() -> None:
    """Built-in positive/negative fixture tests."""
    import shutil

    if _SELF_TEST_DIR.exists():
        shutil.rmtree(_SELF_TEST_DIR)

    violations_count = 0
    try:
        # Fixture 1: violation — direct status assignment
        _write_fixture(
            "violation_direct.py",
            'task.status = "running"\n',
        )
        # Fixture 2: clean — no mutation
        _write_fixture(
            "clean_file.py",
            "result = fsm.transition(current, target)\n",
        )
        # Fixture 3: violation — raw SQL
        _write_fixture(
            "violation_sql.py",
            'UPDATE tasks SET status = "failed"\n',
        )
        # Fixture 4: suppressed with noqa
        _write_fixture(
            "suppressed.py",
            'task.status = "running"  # noqa: FSM001 — test fixture\n',
        )

        violations = check_paths([_SELF_TEST_DIR])
        vfiles = {v.file.name for v in violations}

        expected_violating = {"violation_direct.py", "violation_sql.py"}
        actual_violating = {v for v in vfiles if v.startswith("violation_")}
        expected_clean = {"clean_file.py", "suppressed.py"}
        actual_clean = {v for v in {"clean_file.py", "suppressed.py"} if v not in vfiles}

        if actual_violating != expected_violating:
            print(
                f"FAIL: expected violations in {expected_violating}, got {actual_violating}",
                file=sys.stderr,
            )
            violations_count += 1

        if actual_clean != expected_clean:
            print(
                f"FAIL: expected clean in {expected_clean}, "
                f"got violations in {expected_clean - actual_clean}",
                file=sys.stderr,
            )
            violations_count += 1

        if violations_count:
            print(f"\n{violations_count} self-test(s) FAILED", file=sys.stderr)
            sys.exit(2)
        else:
            print("All self-tests passed.")
    finally:
        if _SELF_TEST_DIR.exists():
            shutil.rmtree(_SELF_TEST_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P6-I3 gate: no task.status mutations outside task_fsm.py",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in fixture tests and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    # Scan services/ and packages/ (where task logic lives)
    scan_paths = [
        REPO_ROOT / "services",
        REPO_ROOT / "packages",
    ]
    violations = check_paths(scan_paths)

    if violations:
        print(f"P6-I3 violations found ({len(violations)}):\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nUse: # noqa: FSM001 — <reason> to suppress if legitimate",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print("P6-I3 gate: clean — no task.status mutations outside task_fsm.py")
        sys.exit(0)


if __name__ == "__main__":
    main()
