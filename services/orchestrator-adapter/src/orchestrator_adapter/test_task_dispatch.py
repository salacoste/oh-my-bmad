"""Tests for task_dispatch functions (Stories 5.10, 5.11, 5.12, 5.13)."""

from __future__ import annotations

import dataclasses

import pytest
from events.payloads import PlanStep

from orchestrator_adapter.domain.task_dispatch import (
    CompletionMetrics,
    PlanParseResult,
    build_completion_payload,
    build_execution_started_payload,
    build_omc_prompt,
    build_plan_ready_payload,
    build_planning_started_payload,
    build_step_completed_payload,
    parse_omc_plan_output,
    parse_step_metrics,
)

# --- build_omc_prompt ---


def test_build_prompt_with_title_and_hint() -> None:
    result = build_omc_prompt("T-001", title="Build auth", hint="Use JWT")
    assert "Task: Build auth" in result
    assert "Hint: Use JWT" in result
    assert "Task ID: T-001" in result


def test_build_prompt_with_repo() -> None:
    result = build_omc_prompt("T-002", repo="org/project")
    assert "Repository: org/project" in result
    assert "Task ID: T-002" in result


def test_build_prompt_minimal() -> None:
    """No title/hint -> falls back to generic prompt."""
    result = build_omc_prompt("T-003")
    assert "Plan task T-003" in result


def test_build_prompt_with_title_only() -> None:
    """Title without hint -> no fallback."""
    result = build_omc_prompt("T-004", title="Do thing")
    assert "Task: Do thing" in result
    assert "Plan task" not in result


# --- parse_omc_plan_output ---


def test_parse_empty() -> None:
    result = parse_omc_plan_output("")
    assert result.summary == ""
    assert result.steps == ()
    assert result.estimated_steps == 0


def test_parse_plan_heading_with_numbered_steps() -> None:
    raw = "# Plan\n\n1. Do X\n2. Do Y\n\n# Other Section\n..."
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 2
    assert result.steps[0].step == 1
    assert "Do X" in result.steps[0].description
    assert "Other Section" not in result.summary
    assert "Do X" in result.summary


def test_parse_numbered_list_fallback() -> None:
    raw = "Some preamble\n1. First step\n2. Second step\n3. Third step"
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 3
    assert "First step" in result.steps[0].description
    assert "Second step" in result.steps[1].description


def test_parse_raw_fallback_creates_single_step() -> None:
    raw = "Just some output without structure"
    result = parse_omc_plan_output(raw)
    assert result.summary == raw
    # Unstructured text gets wrapped into a single fallback step.
    assert result.estimated_steps == 1
    assert result.steps[0].step == 1


def test_parse_truncates_long_plan() -> None:
    raw = "# Plan\n\n" + "X" * 5000
    result = parse_omc_plan_output(raw)
    assert len(result.summary) <= 2000


def test_parse_plan_section_heading_ignored() -> None:
    raw = "## Implementation Plan\n\n1) Setup project\n2) Write tests"
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 2
    assert "Setup project" in result.steps[0].description


def test_parse_multiline_step_description() -> None:
    raw = "1. First step\n   with extra detail\n2. Second step"
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 2
    # Multi-line description collapsed to single line.
    assert "extra detail" in result.steps[0].description


def test_parse_step_description_capped() -> None:
    long_desc = "A" * 600
    raw = f"1. {long_desc}"
    result = parse_omc_plan_output(raw)
    assert len(result.steps[0].description) <= 500


def test_plan_parse_result_frozen() -> None:
    result = PlanParseResult(summary="test", steps=())

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.summary = "changed"  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.steps = ()  # type: ignore[misc]


# --- payload builders ---


def test_planning_started_payload() -> None:
    payload = build_planning_started_payload("T-100")
    assert payload == {"task_id": "T-100"}


def test_plan_ready_payload_with_structured_steps() -> None:
    plan_result = parse_omc_plan_output("1. Step one\n2. Step two")
    payload = build_plan_ready_payload("T-100", plan_result)
    assert payload["task_id"] == "T-100"
    assert payload["plan_summary"] != ""
    assert payload["estimated_steps"] == 2
    assert len(payload["plan"]) == 2
    assert payload["plan"][0]["step"] == 1
    assert "Step one" in payload["plan"][0]["description"]


