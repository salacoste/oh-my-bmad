"""status command — show task state via GET /v1/tasks/{task_id} (Story 4.2 AC-2)."""

from __future__ import annotations

import sys

import httpx
import typer

from console_cli.adapters.registry_api_client import (
    TASK_ID_PATTERN,
    RegistryAPIClient,
    RegistryResponseError,
    parse_error_detail,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.runner import run_async


def status(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
) -> None:
    """Show task / agent / pipeline status."""
    if not TASK_ID_PATTERN.match(task_id):
        print(f"Error: Invalid task ID format: {task_id!r}", file=sys.stderr)
        raise SystemExit(1) from None

    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    try:
        result = run_async(client.get_task(task_id=task_id))
    except httpx.ConnectError:
        print(
            "Error: Could not reach registry-api. Is docker compose up?",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            print(f"Task {task_id} not found.", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"Error: {parse_error_detail(exc)}", file=sys.stderr)
        raise SystemExit(1) from None
    except RegistryResponseError as exc:
        print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"Task:   {result.task_id}")
    print(f"Status: {result.status}")
    if result.title:
        print(f"Title:  {result.title}")
    print(f"Actor:  {result.actor.kind}/{result.actor.id}")
    if result.last_event:
        print(f"Last:   {result.last_event.type} ({result.last_event.emitted_at.isoformat()})")
    if result.next_commands:
        print(f"Next:   {', '.join(result.next_commands)}")
