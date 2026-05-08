"""Task dispatch — translates between platform task model and OMC contract (Story 5.10).

Provides functions to:
- Build an OMC prompt from platform task fields
- Parse OMC output to extract a plan summary
- Construct typed event payloads for the planning lifecycle
"""

from __future__ import annotations

import re

from events.payloads import TaskPlanningStartedPayload, TaskPlanReadyPayload


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


def parse_omc_plan_output(raw_output: str) -> str:
    """Extract plan summary from OMC output.

    Looks for common planning section markers (markdown headings, numbered lists).
    Falls back to the first non-empty 500 chars if no structure found.
    """
    if not raw_output:
        return ""

    # Try to find a "Plan" section heading.
    plan_match = re.search(
        r"(?:^|\n)(?:#+\s*(?:Plan|Implementation Plan|Steps).*?\n)(.*?)(?=(?:\n#+\s)|\Z)",
        raw_output,
        re.DOTALL | re.IGNORECASE,
    )
    if plan_match and plan_match.group(1).strip():
        return plan_match.group(1).strip()[:2000]

    # Try numbered list (1. ... 2. ...).
    numbered = re.findall(r"(?:^|\n)\s*\d+[.)]\s+\S.*", raw_output)
    if numbered:
        return "\n".join(numbered).strip()[:2000]

    # Fallback: first non-empty content up to 500 chars.
    lines = [line for line in raw_output.splitlines() if line.strip()]
    if lines:
        return "\n".join(lines)[:500]

    return ""


def build_planning_started_payload(task_id: str) -> dict[str, object]:
    """Build a ``task.planning.started`` event payload dict."""
    return TaskPlanningStartedPayload(task_id=task_id).model_dump()


def build_plan_ready_payload(task_id: str, plan_summary: str) -> dict[str, object]:
    """Build a ``task.plan.ready`` event payload dict."""
    return TaskPlanReadyPayload(task_id=task_id, plan_summary=plan_summary).model_dump()
