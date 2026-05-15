"""Tests for GitHubClient adapter — PR draft, branch ops, retries, config (Story 5.7).

Mocks aiohttp.ClientSession by injecting fake session objects that return
async-context-manager responses.  No live GitHub API calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest
from pydantic import SecretStr

from worker_wrapper.adapters.github_client import (
    BranchResult,
    GitHubClient,
    PRDraftResult,
)
from worker_wrapper.app.config import WorkerSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN = "ghp_testtoken123"


def _settings(**overrides: Any) -> WorkerSettings:
    defaults: dict[str, Any] = {
        "github_token": SecretStr(_TOKEN),
        "github_api_base_url": "https://api.github.com",
        "github_timeout_s": 10.0,
    }
    defaults.update(overrides)
    return WorkerSettings(**defaults)


class _FakeResponse:
    """Async-context-manager response mock."""

    def __init__(
        self,
        status: int = 200,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self._json = json_body or {}
        self.request_info = MagicMock()
        self.history: list[Any] = []

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._json

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


class _MockSession:
    """Mock aiohttp.ClientSession that returns ordered async-cm responses."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0
        self.last_kwargs: dict[str, Any] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.call_count += 1
        self.last_kwargs = kwargs
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return _FakeResponse(status=500, json_body={"message": "no more mocks"})

    async def close(self) -> None:
        pass


class _ErrorSession:
    """Mock session whose request() raises on every call."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def request(self, method: str, url: str, **kwargs: Any) -> None:
        self.call_count += 1
        raise self._error

    async def close(self) -> None:
        pass


def _client(settings: WorkerSettings, session: Any) -> GitHubClient:
    """Create a GitHubClient with an injected mock session and token."""
    client = GitHubClient(settings)
    client._session = session
    client._token = settings.github_token.get_secret_value()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPRDraftCreation:
    """AC-1: PR draft creation."""

    @pytest.mark.asyncio
    async def test_happy_path_pr_creation(self) -> None:
        settings = _settings()
        resp = _FakeResponse(
            status=201,
            json_body={
                "html_url": "https://github.com/owner/repo/pull/42",
                "number": 42,
            },
        )
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft(
            "owner", "repo", "Add feature", "feat-x", "main", "Description"
        )

        assert isinstance(result, PRDraftResult)
        assert result.success is True
        assert result.url == "https://github.com/owner/repo/pull/42"
        assert result.number == 42
        assert result.error is None
        assert session.call_count == 1


class TestBranchCreation:
    """AC-4: Branch operations."""

    @pytest.mark.asyncio
    async def test_happy_path_branch_creation(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=201, json_body={"ref": "refs/heads/feat-x"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_branch(
            "owner", "repo", "refs/heads/feat-x", "abc123def456abc123def456abc123def456abcd"
        )

        assert isinstance(result, BranchResult)
        assert result.success is True
        assert result.ref == "refs/heads/feat-x"
        assert result.error is None
        assert session.call_count == 1


class TestRetryBehavior:
    """AC-2: Retry resilience — 5xx retried, 4xx not."""

    @pytest.mark.asyncio
    async def test_retries_on_500_then_succeeds(self) -> None:
        settings = _settings()
        error_500 = _FakeResponse(status=500, json_body={"message": "Internal Server Error"})
        success = _FakeResponse(
            status=201,
            json_body={
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            },
        )
        session = _MockSession([error_500, success])
        client = _client(settings, session)

        result = await client.create_pr_draft("owner", "repo", "Title", "head", "main")

        assert result.success is True
        assert session.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=400, json_body={"message": "Bad request"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("owner", "repo", "Title", "head", "main")

        assert result.success is False
        assert session.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_404(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=404, json_body={"message": "Not Found"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("owner", "repo", "Title", "head", "main")

        assert result.success is False
        assert "Not Found" in (result.error or "")
        assert session.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_422(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=422, json_body={"message": "Validation Failed"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("owner", "repo", "Title", "head", "main")

        assert result.success is False
        assert session.call_count == 1


class TestRateLimit:
    """AC-2: 429 rate limit triggers retry."""

    @pytest.mark.asyncio
    async def test_retries_on_429(self) -> None:
        settings = _settings()
        rate_limited = _FakeResponse(status=429, json_body={"message": "Rate limited"})
        success = _FakeResponse(
            status=201,
            json_body={"html_url": "https://github.com/o/r/pull/5", "number": 5},
        )
        session = _MockSession([rate_limited, success])
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is True
        assert session.call_count == 2


class TestAuthAndToken:
    """AC-6, AC-7: Config and secret hygiene."""

    @pytest.mark.asyncio
    async def test_empty_token_returns_pr_error(self) -> None:
        settings = _settings(github_token=SecretStr(""))
        client = _client(settings, None)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "GITHUB_TOKEN" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_token_returns_branch_error(self) -> None:
        settings = _settings(github_token=SecretStr(""))
        client = _client(settings, None)

        result = await client.create_branch("o", "r", "refs/heads/x", "abc")

        assert result.success is False
        assert "GITHUB_TOKEN" in (result.error or "")


class TestTimeout:
    """AC-2: Total timeout handling — per-request timeout triggers retry."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self) -> None:
        settings = _settings(github_timeout_s=0.5)
        session = _ErrorSession(TimeoutError())
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "timed out" in (result.error or "").lower()


