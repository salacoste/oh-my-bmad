"""Tests for ``console-cli key-status`` + ``get_key_status`` client (Story 11.5.1).

Two tests per the Story 11.5 AC6 deferral budget:

1. ``test_key_status_console_cli_renders_current_fingerprint`` — happy path:
   mocks the httpx GET; asserts the 4-line operator-readable block is in
   stdout and exit code is 0.
2. ``test_key_status_console_cli_404_cold_start_renders_operator_message``
   (AC5 bonus) — registry-api 404 → operator-readable cold-start message
   on stderr + SystemExit(1).

Pattern mirrors ``test_ping_command.py`` exactly: ``typer.testing.CliRunner``
+ ``patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=...)``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli.adapters.registry_api_client import (
    KeyStatusResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.main import app

# Shared fixture values mirror registry-api/.../routes/test_key_status.py +
# telegram-gateway/.../test_key_status_command.py for cross-service determinism.
_FIXTURE_FINGERPRINT = "a1b2c3d4e5f6789a"
_FIXTURE_ROTATED_BY = "http-api"
_FIXTURE_ROTATED_AT_ISO = "2026-05-21T14:30:00Z"

_KEY_STATUS_RESPONSE_BODY = {
    "fingerprint": _FIXTURE_FINGERPRINT,
    "rotated_at": _FIXTURE_ROTATED_AT_ISO,
    "rotated_by_actor_id": _FIXTURE_ROTATED_BY,
}


def _make_client() -> RegistryAPIClient:
    return RegistryAPIClient(base_url="http://registry-api:8080")


def _mock_key_status_200(body: dict[str, object] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json=body or _KEY_STATUS_RESPONSE_BODY,
        request=httpx.Request("GET", "http://registry-api:8080/v1/key-status"),
    )


def _mock_key_status_error(status: int, body: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "http://registry-api:8080/v1/key-status"),
    )


# ---------------------------------------------------------------------------
# AC2 — get_key_status client method tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_key_status_success() -> None:
    """Client returns typed KeyStatusResponseLocal on 200."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_key_status_200(),
    ):
        result = await client.get_key_status()
    assert isinstance(result, KeyStatusResponseLocal)
    assert result.fingerprint == _FIXTURE_FINGERPRINT
    assert result.rotated_by_actor_id == _FIXTURE_ROTATED_BY


@pytest.mark.asyncio
async def test_get_key_status_malformed_body() -> None:
    """Malformed 200 body → RegistryResponseError (not raw ValidationError)."""
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_mock_key_status_200({"unexpected": True}),
        ),
        pytest.raises(RegistryResponseError, match="malformed body"),
    ):
        await client.get_key_status()


@pytest.mark.asyncio
async def test_get_key_status_404_propagates() -> None:
    """404 cold-start path propagates as httpx.HTTPStatusError (CLI catches + renders)."""
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_mock_key_status_error(404, {"detail": "not materialized"}),
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client.get_key_status()


# ---------------------------------------------------------------------------
# AC4 / AC5 — happy path (Story 11.5 AC6 spec test name)
# ---------------------------------------------------------------------------


def test_key_status_console_cli_renders_current_fingerprint() -> None:
    """AC4 happy path — stdout contains the verbatim 4-line operator block + exit 0."""
    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_key_status_200(),
    ):
        result = runner.invoke(app, ["key-status"])

    assert result.exit_code == 0, (
        f"expected exit 0, got {result.exit_code}; stdout={result.stdout!r}; "
        f"stderr={result.output!r}"
    )

    # Block header + all 3 fields + terminator.
    assert "Operator HMAC signing key:" in result.stdout
    assert _FIXTURE_FINGERPRINT in result.stdout
    assert _FIXTURE_ROTATED_BY in result.stdout
    assert _FIXTURE_ROTATED_AT_ISO in result.stdout
    assert "Signing active: yes" in result.stdout


# ---------------------------------------------------------------------------
# AC5 bonus — cold-start (404) path
# ---------------------------------------------------------------------------


def test_key_status_console_cli_404_cold_start_renders_operator_message() -> None:
    """AC5 cold-start: 404 → operator-readable message on stderr + exit 1."""
    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_key_status_error(
            404, {"detail": "key fingerprint not yet materialized"}
        ),
    ):
        result = runner.invoke(app, ["key-status"])

    assert result.exit_code == 1, (
        f"expected exit 1, got {result.exit_code}; output={result.output!r}"
    )
    # `runner.invoke` with default mix_stderr=True puts stderr into result.output.
    assert "not yet materialized" in result.output.lower(), (
        f"expected cold-start message; got {result.output!r}"
    )


# ---------------------------------------------------------------------------
# AC5 bonus — network error path (sanity check that exception envelope works)
# ---------------------------------------------------------------------------


def test_key_status_console_cli_network_error_renders_operator_message() -> None:
    """Network unreachable → operator-readable error on stderr + exit 1."""
    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("refused"),
    ):
        result = runner.invoke(app, ["key-status"])

    assert result.exit_code == 1
    assert "Could not reach registry-api" in result.output
