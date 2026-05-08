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
