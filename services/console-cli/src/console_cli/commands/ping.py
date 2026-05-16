"""ping command — GET /v1/health platform health summary (Story 4.3 AC-5)."""

from __future__ import annotations

import sys

import httpx

from console_cli.adapters.error_renderer import render_http_error
from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.metadata import mint_read_metadata
from console_cli.app.runner import run_async


def ping() -> None:
    """Health-check the platform."""
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)
    # Pass-2 S8: read-only GET — no idempotency_key needed.
    metadata = mint_read_metadata()

    try:
        result = run_async(
            client.get_platform_health(
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
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    print(
        f"pong · registry: {result.registry_status} · "
        f"worker: {result.worker_status} · "
        f"clawhip: {result.clawhip_queue_depth} events · "
        f"version: {result.version}"
    )
