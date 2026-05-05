"""Typer app factory — registers all subcommands (Story 4.1 AC-1/AC-2)."""

from __future__ import annotations

import typer

from console_cli.commands import (
    agent,
    approve,
    events,
    logs,
    ping,
    reject,
    retry,
    status,
    stop,
    task,
)

app = typer.Typer(
    name="oh-my-bmad-cli",
    help="Operator CLI for oh-my-bmad platform.",
    no_args_is_help=True,
)

app.command()(task.task)
app.command()(status.status)
app.command()(logs.logs)
app.command()(approve.approve)
app.command()(reject.reject)
app.command()(stop.stop)
app.command()(retry.retry)
app.command()(ping.ping)
app.command()(agent.agent)
app.command()(events.events)


__all__ = ["app"]
