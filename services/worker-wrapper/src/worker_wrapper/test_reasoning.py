"""Tests for reasoning breadcrumb extraction + secret sanitization (Story 5.5).

Covers: sanitize_reasoning_text, classify_reasoning_block,
extract_reasoning_text, build_reasoning_breadcrumb,
extract_reasoning_from_content, schema-registry registration,
domain-layer IO-free enforcement.
"""

from __future__ import annotations

import inspect
from typing import Any

from worker_wrapper.domain.reasoning import (
    _MAX_REASONING_LEN,
    build_reasoning_breadcrumb,
    classify_reasoning_block,
    extract_reasoning_from_content,
    extract_reasoning_text,
    sanitize_reasoning_text,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thinking_block(text: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": text}


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _tool_use_block(
    name: str = "Write",
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": "toolu_01",
        "name": name,
        "input": input_data or {},
    }


def _tool_result_block() -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": "toolu_01",
        "content": "File written",
    }


_SESSION_ID = "s-0192abc0-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------
# Tests: sanitize_reasoning_text
# ---------------------------------------------------------------------------


class TestSanitizeReasoningText:
    def test_clean_text_passes_through(self) -> None:
        text, suppressed = sanitize_reasoning_text("Planning the implementation")
        assert text == "Planning the implementation"
        assert not suppressed

    def test_empty_string(self) -> None:
        text, suppressed = sanitize_reasoning_text("")
        assert text == ""
        assert not suppressed

    def test_secret_detected_suppresses(self) -> None:
        text, suppressed = sanitize_reasoning_text(
            "Using key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA to proceed"
        )
        assert text == ""
        assert suppressed

    def test_github_token_suppresses(self) -> None:
        text, suppressed = sanitize_reasoning_text(
            "Push with ghp_" + "A" * 36
        )
        assert text == ""
        assert suppressed

    def test_long_text_truncated(self) -> None:
        long_text = "x" * (_MAX_REASONING_LEN + 100)
        text, suppressed = sanitize_reasoning_text(long_text)
        assert len(text) == _MAX_REASONING_LEN
        assert not suppressed

    def test_secret_in_long_text_suppresses(self) -> None:
        long_text = "Planning... " + "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        text, suppressed = sanitize_reasoning_text(long_text)
        assert text == ""
        assert suppressed


# ---------------------------------------------------------------------------
# Tests: classify_reasoning_block
# ---------------------------------------------------------------------------


class TestClassifyReasoningBlock:
    def test_thinking_always_plan_drafted(self) -> None:
        block = _thinking_block("Let me think...")
        assert classify_reasoning_block(block) == "plan_drafted"

    def test_thinking_ignores_context(self) -> None:
        block = _thinking_block("Hmm")
        assert classify_reasoning_block(block, "tool_result", "tool_use") == "plan_drafted"

    def test_text_before_tool_use_is_rationale(self) -> None:
        block = _text_block("I'll edit the file")
        assert classify_reasoning_block(block, None, "tool_use") == "tool_call_rationale"

    def test_text_after_tool_result_is_step_summary(self) -> None:
        block = _text_block("Good, tests passed")
        assert classify_reasoning_block(block, "tool_result", None) == "step_summary"

    def test_text_alone_is_plan_drafted(self) -> None:
        block = _text_block("Let me start implementing")
        assert classify_reasoning_block(block) == "plan_drafted"

    def test_text_between_two_texts_is_plan_drafted(self) -> None:
        block = _text_block("Some planning text")
        assert classify_reasoning_block(block, "text", "text") == "plan_drafted"

    def test_non_reasoning_block_returns_none(self) -> None:
        block = _tool_use_block()
        assert classify_reasoning_block(block) is None

    def test_tool_result_returns_none(self) -> None:
        block = _tool_result_block()
        assert classify_reasoning_block(block) is None


# ---------------------------------------------------------------------------
# Tests: extract_reasoning_text
# ---------------------------------------------------------------------------


class TestExtractReasoningText:
    def test_thinking_block(self) -> None:
        assert extract_reasoning_text(_thinking_block("my thoughts")) == "my thoughts"

    def test_text_block(self) -> None:
        assert extract_reasoning_text(_text_block("my plan")) == "my plan"

    def test_tool_use_block_empty(self) -> None:
        assert extract_reasoning_text(_tool_use_block()) == ""

    def test_missing_field(self) -> None:
        assert extract_reasoning_text({"type": "thinking"}) == ""


# ---------------------------------------------------------------------------
# Tests: build_reasoning_breadcrumb
# ---------------------------------------------------------------------------


class TestBuildReasoningBreadcrumb:
    def test_thinking_breadcrumb(self) -> None:
        bc = build_reasoning_breadcrumb(
            _thinking_block("Let me analyze"),
            session_id=_SESSION_ID,
        )
        assert bc is not None
        assert bc.event_type == "agent.reasoning.plan_drafted"
        assert bc.subtype == "plan_drafted"
        assert bc.text == "Let me analyze"
        assert not bc.suppressed
        assert bc.raw_length == len("Let me analyze")

    def test_tool_call_rationale_with_tool_name(self) -> None:
        next_block = _tool_use_block("Write")
        bc = build_reasoning_breadcrumb(
            _text_block("I'll write the file"),
            session_id=_SESSION_ID,
            next_block_type="tool_use",
            next_block=next_block,
        )
        assert bc is not None
        assert bc.subtype == "tool_call_rationale"
        assert bc.tool_name == "Write"

    def test_step_summary_after_tool_result(self) -> None:
        bc = build_reasoning_breadcrumb(
            _text_block("Tests passed"),
            session_id=_SESSION_ID,
            prev_block_type="tool_result",
        )
        assert bc is not None
        assert bc.subtype == "step_summary"

    def test_empty_text_returns_none(self) -> None:
        bc = build_reasoning_breadcrumb(
            _text_block("   "),
            session_id=_SESSION_ID,
        )
        assert bc is None

    def test_tool_use_block_returns_none(self) -> None:
        bc = build_reasoning_breadcrumb(
            _tool_use_block(),
            session_id=_SESSION_ID,
        )
        assert bc is None

    def test_secret_suppresses_text(self) -> None:
        bc = build_reasoning_breadcrumb(
            _thinking_block("Key is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
            session_id=_SESSION_ID,
        )
        assert bc is not None
        assert bc.suppressed
        assert bc.text == ""
        assert bc.raw_length > 0

    def test_no_tool_name_when_not_rationale(self) -> None:
        bc = build_reasoning_breadcrumb(
            _text_block("Starting work"),
            session_id=_SESSION_ID,
        )
        assert bc is not None
        assert bc.tool_name is None


# ---------------------------------------------------------------------------
# Tests: extract_reasoning_from_content
# ---------------------------------------------------------------------------


class TestExtractReasoningFromContent:
    def test_thinking_plus_text_plus_tool_use(self) -> None:
        content = [
            _thinking_block("Let me analyze"),
            _text_block("I'll write a file"),
            _tool_use_block("Write"),
        ]
        breadcrumbs = extract_reasoning_from_content(content, _SESSION_ID)
        assert len(breadcrumbs) == 2
        assert breadcrumbs[0].subtype == "plan_drafted"
        assert breadcrumbs[1].subtype == "tool_call_rationale"
        assert breadcrumbs[1].tool_name == "Write"

    def test_step_summary_after_tool_result(self) -> None:
        content = [
            _tool_use_block("Bash"),
            _tool_result_block(),
            _text_block("Good, that worked"),
        ]
        breadcrumbs = extract_reasoning_from_content(content, _SESSION_ID)
        assert len(breadcrumbs) == 1
        assert breadcrumbs[0].subtype == "step_summary"

    def test_multi_turn_message(self) -> None:
        content = [
            _thinking_block("Analyzing requirements"),
            _text_block("I'll edit the config"),
            _tool_use_block("Edit"),
            _tool_result_block(),
            _text_block("Now I'll run tests"),
            _tool_use_block("Bash", {"command": "pytest"}),
            _tool_result_block(),
            _text_block("All green"),
        ]
        breadcrumbs = extract_reasoning_from_content(content, _SESSION_ID)
        assert len(breadcrumbs) == 4
        assert breadcrumbs[0].subtype == "plan_drafted"  # thinking
        assert breadcrumbs[1].subtype == "tool_call_rationale"  # text before Edit
        assert breadcrumbs[2].subtype == "tool_call_rationale"  # text before Bash
        assert breadcrumbs[3].subtype == "step_summary"  # text after tool_result

    def test_empty_content(self) -> None:
        assert extract_reasoning_from_content([], _SESSION_ID) == []

    def test_non_reasoning_dict_blocks_skipped(self) -> None:
        content: list[dict[str, Any]] = [_tool_result_block(), _tool_use_block()]
        assert extract_reasoning_from_content(content, _SESSION_ID) == []

    def test_only_tool_use_no_reasoning(self) -> None:
        content = [_tool_use_block(), _tool_use_block("Bash")]
        assert extract_reasoning_from_content(content, _SESSION_ID) == []

    def test_empty_text_blocks_skipped(self) -> None:
        content = [_text_block(""), _text_block("   ")]
        assert extract_reasoning_from_content(content, _SESSION_ID) == []


# ---------------------------------------------------------------------------
# Tests: NFR — domain layer has zero IO imports
# ---------------------------------------------------------------------------


class TestDomainNoIO:
    def test_no_asyncio_import(self) -> None:
        import worker_wrapper.domain.reasoning as mod

        source = inspect.getsource(mod)
        assert "import asyncio" not in source
        assert "import structlog" not in source
        assert "import aiohttp" not in source
        assert "import os" not in source

    def test_no_io_dependencies(self) -> None:
        import worker_wrapper.domain.reasoning as mod

        for _name, attr in inspect.getmembers(mod):
            if inspect.isfunction(attr) or inspect.isclass(attr):
                src = inspect.getsource(attr)
                assert "asyncio" not in src


# ---------------------------------------------------------------------------
# Tests: schema registry — agent.reasoning.* types registered
# ---------------------------------------------------------------------------


class TestSchemaRegistry:
    def test_agent_reasoning_types_registered(self) -> None:
        import importlib

        import registry_state.domain.event_types as et_mod
        importlib.reload(et_mod)
        from events.schema_registry import REGISTRY

        keys = set(REGISTRY.keys())
        assert ("agent.reasoning.plan_drafted", "1.0.0") in keys
        assert ("agent.reasoning.tool_call_rationale", "1.0.0") in keys
        assert ("agent.reasoning.step_summary", "1.0.0") in keys

    def test_payload_model_valid(self) -> None:
        from events.payloads import AgentReasoningBreadcrumbPayload

        payload = AgentReasoningBreadcrumbPayload(
            session_id=_SESSION_ID,
            subtype="plan_drafted",
            text="I'll implement the feature",
            raw_length=27,
        )
        assert payload.subtype == "plan_drafted"
        assert not payload.suppressed
        assert payload.tool_name is None

    def test_payload_suppressed_valid(self) -> None:
        from events.payloads import AgentReasoningBreadcrumbPayload

        payload = AgentReasoningBreadcrumbPayload(
            session_id=_SESSION_ID,
            subtype="tool_call_rationale",
            text="",
            suppressed=True,
            tool_name="Write",
            raw_length=50,
        )
        assert payload.suppressed
        assert payload.tool_name == "Write"
