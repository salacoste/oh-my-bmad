"""Tests for RegistryAPIClient and task command (Story 4.2 AC-9)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from console_cli._test_fixtures import FAKE_TRACE_ID_UUIDV7, UUIDV7_BARE_RE_PATTERN
from console_cli.adapters.registry_api_client import (
    TASK_ID_PATTERN,
    CreateTaskResponseLocal,
    RegistryAPIClient,
    RegistryResponseError,
)
from console_cli.app.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_BASE_URL = "http://registry-api:8080"
_FAKE_TASK_ID = "t-019abcde-f012-7abc-8def-0123456789ab"


def _make_response(
    status_code: int = 201,
    body: Mapping[str, object] | None = None,
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
                trace_id=FAKE_TRACE_ID_UUIDV7,
            )
        headers = mock_post.call_args[1]["headers"]
        assert headers["X-Trace-Id"] == FAKE_TRACE_ID_UUIDV7

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

    # ---------------------------------------------------------------------
    # Pass-2 S1: whitespace / NUL / CRLF rejection at the boundary —
    # ``isinstance(trace_id, str) and is_valid_trace_id(trace_id)`` rejects
    # values that the pre-pass-2 ``and trace_id`` guard would have accepted.
    # ---------------------------------------------------------------------

    @pytest.mark.parametrize(
        "malformed_trace_id",
        [
            # Whitespace-only (truthy under naive ``and trace_id``).
            " ",
            "    ",
            # Leading / trailing whitespace around an otherwise-valid uuidv7.
            f" {FAKE_TRACE_ID_UUIDV7} ",
            # Newline-terminated (LF) — could split a header in a naive
            # http parser.
            f"{FAKE_TRACE_ID_UUIDV7}\n",
            # CRLF injection attempt (RFC 7230 §3.2.4) — would append
            # a phantom ``X-Evil`` header if the value reached httpx.
            f"{FAKE_TRACE_ID_UUIDV7}\r\nX-Evil: 1",
            # NUL byte embedded.
            f"{FAKE_TRACE_ID_UUIDV7}\x00",
            # Random garbage that's not a uuidv7.
            "not-a-uuid",
        ],
    )
    @pytest.mark.asyncio
    async def test_rejects_malformed_trace_id_at_boundary(self, malformed_trace_id: str) -> None:
        """Pass-2 S1 — ``is_valid_trace_id`` guard drops whitespace/CRLF/garbage."""
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
                trace_id=malformed_trace_id,
            )
        headers = mock_post.call_args[1]["headers"]
        assert "X-Trace-Id" not in headers, (
            f"malformed trace_id {malformed_trace_id!r} leaked into outbound header"
        )


# ---------------------------------------------------------------------------
# R11 — per-method X-Trace-Id propagation unit tests for the 3 client methods
# that lacked dedicated coverage (get_task / get_logs_digest /
# get_platform_health).
# ---------------------------------------------------------------------------


def _task_body_for_get() -> dict[str, object]:
    return {
        "task_id": _FAKE_TASK_ID,
        "status": "planning",
        "title": "trace test",
        "created_at": "2026-05-05T12:00:00Z",
        "updated_at": "2026-05-05T12:01:00Z",
        "actor": {"kind": "operator", "id": "console"},
        "last_event": None,
        "next_commands": [],
    }


def _logs_digest_body_for_get() -> dict[str, object]:
    return {
        "task_id": _FAKE_TASK_ID,
        "digest": "fixture digest",
        "truncated": False,
        "line_count": 1,
    }


def _health_body_for_get() -> dict[str, object]:
    return {
        "registry_status": "healthy",
        "worker_status": "idle",
        "clawhip_queue_depth": 0,
        "version": "v0.1.0",
    }


@pytest.mark.asyncio
async def test_get_task_sends_x_trace_id_header_when_provided() -> None:
    """R11 — ``get_task`` propagates trace_id as X-Trace-Id."""
    client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=httpx.Response(
            200,
            json=_task_body_for_get(),
            request=httpx.Request("GET", f"{_FAKE_BASE_URL}/v1/tasks/{_FAKE_TASK_ID}"),
        ),
    ) as mock_get:
        await client.get_task(task_id=_FAKE_TASK_ID, trace_id=FAKE_TRACE_ID_UUIDV7)
    headers = mock_get.call_args[1]["headers"]
    assert headers["X-Trace-Id"] == FAKE_TRACE_ID_UUIDV7


@pytest.mark.asyncio
async def test_get_logs_digest_sends_x_trace_id_header_when_provided() -> None:
    """R11 — ``get_logs_digest`` propagates trace_id as X-Trace-Id."""
    client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=httpx.Response(
            200,
            json=_logs_digest_body_for_get(),
            request=httpx.Request(
                "GET",
                f"{_FAKE_BASE_URL}/v1/tasks/{_FAKE_TASK_ID}/logs/digest",
            ),
        ),
    ) as mock_get:
        await client.get_logs_digest(task_id=_FAKE_TASK_ID, trace_id=FAKE_TRACE_ID_UUIDV7)
    headers = mock_get.call_args[1]["headers"]
    assert headers["X-Trace-Id"] == FAKE_TRACE_ID_UUIDV7


@pytest.mark.asyncio
async def test_get_platform_health_sends_x_trace_id_header_when_provided() -> None:
    """R11 — ``get_platform_health`` propagates trace_id as X-Trace-Id."""
    client = RegistryAPIClient(base_url=_FAKE_BASE_URL)
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=httpx.Response(
            200,
            json=_health_body_for_get(),
            request=httpx.Request("GET", f"{_FAKE_BASE_URL}/v1/health"),
        ),
    ) as mock_get:
        await client.get_platform_health(trace_id=FAKE_TRACE_ID_UUIDV7)
    headers = mock_get.call_args[1]["headers"]
    assert headers["X-Trace-Id"] == FAKE_TRACE_ID_UUIDV7


class TestTaskCliRunnerTraceIdPropagation:
    """AC9 — end-to-end CliRunner test asserting ``X-Trace-Id`` reaches the wire."""

    def test_task_command_propagates_trace_id_to_registry_api(self) -> None:
        """AC9 — ``oh-my-bmad-cli task <title>`` outbound X-Trace-Id is bare UUIDv7."""
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
        assert re.match(UUIDV7_BARE_RE_PATTERN, headers["X-Trace-Id"]), (
            f"X-Trace-Id {headers['X-Trace-Id']!r} must be bare UUIDv7"
        )
        # Sanity: X-Request-ID is still set (AC5 — existing semantics unchanged).
        assert "X-Request-ID" in headers
        # And it's a distinct value from X-Trace-Id (independent mints).
        assert headers["X-Request-ID"] != headers["X-Trace-Id"]