def test_plan_ready_payload_empty_result() -> None:
    plan_result = parse_omc_plan_output("")
    payload = build_plan_ready_payload("T-200", plan_result)
    assert payload["task_id"] == "T-200"
    assert payload["plan_summary"] == ""
    assert payload["estimated_steps"] == 0
    assert payload["plan"] == ()


def test_parse_whitespace_only() -> None:
    """Whitespace-only input treated like empty."""
    result = parse_omc_plan_output("   \n\n   ")
    assert result.summary == ""
    assert result.steps == ()
    assert result.estimated_steps == 0


def test_parse_non_sequential_step_numbers() -> None:
    """Non-sequential numbers preserved; estimated_steps is count, not max."""
    raw = "5. Do X\n10. Do Y"
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 2
    assert result.steps[0].step == 5
    assert result.steps[1].step == 10


def test_parse_step_zero_skipped() -> None:
    """Step number 0 is skipped (PlanStep requires ge=1)."""
    raw = "0. Bad step\n1. Good step"
    result = parse_omc_plan_output(raw)
    assert result.estimated_steps == 1
    assert result.steps[0].step == 1


def test_plan_ready_payload_backward_compat() -> None:
    """Old-format payload (task_id + plan_summary only) deserializes with defaults."""
    from events.payloads import TaskPlanReadyPayload

    payload = TaskPlanReadyPayload.model_validate(
        {"task_id": "T-300", "plan_summary": "Build auth"}
    )
    assert payload.plan == ()
    assert payload.estimated_steps == 0


# --- Story 5.12: execution-driving payload builders ---


def test_execution_started_payload() -> None:
    payload = build_execution_started_payload("T-001", "s-placeholder")
    assert payload["task_id"] == "T-001"
    assert payload["session_id"] == "s-placeholder"


def test_step_completed_payload() -> None:
    step = PlanStep(step=1, description="Do something")
    payload = build_step_completed_payload("T-001", step, "output text")
    assert payload["task_id"] == "T-001"
    assert payload["step"] == 1
    assert payload["description"] == "Do something"
    assert payload["output_summary"] == "output text"


def test_step_completed_payload_truncates_long_output() -> None:
    step = PlanStep(step=3, description="Run tests")
    long_output = "X" * 5000
    payload = build_step_completed_payload("T-002", step, long_output)
    assert len(payload["output_summary"]) == 2000


def test_completion_payload_with_step_outputs() -> None:
    plan_result = PlanParseResult(
        summary="Build feature",
        steps=(
            PlanStep(step=1, description="Write code"),
            PlanStep(step=2, description="Write tests"),
        ),
    )
    step_outputs = {1: "Code written", 2: "Tests pass"}
    payload = build_completion_payload("T-001", plan_result, step_outputs)
    assert payload["task_id"] == "T-001"
    assert "Step 1:" in payload["summary"]
    assert "Step 2:" in payload["summary"]
    assert "Code written" in payload["summary"]


def test_completion_payload_empty_step_outputs_uses_plan_summary() -> None:
    plan_result = PlanParseResult(
        summary="Build feature",
        steps=(PlanStep(step=1, description="Write code"),),
    )
    payload = build_completion_payload("T-002", plan_result, {})
    assert payload["summary"] == "Build feature"


def test_completion_payload_empty_plan() -> None:
    plan_result = PlanParseResult(summary="")
    payload = build_completion_payload("T-003", plan_result, {})
    assert payload["task_id"] == "T-003"
    assert payload["summary"] == "Task completed."


def test_completion_payload_summary_capped_at_2000() -> None:
    steps = tuple(PlanStep(step=i, description=f"Step {i}") for i in range(1, 51))
    plan_result = PlanParseResult(summary="long plan", steps=steps)
    step_outputs = {i: "Y" * 200 for i in range(1, 51)}
    payload = build_completion_payload("T-004", plan_result, step_outputs)
    assert len(payload["summary"]) <= 2000


