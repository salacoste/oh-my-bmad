"""Tests for console-cli Typer app scaffold (Story 4.1 AC-8)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from console_cli.app.main import app

runner = CliRunner()

ALL_COMMANDS = [
    "task",
    "status",
    "logs",
    "approve",
    "reject",
    "stop",
    "retry",
    "ping",
    "agent",
    "events",
]

# Commands that now require arguments (Stories 4.2 + 4.3 replaced stubs).
REQUIRES_ARGS = {"task", "status", "logs", "approve", "reject", "stop", "retry", "agent", "events"}

# Commands that are fully implemented and work bare (no args needed).
REAL_NO_ARGS = {"ping"}


def test_import_app() -> None:
    """Importing the app succeeds."""
    from console_cli.app.main import app as _app  # noqa: F401


def test_help_exits_zero() -> None:
    """--help exits 0 and lists all subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ALL_COMMANDS:
        assert cmd in result.output


@pytest.mark.parametrize("cmd", ALL_COMMANDS)
def test_command_registered(cmd: str) -> None:
    """Each command is registered and responds to --help."""
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("cmd", REQUIRES_ARGS)
def test_command_requires_args(cmd: str) -> None:
    """Commands that require arguments exit with error when invoked bare."""
    result = runner.invoke(app, [cmd])
    assert result.exit_code != 0


@pytest.mark.parametrize("cmd", REAL_NO_ARGS)
def test_command_runs_bare(cmd: str) -> None:
    """Fully implemented commands that accept no required args can be invoked."""
    result = runner.invoke(app, [cmd])
    # ping/retry/events may fail at runtime (no server) but should not
    # fail with a Typer "Missing argument" error.
    assert "Missing argument" not in (result.output + (result.stderr or ""))


def test_no_args_shows_help() -> None:
    """Bare invocation shows help (no_args_is_help=True)."""
    result = runner.invoke(app, [])
    # Typer/Click exit code 0 or 2 for no-args help display.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.output
