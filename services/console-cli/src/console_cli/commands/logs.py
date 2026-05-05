"""logs command — GET /v1/tasks/{task_id}/logs/digest (Story 4.2 AC-3)."""

from __future__ import annotations

import sys

import httpx
import typer

from console_cli.adapters.registry_api_client import (
    TASK_ID_PATTERN,
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.config import ConsoleSettings
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

    try:
        result = run_async(client.get_logs_digest(task_id=task_id))
    except httpx.ConnectError:
        print(
            "Error: Could not reach registry-api. Is docker compose up?",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            print(
                f"Logs not available for task {task_id} (endpoint not deployed or task not found)."
            )
            raise SystemExit(1) from None
        detail = _parse_error_detail(exc)
        print(f"Error: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
    except RegistryResponseError as exc:
        print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print(result.digest)
    if result.truncated:
        print(f"\n(truncated — {result.line_count} lines shown)")


def _parse_error_detail(exc: httpx.HTTPStatusError) -> str:
    """Extract human-readable detail from RFC 7807 problem+json or raw text."""
    try:
        body = exc.response.json()
        return body.get("detail", exc.response.text)
    except Exception:
        return exc.response.text
