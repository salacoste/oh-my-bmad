"""agent command — show runtime info for a task (Story 4.3 AC-6)."""

from __future__ import annotations

import sys

import httpx
import typer

from console_cli.adapters.error_renderer import render_http_error
from console_cli.adapters.registry_api_client import (
    TASK_ID_PATTERN,
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.runner import run_async

_DEFAULT_RUNTIME = "claude-code"


def agent(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
) -> None:
    """List or inspect agents."""
    if not TASK_ID_PATTERN.match(task_id):
        print(f"Error: Invalid task ID format: {task_id!r}", file=sys.stderr)
        raise SystemExit(1) from None

    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    from events import new_request_id

    request_id = new_request_id()

    try:
        result = run_async(client.get_task(task_id=task_id, request_id=request_id))
    except httpx.ConnectError:
        print(
            "Error: Could not reach registry-api. Is docker compose up?",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.TimeoutException:
        print(
            "Error: registry-api timed out. Try again or increase timeout.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        render_http_error(exc)
    except RegistryResponseError as exc:
        print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"Task {task_id}: status={result.status} runtime={_DEFAULT_RUNTIME}")