def test_step_completed_payload_preserves_step_number() -> None:
    step = PlanStep(step=7, description="Nth step")
    payload = build_step_completed_payload("T-005", step, "done")
    assert payload["step"] == 7


# --- Story 5.13: parse_step_metrics and enriched build_completion_payload ---


def test_parse_metrics_git_diff() -> None:
    output = "3 files changed, 42 insertions(+), 10 deletions(-)"
    metrics = parse_step_metrics({1: output})
    assert metrics.files_changed == 3
    assert metrics.lines_added == 42
    assert metrics.lines_removed == 10
    assert metrics.ci_state == "unknown"


def test_parse_metrics_pytest_green() -> None:
    output = "12 passed in 3.45s"
    metrics = parse_step_metrics({1: output})
    assert metrics.tests_added == 12
    assert metrics.ci_state == "green"


def test_parse_metrics_pytest_red() -> None:
    output = "8 passed, 2 failed in 1.20s"
    metrics = parse_step_metrics({1: output})
    assert metrics.tests_added == 8  # counts passed, not total
    assert metrics.ci_state == "red"


def test_parse_metrics_tests_added_pattern() -> None:
    output = "5 tests added to the suite"
    metrics = parse_step_metrics({1: output})
    assert metrics.tests_added == 5


def test_parse_metrics_empty_outputs() -> None:
    metrics = parse_step_metrics({})
    assert metrics.files_changed is None
    assert metrics.lines_added is None
    assert metrics.lines_removed is None
    assert metrics.tests_added is None
    assert metrics.ci_state == "unknown"
    assert metrics.blockers_count == 0


def test_parse_metrics_malformed_output() -> None:
    metrics = parse_step_metrics({1: "random gibberish without patterns"})
    assert metrics.files_changed is None
    assert metrics.ci_state == "unknown"


def test_parse_metrics_multi_step_aggregation() -> None:
    outputs = {
        1: "1 file changed, 20 insertions(+), 5 deletions(-)",
        2: "2 files changed, 30 insertions(+), 15 deletions(-)\n4 passed",
        3: "8 passed, 1 failed",
    }
    metrics = parse_step_metrics(outputs)
    assert metrics.files_changed == 3
    assert metrics.lines_added == 50
    assert metrics.lines_removed == 20
    assert metrics.tests_added == 12  # 4 passed (step 2) + 8 passed (step 3)
    assert metrics.ci_state == "red"


def test_parse_metrics_ci_state_unknown_when_no_tests() -> None:
    output = "2 files changed, 10 insertions(+)"
    metrics = parse_step_metrics({1: output})
    assert metrics.ci_state == "unknown"


def test_completion_metrics_frozen() -> None:
    metrics = CompletionMetrics()
    with pytest.raises(dataclasses.FrozenInstanceError):
        metrics.files_changed = 99  # type: ignore[misc]


def test_build_completion_payload_with_metrics() -> None:
    plan_result = PlanParseResult(
        summary="Build feature",
        steps=(PlanStep(step=1, description="Write code"),),
    )
    metrics = CompletionMetrics(
        files_changed=3, lines_added=42, lines_removed=10,
        tests_added=12, ci_state="green", blockers_count=0,
    )
    payload = build_completion_payload("T-001", plan_result, {1: "done"}, metrics)
    assert payload["task_id"] == "T-001"
    assert payload["files_changed"] == 3
    assert payload["lines_added"] == 42
    assert payload["lines_removed"] == 10
    assert payload["tests_added"] == 12
    assert payload["ci_state"] == "green"
    assert payload["blockers_count"] is None  # 0 → None


def test_build_completion_payload_with_blockers() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics(blockers_count=2)
    payload = build_completion_payload("T-002", plan_result, {}, metrics)
    assert payload["blockers_count"] == 2


