"""Tests for status command (Story 4.2 AC-9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    RegistryResponseError,
    TaskResponseLocal,
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
        request=httpx.Request("GET", f"http://registry-api:8080/v1/tasks/{_FAKE_TASK_ID}"),
    )


def _task_response_body() -> dict:
    return {
        "task_id": _FAKE_TASK_ID,
        "status": "planning",
        "title": "Add idempotency middleware",
        "created_at": "2026-05-05T12:00:00Z",
        "updated_at": "2026-05-05T12:01:00Z",
        "actor": {"kind": "operator", "id": "console"},
        "last_event": {
            "id": "e-019abcde-f012-7abc-8def-111111111111",
            "type": "task_created",
            "emitted_at": "2026-05-05T12:00:00Z",
        },
        "next_commands": ["approve", "reject"],
    }


# ---------------------------------------------------------------------------
# get_task tests
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """AC-2: get_task returns TaskResponseLocal on 200."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_get_response(200, _task_response_body())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = await client.get_task(task_id=_FAKE_TASK_ID)

        assert isinstance(result, TaskResponseLocal)
        assert result.task_id == _FAKE_TASK_ID
        assert result.status == "planning"
        assert result.title == "Add idempotency middleware"
        assert result.next_commands == ["approve", "reject"]

    @pytest.mark.asyncio
    async def test_404_raises(self) -> None:
        """404 for nonexistent task."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_get_response(404, {"detail": f"Task {_FAKE_TASK_ID} not found"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_task(task_id=_FAKE_TASK_ID)

    @pytest.mark.asyncio
    async def test_invalid_task_id_raises_value_error(self) -> None:
        """ValueError for task_id that doesn't match TASK_ID_PATTERN."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)

        with pytest.raises(ValueError, match="Invalid task_id"):
            await client.get_task(task_id="invalid-id")

    @pytest.mark.asyncio
    async def test_malformed_body_raises(self) -> None:
        """RegistryResponseError on 200 with missing required fields."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_get_response(200, {"unexpected": "body"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(RegistryResponseError, match="malformed body"):
                await client.get_task(task_id=_FAKE_TASK_ID)


# ---------------------------------------------------------------------------
# status command integration tests
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_invalid_task_id_format(self) -> None:
        """Invalid task ID format prints error and exits 1."""
        result = runner.invoke(app, ["status", "bad-id"])
        assert result.exit_code == 1
        assert "Invalid task ID" in result.output

    def test_success_output(self) -> None:
        """AC-2: status command renders task state."""
        fake_response = _make_get_response(200, _task_response_body())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = runner.invoke(app, ["status", _FAKE_TASK_ID])

        assert result.exit_code == 0
        assert _FAKE_TASK_ID in result.output
        assert "planning" in result.output
        assert "approve" in result.output

    def test_404_output(self) -> None:
        """404 prints not-found message and exits 4."""
        fake_response = _make_get_response(404, {"detail": f"Task {_FAKE_TASK_ID} not found"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = runner.invoke(app, ["status", _FAKE_TASK_ID])

        assert result.exit_code == 4
        assert "Error:" in (result.output + (result.stderr or ""))
