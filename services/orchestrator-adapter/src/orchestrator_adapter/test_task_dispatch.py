"""Tests for task_dispatch translation functions (Story 5.10 AC-9, Story 5.11)."""

from __future__ import annotations

import dataclasses

import pytest

from orchestrator_adapter.domain.task_dispatch import (
    PlanParseResult,
    build_omc_prompt,
    build_plan_ready_payload,
    build_planning_started_payload,
    parse_omc_plan_output,
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
