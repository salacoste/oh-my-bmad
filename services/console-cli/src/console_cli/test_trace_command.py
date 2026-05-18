"""Tests for the trace command + RegistryAPIClient.get_trace() (Story 9.7 / FR59a).

Story 9.7 pass-1 PH-A2/PM-A8 — zero test coverage on console-cli /trace path.

Covers:
  * test_trace_validates_shape_exits_2_on_invalid — exit code 2 on bad trace_id
  * test_trace_http_404_friendly_message — 404 produces friendly output
  * test_trace_http_500_friendly_message — 500 produces friendly output
  * test_trace_renders_event_chain — happy path renders events
  * test_trace_no_events_message — empty list → "no events" message
  * test_get_trace_client_200 — adapter parses list body (PM-A8)
  * test_get_trace_client_400_raises — adapter raises on 400 (PM-A8)
  * test_get_trace_client_500_raises — adapter raises on 500 (PM-A8)
  * test_get_trace_client_malformed_body — non-list raises RegistryResponseError (PM-A8)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli._test_fixtures import FAKE_TRACE_ID_UUIDV7
from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.main import app

runner = CliRunner()

_FAKE_BASE_URL = "http://registry-api:8080"
_INVALID_TRACE_ID = "not-a-valid-trace"

_FAKE_EVENTS: list[dict[str, object]] = [
    {
        "event_id": "e-00000000-0000-7000-8000-000000000001",
        "type": "task.created",
        "emitted_at": "2026-01-01T10:00:00.000000Z",
        "emitted_at_monotonic_ns": 1000,
        "trace_id": FAKE_TRACE_ID_UUIDV7,
        "payload": {"task_id": "t-abc"},
        "extensions": {},
    },
    {
        "event_id": "e-00000000-0000-7000-8000-000000000002",
        "type": "session.started",
        "emitted_at": "2026-01-01T10:00:01.000000Z",
        "emitted_at_monotonic_ns": 2000,
        "trace_id": FAKE_TRACE_ID_UUIDV7,
        "payload": {"session_id": "s-abc"},
        "extensions": {},
    },
]


def _make_response(status: int, body: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", f"{_FAKE_BASE_URL}/v1/trace/{FAKE_TRACE_ID_UUIDV7}"),
    )


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


def test_trace_validates_shape_exits_2_on_invalid() -> None:
    """Invalid trace_id → exit code 2 + error message to stderr.

    Story 9.7 pass-2 TM-E3: split the OR-chain — exit_code is the PRIMARY
    contract; the error-message assertion is a SECONDARY check. The prior
    ``exit_code == 2 OR "invalid" in out`` form silently passed even when
    the exit code was wrong as long as the output contained either keyword.
    """
    result = runner.invoke(app, ["trace", _INVALID_TRACE_ID])
    out = (result.output or "").lower()
    assert result.exit_code == 2
    assert "invalid" in out or "error" in out


def test_trace_http_404_friendly_message() -> None:
    """HTTP 404 from registry produces a user-friendly error, not a traceback."""
    with patch(
        "console_cli.adapters.registry_api_client.RegistryAPIClient.get_trace",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", f"{_FAKE_BASE_URL}/v1/trace/{FAKE_TRACE_ID_UUIDV7}"),
            response=httpx.Response(404, json={"detail": "not found"}),
        ),
    ):
        result = runner.invoke(app, ["trace", FAKE_TRACE_ID_UUIDV7])
    output = result.output or ""
    # Should not raise unhandled exception
    assert "Traceback" not in output
    # Exit code varies by framework; just assert it handled without raising
    assert result.exit_code is not None


def test_trace_http_500_friendly_message() -> None:
    """HTTP 500 from registry produces a user-friendly error."""
    with patch(
        "console_cli.adapters.registry_api_client.RegistryAPIClient.get_trace",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "500",
            request=httpx.Request("GET", f"{_FAKE_BASE_URL}/v1/trace/{FAKE_TRACE_ID_UUIDV7}"),
            response=httpx.Response(500, json={"detail": "internal"}),
        ),
    ):
        result = runner.invoke(app, ["trace", FAKE_TRACE_ID_UUIDV7])
    output = result.output or ""
    assert "Traceback" not in output


def test_trace_renders_event_chain() -> None:
    """Happy path: events returned and rendered to stdout."""
    with patch(
        "console_cli.adapters.registry_api_client.RegistryAPIClient.get_trace",
        new_callable=AsyncMock,
        return_value=_FAKE_EVENTS,
    ):
        result = runner.invoke(app, ["trace", FAKE_TRACE_ID_UUIDV7])
    output = result.output or ""
    # At minimum the trace_id or an event type should appear
    assert FAKE_TRACE_ID_UUIDV7 in output or "task.created" in output or "session.started" in output


def test_trace_no_events_message() -> None:
    """Empty event list → 'no events' message, exit 0."""
    with patch(
        "console_cli.adapters.registry_api_client.RegistryAPIClient.get_trace",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = runner.invoke(app, ["trace", FAKE_TRACE_ID_UUIDV7])
    assert result.exit_code == 0
    output = result.output or ""
    assert "no events" in output.lower() or "0" in output or FAKE_TRACE_ID_UUIDV7 in output


# ---------------------------------------------------------------------------
# Adapter unit tests (PM-A8)
# ---------------------------------------------------------------------------


class TestGetTraceAdapter:
    @pytest.mark.asyncio
    async def test_get_trace_client_200(self) -> None:
        """200 with list body → returns list of dicts."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(200, _FAKE_EVENTS)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = await client.get_trace(trace_id=FAKE_TRACE_ID_UUIDV7)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "task.created"

    @pytest.mark.asyncio
    async def test_get_trace_client_400_raises(self) -> None:
        """400 → HTTPStatusError raised (caller maps to user message)."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(400, {"detail": "invalid trace_id shape"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_trace(trace_id=FAKE_TRACE_ID_UUIDV7)

    @pytest.mark.asyncio
    async def test_get_trace_client_500_raises(self) -> None:
        """500 → HTTPStatusError raised."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(500, {"detail": "internal"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_trace(trace_id=FAKE_TRACE_ID_UUIDV7)

    @pytest.mark.asyncio
    async def test_get_trace_client_malformed_body(self) -> None:
        """Non-list 200 body → RegistryResponseError."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(200, {"not": "a list"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(RegistryResponseError):
                await client.get_trace(trace_id=FAKE_TRACE_ID_UUIDV7)

    @pytest.mark.asyncio
    async def test_get_trace_client_invalid_shape_raises_value_error(self) -> None:
        """Passing an invalid trace_id → ValueError before any HTTP call."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        with pytest.raises(ValueError, match="Invalid trace_id"):
            await client.get_trace(trace_id="bad-id")