class TestIdempotencyKey:
    """AC-2: Idempotency key header."""

    @pytest.mark.asyncio
    async def test_idempotency_key_header_present(self) -> None:
        settings = _settings()
        resp = _FakeResponse(
            status=201,
            json_body={"html_url": "https://github.com/o/r/pull/1", "number": 1},
        )
        session = _MockSession([resp])
        client = _client(settings, session)

        await client.create_pr_draft("o", "r", "T", "h", "main")

        headers = session.last_kwargs.get("headers", {})
        assert "GitHub-Idempotency-Key" in headers


class TestAuthError:
    """AC-5: No exceptions for expected failures."""

    @pytest.mark.asyncio
    async def test_401_returns_error_result(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=401, json_body={"message": "Bad credentials"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "Bad credentials" in (result.error or "")
        assert session.call_count == 1

    @pytest.mark.asyncio
    async def test_403_returns_error_result(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=403, json_body={"message": "Forbidden"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "Forbidden" in (result.error or "")
        assert session.call_count == 1

    @pytest.mark.asyncio
    async def test_401_branch_returns_error(self) -> None:
        settings = _settings()
        resp = _FakeResponse(status=401, json_body={"message": "Bad credentials"})
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_branch(
            "o",
            "r",
            "refs/heads/x",
            "abc123def456abc123def456abc123def456abcd",
        )

        assert result.success is False
        assert "Bad credentials" in (result.error or "")


class TestNetworkError:
    """AC-2: Network errors trigger retry, all failures return error result."""

    @pytest.mark.asyncio
    async def test_client_error_returns_error_result(self) -> None:
        settings = _settings()
        session = _ErrorSession(aiohttp.ClientError("Connection refused"))
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "Connection refused" in (result.error or "")
        assert session.call_count == 3  # 3 attempts, all failed


class TestConfigLoading:
    """AC-6: Config via pydantic-settings."""

    def test_github_settings_defaults(self) -> None:
        s = WorkerSettings()
        assert s.github_api_base_url == "https://api.github.com"
        assert s.github_timeout_s == 10.0

    def test_github_token_is_secret_str(self) -> None:
        s = WorkerSettings()
        assert isinstance(s.github_token, SecretStr)
        assert s.github_token.get_secret_value() == ""

    def test_custom_base_url(self) -> None:
        settings = _settings(github_api_base_url="https://github.enterprise.com/api/v3")
        assert settings.github_api_base_url == "https://github.enterprise.com/api/v3"

    def test_timeout_must_be_positive(self) -> None:
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            _settings(github_timeout_s=0.0)

    def test_timeout_must_be_positive_negative(self) -> None:
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            _settings(github_timeout_s=-1.0)


class TestContextManager:
    """AC-3: Async context manager for session lifecycle."""

    @pytest.mark.asyncio
    async def test_session_created_and_closed(self) -> None:
        settings = _settings()
        async with GitHubClient(settings) as client:
            assert client._session is not None
            assert client._token == _TOKEN
        assert client._session is None
        assert client._closed is True

    @pytest.mark.asyncio
    async def test_request_without_context_manager_raises(self) -> None:
        settings = _settings()
        client = GitHubClient(settings)
        with pytest.raises(RuntimeError, match="async context manager"):
            await client._request("GET", "/test")


class TestInputValidation:
    """Input validation on owner, repo, ref, sha."""

    @pytest.mark.asyncio
    async def test_invalid_owner_with_slash(self) -> None:
        settings = _settings()
        client = _client(settings, None)
        result = await client.create_pr_draft("bad/owner", "repo", "T", "h", "main")
        assert result.success is False
        assert "owner" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_empty_repo(self) -> None:
        settings = _settings()
        client = _client(settings, None)
        result = await client.create_pr_draft("owner", "", "T", "h", "main")
        assert result.success is False
        assert "repo" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_invalid_ref_prefix(self) -> None:
        settings = _settings()
        client = _client(settings, None)
        result = await client.create_branch("o", "r", "heads/x", "a" * 40)
        assert result.success is False
        assert "refs/" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_sha_format(self) -> None:
        settings = _settings()
        client = _client(settings, None)
        result = await client.create_branch("o", "r", "refs/heads/x", "not-hex")
        assert result.success is False
        assert "sha" in (result.error or "").lower()


class TestError422Shape:
    """422 response with errors array."""

    @pytest.mark.asyncio
    async def test_422_with_errors_array(self) -> None:
        settings = _settings()
        resp = _FakeResponse(
            status=422,
            json_body={
                "message": "Validation Failed",
                "errors": [
                    {"message": "Invalid branch name"},
                    {"message": "SHA mismatch"},
                ],
            },
        )
        session = _MockSession([resp])
        client = _client(settings, session)

        result = await client.create_pr_draft("o", "r", "T", "h", "main")

        assert result.success is False
        assert "Invalid branch name" in (result.error or "")
        assert "SHA mismatch" in (result.error or "")
        assert session.call_count == 1
