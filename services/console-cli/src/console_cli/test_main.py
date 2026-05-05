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

# Commands that now require arguments (Story 4.2 replaced stubs).
REQUIRES_ARGS = {"task", "status", "logs"}


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


@pytest.mark.parametrize("cmd", [c for c in ALL_COMMANDS if c not in REQUIRES_ARGS])
def test_stub_command_still_runs(cmd: str) -> None:
    """Remaining stub commands still print not-yet-implemented."""
    result = runner.invoke(app, [cmd])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output


def test_no_args_shows_help() -> None:
    """Bare invocation shows help (no_args_is_help=True)."""
    result = runner.invoke(app, [])
    # Typer/Click exit code 0 or 2 for no-args help display.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.output
