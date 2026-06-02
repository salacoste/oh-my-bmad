"""Typer app factory — registers all subcommands (Story 4.1 AC-1/AC-2)."""

from __future__ import annotations

import typer

from console_cli.commands import (
    agent,
    approve,
    events,
    key_status,
    logs,
    ping,
    reject,
    retry,
    status,
    stop,
    task,
    trace,
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
app.command(name="events")(events.events)
app.command(name="trace")(trace.trace)
# Story 11.5.1 / FR65a — explicit name="key-status" so typer surfaces the
# hyphenated command form expected by operators (matches Telegram's /key-status).
app.command(name="key-status")(key_status.key_status)


__all__ = ["app"]
