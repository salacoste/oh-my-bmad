"""GitHub REST API adapter — PR draft creation, branch operations, retries (Story 5.7).

Uses ``aiohttp`` for async HTTP and ``tenacity`` for 3x exponential-backoff
retries on 5xx / network errors.  Structured result dataclasses — no exceptions
propagated for expected failures.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import structlog
import tenacity
from events.ids import new_idempotency_key

from worker_wrapper.app.config import WorkerSettings

_RETRY = tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=0.5, max=5)
    + tenacity.wait_random(0, 0.5),
    retry=tenacity.retry_if_exception_type(
        (aiohttp.ClientError, asyncio.TimeoutError),
    ),
    reraise=True,
)


@dataclass
class PRDraftResult:
    """Structured result from PR draft creation."""

    success: bool
    url: str | None = None
    number: int | None = None
    error: str | None = None


@dataclass
class BranchResult:
    """Structured result from branch ref creation."""

    success: bool
    ref: str | None = None
    error: str | None = None


class GitHubClient:
    """Async GitHub REST API adapter with retries.

    Usage::

        async with GitHubClient(settings) as gh:
            result = await gh.create_pr_draft(
                "owner", "repo", "Title", "head-branch", "main", "Body"
            )
            if result.success:
                print(result.url)
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> GitHubClient:
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._settings.github_token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            h["GitHub-Idempotency-Key"] = idempotency_key
        return h

    def _url(self, path: str) -> str:
        base = self._settings.github_api_base_url.rstrip("/")
        return f"{base}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, object],
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Make an HTTP request with retries and timeout wrapping."""
        if self._session is None:
            raise RuntimeError("GitHubClient must be used as async context manager")
        token = self._settings.github_token.get_secret_value()
        if not token:
            return 0, {}
        log = structlog.get_logger(__name__)
        key = idempotency_key or new_idempotency_key()
        headers = self._headers(key)
        url = self._url(path)

        @_RETRY
        async def _do() -> tuple[int, dict[str, object]]:
            assert self._session is not None
            timeout = aiohttp.ClientTimeout(total=self._settings.github_timeout_s)
            async with self._session.request(
                method, url, json=json_body, headers=headers, timeout=timeout
            ) as resp:
                body: dict[str, object] = {}
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {}
                if resp.status >= 400:
                    log.warning(
                        "github_api_error",
                        status=resp.status,
                        path=path,
                    )
                    if resp.status in (429,):
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message="Rate limited",
                        )
                    if resp.status >= 500:
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message=body.get("message", "Server error"),
                        )
                return resp.status, body

        return await asyncio.wait_for(_do(), timeout=self._settings.github_timeout_s * 3)

    async def create_pr_draft(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> PRDraftResult:
        """Create a draft PR via ``POST /repos/{owner}/{repo}/pulls``."""
        log = structlog.get_logger(__name__)
        token = self._settings.github_token.get_secret_value()
        if not token:
            log.warning("github_token_missing")
            return PRDraftResult(success=False, error="GITHUB_TOKEN not configured")

        path = f"/repos/{owner}/{repo}/pulls"
        payload: dict[str, object] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": True,
        }

        try:
            status, resp = await self._request("POST", path, payload)
        except TimeoutError:
            log.error("github_pr_timeout")
            return PRDraftResult(success=False, error="Request timed out after retries")
        except aiohttp.ClientError as exc:
            log.error("github_pr_error", error=str(exc))
            return PRDraftResult(success=False, error=str(exc))

        if status in (201, 200):
            url = resp.get("html_url")
            number = resp.get("number")
            log.info(
                "github_pr_created",
                number=number,
                url=url,
            )
            return PRDraftResult(
                success=True,
                url=str(url) if url is not None else None,
                number=int(number) if number is not None else None,
            )

        message = resp.get("message", "Unknown error")
        return PRDraftResult(success=False, error=str(message))

    async def create_branch(
        self,
        owner: str,
        repo: str,
        ref: str,
        sha: str,
    ) -> BranchResult:
        """Create a branch ref via ``POST /repos/{owner}/{repo}/git/refs``."""
        log = structlog.get_logger(__name__)
        token = self._settings.github_token.get_secret_value()
        if not token:
            log.warning("github_token_missing")
            return BranchResult(success=False, error="GITHUB_TOKEN not configured")

        path = f"/repos/{owner}/{repo}/git/refs"
        payload: dict[str, object] = {"ref": ref, "sha": sha}

        try:
            status, resp = await self._request("POST", path, payload)
        except TimeoutError:
            log.error("github_branch_timeout")
            return BranchResult(success=False, error="Request timed out after retries")
        except aiohttp.ClientError as exc:
            log.error("github_branch_error", error=str(exc))
            return BranchResult(success=False, error=str(exc))

        if status in (201, 200):
            log.info("github_branch_created", ref=ref)
            return BranchResult(success=True, ref=ref)

        message = resp.get("message", "Unknown error")
        return BranchResult(success=False, error=str(message))


__all__ = [
    "BranchResult",
    "GitHubClient",
    "PRDraftResult",
]
