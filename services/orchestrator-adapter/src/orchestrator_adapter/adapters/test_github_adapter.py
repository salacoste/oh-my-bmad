"""Tests for GitHub adapter — PR draft creation (Story 5.14)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from orchestrator_adapter.adapters.github_adapter import GitHubAdapter

_PATCH_TARGET = (
    "orchestrator_adapter.adapters.github_adapter.aiohttp.ClientSession"
)


def _adapter(token: str = "ghp_test123") -> GitHubAdapter:
    return GitHubAdapter(
        token=token, base_url="https://api.github.com", timeout_s=5.0,
    )


def _mock_response(
    status: int,
    body: dict[str, Any],
) -> MagicMock:
    """Build a mock response that doubles as an async context manager."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.request_info = MagicMock()
    resp.history = ()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class _FakeSessionBase:
    """Async context manager session — ``request()`` returns an async CM."""

    async def __aenter__(self) -> _FakeSessionBase:
        return self

    async def __aexit__(self, *a: object) -> None:
        pass


# --- happy path ---


@pytest.mark.asyncio
async def test_create_pr_draft_success() -> None:
    resp = _mock_response(201, {
        "html_url": "https://github.com/owner/repo/pull/42",
        "number": 42,
        "head": {"ref": "task/T-001"},
    })

    class FakeSession(_FakeSessionBase):
        def request(self, *a: object, **kw: object) -> MagicMock:
            return resp

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main", "Body",
        )

    assert result.success
    assert result.url == "https://github.com/owner/repo/pull/42"
    assert result.number == 42
    assert result.branch == "task/T-001"


# --- retry on 5xx ---


@pytest.mark.asyncio
async def test_create_pr_draft_retries_on_500() -> None:
    error_resp = _mock_response(500, {"message": "Internal Server Error"})
    success_resp = _mock_response(201, {
        "html_url": "https://github.com/owner/repo/pull/1",
        "number": 1,
        "head": {"ref": "task/T-001"},
    })

    call_count = 0

    class FakeSession(_FakeSessionBase):
        def request(self, *a: object, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return error_resp
            return success_resp

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main",
        )

    assert result.success
    assert call_count == 3


# --- no retry on 4xx ---


@pytest.mark.asyncio
async def test_create_pr_draft_no_retry_on_422() -> None:
    resp = _mock_response(422, {
        "message": "Validation Failed",
        "errors": [{"message": "No commits between main and task/T-001"}],
    })

    call_count = 0

    class FakeSession(_FakeSessionBase):
        def request(self, *a: object, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return resp

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main",
        )

    assert not result.success
    assert "No commits" in (result.error or "")
    assert call_count == 1


# --- timeout ---


@pytest.mark.asyncio
async def test_create_pr_draft_timeout() -> None:
    class FakeSession(_FakeSessionBase):
        def request(self, *a: object, **kw: object) -> MagicMock:
            raise TimeoutError()

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main",
        )

    assert not result.success
    assert "timed out" in (result.error or "").lower()


# --- empty token ---


@pytest.mark.asyncio
async def test_create_pr_draft_empty_token() -> None:
    adapter = _adapter(token="")
    result = await adapter.create_pr_draft(
        "owner", "repo", "Title", "task/T-001", "main",
    )
    assert not result.success
    assert "token" in (result.error or "").lower()


# --- validation: bad owner/repo ---


@pytest.mark.asyncio
async def test_create_pr_draft_invalid_owner() -> None:
    adapter = _adapter()
    result = await adapter.create_pr_draft(
        "bad/owner", "repo", "Title", "task/T-001", "main",
    )
    assert not result.success
    assert "owner" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_create_pr_draft_invalid_repo() -> None:
    adapter = _adapter()
    result = await adapter.create_pr_draft(
        "owner", "", "Title", "task/T-001", "main",
    )
    assert not result.success
    assert "repo" in (result.error or "").lower()


# --- idempotency key header ---


@pytest.mark.asyncio
async def test_create_pr_draft_includes_idempotency_key() -> None:
    resp = _mock_response(201, {
        "html_url": "https://github.com/o/r/pull/1",
        "number": 1,
        "head": {"ref": "task/T-001"},
    })
    captured_headers: dict[str, str] = {}

    class FakeSession(_FakeSessionBase):
        def request(
            self, method: str, url: str, **kw: object,
        ) -> MagicMock:
            captured_headers.update(kw.get("headers", {}))  # type: ignore[arg-type]
            return resp

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main",
        )

    assert "GitHub-Idempotency-Key" in captured_headers
    assert captured_headers["Authorization"] == "Bearer ghp_test123"


# --- network error ---


@pytest.mark.asyncio
async def test_create_pr_draft_network_error() -> None:
    class FakeSession(_FakeSessionBase):
        def request(self, *a: object, **kw: object) -> MagicMock:
            raise aiohttp.ClientError("Connection refused")

    with patch(_PATCH_TARGET, return_value=FakeSession()):
        adapter = _adapter()
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "task/T-001", "main",
        )

    assert not result.success
    assert "Connection refused" in (result.error or "")
