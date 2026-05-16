"""Tests for RegistryAPIClient and task command (Story 4.2 AC-9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from console_cli.adapters.registry_api_client import (
    TASK_ID_PATTERN,
    CreateTaskResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_BASE_URL = "http://registry-api:8080"
_FAKE_TASK_ID = "t-019abcde-f012-7abc-8def-0123456789ab"


def _make_response(
    status_code: int = 201,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=body,
        headers=headers or {},
        request=httpx.Request("POST", "http://registry-api:8080/v1/tasks"),
    )


# ---------------------------------------------------------------------------
# TASK_ID_PATTERN tests
# ---------------------------------------------------------------------------


class TestTaskIdPattern:
    def test_valid_task_id(self) -> None:
        assert TASK_ID_PATTERN.match(_FAKE_TASK_ID)

    def test_missing_prefix(self) -> None:
        assert not TASK_ID_PATTERN.match("019abcde-f012-7abc-8def-0123456789ab")

    def test_uppercase_hex_rejected(self) -> None:
        assert not TASK_ID_PATTERN.match("t-019ABCDE-f012-7abc-8def-0123456789ab")

    def test_wrong_version_nibble(self) -> None:
        # Version nibble must be 7; changing it to 6 should fail.
        bad = "t-019abcde-f012-6abc-8def-0123456789ab"
        assert not TASK_ID_PATTERN.match(bad)


# ---------------------------------------------------------------------------
# create_task tests
# ---------------------------------------------------------------------------


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """AC-1: create_task returns CreateTaskResponseLocal on 201."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        body = {
            "task_id": _FAKE_TASK_ID,
            "event_id": "e-019abcde-f012-7abc-8def-0123456789ab",
            "created_at": "2026-05-05T12:00:00Z",
        }
        fake_response = _make_response(201, body)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            result = await client.create_task(
                title="add idempotency middleware",
                idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
            )

        assert isinstance(result, CreateTaskResponseLocal)
        assert result.task_id == _FAKE_TASK_ID
        assert result.idempotency_status == "applied"

    @pytest.mark.asyncio
    async def test_malformed_body_raises(self) -> None:
        """RegistryResponseError on 2xx with missing required fields."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(201, {"unexpected": "body"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(RegistryResponseError, match="malformed body"):
                await client.create_task(
                    title="test",
                    idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                )

    @pytest.mark.asyncio
    async def test_http_error_raises(self) -> None:
        """HTTPStatusError on 422 validation error."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        fake_response = _make_response(
            422,
            {"detail": "title is required"},
        )

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=fake_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.create_task(
                    title="test",
                    idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                )

    @pytest.mark.asyncio
    async def test_connect_error(self) -> None:
        """ConnectError propagates when registry-api is unreachable."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            with pytest.raises(httpx.ConnectError):
                await client.create_task(
                    title="test",
                    idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                )


# ---------------------------------------------------------------------------
# Story 9.4 — X-Trace-Id header propagation tests (AC6 #4-#6 + AC9)
# ---------------------------------------------------------------------------


class TestCreateTaskTraceIdHeader:
    """Verify ``create_task`` propagates ``trace_id`` as ``X-Trace-Id`` header."""

    _FAKE_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"

    @pytest.mark.asyncio
    async def test_sends_x_trace_id_header_when_provided(self) -> None:
        """AC6 #4 — explicit trace_id reaches the outbound httpx request."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        body = {
            "task_id": _FAKE_TASK_ID,
            "event_id": "e-019abcde-f012-7abc-8def-0123456789ab",
            "created_at": "2026-05-05T12:00:00Z",
        }
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_make_response(201, body),
        ) as mock_post:
            await client.create_task(
                title="trace test",
                idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                trace_id=self._FAKE_TRACE_ID,
            )
        headers = mock_post.call_args[1]["headers"]
        assert headers["X-Trace-Id"] == self._FAKE_TRACE_ID

    @pytest.mark.asyncio
    async def test_omits_x_trace_id_header_when_none(self) -> None:
        """AC6 #5 — no header set when trace_id is None (default)."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        body = {
            "task_id": _FAKE_TASK_ID,
            "event_id": "e-019abcde-f012-7abc-8def-0123456789ab",
            "created_at": "2026-05-05T12:00:00Z",
        }
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_make_response(201, body),
        ) as mock_post:
            await client.create_task(
                title="trace test",
                idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                trace_id=None,
            )
        headers = mock_post.call_args[1]["headers"]
        assert "X-Trace-Id" not in headers

    @pytest.mark.asyncio
    async def test_omits_x_trace_id_header_when_empty_string(self) -> None:
        """AC6 #6 — empty-string trace_id treated as None (defense-in-depth, Q9 pattern)."""
        client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
        body = {
            "task_id": _FAKE_TASK_ID,
            "event_id": "e-019abcde-f012-7abc-8def-0123456789ab",
            "created_at": "2026-05-05T12:00:00Z",
        }
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_make_response(201, body),
        ) as mock_post:
            await client.create_task(
                title="trace test",
                idempotency_key="019abcde-f012-7abc-8def-0123456789ab",
                trace_id="",
            )
        headers = mock_post.call_args[1]["headers"]
        assert "X-Trace-Id" not in headers


class TestTaskCliRunnerTraceIdPropagation:
    """AC9 — end-to-end CliRunner test asserting ``X-Trace-Id`` reaches the wire."""

    _UUIDV7_BARE_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

    def test_task_command_propagates_trace_id_to_registry_api(self) -> None:
        """AC9 — ``oh-my-bmad-cli task <title>`` outbound X-Trace-Id is bare UUIDv7."""
        import re

        from typer.testing import CliRunner

        from console_cli.app.main import app

        body = {
            "task_id": _FAKE_TASK_ID,
            "event_id": "e-019abcde-f012-7abc-8def-0123456789ab",
            "created_at": "2026-05-05T12:00:00Z",
        }
        runner = CliRunner()
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=_make_response(201, body),
        ) as mock_post:
            result = runner.invoke(app, ["task", "trace propagation test"])

        assert result.exit_code == 0, result.output
        headers = mock_post.call_args[1]["headers"]
        assert "X-Trace-Id" in headers
        assert re.match(self._UUIDV7_BARE_RE, headers["X-Trace-Id"]), (
            f"X-Trace-Id {headers['X-Trace-Id']!r} must be bare UUIDv7"
        )
        # Sanity: X-Request-ID is still set (AC5 — existing semantics unchanged).
        assert "X-Request-ID" in headers
        # And it's a distinct value from X-Trace-Id (independent mints).
        assert headers["X-Request-ID"] != headers["X-Trace-Id"]
