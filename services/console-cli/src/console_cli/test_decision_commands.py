"""Tests for submit_decision client method + approve/reject/stop/retry commands (Story 4.3)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli._test_fixtures import FAKE_TRACE_ID_UUIDV7, UUIDV7_BARE_RE_PATTERN
from console_cli.adapters.registry_api_client import (
    DecisionResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.main import app

_VALID_TASK_ID = "t-0192a1b5-1234-7abc-89de-f0123456789a"
_DECISION_RESPONSE_BODY = {
    "task_id": _VALID_TASK_ID,
    "decision_id": "d-0192a1b5-5678-7def-90ab-cdef01234567",
    "action": "approve",
    "decided_at": "2026-05-06T12:00:00Z",
    "idempotency_status": "applied",
}


def _make_client() -> RegistryAPIClient:
    return RegistryAPIClient(base_url="http://registry-api:8080")


def _mock_200(body: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json=body,
        request=httpx.Request("POST", "http://registry-api:8080/v1/tasks/"),
    )


def _mock_error(
    status: int,
    body: dict[str, object],
) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", "http://registry-api:8080/v1/tasks/"),
    )


_CONNECT_ERROR = httpx.ConnectError("refused")


# --- submit_decision client method tests ---


@pytest.mark.asyncio
async def test_submit_decision_approve_success() -> None:
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(_DECISION_RESPONSE_BODY),
    ):
        result = await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
            request_id="req-456",
        )
    assert isinstance(result, DecisionResponseLocal)
    assert result.task_id == _VALID_TASK_ID
    assert result.action == "approve"
    assert result.idempotency_status == "applied"


@pytest.mark.asyncio
async def test_submit_decision_reject_with_hint() -> None:
    body = {**_DECISION_RESPONSE_BODY, "action": "reject"}
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ) as mock_post:
        result = await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="reject",
            idempotency_key="ik-123",
            hint="wrong branch",
        )
    assert result.action == "reject"
    call_kwargs = mock_post.call_args
    assert call_kwargs[1]["json"]["hint"] == "wrong branch"


@pytest.mark.asyncio
async def test_submit_decision_hint_omitted_when_none() -> None:
    body = {**_DECISION_RESPONSE_BODY, "action": "stop"}
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ) as mock_post:
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="stop",
            idempotency_key="ik-123",
        )
    call_kwargs = mock_post.call_args
    assert "hint" not in call_kwargs[1]["json"]


@pytest.mark.asyncio
async def test_submit_decision_invalid_task_id() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="Invalid task_id"):
        await client.submit_decision(
            task_id="bad-id",
            action="approve",
            idempotency_key="ik-123",
        )


@pytest.mark.asyncio
async def test_submit_decision_malformed_body() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_mock_200({"unexpected": True}),
        ),
        pytest.raises(RegistryResponseError, match="malformed body"),
    ):
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
        )


@pytest.mark.asyncio
async def test_submit_decision_http_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_mock_error(
                404,
                {"type": "about:blank", "title": "Not Found", "detail": "task not found"},
            ),
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
        )


@pytest.mark.asyncio
async def test_submit_decision_network_error() -> None:
    client = _make_client()
    with (
        patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=_CONNECT_ERROR,
        ),
        pytest.raises(httpx.ConnectError),
    ):
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
        )


# --- approve command tests ---


def test_approve_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    body = {**_DECISION_RESPONSE_BODY, "action": "approve"}
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ):
        result = runner.invoke(app, ["approve", _VALID_TASK_ID])
    assert result.exit_code == 0
    assert f"Approved {_VALID_TASK_ID}" in result.output


def test_approve_command_invalid_task_id() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["approve", "bad-id"])
    assert result.exit_code != 0
    assert "Invalid task ID" in (result.output + (result.stderr or ""))


def test_approve_command_network_error() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=_CONNECT_ERROR,
    ):
        result = runner.invoke(app, ["approve", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "Could not reach registry-api" in (result.output + (result.stderr or ""))


def test_approve_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_error(
            404,
            {"type": "about:blank", "title": "Not Found", "detail": "task not found"},
        ),
    ):
        result = runner.invoke(app, ["approve", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


# --- reject command tests ---


def test_reject_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    body = {**_DECISION_RESPONSE_BODY, "action": "reject"}
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ):
        result = runner.invoke(app, ["reject", _VALID_TASK_ID, "wrong branch"])
    assert result.exit_code == 0
    assert f"Rejected {_VALID_TASK_ID}" in result.output
    assert "wrong branch" in result.output


def test_reject_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_error(
            404,
            {"type": "about:blank", "title": "Not Found", "detail": "task not found"},
        ),
    ):
        result = runner.invoke(app, ["reject", _VALID_TASK_ID, "bad"])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


# --- stop command tests ---


def test_stop_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    body = {**_DECISION_RESPONSE_BODY, "action": "stop"}
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ):
        result = runner.invoke(app, ["stop", _VALID_TASK_ID])
    assert result.exit_code == 0
    assert f"Stopped {_VALID_TASK_ID}" in result.output


def test_stop_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_error(
            404,
            {"type": "about:blank", "title": "Not Found", "detail": "task not found"},
        ),
    ):
        result = runner.invoke(app, ["stop", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


# --- retry command tests ---


def test_retry_command_success() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    body = {**_DECISION_RESPONSE_BODY, "action": "retry"}
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ):
        result = runner.invoke(app, ["retry", _VALID_TASK_ID, "--hint", "fix the rate limit"])
    assert result.exit_code == 0
    assert f"Retrying {_VALID_TASK_ID}" in result.output


def test_retry_command_without_hint() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    body = {**_DECISION_RESPONSE_BODY, "action": "retry"}
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(body),
    ) as mock_post:
        result = runner.invoke(app, ["retry", _VALID_TASK_ID])
    assert result.exit_code == 0
    call_kwargs = mock_post.call_args
    assert "hint" not in call_kwargs[1]["json"]


def test_retry_command_task_not_found() -> None:
    from typer.testing import CliRunner

    from console_cli.app.main import app

    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_error(
            404,
            {"type": "about:blank", "title": "Not Found", "detail": "task not found"},
        ),
    ):
        result = runner.invoke(app, ["retry", _VALID_TASK_ID])
    assert result.exit_code != 0
    assert "not found" in (result.output + (result.stderr or "")).lower()


# ---------------------------------------------------------------------------
# Story 9.4 — X-Trace-Id header propagation on submit_decision (AC6 #7-#9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_decision_sends_x_trace_id_header_when_provided() -> None:
    """AC6 #7 — explicit trace_id propagates to outbound POST decisions."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(_DECISION_RESPONSE_BODY),
    ) as mock_post:
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
            trace_id=FAKE_TRACE_ID_UUIDV7,
        )
    headers = mock_post.call_args[1]["headers"]
    assert headers["X-Trace-Id"] == FAKE_TRACE_ID_UUIDV7


