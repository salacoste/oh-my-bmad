"""ping command — GET /v1/health platform health summary (Story 4.3 AC-5)."""

from __future__ import annotations

import sys

import httpx

from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
    parse_error_detail,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.runner import run_async


def ping() -> None:
    """Health-check the platform."""
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    from events import new_request_id

    request_id = new_request_id()

    try:
        result = run_async(client.get_platform_health(request_id=request_id))
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

    print(
        f"pong · registry: {result.registry_status} · "
        f"worker: {result.worker_status} · "
        f"clawhip: {result.clawhip_queue_depth} events · "
        f"version: {result.version}"
    )
