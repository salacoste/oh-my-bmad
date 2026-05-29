"""key-status command — GET /v1/key-status operator-facing surface (Story 11.5.1 / FR65a).

Mirrors :mod:`console_cli.commands.ping` exactly: read-only GET, no idempotency
key, same exception envelope. Prints a 4-line operator-readable block of the
currently-active HMAC signing-key fingerprint plus rotation provenance.

Cold-start: when registry-state has not yet observed any ``key.rotated`` event,
registry-api returns 404; this command prints an operator-readable
"key fingerprint not yet materialized" message rather than a stack trace.

Security: the 16-hex ``fingerprint`` is a one-way SHA-256[:8] truncation per
ADR-0006 §Key-fingerprint — operator-readable; does NOT reveal
``OPERATOR_HMAC_KEY``.
"""

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


def key_status() -> None:
    """Show the current HMAC signing-key fingerprint + rotation provenance."""
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)
    # Read-only GET — no idempotency_key needed (matches ping.py pattern).
    metadata = mint_read_metadata()

    try:
        result = run_async(
            client.get_key_status(
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
        # Cold-start path: registry-state hasn't observed key.rotated yet.
        # Render an operator-readable message; SystemExit(1) so scripts can
        # distinguish "no fingerprint yet" from "command succeeded".
        if exc.response.status_code == 404:
            print(
                "Error: Key fingerprint not yet materialized — registry-state "
                "has not observed a key.rotated event since bootstrap. "
                "Restart the stack to trigger detection.",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        render_http_error(exc)
    except RegistryResponseError as exc:
        print(f"Error: Registry returned unexpected response: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # Render the 4-line operator-readable block. NOT one-line like /ping —
    # multi-line is intentional so operators can copy-paste the fingerprint
    # into runbooks.
    #
    # Story 11.5.1 /code-review-fix M1: explicitly normalize naive datetimes to
    # UTC before formatting so the trailing ``Z`` is always present (server
    # response WILL be tz-aware, but a future deserializer change to a naive
    # datetime would silently drop the ``Z`` suffix).
    from datetime import UTC

    rotated_at = (
        result.rotated_at
        if result.rotated_at.tzinfo is not None
        else result.rotated_at.replace(tzinfo=UTC)
    )
    rotated_at_iso = rotated_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    print("Operator HMAC signing key:")
    print(f"  Fingerprint:    {result.fingerprint}")
    print(f"  Last rotated:   {rotated_at_iso}")
    print(f"  Rotated by:     {result.rotated_by_actor_id}")
    print("  Signing active: yes")
