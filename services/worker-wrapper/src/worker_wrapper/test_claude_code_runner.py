"""Tests for Claude Code subprocess supervision + event extraction (Story 5.4).

Covers: subprocess spawn args, stream-json parsing, tool_use -> event mapping,
error exit handling, max-turns handling, timeout enforcement, graceful
shutdown, NFR-O1 compliance, spawn failure, None-safe result building,
output-format validation, and cancel cleanup.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_wrapper.adapters.claude_code_runner import (
    _COMMIT_PATTERN,
    _TEST_PATTERN,
    ClaudeCodeRunner,
    ExtractedEvent,
)
from worker_wrapper.app.config import WorkerSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> WorkerSettings:
    """Build a WorkerSettings with sensible test defaults."""
    defaults: dict[str, Any] = {
        "anthropic_api_key": "sk-test-key-123",
        "claude_command": "claude",
        "claude_max_turns": 0,
        "claude_timeout_s": 60.0,
        "claude_output_format": "stream-json",
    }
    defaults.update(overrides)
    return WorkerSettings(**defaults)


def _make_msg(
    msg_type: str = "assistant",
    **extra: Any,
) -> dict[str, Any]:
    """Build a minimal SDK message dict."""
    base: dict[str, Any] = {"type": msg_type, "session_id": "s-test-001"}
    base.update(extra)
    return base


def _tool_use_block(
    name: str,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a tool_use content block."""
    return {
        "type": "tool_use",
        "id": "toolu_01",
        "name": name,
        "input": input_data or {},
    }


def _text_block(text: str = "I'll edit the file.") -> dict[str, Any]:
    """Build a text content block."""
    return {"type": "text", "text": text}