def test_build_completion_payload_without_metrics_backward_compat() -> None:
    plan_result = PlanParseResult(
        summary="Build feature",
        steps=(PlanStep(step=1, description="Write code"),),
    )
    payload = build_completion_payload("T-003", plan_result, {1: "done"})
    assert payload["task_id"] == "T-003"
    assert payload["files_changed"] is None
    assert payload["lines_added"] is None
    assert payload["ci_state"] is None
    assert payload["blockers_count"] is None


def test_build_completion_payload_zero_metrics_are_none() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics()  # all None, ci_state="unknown"
    payload = build_completion_payload("T-004", plan_result, {}, metrics)
    assert payload["files_changed"] is None
    assert payload["lines_added"] is None
    assert payload["lines_removed"] is None
    assert payload["tests_added"] is None
    assert payload["ci_state"] is None
    assert payload["blockers_count"] is None


def test_build_completion_payload_partial_metrics() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics(files_changed=5, ci_state="red")
    payload = build_completion_payload("T-005", plan_result, {}, metrics)
    assert payload["files_changed"] == 5
    assert payload["lines_added"] is None  # not extracted
    assert payload["ci_state"] == "red"
    assert payload["blockers_count"] is None


def test_parse_metrics_single_file_grammar() -> None:
    output = "1 file changed, 1 insertion(+), 1 deletion(-)"
    metrics = parse_step_metrics({1: output})
    assert metrics.files_changed == 1
    assert metrics.lines_added == 1
    assert metrics.lines_removed == 1


def test_parse_metrics_passed_zero_failed_is_green() -> None:
    output = "5 passed, 0 failed"
    metrics = parse_step_metrics({1: output})
    assert metrics.ci_state == "green"
    assert metrics.tests_added == 5


def test_parse_metrics_duplicate_patterns_aggregated() -> None:
    """findall captures every match within a single step output."""
    output = "Suite A: 5 passed\nSuite B: 3 passed, 1 failed"
    metrics = parse_step_metrics({1: output})
    assert metrics.tests_added == 8  # 5 + 3
    assert metrics.ci_state == "red"


def test_parse_metrics_tests_added_and_passed_both_present() -> None:
    """When both 'tests added' and 'passed' patterns match, 'tests added' wins."""
    output = "5 tests added to the suite\n3 passed"
    metrics = parse_step_metrics({1: output})
    assert metrics.tests_added == 5


def test_parse_metrics_zero_passed_zero_failed() -> None:
    output = "0 passed, 0 failed"
    metrics = parse_step_metrics({1: output})
    assert metrics.ci_state == "green"
    assert metrics.tests_added == 0


def test_build_completion_payload_large_values_clamped() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics(files_changed=2_000_000, lines_added=3_000_000_000)
    payload = build_completion_payload("T-001", plan_result, {}, metrics)
    assert payload["files_changed"] == 1_000_000
    assert payload["lines_added"] == 1_000_000_000


# --- Story 5.14: PR field tests ---


def test_build_completion_payload_with_pr_fields() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics(ci_state="green")
    payload = build_completion_payload(
        "T-100", plan_result, {}, metrics,
        pr_url="https://github.com/o/r/pull/7",
        pr_number=7,
        pr_branch="task/T-100",
    )
    assert payload["pr_url"] == "https://github.com/o/r/pull/7"
    assert payload["pr_number"] == 7
    assert payload["pr_branch"] == "task/T-100"


def test_build_completion_payload_without_pr_fields_backward_compat() -> None:
    plan_result = PlanParseResult(summary="test")
    payload = build_completion_payload("T-101", plan_result, {})
    assert payload.get("pr_url") is None
    assert payload.get("pr_number") is None
    assert payload.get("pr_branch") is None


def test_build_completion_payload_pr_fields_none_when_not_provided() -> None:
    plan_result = PlanParseResult(summary="test")
    metrics = CompletionMetrics(ci_state="green")
    payload = build_completion_payload(
        "T-102", plan_result, {}, metrics,
        pr_url=None, pr_number=None, pr_branch=None,
    )
    assert payload.get("pr_url") is None
    assert payload.get("pr_number") is None
    assert payload.get("pr_branch") is None
