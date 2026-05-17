"""trace command — GET /v1/trace/{trace_id} causal-chain query (Story 9.7 / FR59a).

Returns every event in the causal chain for a given trace_id. Historical
query — no --follow mode (unlike events, /trace is a one-shot lookup).

Usage:
    oh-my-bmad trace <trace-id>

Story 9.7 / FR59a / Architecture §"trace_id propagation wiring" §line-1169.
"""

from __future__ import annotations

import json
import sys

import httpx
import typer
from events.envelope import is_valid_trace_id  # noqa: IMP001

from console_cli.adapters.error_renderer import render_http_error
from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.config import ConsoleSettings
from console_cli.app.metadata import mint_read_metadata
from console_cli.app.runner import run_async


def _exit_transport_error() -> None:
    """Handle ``httpx.ConnectError`` / ``TimeoutException`` / ``TransportError``."""
    print(
        "Error: Connection to registry-api lost. Is docker compose up?",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


def trace(
    trace_id: str = typer.Argument(..., help="Trace ID (bare UUIDv7 or tg:<update_id>)"),
) -> None:
    """Show every event in the causal chain for <trace-id>.

    Story 9.7 / FR59a / Architecture §"trace_id propagation wiring" §line-1169.
    Queries GET /v1/trace/{trace_id} on registry-api and prints each event as
    canonical JSON to stdout, one event per line.
    """
    # Validate shape per Story 9.1 contract before making an HTTP call.
    if not is_valid_trace_id(trace_id):
        print(
            f"Error: trace_id must be a bare UUIDv7 or 'tg:<update_id>'; got {trace_id!r}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)
    metadata = mint_read_metadata()

    try:
        result = run_async(
            client.get_trace(
                trace_id=trace_id,
                request_id=metadata.request_id,
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
    except httpx.TransportError:
        _exit_transport_error()
    except httpx.HTTPStatusError as exc:
        render_http_error(exc)
        raise SystemExit(1) from None
    except RegistryResponseError as exc:
        print(
            f"Error: Registry returned unexpected response: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    if not result:
        print(f"No events found for trace_id={trace_id}", file=sys.stderr)
        return

    for event in result:
        print(json.dumps(event, sort_keys=True), flush=True)


__all__ = ["trace"]
