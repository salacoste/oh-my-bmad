"""Tests for agent command (Story 4.3 AC-6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

_VALID_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"

_TASK_RESPONSE_BODY = {
    "task_id": _VALID_TASK_ID,
    "status": "running",
    "title": "add idempotency middleware",
    "created_at": "2026-05-06T10:00:00Z",
    "updated_at": "2026-05-06T11:00:00Z",
    "actor": {"kind": "operator", "id": "console"},
    "last_event": None,
    "next_commands": [],
}

_CONNECT_ERROR = httpx.ConnectError("refused")


def _mock_task_200() -> httpx.Response:
    return httpx.Response(
        200,
        json=_TASK_RESPONSE_BODY,
        request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/"),
    )


def _mock_task_error(
    status: int,
    body: dict[str, object],
) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "http://registry-api:8080/v1/tasks/"),
    )


# --- agent command tests ---


def test_agent_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_task_200(),
    ):
        result = runner.invoke(app, ["agent", _VALID_TASK_ID])
    assert result.exit_code == 0
    assert f"Task {_VALID_TASK_ID}" in result.output
    assert "runtime=claude-code" in result.output


def test_agent_command_invalid_task_id() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["agent", "bad-id"])
    assert result.exit_code != 0
    assert "Invalid task ID" in (result.output + (result.stderr or ""))


def test_agent_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_task_error(404, {"detail": "task not found"}),
    ):
        result = runner.invoke(app, ["agent", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


def test_agent_command_network_error() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=_CONNECT_ERROR,
    ):
        result = runner.invoke(app, ["agent", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "Could not reach registry-api" in (result.output + (result.stderr or ""))


def test_agent_command_malformed_body() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_task_error(200, {"unexpected": True}),
    ):
        result = runner.invoke(app, ["agent", _VALID_TASK_ID])
    assert result.exit_code != 0
