"""Anthropic API adapter for LLM-powered task event digests (Story 7.3 / FR5).

Calls ``anthropic.AsyncAnthropic.messages.create()`` with a bounded prompt over
the task's recent events and returns a human-readable digest. Falls back to a
raw-event formatted summary on any Anthropic API error — the endpoint NEVER
returns a 500 due to LLM failures.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import anthropic

_log = logging.getLogger("registry_api.adapters.llm_digest")

# Rough token budget: ~4 chars per token. 4000 input tokens ≈ 16 000 chars.
_MAX_INPUT_CHARS = 16_000
# Cap at ~50 events; if more exist, truncate from the oldest.
_MAX_EVENTS = 50

_SYSTEM_PROMPT = (
    "You are a concise task-status summarizer for a software engineering agent platform. "
    "Given a chronological list of task events (newest first), produce a summary of AT MOST "
    "20 lines. Focus on: key state transitions, blockers encountered, the agent's last "
    "decision or action, and current status. Use short timestamps (HH:MM). "
    "Do NOT include any preamble or header — start directly with the summary."
)

_MAX_OUTPUT_TOKENS = 1024


def _get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "") or "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class EventRow:
    """Lightweight event representation passed to the adapter.

    Avoids coupling to the ORM ``Event`` model — the route handler maps
    from ORM rows to this plain dataclass.
    """

    type: str
    emitted_at_iso: str  # ISO-8601 string from datetime
    payload_json: str


def _format_event(ev: EventRow) -> str:
    """Format a single event as ``[HH:MM] type: summary_or_excerpt``."""
    hhmm = ev.emitted_at_iso[11:16] if len(ev.emitted_at_iso) >= 16 else "invalid-timestamp"

    summary = ""
    try:
        data: Any = json.loads(ev.payload_json)
        if isinstance(data, dict):
            summary = data.get("reason") or data.get("description") or data.get("summary") or ""
    except (json.JSONDecodeError, AttributeError):
        pass

    if not summary:
        # Truncate raw JSON as a last resort.
        summary = ev.payload_json[:120]

    return f"[{hhmm}] {ev.type}: {summary[:200]}"


def _build_fallback_digest(formatted_events: list[str], truncated: bool) -> str:
    """Build a raw-event fallback digest when the LLM is unavailable.

    Receives pre-formatted event lines (already truncated to budget) rather
    than raw EventRow objects, ensuring consistency with the LLM code path.
    """
    lines = ["(LLM unavailable — raw event summary)"]
    # Show at most 19 formatted events so total stays <= 20 lines.
    for line in formatted_events[:19]:
        lines.append(line)
    if truncated or len(formatted_events) > 19:
        lines.append("… (truncated)")
    return "\n".join(lines)


async def summarize_events(
    events: list[EventRow],
    *,
    client: anthropic.AsyncAnthropic | None,
) -> tuple[str, bool]:
    """Summarize events into a human-readable digest.

    Args:
        events: Task events ordered newest-first (from the DB query).
        client: Anthropic async client, or ``None`` if no API key configured.

    Returns:
        ``(digest_text, truncated_flag)`` — *truncated* is ``True`` when
        older events were dropped to fit the token budget.
    """
    if not events:
        return ("No events to summarize.", False)

    # Determine truncation: cap events and total chars.
    truncated = len(events) > _MAX_EVENTS
    working = events[:_MAX_EVENTS]

    # Build the event context, respecting the char budget.
    formatted: list[str] = []
    total_chars = 0
    for ev in working:
        line = _format_event(ev)
        if total_chars + len(line) + 1 > _MAX_INPUT_CHARS:
            truncated = True
            break
        formatted.append(line)
        total_chars += len(line) + 1

    event_context = "\n".join(formatted)

    # No client → fallback digest.
    if client is None:
        return (_build_fallback_digest(formatted, truncated), truncated)

    user_message = (
        f"Summarize these task events (newest first). "
        f"{'(Older events were truncated.) ' if truncated else ''}"
        f"Produce at most 20 lines.\n\n{event_context}"
    )

    try:
        response = await client.messages.create(
            model=_get_model(),
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        # Extract text from the response content blocks.
        text_parts = [block.text for block in response.content if block.type == "text"]
        digest = "\n".join(text_parts).strip()
        if not digest:
            return (_build_fallback_digest(formatted, truncated), truncated)
        return (digest, truncated)
    except Exception:
        _log.warning(
            "Anthropic call failed; returning fallback digest",
            exc_info=True,
        )
        return (_build_fallback_digest(formatted, truncated), truncated)


__all__ = ["EventRow", "summarize_events"]