async def _mock_process(
    stdout_lines: list[bytes] | None = None,
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = 42
    proc.returncode = None

    # Build a mock stdout that yields lines then closes.
    if stdout_lines is not None:

        async def _stdout_iter() -> Any:
            for line in stdout_lines:
                yield line

        proc.stdout = _stdout_iter()
    else:
        proc.stdout = None

    # Mock stderr read.
    stderr_reader = AsyncMock(return_value=stderr)
    proc.stderr = MagicMock()
    proc.stderr.read = stderr_reader

    # After await process.wait(), set returncode.
    async def _wait_and_set() -> None:
        proc.returncode = returncode
        return None

    proc.wait = _wait_and_set
    proc.terminate = MagicMock()
    proc.kill = MagicMock()

    return proc


# ---------------------------------------------------------------------------
# Tests: _build_args
# ---------------------------------------------------------------------------


class TestBuildArgs:
    def test_default_args(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        args = runner._build_args("do something")
        assert args == [
            "-p",
            "do something",
            "--output-format",
            "stream-json",
        ]

    def test_max_turns_included(self) -> None:
        runner = ClaudeCodeRunner(_settings(claude_max_turns=10))
        args = runner._build_args("do something")
        assert "--max-turns" in args
        assert "10" in args

    def test_max_turns_zero_excluded(self) -> None:
        runner = ClaudeCodeRunner(_settings(claude_max_turns=0))
        args = runner._build_args("do something")
        assert "--max-turns" not in args


# ---------------------------------------------------------------------------
# Tests: _classify_tool_use (event extraction)
# ---------------------------------------------------------------------------


class TestClassifyToolUse:
    def test_write_maps_to_file_edited(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Write",
            {"file_path": "/tmp/x.py", "content": "pass"},
        )
        assert event is not None
        assert event.event_type == "file.edited"
        assert event.tool_name == "Write"

    def test_edit_maps_to_file_edited(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Edit",
            {"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"},
        )
        assert event is not None
        assert event.event_type == "file.edited"
        assert event.tool_name == "Edit"

    def test_bash_pytest_maps_to_test_run(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Bash",
            {"command": "pytest tests/ -x"},
        )
        assert event is not None
        assert event.event_type == "test.run"

    def test_bash_cargo_test_maps_to_test_run(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Bash",
            {"command": "cargo test"},
        )
        assert event is not None
        assert event.event_type == "test.run"

    def test_bash_git_commit_maps_to_commit_created(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Bash",
            {"command": "git commit -m 'feat: add thing'"},
        )
        assert event is not None
        assert event.event_type == "commit.created"

    def test_bash_other_returns_none(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Bash",
            {"command": "ls -la"},
        )
        assert event is None

    def test_unknown_tool_returns_none(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use(
            "Read",
            {"file_path": "/tmp/x.py"},
        )
        assert event is None

    def test_bash_command_not_string(self) -> None:
        event = ClaudeCodeRunner._classify_tool_use("Bash", {"command": 42})
        assert event is None


# ---------------------------------------------------------------------------
# Tests: _extract_events from full assistant message
# ---------------------------------------------------------------------------


class TestExtractEvents:
    def test_extracts_from_assistant_message(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        msg = _make_msg(
            "assistant",
            message={
                "content": [
                    _text_block("I'll write a file."),
                    _tool_use_block("Write", {"file_path": "/tmp/x.py", "content": "pass"}),
                    _tool_use_block("Bash", {"command": "pytest tests/"}),
                ],
            },
        )
        runner._extract_events(msg)
        assert len(runner._events) == 2
        assert runner._events[0].event_type == "file.edited"
        assert runner._events[1].event_type == "test.run"

    def test_ignores_text_only_message(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        msg = _make_msg(
            "assistant",
            message={"content": [_text_block()]},
        )
        runner._extract_events(msg)
        assert len(runner._events) == 0

    def test_handles_non_list_content(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        msg = _make_msg("assistant", message={"content": "oops"})
        runner._extract_events(msg)
        assert len(runner._events) == 0

    def test_handles_missing_content(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        msg = _make_msg("assistant", message={})
        runner._extract_events(msg)
        assert len(runner._events) == 0


# ---------------------------------------------------------------------------
# Tests: _handle_message
# ---------------------------------------------------------------------------


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_system_message_extracts_session_id(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        await runner._handle_message(
            {"type": "system", "subtype": "init", "session_id": "s-abc123"},
        )
        assert runner._session_id == "s-abc123"

    @pytest.mark.asyncio
    async def test_result_message_stored(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        result_msg = {
            "type": "result",
            "subtype": "success",
            "cost_usd": 0.003,
            "duration_ms": 1234,
            "num_turns": 6,
            "session_id": "s-abc123",
        }
        await runner._handle_message(result_msg)
        assert runner._result_msg == result_msg

    @pytest.mark.asyncio
    async def test_unknown_type_no_crash(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        await runner._handle_message({"type": "user", "message": {}})
        assert runner._session_id == ""
        assert runner._events == []


# ---------------------------------------------------------------------------
# Tests: _build_result
# ---------------------------------------------------------------------------


class TestBuildResult:
    def test_success_result(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        runner._session_id = "s-abc"
        runner._result_msg = {
            "type": "result",
            "subtype": "success",
            "cost_usd": 0.005,
            "duration_ms": 2000,
            "num_turns": 3,
        }
        runner._events = [
            ExtractedEvent(event_type="file.edited", tool_name="Write"),
        ]
        result = runner._build_result(0, "")
        assert result.exit_code == 0
        assert result.session_id == "s-abc"
        assert result.cost_usd == 0.005
        assert result.duration_ms == 2000
        assert result.num_turns == 3
        assert result.error is None
        assert len(result.events) == 1

    def test_max_turns_error(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        runner._result_msg = {"type": "result", "subtype": "error_max_turns"}
        result = runner._build_result(0, "")
        assert result.error == "Max turns reached"

    def test_is_error_flag(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        runner._result_msg = {
            "type": "result",
            "is_error": True,
            "result": "Something went wrong",
        }
        result = runner._build_result(1, "")
        assert result.error == "Something went wrong"

    def test_null_fields_coalesced(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        runner._result_msg = {
            "type": "result",
            "cost_usd": None,
            "duration_ms": None,
            "num_turns": None,
        }
        result = runner._build_result(0, "")
        assert result.cost_usd == 0.0
        assert result.duration_ms == 0
        assert result.num_turns == 0

    def test_stderr_stored(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        result = runner._build_result(1, "some error output")
        assert result.stderr == "some error output"


# ---------------------------------------------------------------------------
# Tests: full run() with mocked subprocess
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_run(self) -> None:
        settings = _settings()
        runner = ClaudeCodeRunner(settings)
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s-001"}).encode(),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            _tool_use_block("Write", {"file_path": "/tmp/a.py"}),
                        ],
                    },
                    "session_id": "s-001",
                }
            ).encode(),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "cost_usd": 0.01,
                    "duration_ms": 500,
                    "num_turns": 2,
                    "session_id": "s-001",
                }
            ).encode(),
        ]
        proc = await _mock_process(stdout_lines=lines, returncode=0)

        with patch.object(runner, "_spawn", return_value=proc):
            result = await runner.run("implement feature X", Path("/worktree"))

        assert result.exit_code == 0
        assert result.session_id == "s-001"
        assert result.cost_usd == 0.01
        assert result.duration_ms == 500
        assert result.num_turns == 2
        assert result.error is None
        assert len(result.events) == 1
        assert result.events[0].event_type == "file.edited"

    @pytest.mark.asyncio
    async def test_error_exit_code(self) -> None:
        settings = _settings()
        runner = ClaudeCodeRunner(settings)
        proc = await _mock_process(
            stdout_lines=[],
            stderr=b"command not found: claude",
            returncode=127,
        )

        with patch.object(runner, "_spawn", return_value=proc):
            result = await runner.run("do something", Path("/worktree"))

        assert result.exit_code == 127
        assert result.error is not None
        assert "127" in result.error
        assert "command not found" in result.error

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self) -> None:
        settings = _settings(claude_timeout_s=0.01)

        runner = ClaudeCodeRunner(settings)

        async def _hang_forever(proc: Any) -> None:
            await asyncio.sleep(100)

        proc = await _mock_process(stdout_lines=[], returncode=0)

        with (
            patch.object(runner, "_spawn", return_value=proc),
            patch.object(runner, "_read_stream", side_effect=_hang_forever),
        ):
            result = await runner.run("do something", Path("/worktree"))

        assert result.exit_code == -1
        assert result.error is not None
        assert "Timed out" in result.error

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = None
        proc.pid = 99

        killed = False

        async def _wait() -> None:
            if not killed:
                raise TimeoutError()
            proc.returncode = -9

        def _kill() -> None:
            nonlocal killed
            killed = True

        proc.terminate = MagicMock()
        proc.wait = _wait
        proc.kill = MagicMock(side_effect=_kill)

        await runner._shutdown_process(proc)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_already_dead(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = 0

        await runner._shutdown_process(proc)

        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_turns_result(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        lines = [
            json.dumps({"type": "system", "session_id": "s-002"}).encode(),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "cost_usd": 0.1,
                    "duration_ms": 10000,
                    "num_turns": 50,
                    "session_id": "s-002",
                }
            ).encode(),
        ]
        proc = await _mock_process(stdout_lines=lines, returncode=0)

        with patch.object(runner, "_spawn", return_value=proc):
            result = await runner.run("complex task", Path("/worktree"))

        assert result.error == "Max turns reached"
        assert result.num_turns == 50

    @pytest.mark.asyncio
    async def test_malformed_json_line_skipped(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        lines = [
            b"not json at all\n",
            json.dumps({"type": "system", "session_id": "s-003"}).encode(),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "s-003",
                }
            ).encode(),
        ]
        proc = await _mock_process(stdout_lines=lines, returncode=0)

        with patch.object(runner, "_spawn", return_value=proc):
            result = await runner.run("task", Path("/worktree"))

        assert result.exit_code == 0
        assert result.session_id == "s-003"

    @pytest.mark.asyncio
    async def test_cancel_forwards_terminate(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = None
        proc.pid = 77
        proc.wait = AsyncMock(return_value=None)
        proc.terminate = MagicMock()

        runner._process = proc
        await runner.cancel()
        proc.terminate.assert_called_once()
        assert runner._process is None

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_structured_result(self) -> None:
        runner = ClaudeCodeRunner(_settings())

        async def _raise_os_error(*args: Any, **kwargs: Any) -> Any:
            raise FileNotFoundError("claude not found")

        with patch.object(runner, "_spawn", side_effect=_raise_os_error):
            result = await runner.run("do something", Path("/worktree"))

        assert result.exit_code == -1
        assert result.error is not None
        assert "Failed to spawn claude" in result.error
        assert "claude not found" in result.error

    @pytest.mark.asyncio
    async def test_cancelled_error_shuts_down_process(self) -> None:
        runner = ClaudeCodeRunner(_settings())
        proc = await _mock_process(stdout_lines=[], returncode=0)
        proc.wait = AsyncMock(return_value=None)
        proc.terminate = MagicMock()

        async def _raise_cancelled(proc: Any) -> None:
            raise asyncio.CancelledError()

        with (
            patch.object(runner, "_spawn", return_value=proc),
            patch.object(runner, "_run_with_process", side_effect=_raise_cancelled),
            pytest.raises(asyncio.CancelledError),
        ):
            await runner.run("do something", Path("/worktree"))

        # Process should have been cleaned up.
        assert runner._process is None


# ---------------------------------------------------------------------------
# Tests: output-format validation
# ---------------------------------------------------------------------------


class TestOutputFormatValidation:
    def test_unsupported_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported claude_output_format"):
            ClaudeCodeRunner(_settings(claude_output_format="json"))

    def test_stream_json_accepted(self) -> None:
        runner = ClaudeCodeRunner(_settings(claude_output_format="stream-json"))
        assert runner is not None


# ---------------------------------------------------------------------------
# Tests: NFR-O1 compliance -- no stdout-parsing patterns
# ---------------------------------------------------------------------------


class TestNfrO1Compliance:
    def test_no_decode_parse_pattern(self) -> None:
        """Verify no ``subprocess.check_output().decode()`` pattern."""
        import inspect

        from worker_wrapper.adapters import claude_code_runner as mod

        source = inspect.getsource(mod)
        forbidden = [
            "check_output",
            "re.search(",
            "re.match(",
            "re.findall(",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"NFR-O1 violation: found '{pattern}' in claude_code_runner.py"
            )

    def test_json_loads_used(self) -> None:
        """Verify structured JSON deserialization is used."""
        import inspect

        from worker_wrapper.adapters import claude_code_runner as mod

        source = inspect.getsource(mod)
        assert "json.loads" in source


# ---------------------------------------------------------------------------
# Tests: config field defaults
# ---------------------------------------------------------------------------


class TestConfigFields:
    def test_claude_command_default(self) -> None:
        s = WorkerSettings()
        assert s.claude_command == "claude"

    def test_claude_max_turns_default(self) -> None:
        s = WorkerSettings()
        assert s.claude_max_turns == 0

    def test_claude_timeout_s_default(self) -> None:
        s = WorkerSettings()
        assert s.claude_timeout_s == 600.0

    def test_claude_output_format_default(self) -> None:
        s = WorkerSettings()
        assert s.claude_output_format == "stream-json"

    def test_anthropic_api_key_default(self) -> None:
        s = WorkerSettings()
        assert s.anthropic_api_key == ""

    def test_env_override_claude_command(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"WORKER_CLAUDE_COMMAND": "/usr/local/bin/claude"}):
            s = WorkerSettings()
            assert s.claude_command == "/usr/local/bin/claude"

    def test_env_override_anthropic_api_key(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"WORKER_ANTHROPIC_API_KEY": "sk-real-key"}):
            s = WorkerSettings()
            assert s.anthropic_api_key == "sk-real-key"


# ---------------------------------------------------------------------------
# Tests: regex patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def test_test_pattern_matches_pytest(self) -> None:
        assert _TEST_PATTERN.search("pytest tests/ -x")

    def test_test_pattern_matches_npm_test(self) -> None:
        assert _TEST_PATTERN.search("npm test")

    def test_test_pattern_matches_cargo_test(self) -> None:
        assert _TEST_PATTERN.search("cargo test --all")

    def test_test_pattern_no_false_positive(self) -> None:
        assert _TEST_PATTERN.search("echo 'contests'") is None

    def test_commit_pattern_matches_git_commit(self) -> None:
        assert _COMMIT_PATTERN.match("git commit -m 'feat: x'")

    def test_commit_pattern_no_false_positive(self) -> None:
        assert _COMMIT_PATTERN.match("git log") is None
