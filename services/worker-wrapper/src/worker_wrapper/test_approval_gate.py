"""Unit tests for approval_gate.needs_approval (Story 6.7, AC-2)."""

from __future__ import annotations

from worker_wrapper.adapters.claude_code_runner import ExtractedEvent
from worker_wrapper.domain.approval_gate import needs_approval


class TestNeedsApproval:
    """Test the pure domain approval-gate detection function."""

    def test_returns_none_on_empty_list(self) -> None:
        assert needs_approval([]) is None

    def test_returns_none_when_no_git_push(self) -> None:
        events = [
            ExtractedEvent(event_type="file.edited", tool_name="Write", tool_input={}),
            ExtractedEvent(event_type="test.run", tool_name="Bash", tool_input={}),
            ExtractedEvent(event_type="commit.created", tool_name="Bash", tool_input={}),
        ]
        assert needs_approval(events) is None

    def test_returns_git_push_event(self) -> None:
        push_event = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
        )
        events = [
            ExtractedEvent(event_type="file.edited", tool_name="Write", tool_input={}),
            push_event,
        ]
        result = needs_approval(events)
        assert result is push_event

    def test_returns_first_git_push_when_multiple(self) -> None:
        first = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push origin feature"},
        )
        second = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
        )
        events = [first, second]
        result = needs_approval(events)
        assert result is first

    def test_ignores_non_push_bash_commands(self) -> None:
        events = [
            ExtractedEvent(
                event_type="test.run",
                tool_name="Bash",
                tool_input={"command": "pytest"},
            ),
            ExtractedEvent(
                event_type="commit.created",
                tool_name="Bash",
                tool_input={"command": "git commit -m 'x'"},
            ),
        ]
        assert needs_approval(events) is None
