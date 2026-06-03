"""Tests for OMCRunner subprocess supervision (Story 5.10 AC-9).

Uses mock subprocess to avoid requiring Node.js / OMC at test time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator_adapter.adapters.omc_runner import OMCRunner

_SPAWN_PATH = "orchestrator_adapter.adapters.omc_runner.asyncio.create_subprocess_exec"


def _make_omc_dir(tmp_path: Path) -> Path:
    """Create a fake OMC directory with bridge/cli.cjs."""
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "cli.cjs").write_text("// fake")
    return tmp_path


def _mock_process(
    *,
    stdout_data: bytes = b"plan output",
    stderr_data: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = None
    proc.pid = 12345

    stdin_mock = AsyncMock()
    stdin_mock.write = MagicMock()
    proc.stdin = stdin_mock

    stdout_mock = AsyncMock()
    stdout_mock.read = AsyncMock(return_value=stdout_data)
    proc.stdout = stdout_mock

    stderr_mock = AsyncMock()
    stderr_mock.read = AsyncMock(return_value=stderr_data)
    proc.stderr = stderr_mock

    async def _set_returncode() -> int:
        proc.returncode = returncode
        return returncode

    proc.wait = _set_returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


@pytest.fixture
def omc_dir(tmp_path: Path) -> Path:
    return _make_omc_dir(tmp_path)


class TestOMCRunnerInit:
    def test_valid_dir(self, omc_dir: Path) -> None:
        runner = OMCRunner(omc_path=omc_dir)
        assert runner._omc_path == omc_dir

    def test_invalid_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            OMCRunner(omc_path=tmp_path / "nonexistent")

    def test_missing_cli(self, tmp_path: Path) -> None:
        (tmp_path / "bridge").mkdir()
        with pytest.raises(ValueError, match="OMC CLI not found"):
            OMCRunner(omc_path=tmp_path)


class TestOMCRunnerRun:
    @pytest.mark.asyncio
    async def test_successful_run(self, omc_dir: Path) -> None:
        proc = _mock_process(stdout_data=b"# Plan\nStep 1", returncode=0)
        with patch(_SPAWN_PATH, return_value=proc):
            runner = OMCRunner(omc_path=omc_dir, timeout_s=30)
            result = await runner.run("test prompt")

        assert result.exit_code == 0
        assert "# Plan" in result.stdout
        assert result.error is None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_nonzero_exit(self, omc_dir: Path) -> None:
        proc = _mock_process(
            stdout_data=b"",
            stderr_data=b"Error: something broke",
            returncode=1,
        )
        with patch(_SPAWN_PATH, return_value=proc):
            runner = OMCRunner(omc_path=omc_dir)
            result = await runner.run("test prompt")

        assert result.exit_code == 1
        assert result.error is not None
        assert "something broke" in result.error

    @pytest.mark.asyncio
    async def test_spawn_failure(self, omc_dir: Path) -> None:
        with patch(_SPAWN_PATH, side_effect=OSError("ENOENT")):
            runner = OMCRunner(omc_path=omc_dir)
            result = await runner.run("test prompt")

        assert result.exit_code == -1
        assert "ENOENT" in result.error

    @pytest.mark.asyncio
    async def test_timeout_triggers_shutdown(self, omc_dir: Path) -> None:
        proc = _mock_process(stdout_data=b"")
        proc.stdout.read = AsyncMock(side_effect=TimeoutError())

        with patch(_SPAWN_PATH, return_value=proc):
            runner = OMCRunner(omc_path=omc_dir, timeout_s=1)
            result = await runner.run("test prompt")

        assert result.exit_code == -1
        assert "Timed out" in result.error

    @pytest.mark.asyncio
    async def test_cancel_running_process(self, omc_dir: Path) -> None:
        proc = _mock_process()
        with patch(_SPAWN_PATH, return_value=proc):
            runner = OMCRunner(omc_path=omc_dir)
            runner._process = proc
            await runner.cancel()

        proc.terminate.assert_called()
        assert runner._process is None


# ---------------------------------------------------------------------------
# Story 9.6 review pass-2 PH0 — trace_id propagation via env.
# ---------------------------------------------------------------------------


class TestOMCRunnerTraceIdEnv:
    """PH0 + pass-3 TH2: ``OMCRunner._spawn`` exports ``OMB_TRACE_ID`` to
    the child env when ``trace_id`` is passed PER-CALL via ``run()`` /
    ``_spawn()``; the env var is absent (or inherits parent's value) when
    ``trace_id`` is None.

    Story 9.6 review pass-3 TH2: ``trace_id`` is no longer stored on the
    runner — it's a per-call kwarg so multiple ``run()`` invocations carry
    distinct task-scoped tokens.
    """

    @pytest.mark.asyncio
    async def test_spawn_sets_omb_trace_id_when_provided(self, omc_dir: Path) -> None:
        from typing import Any

        captured_env: dict[str, str] = {}

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured_env.update(kw.get("env", {}))
            return _mock_process()

        with patch(_SPAWN_PATH, side_effect=_fake_exec):
            runner = OMCRunner(omc_path=omc_dir)
            await runner._spawn("hello", trace_id="01917e5c-a7d1-7000-8abc-0123456789ab")
        assert captured_env.get("OMB_TRACE_ID") == "01917e5c-a7d1-7000-8abc-0123456789ab"

    @pytest.mark.asyncio
    async def test_spawn_omits_omb_trace_id_when_not_provided(
        self, omc_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``trace_id`` is None, ``_spawn`` does NOT set ``OMB_TRACE_ID``
        on top of the parent's env (the env var is either inherited from the
        parent if set, or absent).

        Story 9.6 review pass-3 TM8: also verify PATH propagates so the env
        is not a stripped-down dict.
        """
        import os as _os
        from typing import Any

        # Strip ambient OMB_TRACE_ID to remove inheritance ambiguity.
        monkeypatch.delenv("OMB_TRACE_ID", raising=False)
        captured_env: dict[str, str] = {}

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured_env.update(kw.get("env", {}))
            return _mock_process()

        with patch(_SPAWN_PATH, side_effect=_fake_exec):
            runner = OMCRunner(omc_path=omc_dir)
            await runner._spawn("hello", trace_id=None)
        assert "OMB_TRACE_ID" not in captured_env
        # Story 9.6 review pass-3 TM8 — PATH propagation regression guard.
        if "PATH" in _os.environ:
            assert captured_env.get("PATH") == _os.environ["PATH"]

    @pytest.mark.asyncio
    async def test_run_per_call_trace_id_yields_distinct_child_envs(self, omc_dir: Path) -> None:
        """Story 9.6 review pass-3 TH2 regression: two ``runner.run()`` calls
        with distinct trace_ids produce two distinct child envs.
        """
        from typing import Any

        captured: list[dict[str, str]] = []

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured.append(dict(kw.get("env", {})))
            return _mock_process()

        tid_a = "01917e5c-a7d1-7000-8abc-0123456789aa"
        tid_b = "01917e5c-a7d1-7000-8abc-0123456789bb"

        with patch(_SPAWN_PATH, side_effect=_fake_exec):
            runner = OMCRunner(omc_path=omc_dir, timeout_s=5)
            await runner.run("prompt-a", trace_id=tid_a)
            await runner.run("prompt-b", trace_id=tid_b)

        assert len(captured) == 2
        assert captured[0].get("OMB_TRACE_ID") == tid_a
        assert captured[1].get("OMB_TRACE_ID") == tid_b
        assert captured[0]["OMB_TRACE_ID"] != captured[1]["OMB_TRACE_ID"]


# ---------------------------------------------------------------------------
# G-SEC-2 (D4) — explicit child-env allowlist.
# ---------------------------------------------------------------------------


class TestOMCRunnerChildEnvAllowlist:
    """G-SEC-2 (D4): ``_spawn`` builds the child env from an explicit
    allowlist (``_build_child_env``) — functional vars (incl. NODE_PATH and a
    pass-through ANTHROPIC_API_KEY) + ``OMB_*``/``CLAUDE_*`` prefixes pass
    through, operator/platform secrets are dropped."""

    @pytest.mark.asyncio
    async def test_spawn_env_contains_needed_vars(
        self, omc_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Functional allowlist + prefix vars + pass-through ANTHROPIC/GITHUB.

        Unlike the worker, OrchestratorSettings has no anthropic_api_key field,
        so ANTHROPIC_API_KEY must pass THROUGH from the parent env.
        """
        from typing import Any

        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/home/agent")
        monkeypatch.setenv("NODE_PATH", "/opt/node_modules")
        monkeypatch.setenv("OMB_FOO", "x")  # prefix passthrough
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-passthrough")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp-needed")

        captured_env: dict[str, str] = {}

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured_env.update(kw.get("env", {}))
            return _mock_process()

        with patch(_SPAWN_PATH, side_effect=_fake_exec):
            runner = OMCRunner(omc_path=omc_dir)
            await runner._spawn("hello", trace_id="01917e5c-a7d1-7000-8abc-0123456789ab")

        assert captured_env["PATH"] == "/usr/bin:/bin"
        assert captured_env["HOME"] == "/home/agent"
        assert captured_env["NODE_PATH"] == "/opt/node_modules"
        assert captured_env["OMB_TRACE_ID"] == "01917e5c-a7d1-7000-8abc-0123456789ab"
        assert captured_env["OMB_FOO"] == "x"
        # ANTHROPIC passes THROUGH (no settings field to re-inject).
        assert captured_env["ANTHROPIC_API_KEY"] == "sk-passthrough"
        # GITHUB_TOKEN retained (git push).
        assert captured_env["GITHUB_TOKEN"] == "ghp-needed"

    @pytest.mark.asyncio
    async def test_spawn_env_excludes_operator_secrets(
        self, omc_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operator/platform secret canaries MUST NOT reach the child env."""
        from typing import Any

        canaries = {
            "OPERATOR_HMAC_KEY": "canary",
            "LITESTREAM_SECRET_ACCESS_KEY": "canary",
            "LITESTREAM_ACCESS_KEY_ID": "canary",
            "TELEGRAM_BOT_TOKEN": "canary",
            "TELEGRAM_WEBHOOK_SECRET_TOKEN": "canary",
            "TG_ALLOWLIST_USER_IDS": "canary",
            "AWS_SECRET_ACCESS_KEY": "canary",
            "OPENAI_API_KEY": "canary",
        }
        for name, value in canaries.items():
            monkeypatch.setenv(name, value)

        captured_env: dict[str, str] = {}

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured_env.update(kw.get("env", {}))
            return _mock_process()

        with patch(_SPAWN_PATH, side_effect=_fake_exec):
            runner = OMCRunner(omc_path=omc_dir)
            await runner._spawn("hello", trace_id=None)

        for name in canaries:
            assert name not in captured_env, f"{name} leaked into child env"
