"""Tests for task_dispatch translation functions (Story 5.10 AC-9)."""

from __future__ import annotations

from orchestrator_adapter.domain.task_dispatch import (
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
    """No title/hint → falls back to generic prompt."""
    result = build_omc_prompt("T-003")
    assert "Plan task T-003" in result


def test_build_prompt_with_title_only() -> None:
    """Title without hint → no fallback."""
    result = build_omc_prompt("T-004", title="Do thing")
    assert "Task: Do thing" in result
    assert "Plan task" not in result


# --- parse_omc_plan_output ---


def test_parse_empty() -> None:
    assert parse_omc_plan_output("") == ""


def test_parse_plan_heading() -> None:
    raw = "# Implementation Plan\n\nStep 1: Do X\nStep 2: Do Y\n\n# Other Section\n..."
    result = parse_omc_plan_output(raw)
    assert "Step 1: Do X" in result
    assert "Other Section" not in result


def test_parse_numbered_list_fallback() -> None:
    raw = "Some preamble\n1. First step\n2. Second step\n3. Third step"
    result = parse_omc_plan_output(raw)
    assert "1. First step" in result
    assert "2. Second step" in result


def test_parse_raw_fallback() -> None:
    raw = "Just some output without structure"
    result = parse_omc_plan_output(raw)
    assert result == raw


def test_parse_truncates_long_plan() -> None:
    raw = "# Plan\n\n" + "X" * 5000
    result = parse_omc_plan_output(raw)
    assert len(result) <= 2000


# --- payload builders ---


def test_planning_started_payload() -> None:
    payload = build_planning_started_payload("T-100")
    assert payload == {"task_id": "T-100"}


def test_plan_ready_payload() -> None:
    payload = build_plan_ready_payload("T-100", "Build the auth module")
    assert payload == {"task_id": "T-100", "plan_summary": "Build the auth module"}
