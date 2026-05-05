"""task command — create a new task via POST /v1/tasks (Story 4.2 AC-1)."""

from __future__ import annotations

import sys

import httpx
import typer

from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
    parse_error_detail,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.runner import run_async


def task(
    title: str = typer.Argument(..., help="Task title / description"),
    repo: str | None = typer.Option(None, "--repo", help="Target repository"),
    hint: str | None = typer.Option(None, "--hint", help="Hint for the orchestrator"),
) -> None:
    """Create a new task."""
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    from events import new_idempotency_key, new_request_id

    idempotency_key = new_idempotency_key()
    request_id = new_request_id()

    try:
        result = run_async(
            client.create_task(
                title=title,
                idempotency_key=idempotency_key,
                request_id=request_id,
                repo=repo,
                hint=hint,
            )
        )
        print(f"Task {result.task_id} created. Planning.")
    except httpx.ConnectError:
        print(
            "Error: Could not reach registry-api. Is docker compose up?",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        print(f"Error: {parse_error_detail(exc)}", file=sys.stderr)
        raise SystemExit(1) from None
    except RegistryResponseError as exc:
        print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
