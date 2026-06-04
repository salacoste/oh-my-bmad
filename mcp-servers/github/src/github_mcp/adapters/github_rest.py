"""github-mcp-local GitHub REST adapter — bounded read operations (Epic 16 / Story 16.3).

A small async GitHub REST client used by the Tier-1 read tools
(``github.issues.list`` / ``github.issues.get`` / ``github.prs.list`` /
``github.prs.get`` / ``github.reviews.list`` / ``github.reviews.get``). It mirrors
the pattern of ``worker_wrapper.adapters.github_client`` — ``aiohttp`` async HTTP +
``tenacity`` 3× exponential-backoff retries on 429/5xx, structured dataclass results,
no exceptions propagated for expected failures — but is a github-mcp-LOCAL copy:
the Story 5.8 import-graph rule (enforced by ``scripts/check_imports.py``) forbids an
mcp-server importing from ``services/``, so the worker adapter CANNOT be imported here.
Only the read surface is implemented (reads need no idempotency key / no write payload).

Auth uses the Story-16.5 narrowly-scoped GitHub credential (``scoped_token``) threaded
in from ``build_server``; the broad ``GITHUB_TOKEN`` is NEVER read here (no
``os.environ`` access at all — the token is an explicit constructor argument).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp
import tenacity

if TYPE_CHECKING:
    from types import TracebackType

log = logging.getLogger(__name__)

# Wall-clock bound for any single GitHub REST call (mirror of git-mcp's per-op
# timeout). A wedged request (slow network, rate-limit hold) must not hang the
# request path — each attempt is bounded and the whole retry budget is capped.
_DEFAULT_TIMEOUT_S: float = 30.0


def _make_retry(total_timeout_s: float) -> tenacity.AsyncRetrying:
    """3× exponential-backoff retry on transient HTTP/network errors.

    Byte-for-byte the worker adapter's policy (Story 5.7): retry on
    ``aiohttp.ClientError`` (raised by ``_request`` on 429/5xx) or a timeout,
    stop after 3 attempts OR the total-timeout budget, and reraise the last
    exception so the caller surfaces a structured error.
    """
    return tenacity.AsyncRetrying(
        stop=tenacity.stop_after_attempt(3) | tenacity.stop_after_delay(total_timeout_s),
        wait=tenacity.wait_exponential(multiplier=0.5, max=5) + tenacity.wait_random(0, 0.5),
        retry=tenacity.retry_if_exception_type(
            (aiohttp.ClientError, asyncio.TimeoutError),
        ),
        reraise=True,
    )


@dataclass
class ReadResult:
    """Structured result from a github-mcp read call.

    ``ok`` flags success; on success ``data`` carries the parsed JSON body (a
    JSON object for ``get`` calls, a list for ``list`` calls). On failure ``error``
    carries a single-line human-readable reason and ``status`` the HTTP code
    (``0`` for a no-token / transport failure that never reached GitHub).
    """

    ok: bool
    status: int = 0
    data: object | None = None
    error: str | None = None


class GitHubReadClient:
    """Async GitHub REST read client with retries (Tier-1 read tools).

    Usage::

        async with GitHubReadClient(scoped_token=tok) as gh:
            result = await gh.list_issues("owner", "repo")
            if result.ok:
                ...

    The client is an ``aiohttp.ClientSession`` wrapper; ``__aenter__`` opens the
    session and ``__aexit__`` closes it. Auth is the SCOPED token only.
    """

    def __init__(
        self,
        *,
        scoped_token: str,
        base_url: str = "https://api.github.com",
        total_timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token = scoped_token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = total_timeout_s
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    async def __aenter__(self) -> GitHubReadClient:
        try:
            self._session = aiohttp.ClientSession()
        except BaseException:
            self._session = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict[str, str]:
        """Bearer auth headers using the SCOPED token (never ``GITHUB_TOKEN``)."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    @staticmethod
    def _extract_error(body: object) -> str:
        """Surface GitHub's ``message`` field (single line), else a generic reason."""
        if isinstance(body, dict):
            message = body.get("message")
            if message is not None:
                return str(message).splitlines()[0] if message else ""
        return "Unknown error"

    async def _request(self, method: str, path: str) -> tuple[int, object]:
        """Central dispatch: bounded retry on 429/5xx, return ``(status, body)`` for 4xx.

        Mirrors the worker adapter's ``_request``: raise ``ClientResponseError`` on
        429 / 5xx (so the tenacity policy retries), return ``(status, body)`` without
        retry for any other status. Missing token short-circuits to ``(0, {})`` so a
        misconfigured credential never reaches GitHub (caller maps it to a structured
        no-token error, identical to the worker adapter's behaviour).
        """
        if self._session is None or self._closed:
            raise RuntimeError("GitHubReadClient must be used as an async context manager")
        if not self._token:
            return 0, {}
        headers = self._headers()
        url = self._url(path)
        retry = _make_retry(self._timeout_s)

        async def _do() -> tuple[int, object]:
            assert self._session is not None  # narrowed by the guard above
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            async with self._session.request(method, url, headers=headers, timeout=timeout) as resp:
                body: object
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ClientError, ValueError):
                    log.debug("github_json_parse_failed", extra={"status": resp.status})
                    body = {}
                if resp.status == 429:
                    log.warning("github_rate_limited", extra={"path": path})
                    raise aiohttp.ClientResponseError(
                        request_info=resp.request_info,
                        history=resp.history,
                        status=resp.status,
                        message="Rate limited",
                    )
                if resp.status >= 500:
                    log.warning("github_server_error", extra={"status": resp.status})
                    raise aiohttp.ClientResponseError(
                        request_info=resp.request_info,
                        history=resp.history,
                        status=resp.status,
                        message=self._extract_error(body),
                    )
                return resp.status, body

        return await retry(_do)

    async def _read(self, path: str) -> ReadResult:
        """Issue a GET to *path* and map the response to a ``ReadResult``.

        A no-token short-circuit, a transport failure, and a 4xx all map to a
        structured ``ok=False`` result — exceptions are never propagated to the
        tool boundary (mirror of the worker adapter's no-raise contract).
        """
        if not self._token:
            return ReadResult(ok=False, status=0, error="scoped GitHub token not configured")
        try:
            status, body = await self._request("GET", path)
        except TimeoutError:
            return ReadResult(ok=False, status=0, error="Request timed out after retries")
        except aiohttp.ClientError as exc:
            return ReadResult(ok=False, status=0, error=str(exc))
        if 200 <= status < 300:
            return ReadResult(ok=True, status=status, data=body)
        return ReadResult(ok=False, status=status, error=self._extract_error(body))

    # ------------------------------------------------------------------
    # Read surface (Tier-1). Owner/repo are placed directly in the path; the
    # handlers validate them before calling (no slash / non-empty) so a path
    # segment cannot be split into an unintended route.
    # ------------------------------------------------------------------

    async def list_issues(self, owner: str, repo: str, *, state: str = "open") -> ReadResult:
        """``GET /repos/{owner}/{repo}/issues`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/issues?state={state}")

    async def get_issue(self, owner: str, repo: str, number: int) -> ReadResult:
        """``GET /repos/{owner}/{repo}/issues/{number}`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/issues/{number}")

    async def list_pull_requests(self, owner: str, repo: str, *, state: str = "open") -> ReadResult:
        """``GET /repos/{owner}/{repo}/pulls`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/pulls?state={state}")

    async def get_pull_request(self, owner: str, repo: str, number: int) -> ReadResult:
        """``GET /repos/{owner}/{repo}/pulls/{number}`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/pulls/{number}")

    async def list_reviews(self, owner: str, repo: str, pull_number: int) -> ReadResult:
        """``GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews")

    async def get_review(
        self, owner: str, repo: str, pull_number: int, review_id: int
    ) -> ReadResult:
        """``GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}`` (Tier-1)."""
        return await self._read(f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}")


__all__ = ["GitHubReadClient", "ReadResult"]
