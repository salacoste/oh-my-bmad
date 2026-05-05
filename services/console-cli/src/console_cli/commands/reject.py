"""reject command — POST /v1/tasks/{task_id}/decisions action=reject (Story 4.3 AC-2)."""

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


def reject(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
    reason: str = typer.Argument(..., help="Rejection reason"),
) -> None:
    """Reject a pending decision."""
    if not TASK_ID_PATTERN.match(task_id):
        print(f"Error: Invalid task ID format: {task_id!r}", file=sys.stderr)
        raise SystemExit(1) from None

    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    from events import new_idempotency_key, new_request_id

    idempotency_key = new_idempotency_key()
    request_id = new_request_id()

    try:
        result = run_async(
            client.submit_decision(
                task_id=task_id,
                action="reject",
                idempotency_key=idempotency_key,
                request_id=request_id,
                hint=reason,
            )
        )
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
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"Rejected {result.task_id} ({result.decision_id}): {reason}")
