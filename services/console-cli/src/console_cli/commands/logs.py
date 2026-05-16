"""logs command — GET /v1/tasks/{task_id}/logs/digest (Story 4.2 AC-3)."""

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
from console_cli.app.metadata import mint_command_metadata
from console_cli.app.runner import run_async


def logs(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
) -> None:
    """Show recent log entries for a task."""
    if not TASK_ID_PATTERN.match(task_id):
        print(f"Error: Invalid task ID format: {task_id!r}", file=sys.stderr)
        raise SystemExit(1) from None

    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)
    metadata = mint_command_metadata()

    try:
        result = run_async(
            client.get_logs_digest(
                task_id=task_id,
                request_id=metadata.request_id,
                trace_id=metadata.trace_id,
            )
        )
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

    print(result.digest)
    if result.truncated:
        print(f"\n(truncated — {result.line_count} lines shown)")
