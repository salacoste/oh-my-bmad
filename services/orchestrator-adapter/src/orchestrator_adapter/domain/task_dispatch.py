"""Task dispatch — translates between platform task model and OMC contract.

Story 5.10 — prompt building, OMC output parsing, typed event payloads.
Story 5.11 — structured plan parsing: ``parse_omc_plan_output`` returns a
``PlanParseResult`` with both a flat ``summary`` string and a structured
``steps`` tuple of ``PlanStep`` models.
Story 5.12 — execution-driving payload builders for step-by-step OMC driving.
Story 5.13 — completion summary metrics extraction and FR9 field enrichment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from events.payloads import (
    PlanStep,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskStepCompletedPayload,
)

_STEP_DESC_CAP: int = 500
_SUMMARY_CAP: int = 2000

# ---------------------------------------------------------------------------
# Story 5.13 — completion summary metrics (FR9)
# ---------------------------------------------------------------------------

# Regex patterns for extracting structured metrics from OMC step output.
_FILES_CHANGED_RE = re.compile(r"(\d+)\s+files?\s+changed", re.IGNORECASE)
_INSERTIONS_RE = re.compile(r"(\d+)\s+insertions?\(\+\)", re.IGNORECASE)
_DELETIONS_RE = re.compile(r"(\d+)\s+deletions?\(-\)", re.IGNORECASE)
_TESTS_PASSED_RE = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_TESTS_FAILED_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_TESTS_ADDED_RE = re.compile(r"(\d+)\s+tests?\s+added", re.IGNORECASE)


@dataclass(frozen=True)
class CompletionMetrics:
    """Aggregated execution metrics extracted from OMC step outputs (Story 5.13 / FR9)."""

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    tests_added: int = 0
    ci_state: Literal["green", "red", "unknown"] = "unknown"
    blockers_count: int = 0


def parse_step_metrics(step_outputs: dict[int, str]) -> CompletionMetrics:
    """Extract structured metrics from OMC step stdout strings.

    Scans each step output for git-diff and pytest-style patterns and
    aggregates them. Unparseable output is treated as zero-count.
    """
    total_files = 0
    total_insertions = 0
    total_deletions = 0
    total_passed = 0
    total_failed = 0
    total_tests_added = 0
    has_test_output = False

    for output in step_outputs.values():
        if not output:
            continue

        m = _FILES_CHANGED_RE.search(output)
        if m:
            total_files += int(m.group(1))
        m = _INSERTIONS_RE.search(output)
        if m:
            total_insertions += int(m.group(1))
        m = _DELETIONS_RE.search(output)
        if m:
            total_deletions += int(m.group(1))
        m = _TESTS_PASSED_RE.search(output)
        if m:
            total_passed += int(m.group(1))
            has_test_output = True
        m = _TESTS_FAILED_RE.search(output)
        if m:
            total_failed += int(m.group(1))
            has_test_output = True
        m = _TESTS_ADDED_RE.search(output)
        if m:
            total_tests_added += int(m.group(1))

    if has_test_output:
        ci_state: Literal["green", "red", "unknown"] = "red" if total_failed > 0 else "green"
    else:
        ci_state = "unknown"

    return CompletionMetrics(
        files_changed=total_files,
        lines_added=total_insertions,
        lines_removed=total_deletions,
        tests_added=total_tests_added or total_passed,
        ci_state=ci_state,
    )


@dataclass(frozen=True)
class PlanParseResult:
    """Structured result from OMC plan output parsing."""

    summary: str
    steps: tuple[PlanStep, ...] = field(default=())

    @property
    def estimated_steps(self) -> int:
        return len(self.steps)


def build_omc_prompt(
    task_id: str,
    title: str | None = None,
    hint: str | None = None,
    repo: str | None = None,
) -> str:
    """Build a planning prompt for OMC from platform task fields."""
    parts: list[str] = []
    if title:
        parts.append(f"Task: {title}")
    if hint:
        parts.append(f"Hint: {hint}")
    if repo:
        parts.append(f"Repository: {repo}")
    parts.append(f"Task ID: {task_id}")
    if not title and not hint:
        parts.append(f"Plan task {task_id}")
    return "\n".join(parts)


_NUMBERED_STEP_RE = re.compile(
    r"(?:^|\n)\s*(\d+)[.)]\s+(.+?)(?=(?:\n\s*\d+[.)])|\Z)",
    re.DOTALL,
)


def _extract_numbered_steps(raw_output: str) -> list[PlanStep]:
    """Extract numbered steps from text like ``1) ...`` or ``1. ...``."""
    matches = _NUMBERED_STEP_RE.findall(raw_output)
    steps: list[PlanStep] = []
    for num_str, desc in matches:
        step_num = int(num_str)
        if step_num < 1:
            continue
        desc_clean = desc.strip().replace("\n", " ")
        if desc_clean:
            steps.append(PlanStep(step=step_num, description=desc_clean[:_STEP_DESC_CAP]))
    return steps


def parse_omc_plan_output(raw_output: str) -> PlanParseResult:
    """Extract structured plan from OMC output.

    Tries in order:
    1. Markdown heading section — extract numbered steps within.
    2. Global numbered list (``1. ... 2. ...``).
    3. Fallback: first non-empty content up to 500 chars as a single step.
    """
    if not raw_output or not raw_output.strip():
        return PlanParseResult(summary="")

    # Try to find a "Plan" section heading and extract steps within it.
    plan_match = re.search(
        r"(?:^|\n)(?:#+\s*(?:Plan\b|Implementation Plan\b|Steps\b).*?\n)(.*?)(?=(?:\n#+\s)|\Z)",
        raw_output,
        re.DOTALL | re.IGNORECASE,
    )
    plan_section: str | None = None
    if plan_match and plan_match.group(1).strip():
        plan_section = plan_match.group(1).strip()

    search_text = plan_section if plan_section is not None else raw_output

    # Extract structured steps.
    steps = _extract_numbered_steps(search_text)

    # Build summary — prefer plan section, else first non-empty lines.
    if plan_section is not None:
        summary = plan_section[:_SUMMARY_CAP]
    else:
        lines = [line for line in raw_output.splitlines() if line.strip()]
        summary = "\n".join(lines)[:500] if lines else ""

    # Fallback: if no structured steps found, create one from summary.
    if not steps and summary:
        steps = (PlanStep(step=1, description=summary[:_STEP_DESC_CAP]),)

    return PlanParseResult(summary=summary, steps=tuple(steps))


def build_planning_started_payload(task_id: str) -> dict[str, object]:
    """Build a ``task.planning.started`` event payload dict."""
    return TaskPlanningStartedPayload(task_id=task_id).model_dump()


def build_plan_ready_payload(task_id: str, plan_result: PlanParseResult) -> dict[str, object]:
    """Build a ``task.plan.ready`` event payload dict with structured steps."""
    return TaskPlanReadyPayload(
        task_id=task_id,
        plan_summary=plan_result.summary,
        plan=plan_result.steps,
        estimated_steps=plan_result.estimated_steps,
    ).model_dump()


def build_execution_started_payload(task_id: str, session_id: str) -> dict[str, object]:
    """Build a ``task.execution.started`` event payload dict."""
    return TaskExecutionStartedPayload(task_id=task_id, session_id=session_id).model_dump()


def build_step_completed_payload(
    task_id: str, step: PlanStep, output_summary: str,
) -> dict[str, object]:
    """Build a ``task.step.completed`` event payload dict."""
    return TaskStepCompletedPayload(
        task_id=task_id,
        step=step.step,
        description=step.description,
        output_summary=output_summary[:2000],
    ).model_dump()


def build_completion_payload(
    task_id: str,
    plan_result: PlanParseResult,
    step_outputs: dict[int, str],
    metrics: CompletionMetrics | None = None,
) -> dict[str, object]:
    """Build a ``task.completed`` event payload dict with synthesized summary.

    When *metrics* is provided, the FR9 structured fields are populated
    on ``TaskCompletedPayload``. When ``None`` (backward compat), all
    FR9 fields remain ``None``.
    """
    parts: list[str] = []
    for s in plan_result.steps:
        out = step_outputs.get(s.step, "")
        if out:
            parts.append(f"Step {s.step}: {out[:200]}")
    summary = "; ".join(parts) if parts else plan_result.summary or "Task completed."
    payload_kwargs: dict[str, object] = {
        "task_id": task_id,
        "summary": summary[:2000],
    }
    if metrics is not None:
        payload_kwargs["files_changed"] = metrics.files_changed or None
        payload_kwargs["lines_added"] = metrics.lines_added or None
        payload_kwargs["lines_removed"] = metrics.lines_removed or None
        payload_kwargs["tests_added"] = metrics.tests_added or None
        payload_kwargs["ci_state"] = metrics.ci_state if metrics.ci_state != "unknown" else None
        payload_kwargs["blockers_count"] = metrics.blockers_count or None
    return TaskCompletedPayload(**payload_kwargs).model_dump()


def build_blocker_raised_payload(task_id: str, reason: str) -> dict[str, object]:
    """Build a ``task.blocker_raised`` event payload dict."""
    return TaskBlockerRaisedPayload(
        task_id=task_id,
        reason=reason[:2000],
    ).model_dump()
