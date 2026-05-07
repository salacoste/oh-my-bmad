"""Reasoning breadcrumb extraction + secret sanitization (Story 5.5).

Domain module — pure event-shaping logic with zero IO dependencies.
Extracts reasoning content from Claude Code SDK content blocks (``thinking``,
``text``), classifies into ``agent.reasoning.*`` subtypes, and sanitizes text
through the secret-hygiene scanner before emission.

This module is called by ``adapters/claude_code_runner.py`` which owns the
subprocess boundary.  Event emission via clawhip-bridge is deferred to the
future task-execution driver (Story 5.12).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from secret_hygiene.scanner import scan_text

logger = logging.getLogger(__name__)

# Reasoning subtypes — match the event namespace registered in schema registry.
ReasoningSubtype = Literal["plan_drafted", "tool_call_rationale", "step_summary"]

# Maximum reasoning text length to extract (prevents unbounded payloads).
_MAX_REASONING_LEN: int = 50_000


@dataclass
class ReasoningBreadcrumb:
    """A reasoning breadcrumb extracted from an assistant message content block.

    Parallel to ``ExtractedEvent`` from the runner adapter — this captures
    *why* the agent acted, not *what* it did.
    """

    event_type: str  # "agent.reasoning.plan_drafted" etc.
    subtype: ReasoningSubtype
    text: str  # sanitized — may be empty if suppressed
    suppressed: bool = False
    tool_name: str | None = None
    raw_length: int = 0


def sanitize_reasoning_text(text: str) -> tuple[str, bool]:
    """Sanitize reasoning text through the secret-hygiene scanner.

    Returns (sanitized_text, was_suppressed).  If any secret pattern is
    detected the entire text is suppressed (replaced with empty string)
    because partial redaction of reasoning text could leave enough context
    to reconstruct the secret.
    """
    if not text:
        return "", False
    matches = scan_text(text)
    if not matches:
        return text[:_MAX_REASONING_LEN], False
    return "", True


def classify_reasoning_block(
    block: dict[str, Any],
    prev_block_type: str | None = None,
    next_block_type: str | None = None,
) -> ReasoningSubtype | None:
    """Determine the reasoning subtype for a content block.

    Classification rules:
    - ``thinking`` block → ``plan_drafted`` (always)
    - ``text`` block before a ``tool_use`` → ``plan_drafted``
    - ``text`` block immediately before ``tool_use`` with rationale → ``tool_call_rationale``
    - ``text`` block after a ``tool_result`` → ``step_summary``
    - Other text blocks → ``plan_drafted`` (default)
    """
    block_type = block.get("type")

    if block_type == "thinking":
        return "plan_drafted"

    if block_type == "text":
        # Text immediately before a tool_use is a tool-call rationale.
        if next_block_type == "tool_use":
            return "tool_call_rationale"
        # Text after a tool_result is a step summary.
        if prev_block_type == "tool_result":
            return "step_summary"
        # Default: planning rationale.
        return "plan_drafted"

    return None


def extract_reasoning_text(block: dict[str, Any]) -> str:
    """Extract raw reasoning text from a content block."""
    block_type = block.get("type")
    if block_type == "thinking":
        return str(block.get("thinking", ""))
    if block_type == "text":
        return str(block.get("text", ""))
    return ""


def build_reasoning_breadcrumb(
    block: dict[str, Any],
    session_id: str,
    prev_block_type: str | None = None,
    next_block_type: str | None = None,
    next_block: dict[str, Any] | None = None,
) -> ReasoningBreadcrumb | None:
    """Build a sanitized reasoning breadcrumb from a content block.

    Orchestrates classify + sanitize + shape.  Returns ``None`` if the block
    is not a reasoning source (not ``thinking`` or ``text``) or if the text
    is empty after stripping.
    """
    raw_text = extract_reasoning_text(block).strip()
    if not raw_text:
        return None

    subtype = classify_reasoning_block(block, prev_block_type, next_block_type)
    if subtype is None:
        return None

    sanitized, suppressed = sanitize_reasoning_text(raw_text)

    # Determine tool_name for tool_call_rationale subtype.
    tool_name: str | None = None
    if subtype == "tool_call_rationale" and next_block is not None:
        tool_name = next_block.get("name")

    event_type = f"agent.reasoning.{subtype}"

    return ReasoningBreadcrumb(
        event_type=event_type,
        subtype=subtype,
        text=sanitized,
        suppressed=suppressed,
        tool_name=tool_name,
        raw_length=len(raw_text),
    )


def extract_reasoning_from_content(
    content: list[dict[str, Any]],
    session_id: str,
) -> list[ReasoningBreadcrumb]:
    """Extract all reasoning breadcrumbs from an assistant message content list.

    Scans content blocks in order, using block-type context to classify each
    reasoning fragment.  Returns breadcrumbs in message order.
    """
    breadcrumbs: list[ReasoningBreadcrumb] = []

    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")
        if block_type not in ("thinking", "text"):
            continue

        prev_block_type = content[i - 1].get("type") if i > 0 else None
        next_block_type = content[i + 1].get("type") if i < len(content) - 1 else None
        next_block = content[i + 1] if i < len(content) - 1 else None

        breadcrumb = build_reasoning_breadcrumb(
            block=block,
            session_id=session_id,
            prev_block_type=prev_block_type,
            next_block_type=next_block_type,
            next_block=next_block,
        )
        if breadcrumb is not None:
            breadcrumbs.append(breadcrumb)

    return breadcrumbs


__all__ = [
    "ReasoningBreadcrumb",
    "ReasoningSubtype",
    "build_reasoning_breadcrumb",
    "classify_reasoning_block",
    "extract_reasoning_from_content",
    "extract_reasoning_text",
    "sanitize_reasoning_text",
]