@pytest.mark.asyncio
async def test_submit_decision_omits_x_trace_id_header_when_none() -> None:
    """AC6 #8 — no X-Trace-Id header when trace_id is None."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(_DECISION_RESPONSE_BODY),
    ) as mock_post:
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
            trace_id=None,
        )
    headers = mock_post.call_args[1]["headers"]
    assert "X-Trace-Id" not in headers


@pytest.mark.asyncio
async def test_submit_decision_omits_x_trace_id_header_when_empty_string() -> None:
    """AC6 #9 — empty-string trace_id filtered (defense-in-depth, Q9 pattern)."""
    client = _make_client()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(_DECISION_RESPONSE_BODY),
    ) as mock_post:
        await client.submit_decision(
            task_id=_VALID_TASK_ID,
            action="approve",
            idempotency_key="ik-123",
            trace_id="",
        )
    headers = mock_post.call_args[1]["headers"]
    assert "X-Trace-Id" not in headers


def test_approve_command_propagates_x_trace_id_to_outbound_request() -> None:
    """End-to-end CliRunner — ``approve`` mints + forwards bare UUIDv7 trace_id.

    R8: also asserts the three distinct mints (X-Request-ID, Idempotency-Key,
    X-Trace-Id) are pairwise distinct values — defends against a regression
    where a single id is accidentally reused across all three headers.
    """
    runner = CliRunner()
    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=_mock_200(_DECISION_RESPONSE_BODY),
    ) as mock_post:
        result = runner.invoke(app, ["approve", _VALID_TASK_ID])
    assert result.exit_code == 0
    headers = mock_post.call_args[1]["headers"]
    assert "X-Trace-Id" in headers
    assert re.match(UUIDV7_BARE_RE_PATTERN, headers["X-Trace-Id"]), headers["X-Trace-Id"]
    # R8 — distinct-mint sanity (mirrors test_task_command's invariant).
    assert "X-Request-ID" in headers
    assert "Idempotency-Key" in headers
    assert headers["X-Request-ID"] != headers["X-Trace-Id"]
    assert headers["X-Request-ID"] != headers["Idempotency-Key"]
    assert headers["Idempotency-Key"] != headers["X-Trace-Id"]
