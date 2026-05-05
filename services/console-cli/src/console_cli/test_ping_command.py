"""Tests for get_platform_health client method + ping command (Story 4.3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from console_cli.adapters.registry_api_client import (
    HealthResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)

_HEALTH_RESPONSE_BODY = {
    "registry_status": "healthy",
    "worker_status": "idle",
    "clawhip_queue_depth": 0,
    "version": "v0.1.0",
}

_CONNECT_ERROR = httpx.ConnectError("refused")


def _make_client() -> RegistryAPIClient:
    return RegistryAPIClient(base_url="http://registry-api:8080")


def _mock_health_200(
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json=body or _HEALTH_RESPONSE_BODY,
        request=httpx.Request("GET", "http://registry-api:8080/v1/health"),
    )


def _mock_health_error(
    status: int,
    body: dict[str, object],
) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "http://registry-api:8080/v1/health"),
    )


# --- get_platform_health client method tests ---


@pytest.mark.asyncio
async def test_get_platform_health_success() -> None:
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_health_200(),
    ):
        result = await client.get_platform_health()
    assert isinstance(result, HealthResponseLocal)
    assert result.registry_status == "healthy"
    assert result.worker_status == "idle"
    assert result.clawhip_queue_depth == 0
    assert result.version == "v0.1.0"


@pytest.mark.asyncio
async def test_get_platform_health_malformed_body() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_mock_health_200({"unexpected": True}),
        ),
        pytest.raises(RegistryResponseError, match="malformed body"),
    ):
        await client.get_platform_health()


@pytest.mark.asyncio
async def test_get_platform_health_http_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_mock_health_error(404, {"detail": "not found"}),
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client.get_platform_health()


@pytest.mark.asyncio
async def test_get_platform_health_network_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=_CONNECT_ERROR,
        ),
        pytest.raises(httpx.ConnectError),
    ):
        await client.get_platform_health()


@pytest.mark.asyncio
async def test_get_platform_health_ignores_extra_fields() -> None:
    body = {**_HEALTH_RESPONSE_BODY, "uptime_seconds": 86400}
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_health_200(body),
    ):
        result = await client.get_platform_health()
    assert result.registry_status == "healthy"


# --- ping command tests ---


def test_ping_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_health_200(),
    ):
        result = runner.invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "pong" in result.output
    assert "registry: healthy" in result.output
    assert "worker: idle" in result.output
    assert "clawhip: 0 events" in result.output
    assert "version: v0.1.0" in result.output


def test_ping_command_network_error() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=_CONNECT_ERROR,
    ):
        result = runner.invoke(app, ["ping"])
    assert result.exit_code != 0


def test_ping_command_http_error() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_health_error(500, {"detail": "internal error"}),
    ):
        result = runner.invoke(app, ["ping"])
    assert result.exit_code != 0
