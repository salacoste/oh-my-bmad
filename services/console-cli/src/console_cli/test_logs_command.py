"""Tests for logs command (Story 4.2 AC-9)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli._test_fixtures import UUIDV7_BARE_RE_PATTERN
from console_cli.adapters.registry_api_client import (
    LogsDigestResponseLocal,
    RegistryAPIClient,
)
from console_cli.app.main import app

runner = CliRunner()

_FAKE_BASE_URL = "http://registry-api:8080"
_FAKE_TASK_ID = "t-019abcde-f012-7abc-8def-0123456789ab"


def _make_get_response(
    status_code: int = 200,
    body: dict | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request(
            "GET",
            f"http://registry-api:8080/v1/tasks/{_FAKE_TASK_ID}/logs/digest",
        ),
    )


def _logs_digest_body() -> dict:
    return {
        "task_id": _FAKE_TASK_ID,
        "digest": "Worker completed planning phase.\nReady for approval.",
        "truncated": False,
        "line_count": 2,
    }


# ---------------------------------------------------------------------------
# get_logs_digest tests
# ---------------------------------------------------------------------------


class TestGetLogsDigest:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """AC-3: get_logs_digest returns LogsDigestResponseLocal on 200."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_get_response(200, _logs_digest_body())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = await client.get_logs_digest(task_id=_FAKE_TASK_ID)

        assert isinstance(result, LogsDigestResponseLocal)
        assert result.task_id == _FAKE_TASK_ID
        assert "planning phase" in result.digest
        assert result.line_count == 2
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_404_endpoint_not_deployed(self) -> None:
        """AC-3: 404 when endpoint not deployed (Story 7.3)."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_get_response(404, {"detail": "Not Found"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_logs_digest(task_id=_FAKE_TASK_ID)

    @pytest.mark.asyncio
    async def test_invalid_task_id_raises_value_error(self) -> None:
        """ValueError for task_id that doesn't match TASK_ID_PATTERN."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)

        with pytest.raises(ValueError, match="Invalid task_id"):
            await client.get_logs_digest(task_id="invalid-id")

    @pytest.mark.asyncio
    async def test_truncated_output(self) -> None:
        """Truncated flag carried through."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        body = {
            "task_id": _FAKE_TASK_ID,
            "digest": "Long output...",
            "truncated": True,
            "line_count": 10,
        }
        fake_response = _make_get_response(200, body)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = await client.get_logs_digest(task_id=_FAKE_TASK_ID)

        assert result.truncated is True
        assert result.line_count == 10


# ---------------------------------------------------------------------------
# logs command integration tests
# ---------------------------------------------------------------------------


class TestLogsCommand:
    def test_invalid_task_id_format(self) -> None:
        """Invalid task ID format prints error and exits 1."""
        result = runner.invoke(app, ["logs", "bad-id"])
        assert result.exit_code == 1
        assert "Invalid task ID" in result.output

    def test_success_output(self) -> None:
        """AC-3: logs command renders digest text."""
        fake_response = _make_get_response(200, _logs_digest_body())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = runner.invoke(app, ["logs", _FAKE_TASK_ID])

        assert result.exit_code == 0
        assert "planning phase" in result.output

    def test_404_output(self) -> None:
        """404 exits with code 4."""
        fake_response = _make_get_response(404, {"detail": "Not Found"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = runner.invoke(app, ["logs", _FAKE_TASK_ID])

        assert result.exit_code == 4
        assert "Error:" in (result.output + (result.stderr or ""))

    def test_truncated_output(self) -> None:
        """Truncated digest shows truncation notice."""
        body = {
            "task_id": _FAKE_TASK_ID,
            "digest": "Long output...",
            "truncated": True,
            "line_count": 10,
        }
        fake_response = _make_get_response(200, body)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = runner.invoke(app, ["logs", _FAKE_TASK_ID])

        assert result.exit_code == 0
        assert "truncated" in result.output


# ---------------------------------------------------------------------------
# Story 9.4 — X-Trace-Id / X-Request-ID propagation tests (R5 + R7)
# ---------------------------------------------------------------------------


def test_logs_command_propagates_x_trace_id_to_registry_api() -> None:
    """R5 — ``oh-my-bmad-cli logs`` mints + forwards bare UUIDv7 trace_id."""
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_make_get_response(200, _logs_digest_body()),
    ) as mock_get:
        result = runner.invoke(app, ["logs", _FAKE_TASK_ID])

    assert result.exit_code == 0, result.output
    headers = mock_get.call_args[1]["headers"]
    assert "X-Trace-Id" in headers
    assert re.match(UUIDV7_BARE_RE_PATTERN, headers["X-Trace-Id"]), headers["X-Trace-Id"]
    assert "X-Request-ID" in headers
    assert headers["X-Request-ID"] != headers["X-Trace-Id"]


def test_logs_command_sends_x_request_id_header() -> None:
    """R7 — ``logs`` now backfills the X-Request-ID header (pre-9.4 it omitted it)."""
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_make_get_response(200, _logs_digest_body()),
    ) as mock_get:
        result = runner.invoke(app, ["logs", _FAKE_TASK_ID])

    assert result.exit_code == 0, result.output
    headers = mock_get.call_args[1]["headers"]
    assert "X-Request-ID" in headers
    assert re.match(UUIDV7_BARE_RE_PATTERN, headers["X-Request-ID"]), headers["X-Request-ID"]
