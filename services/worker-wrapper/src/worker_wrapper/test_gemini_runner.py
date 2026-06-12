"""Focused GeminiRunner unit tests for mutation hardening."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from worker_wrapper.adapters.gemini_runner import GeminiRunner
from worker_wrapper.app.config import WorkerSettings


class _AsyncBytes:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> _AsyncBytes:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _ProcessWithStdout:
    def __init__(self, lines: list[bytes] | None) -> None:
        self.stdout = _AsyncBytes(lines) if lines is not None else None


class TestGeminiRunnerCommandAndSpawn:
    def test_build_args_uses_json_run_mode(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        assert runner._build_args("hello") == ["run", "--json", "hello"]

    def test_health_check_command_uses_configured_command(self) -> None:
        runner = GeminiRunner(WorkerSettings(gemini_command="/opt/bin/gemini"))

        assert runner._health_check_command() == "/opt/bin/gemini"

    def test_health_check_command_blank_falls_back_to_empty_string(self) -> None:
        runner = GeminiRunner(WorkerSettings(gemini_command=""))

        assert runner._health_check_command() == ""

    @pytest.mark.asyncio
    async def test_spawn_injects_settings_key_trace_and_args(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-parent-should-not-leak")
        monkeypatch.setenv("GEMINI_CONFIG_DIR", "/tmp/gemini-config")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-canary")
        settings = WorkerSettings(
            gemini_command="gemini-test",
            google_api_key="AIza-settings-key",
        )
        runner = GeminiRunner(settings)
        captured: dict[str, Any] = {}

        async def _fake_exec(*args: Any, **kwargs: Any) -> Any:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return AsyncMock(spec=asyncio.subprocess.Process)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await runner._spawn("do work", tmp_path)

        assert captured["args"] == ("gemini-test", "run", "--json", "do work")
        assert captured["kwargs"]["cwd"] == str(tmp_path)
        assert captured["kwargs"]["stdout"] == asyncio.subprocess.PIPE
        assert captured["kwargs"]["stderr"] == asyncio.subprocess.PIPE
        env = captured["kwargs"]["env"]
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["GEMINI_CONFIG_DIR"] == "/tmp/gemini-config"
        assert env["GEMINI_API_KEY"] == "AIza-settings-key"
        assert "OMB_TRACE_ID" in env
        assert "OPENAI_API_KEY" not in env


class TestGeminiRunnerParsing:
    @pytest.mark.parametrize(
        ("tool_name", "expected"),
        [
            ("write", "file.edited"),
            ("edit", "file.edited"),
            ("create_file", "file.edited"),
            ("apply_edit", "file.edited"),
            ("bash", "test.run"),
            ("shell", "test.run"),
            ("run_command", "test.run"),
        ],
    )
    def test_classify_supported_tools(self, tool_name: str, expected: str) -> None:
        command = "pytest tests" if expected == "test.run" else ""

        event = GeminiRunner._classify_tool_use(tool_name, {"command": command})

        assert event is not None
        assert event.event_type == expected
        assert event.tool_name == tool_name

    def test_classify_shell_git_push_takes_priority(self) -> None:
        event = GeminiRunner._classify_tool_use(
            "bash",
            {"command": "pytest && git push origin main"},
        )

        assert event is not None
        assert event.event_type == "git.push"

    def test_classify_shell_commit_or_add(self) -> None:
        event = GeminiRunner._classify_tool_use(
            "shell",
            {"command": "git add . && pytest"},
        )

        assert event is not None
        assert event.event_type == "commit.created"

    def test_classify_ignores_unknown_and_non_string_commands(self) -> None:
        assert GeminiRunner._classify_tool_use("unknown", {}) is None
        assert GeminiRunner._classify_tool_use("bash", {"command": ["pytest"]}) is None

    def test_extract_usage_prefers_gemini_usage_metadata(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        runner._extract_usage(
            {
                "usageMetadata": {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 7,
                },
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

        assert runner._input_tokens == 11
        assert runner._output_tokens == 7

    def test_extract_usage_falls_back_to_codex_style_usage(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        runner._extract_usage({"usage": {"input_tokens": 100, "output_tokens": 50}})

        assert runner._input_tokens == 100
        assert runner._output_tokens == 50

    def test_extract_events_skips_invalid_shapes_and_normalizes_input(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        runner._extract_events(
            {
                "tool_calls": [
                    "not-a-dict",
                    {"name": "write", "input": "not-a-dict"},
                    {"name": "bash", "input": {"command": "npm test"}},
                ]
            }
        )

        assert [event.event_type for event in runner._events] == [
            "file.edited",
            "test.run",
        ]
        assert runner._events[0].tool_input == {}

    def test_extract_events_ignores_non_list_tool_calls(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        runner._extract_events({"tool_calls": {"name": "write"}})

        assert runner._events == []

    def test_handle_message_updates_session_and_turn_state(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        runner._handle_message({"type": "session.created", "session_id": "s-123"})
        runner._handle_message(
            {
                "type": "turn.completed",
                "tool_calls": [{"name": "bash", "input": {"command": "go test ./..."}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
            }
        )
        runner._handle_message({"type": "message", "content": "ignored"})

        assert runner._session_id == "s-123"
        assert runner._num_turns == 1
        assert runner._input_tokens == 3
        assert runner._output_tokens == 4
        assert [event.event_type for event in runner._events] == ["test.run"]

    @pytest.mark.parametrize(
        ("exit_code", "stderr", "expected_error"),
        [
            (0, "", None),
            (1, "task failed", "task failed"),
            (1, "", "Task error"),
            (2, "", "Invalid arguments or configuration"),
            (-1, "", "Timed out"),
            (130, "", None),
            (137, "", None),
            (99, "boom", "Unexpected exit code 99: boom"),
        ],
    )
    def test_build_result_exit_code_mapping(
        self,
        exit_code: int,
        stderr: str,
        expected_error: str | None,
    ) -> None:
        runner = GeminiRunner(WorkerSettings())
        runner._session_id = "s-1"
        runner._input_tokens = 2
        runner._output_tokens = 5

        result = runner._build_result(exit_code, stderr)

        assert result.exit_code == exit_code
        assert result.error == expected_error
        assert result.session_id == "s-1"
        assert result.input_tokens == 2
        assert result.output_tokens == 5


class TestGeminiRunnerStream:
    @pytest.mark.asyncio
    async def test_read_stream_handles_valid_malformed_and_invalid_jsonl(self) -> None:
        runner = GeminiRunner(WorkerSettings())
        process = _ProcessWithStdout(
            [
                b"\n",
                b"{bad json}\n",
                b'{"type":"session.created","session_id":"s-jsonl"}\n',
                b'{"type":"turn.completed","tool_calls":"invalid-schema"}\n',
                (
                    b'{"type":"turn.completed","tool_calls":[{"name":"bash",'
                    b'"input":{"command":"just test"}}],"usageMetadata":'
                    b'{"promptTokenCount":8,"candidatesTokenCount":9}}\n'
                ),
                b'{"type":"unknown","payload":true}\n',
            ]
        )

        await runner._read_stream(process)  # type: ignore[arg-type]

        assert runner._session_id == "s-jsonl"
        assert runner._num_turns == 1
        assert runner._input_tokens == 8
        assert runner._output_tokens == 9
        assert [event.event_type for event in runner._events] == ["test.run"]

    @pytest.mark.asyncio
    async def test_read_stream_no_stdout_is_noop(self) -> None:
        runner = GeminiRunner(WorkerSettings())

        await runner._read_stream(_ProcessWithStdout(None))  # type: ignore[arg-type]

        assert runner._session_id == ""
        assert runner._events == []
