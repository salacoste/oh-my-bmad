"""GitHub REST API adapter — PR draft creation, branch operations, retries (Story 5.7).

Uses ``aiohttp`` for async HTTP and ``tenacity`` for 3x exponential-backoff
retries on 5xx / network errors.  Structured result dataclasses — no exceptions
propagated for expected failures.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import aiohttp
import structlog
import tenacity
from events.ids import new_idempotency_key

from worker_wrapper.app.config import WorkerSettings

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _make_retry(total_timeout_s: float) -> tenacity.AsyncRetrying:
    return tenacity.AsyncRetrying(
        stop=tenacity.stop_after_attempt(3) | tenacity.stop_after_delay(total_timeout_s),
        wait=tenacity.wait_exponential(multiplier=0.5, max=5) + tenacity.wait_random(0, 0.5),
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
        self._closed = False
        self._token: str = ""

    async def __aenter__(self) -> GitHubClient:
        try:
            self._session = aiohttp.ClientSession()
        except BaseException:
            self._session = None
            raise
        self._token = self._settings.github_token.get_secret_value()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            h["GitHub-Idempotency-Key"] = idempotency_key
        return h

    def _url(self, path: str) -> str:
        base = self._settings.github_api_base_url.rstrip("/")
        return f"{base}{path}"

    def _validate_owner_repo(self, owner: str, repo: str) -> str | None:
        if not owner or "/" in owner:
            return f"Invalid owner: {owner!r}"
        if not repo or "/" in repo:
            return f"Invalid repo: {repo!r}"
        return None

    def _validate_branch_params(self, ref: str, sha: str) -> str | None:
        if not ref.startswith("refs/"):
            return f"ref must start with 'refs/': {ref!r}"
        if not _SHA_RE.match(sha):
            return f"sha must be 40-char hex: {sha!r}"
        return None

    @staticmethod
    def _extract_error(resp_body: dict[str, object]) -> str:
        message = resp_body.get("message")
        errors = resp_body.get("errors")
        if isinstance(errors, list) and errors:
            parts = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]
            return f"{message}: {'; '.join(parts)}" if message else "; ".join(parts)
        return str(message) if message is not None else "Unknown error"

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Make an HTTP request with retries and per-attempt timeout."""
        if self._session is None or self._closed:
            raise RuntimeError("GitHubClient must be used as async context manager")
        if not self._token:
            return 0, {}
        log = structlog.get_logger(__name__)
        key = idempotency_key or new_idempotency_key()
        headers = self._headers(key)
        url = self._url(path)
        retry = _make_retry(self._settings.github_timeout_s)

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
                    log.debug("github_json_parse_failed", status=resp.status, path=path)
                    body = {}
                if resp.status >= 400:
                    if resp.status == 429:
                        log.warning("github_rate_limited", path=path)
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message="Rate limited",
                        )
                    if resp.status >= 500:
                        log.warning(
                            "github_server_error",
                            status=resp.status,
                            path=path,
                        )
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message=body.get("message", "Server error"),
                        )
                    log.debug(
                        "github_client_error",
                        status=resp.status,
                        path=path,
                    )
                return resp.status, body

        return await retry(_do)

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
        if not self._token:
            log.warning("github_token_missing")
            return PRDraftResult(success=False, error="GITHUB_TOKEN not configured")
        validation = self._validate_owner_repo(owner, repo)
        if validation:
            return PRDraftResult(success=False, error=validation)

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

        return PRDraftResult(success=False, error=self._extract_error(resp))

    async def create_branch(
        self,
        owner: str,
        repo: str,
        ref: str,
        sha: str,
    ) -> BranchResult:
        """Create a branch ref via ``POST /repos/{owner}/{repo}/git/refs``."""
        log = structlog.get_logger(__name__)
        if not self._token:
            log.warning("github_token_missing")
            return BranchResult(success=False, error="GITHUB_TOKEN not configured")
        validation = self._validate_owner_repo(owner, repo)
        if validation:
            return BranchResult(success=False, error=validation)
        validation = self._validate_branch_params(ref, sha)
        if validation:
            return BranchResult(success=False, error=validation)

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

        return BranchResult(success=False, error=self._extract_error(resp))


__all__ = [
    "BranchResult",
    "GitHubClient",
    "PRDraftResult",
]
