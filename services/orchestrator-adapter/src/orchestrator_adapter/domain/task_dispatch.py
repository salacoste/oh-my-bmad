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

from annotated_types import Le as _Le
from events.payloads import (
    PlanStep,
    TaskBlockerRaisedPayload,
    TaskBudgetExceededPayload,
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
_TESTS_PASSED_RE = re.compile(
    r"(\d+)\s+passed(?:\s+in\s+[\d.]+s)?(?=\s*[,\n]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_TESTS_FAILED_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_TESTS_ADDED_RE = re.compile(r"(\d+)\s+tests?\s+added", re.IGNORECASE)


@dataclass(frozen=True)
class CompletionMetrics:
    """Aggregated execution metrics extracted from OMC step outputs (Story 5.13 / FR9).

    Fields are ``None`` when no matching pattern was found in OMC output, allowing
    downstream consumers to distinguish "not extracted" from "extracted as zero".
    """

    files_changed: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    tests_added: int | None = None
    ci_state: Literal["green", "red", "unknown"] = "unknown"
    blockers_count: int = 0


def parse_step_metrics(step_outputs: dict[int, str]) -> CompletionMetrics:
    """Extract structured metrics from OMC step stdout strings.

    Scans each step output for git-diff and pytest-style patterns and
    aggregates across all steps. Uses ``findall`` to capture every match
    within a single step output. Fields remain ``None`` when no matching
    pattern is found; ``0`` when a pattern matched but the value is zero.
    Unparseable output leaves all fields as ``None``.
    """
    total_files = 0
    total_insertions = 0
    total_deletions = 0
    total_passed = 0
    total_failed = 0
    total_tests_added = 0
    found_files = False
    found_insertions = False
    found_deletions = False
    found_passed = False
    found_failed = False
    found_tests_added = False

    for output in step_outputs.values():
        if not output:
            continue

        matches = _FILES_CHANGED_RE.findall(output)
        if matches:
            total_files += sum(int(m) for m in matches)
            found_files = True
        matches = _INSERTIONS_RE.findall(output)
        if matches:
            total_insertions += sum(int(m) for m in matches)
            found_insertions = True
        matches = _DELETIONS_RE.findall(output)
        if matches:
            total_deletions += sum(int(m) for m in matches)
            found_deletions = True
        matches = _TESTS_PASSED_RE.findall(output)
        if matches:
            total_passed += sum(int(m) for m in matches)
            found_passed = True
        matches = _TESTS_FAILED_RE.findall(output)
        if matches:
            total_failed += sum(int(m) for m in matches)
            found_failed = True
        matches = _TESTS_ADDED_RE.findall(output)
        if matches:
            total_tests_added += sum(int(m) for m in matches)
            found_tests_added = True

    has_test_output = found_passed or found_failed
    if has_test_output:
        ci_state: Literal["green", "red", "unknown"] = "red" if total_failed > 0 else "green"
    else:
        ci_state = "unknown"

    tests_added_value: int | None = None
    if found_tests_added:
        tests_added_value = total_tests_added

    return CompletionMetrics(
        files_changed=total_files if found_files else None,
        lines_added=total_insertions if found_insertions else None,
        lines_removed=total_deletions if found_deletions else None,
        tests_added=tests_added_value,
        ci_state=ci_state,
    )


# ---------------------------------------------------------------------------
# Story 5.15 — per-task budget enforcement (FR44 / NFR-P5)
# ---------------------------------------------------------------------------

_TOKEN_USAGE_RE = re.compile(r"(\d+)\s+tokens?", re.IGNORECASE)
_TOKEN_USAGE_AFTER_RE = re.compile(r"\btoken(?!iz)\w*\D+(\d+)", re.IGNORECASE)


def parse_token_usage(raw_output: str) -> int | None:
    """Extract token count from OMC step stdout.

    Matches patterns like ``"1234 tokens"``, ``"tokens: 500"``,
    ``"Token usage: 500"``, ``"Total tokens: 999"``.
    Returns ``None`` when no pattern matches.
    """
    if not raw_output:
        return None
    matches = _TOKEN_USAGE_RE.findall(raw_output)
    if not matches:
        matches = _TOKEN_USAGE_AFTER_RE.findall(raw_output)
    if not matches:
        return None
    return sum(int(m) for m in matches)


@dataclass(frozen=True)
class BudgetTracker:
    """Immutable tracker for cumulative token usage across task steps."""

    limit: int
    used: int = 0

    def consume(self, tokens: int) -> BudgetTracker:
        """Return a new tracker with *tokens* added to the cumulative total."""
        if tokens < 0:
            raise ValueError(f"consume() requires non-negative tokens, got {tokens}")
        return BudgetTracker(limit=self.limit, used=self.used + tokens)

    @property
    def is_exceeded(self) -> bool:
        return self.used > self.limit if self.limit > 0 else False


def build_budget_exceeded_payload(
    task_id: str,
    tracker: BudgetTracker,
    step: int,
) -> dict[str, object]:
    """Build a ``task.budget_exceeded`` event payload dict."""
    return TaskBudgetExceededPayload(
        task_id=task_id,
        token_limit=tracker.limit,
        tokens_used=tracker.used,
        step=step,
    ).model_dump()


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


# DOTALL is intentional: ``(.+?)`` spans multi-line step descriptions so
# ``desc.strip().replace("\\n", " ")`` can flatten them. The ``\\n`` anchor
# inside the lookahead ``\\n\\s*\\d+[.)]`` ensures we only match the next
# step number at the start of a new line — inline numbers like ``"Compare 2.0
# vs 3.0"`` within a description won't trigger a false split.
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
    task_id: str,
    step: PlanStep,
    output_summary: str,
) -> dict[str, object]:
    """Build a ``task.step.completed`` event payload dict."""
    return TaskStepCompletedPayload(
        task_id=task_id,
        step=step.step,
        description=step.description,
        output_summary=output_summary[:2000],
    ).model_dump()


# Pydantic payload upper bounds extracted from TaskCompletedPayload Field constraints.
# Extracts the Le(…) bound from metadata to avoid duplicating magic numbers.

_TaskCompletedFields = TaskCompletedPayload.model_fields


def _extract_le(field_name: str) -> int:
    constraint = next(
        (m for m in _TaskCompletedFields[field_name].metadata if isinstance(m, _Le)),
        None,
    )
    if constraint is None:
        raise ValueError(f"No Le constraint on field {field_name!r}")
    return int(constraint.le)


_MAX_COUNT: int = _extract_le("files_changed")
_MAX_LINES: int = _extract_le("lines_added")


def _clamp(value: int | None, upper: int) -> int | None:
    if value is None:
        return None
    return max(0, min(value, upper))


def build_completion_payload(
    task_id: str,
    plan_result: PlanParseResult,
    step_outputs: dict[int, str],
    metrics: CompletionMetrics | None = None,
    *,
    pr_url: str | None = None,
    pr_number: int | None = None,
    pr_branch: str | None = None,
    token_usage: int | None = None,
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
        payload_kwargs["files_changed"] = _clamp(metrics.files_changed, _MAX_COUNT)
        payload_kwargs["lines_added"] = _clamp(metrics.lines_added, _MAX_LINES)
        payload_kwargs["lines_removed"] = _clamp(metrics.lines_removed, _MAX_LINES)
        payload_kwargs["tests_added"] = _clamp(metrics.tests_added, _MAX_COUNT)
        # ci_state="unknown" → None: cleaner Telegram rendering than showing "unknown".
        payload_kwargs["ci_state"] = metrics.ci_state if metrics.ci_state != "unknown" else None
        # blockers_count reflects blocker/budget-exceeded exits that break out of
        # the step loop; forward-compatible for multi-blocker continuation (Story 5.17).
        payload_kwargs["blockers_count"] = metrics.blockers_count or None
    if pr_url is not None:
        payload_kwargs["pr_url"] = pr_url
    if pr_number is not None:
        payload_kwargs["pr_number"] = pr_number
    if pr_branch is not None:
        payload_kwargs["pr_branch"] = pr_branch
    if token_usage is not None:
        payload_kwargs["token_usage"] = token_usage
    return TaskCompletedPayload(**payload_kwargs).model_dump()


def build_blocker_raised_payload(task_id: str, reason: str) -> dict[str, object]:
    """Build a ``task.blocker_raised`` event payload dict."""
    return TaskBlockerRaisedPayload(
        task_id=task_id,
        reason=reason[:2000],
    ).model_dump()
