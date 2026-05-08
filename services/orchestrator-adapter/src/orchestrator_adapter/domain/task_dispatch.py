"""Task dispatch — translates between platform task model and OMC contract.

Story 5.10 — prompt building, OMC output parsing, typed event payloads.
Story 5.11 — structured plan parsing: ``parse_omc_plan_output`` returns a
``PlanParseResult`` with both a flat ``summary`` string and a structured
``steps`` tuple of ``PlanStep`` models.
Story 5.12 — execution-driving payload builders for step-by-step OMC driving.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from events.payloads import (
    PlanStep,
    TaskCompletedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskStepCompletedPayload,
)

_STEP_DESC_CAP: int = 500
_SUMMARY_CAP: int = 2000


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
    task_id: str, plan_result: PlanParseResult, step_outputs: dict[int, str],
) -> dict[str, object]:
    """Build a ``task.completed`` event payload dict with synthesized summary."""
    parts: list[str] = []
    for s in plan_result.steps:
        out = step_outputs.get(s.step, "")
        if out:
            parts.append(f"Step {s.step}: {out[:200]}")
    summary = "; ".join(parts) if parts else plan_result.summary or "Task completed."
    return TaskCompletedPayload(
        task_id=task_id,
        summary=summary[:2000],
    ).model_dump()
