"""Tests for get_task_events client method + events command (Story 4.4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from console_cli.adapters.registry_api_client import (
    RegistryAPIClient,
    TaskEventsResponseLocal,
)

_VALID_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"

_EVENTS_RESPONSE_BODY = [
    {
        "event_id": "e-0192a1b5-aaaa-7abc-89de-f0123456789a",
        "type": "task.created",
        "emitted_at": "2026-05-06T10:00:00Z",
        "payload": {"title": "test task"},
    },
    {
        "event_id": "e-0192a1b5-bbbb-7abc-89de-f0123456789a",
        "type": "task.decided",
        "emitted_at": "2026-05-06T10:01:00Z",
        "payload": {"action": "approve"},
    },
]

_CONNECT_ERROR = httpx.ConnectError("refused")


def _make_client() -> RegistryAPIClient:
    return RegistryAPIClient(base_url="http://registry-api:8080")


def _mock_events_200(
    body: list[dict[str, object]] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json=body if body is not None else _EVENTS_RESPONSE_BODY,
        request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/"),
    )


def _mock_events_error(
    status: int,
    body: dict[str, object],
) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/"),
    )


# --- get_task_events client method tests ---


@pytest.mark.asyncio
async def test_get_task_events_success() -> None:
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200(),
    ):
        result = await client.get_task_events(task_id=_VALID_TASK_ID)
    assert isinstance(result, TaskEventsResponseLocal)
    assert len(result.events) == 2
    assert result.events[0]["type"] == "task.created"


@pytest.mark.asyncio
async def test_get_task_events_empty() -> None:
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200([]),
    ):
        result = await client.get_task_events(task_id=_VALID_TASK_ID)
    assert result.events == []


@pytest.mark.asyncio
async def test_get_task_events_with_since() -> None:
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200([]),
    ) as mock_get:
        await client.get_task_events(task_id=_VALID_TASK_ID, since="2026-05-06T10:00:00Z")
    call_kwargs = mock_get.call_args
    assert call_kwargs[1]["params"]["since"] == "2026-05-06T10:00:00Z"


@pytest.mark.asyncio
async def test_get_task_events_invalid_task_id() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="Invalid task_id"):
        await client.get_task_events(task_id="bad-id")


@pytest.mark.asyncio
async def test_get_task_events_http_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=_mock_events_error(
                404,
                {"detail": "task not found"},
            ),
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client.get_task_events(task_id=_VALID_TASK_ID)


@pytest.mark.asyncio
async def test_get_task_events_network_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=_CONNECT_ERROR,
        ),
        pytest.raises(httpx.ConnectError),
    ):
        await client.get_task_events(task_id=_VALID_TASK_ID)


@pytest.mark.asyncio
async def test_get_task_events_malformed_body() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            return_value=httpx.Response(
                200,
                content=b"not json",
                request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/"),
            ),
        ),
        pytest.raises(json.JSONDecodeError),
    ):
        await client.get_task_events(task_id=_VALID_TASK_ID)


# --- events command tests (non-follow) ---


def test_events_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200(),
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID])
    assert result.exit_code == 0
    assert "task.created" in result.output
    assert "task.decided" in result.output


def test_events_command_no_events() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200([]),
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID])
    assert result.exit_code == 0
    assert "No events found" in (result.output + (result.stderr or ""))


def test_events_command_invalid_task_id() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "bad-id"])
    assert result.exit_code != 0
    assert "Invalid task ID" in (result.output + (result.stderr or ""))


def test_events_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_error(
            404,
            {"detail": "task not found"},
        ),
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


def test_events_command_network_error() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=_CONNECT_ERROR,
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "Could not reach registry-api" in (result.output + (result.stderr or ""))


# --- events --follow tests ---


def test_events_follow_prints_initial_events() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    first_resp = _mock_events_200(_EVENTS_RESPONSE_BODY)
    empty_resp = _mock_events_200([])
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=[first_resp, empty_resp],
        ),
        patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=RuntimeError("stop poll"),
        ),
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID, "--follow"])
    # RuntimeError becomes a non-zero exit (not 0) but events printed
    assert "task.created" in result.output
    assert "task.decided" in result.output


def test_events_follow_ctrl_c_exits_cleanly() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=KeyboardInterrupt(),
    ):
        result = runner.invoke(app, ["events", _VALID_TASK_ID, "--follow"])
    assert result.exit_code == 0


def test_events_follow_cursor_uses_since() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    first_resp = _mock_events_200(_EVENTS_RESPONSE_BODY)
    empty_resp = _mock_events_200([])
    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=[first_resp, empty_resp],
        ) as mock_get,
        patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=[None, RuntimeError("stop")],
        ),
    ):
        runner.invoke(app, ["events", _VALID_TASK_ID, "--follow"])
    # Second call should have since param from last emitted_at
    second_call = mock_get.call_args_list[1]
    assert second_call[1]["params"]["since"] == "2026-05-06T10:01:00Z"


# ---------------------------------------------------------------------------
# Story 9.4 — X-Trace-Id header propagation on get_task_events (AC6 #10-#11)
# ---------------------------------------------------------------------------


_FAKE_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"


@pytest.mark.asyncio
async def test_get_task_events_sends_x_trace_id_header_when_provided() -> None:
    """AC6 #10 — get_task_events propagates explicit trace_id as X-Trace-Id."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200(),
    ) as mock_get:
        await client.get_task_events(task_id=_VALID_TASK_ID, trace_id=_FAKE_TRACE_ID)
    headers = mock_get.call_args[1]["headers"]
    assert headers["X-Trace-Id"] == _FAKE_TRACE_ID


@pytest.mark.asyncio
async def test_get_task_events_omits_x_trace_id_header_when_none() -> None:
    """AC6 #11 — no X-Trace-Id header when trace_id is None."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200(),
    ) as mock_get:
        await client.get_task_events(task_id=_VALID_TASK_ID, trace_id=None)
    headers = mock_get.call_args[1]["headers"]
    assert "X-Trace-Id" not in headers


@pytest.mark.asyncio
async def test_get_task_events_omits_x_trace_id_header_when_empty_string() -> None:
    """Empty-string trace_id filtered (defense-in-depth, Q9 pattern)."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_events_200(),
    ) as mock_get:
        await client.get_task_events(task_id=_VALID_TASK_ID, trace_id="")
    headers = mock_get.call_args[1]["headers"]
    assert "X-Trace-Id" not in headers
