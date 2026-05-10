"""events command — GET /v1/tasks/{task_id}/events with --follow live tail (Story 4.4)."""

from __future__ import annotations

import asyncio
import json
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

_POLL_INTERVAL = 0.5
_MAX_RETRIES = 5


async def _poll_events(task_id: str) -> None:
    """Long-lived polling loop for --follow mode.

    Uses a single AsyncClient for the polling duration to avoid
    creating a new TCP connection every 0.5 s.
    """
    settings = ConsoleSettings()
    base_url = settings.registry_api_base_url

    from events import new_request_id as _new_request_id

    since: str | None = None
    retries = 0

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if since is not None:
                params["since"] = since

            headers: dict[str, str] = {"X-Request-ID": _new_request_id()}

            try:
                response = await client.get(
                    f"/v1/tasks/{task_id}/events",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                retries += 1
                if retries > _MAX_RETRIES:
                    raise
                backoff = min(_POLL_INTERVAL * 2**retries, 30)
                print(
                    f"Warning: poll failed ({exc}), retrying in {backoff:.0f}s...",
                    file=sys.stderr,
                )
                await asyncio.sleep(backoff)
                continue

            retries = 0
            data = response.json()
            if not isinstance(data, list):
                msg = f"expected JSON array, got {type(data).__name__}"
                raise RegistryResponseError(msg)

            for event in data:
                print(json.dumps(event, sort_keys=True), flush=True)
                emitted = event.get("emitted_at")
                if isinstance(emitted, str) and (since is None or emitted > since):
                    since = emitted

            if not data and since is None:
                print("Waiting for events...", file=sys.stderr)

            await asyncio.sleep(_POLL_INTERVAL)


def events(
    task_id: str = typer.Argument(..., help="Task ID (t-<uuidv7>)"),
    follow: bool = typer.Option(False, "--follow", help="Stream events in real time"),
) -> None:
    """Tail or query the event log."""
    if not TASK_ID_PATTERN.match(task_id):
        print(f"Error: Invalid task ID format: {task_id!r}", file=sys.stderr)
        raise SystemExit(1) from None

    if follow:
        try:
            run_async(_poll_events(task_id))
        except KeyboardInterrupt:
            return
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
            print(
                "Error: Connection to registry-api lost. Is docker compose up?",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        except httpx.HTTPStatusError as exc:
            render_http_error(exc)
        except RegistryResponseError as exc:
            print(
                f"Error: Registry returned unexpected response: {exc}",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    # Non-follow: single fetch via RegistryAPIClient
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)

    from events import new_request_id

    request_id = new_request_id()

    try:
        result = run_async(client.get_task_events(task_id=task_id, request_id=request_id))
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
        print(
            "Error: Connection to registry-api lost. Is docker compose up?",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        render_http_error(exc)
    except RegistryResponseError as exc:
        print(
            f"Error: Registry returned unexpected response: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    for event in result.events:
        print(json.dumps(event, sort_keys=True), flush=True)

    if not result.events:
        print(f"No events found for task {task_id}.", file=sys.stderr)
