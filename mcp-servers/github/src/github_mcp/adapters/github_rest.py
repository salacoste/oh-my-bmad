"""github-mcp-local GitHub REST adapter — read (Tier-1) + write (Tier-3) operations.

A small async GitHub REST client. The Tier-1 read tools (``github.issues.list`` /
``github.issues.get`` / ``github.prs.list`` / ``github.prs.get`` /
``github.reviews.list`` / ``github.reviews.get`` — Story 16.3) use
:class:`GitHubReadClient`; the Tier-3 write tools (``github.issues.create`` /
``github.issues.update`` / ``github.prs.create`` / ``github.prs.update`` /
``github.reviews.request`` / ``github.comment.create`` — Story 16.4) use
:class:`GitHubWriteClient`. Both mirror the pattern of
``worker_wrapper.adapters.github_client`` — ``aiohttp`` async HTTP + ``tenacity``
3× exponential-backoff retries on 429/5xx, structured dataclass results, no
exceptions propagated for expected failures — but are a github-mcp-LOCAL copy: the
Story 5.8 import-graph rule (enforced by ``scripts/check_imports.py``) forbids an
mcp-server importing from ``services/``, so the worker adapter CANNOT be imported here.

Auth uses the Story-16.5 narrowly-scoped GitHub credential (``scoped_token``) threaded
in from ``build_server``; the broad ``GITHUB_TOKEN`` is NEVER read here (no
``os.environ`` access at all — the token is an explicit constructor argument). The
scoped token is the client's bearer ONLY — it is NEVER returned in a result.

Phase-1 write semantics (Story 16.4): the write methods construct the exact GitHub
REST request shape (POST/PATCH path, ``GitHub-Idempotency-Key`` header where GitHub
supports it, JSON body) but, until the narrowly-scoped credential is wired into the
deployed composes (Stories 16.5 / 16.6), issue NO real GitHub HTTP — they return a
deterministic *simulated* :class:`WriteResult`. This mirrors git-mcp's Story-15.4
DECISION-1(A) "the push target is a LOCAL bare remote (no network, no credentials)":
the audit ``github.*`` event still fires through the FR26 single-writer surface, and
flipping ``simulate=False`` (16.5/16.6) routes the already-built request to GitHub.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp
import tenacity
from events.ids import new_idempotency_key  # noqa: IMP001 — packages/

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


@dataclass
class WriteResult:
    """Structured result from a github-mcp write call (Tier-3, Story 16.4).

    ``ok`` flags success; on success ``number`` carries the created/updated
    resource number (issue / PR number) when GitHub returns one and ``url`` the
    resource's ``html_url``. On failure ``error`` carries a single-line
    human-readable reason and ``status`` the HTTP code (``0`` for a no-token /
    transport failure that never reached GitHub, OR a Phase-1 simulated write).

    The scoped credential is NEVER surfaced here — disclosing it through the tool
    boundary would itself be the leak the scoped-token contract guards against.
    """

    ok: bool
    status: int = 0
    number: int | None = None
    url: str | None = None
    error: str | None = None


class GitHubWriteClient:
    """Async GitHub REST write client with retries (Tier-3 write tools, Story 16.4).

    Usage::

        async with GitHubWriteClient(scoped_token=tok) as gh:
            result = await gh.create_issue("owner", "repo", title="t", body="b")
            if result.ok:
                ...

    The client is an ``aiohttp.ClientSession`` wrapper; ``__aenter__`` opens the
    session and ``__aexit__`` closes it. Auth is the SCOPED token only (NEVER the
    broad ``GITHUB_TOKEN``).

    Phase-1 (``simulate=True``, the default): the write methods construct the exact
    GitHub REST request (path, ``GitHub-Idempotency-Key`` header for the
    issue/PR/comment ``POST`` endpoints that accept it, JSON body) but issue NO real
    HTTP — they return a deterministic simulated ``WriteResult`` so the audit
    ``github.*`` event fires without a live GitHub call (no scoped credential is
    wired until Stories 16.5 / 16.6). Set ``simulate=False`` once the deployed
    composes thread the narrowly-scoped token to route the request to GitHub.
    """

    def __init__(
        self,
        *,
        scoped_token: str,
        base_url: str = "https://api.github.com",
        total_timeout_s: float = _DEFAULT_TIMEOUT_S,
        simulate: bool = True,
    ) -> None:
        self._token = scoped_token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = total_timeout_s
        self._simulate = simulate
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    async def __aenter__(self) -> GitHubWriteClient:
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

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        """Bearer auth + JSON headers using the SCOPED token (never ``GITHUB_TOKEN``).

        ``GitHub-Idempotency-Key`` is attached only where GitHub honours it (the
        issue / PR / comment ``POST`` endpoints) so a retried create is not
        duplicated; ``PATCH`` updates are idempotent by target and omit it.
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            headers["GitHub-Idempotency-Key"] = idempotency_key
        return headers

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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object],
        idempotency_key: str | None = None,
    ) -> tuple[int, object]:
        """Central dispatch: bounded retry on 429/5xx, return ``(status, body)`` for 4xx.

        Mirrors the read client's ``_request`` for the write verbs (``POST`` /
        ``PATCH``): raise ``ClientResponseError`` on 429 / 5xx (so the tenacity
        policy retries), return ``(status, body)`` without retry for any other
        status. Missing token short-circuits to ``(0, {})`` so a misconfigured
        credential never reaches GitHub.
        """
        if self._session is None or self._closed:
            raise RuntimeError("GitHubWriteClient must be used as an async context manager")
        if not self._token:
            return 0, {}
        headers = self._headers(idempotency_key=idempotency_key)
        url = self._url(path)
        retry = _make_retry(self._timeout_s)

        async def _do() -> tuple[int, object]:
            assert self._session is not None  # narrowed by the guard above
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            async with self._session.request(
                method, url, json=json_body, headers=headers, timeout=timeout
            ) as resp:
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

    async def _write(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object],
        idempotent_create: bool = False,
        simulated_number: int | None = None,
    ) -> WriteResult:
        """Issue *method* to *path*, mapping the response to a ``WriteResult``.

        Phase-1 (``simulate=True``): no real HTTP — return a deterministic
        ``ok=True`` simulated result (the audit ``github.*`` event still fires
        through the FR26 single-writer surface). Otherwise route the already-built
        request to GitHub; a no-token short-circuit, a transport failure, and a 4xx
        all map to a structured ``ok=False`` result — exceptions are never
        propagated to the tool boundary.
        """
        if not self._token:
            return WriteResult(ok=False, status=0, error="scoped GitHub token not configured")
        # ``idempotent_create`` mints a key NOW so the request shape is fully
        # built even in simulate mode (16.5/16.6 flip ``simulate=False`` with no
        # further change); the key is request-scoped and never surfaced.
        key = new_idempotency_key() if idempotent_create else None
        if self._simulate:
            return WriteResult(ok=True, status=0, number=simulated_number, url=None)
        try:
            status, body = await self._request(
                method, path, json_body=json_body, idempotency_key=key
            )
        except TimeoutError:
            return WriteResult(ok=False, status=0, error="Request timed out after retries")
        except aiohttp.ClientError as exc:
            return WriteResult(ok=False, status=0, error=str(exc))
        if 200 <= status < 300:
            number = body.get("number") if isinstance(body, dict) else None
            url = body.get("html_url") if isinstance(body, dict) else None
            return WriteResult(
                ok=True,
                status=status,
                number=int(number) if isinstance(number, int) else None,
                url=str(url) if isinstance(url, str) else None,
            )
        return WriteResult(ok=False, status=status, error=self._extract_error(body))

    # ------------------------------------------------------------------
    # Write surface (Tier-3). Owner/repo/number are placed directly in the path;
    # the handlers validate owner/repo before calling (no slash / non-empty) so a
    # path segment cannot be split into an unintended route.
    # ------------------------------------------------------------------

    async def create_issue(self, owner: str, repo: str, *, title: str, body: str) -> WriteResult:
        """``POST /repos/{owner}/{repo}/issues`` (Tier-3)."""
        return await self._write(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json_body={"title": title, "body": body},
            idempotent_create=True,
        )

    async def update_issue(self, owner: str, repo: str, number: int, *, body: str) -> WriteResult:
        """``PATCH /repos/{owner}/{repo}/issues/{number}`` (Tier-3)."""
        return await self._write(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{number}",
            json_body={"body": body},
            simulated_number=number,
        )

    async def create_pull_request(
        self, owner: str, repo: str, *, title: str, head: str, base: str, body: str
    ) -> WriteResult:
        """``POST /repos/{owner}/{repo}/pulls`` (Tier-3)."""
        return await self._write(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "head": head, "base": base, "body": body},
            idempotent_create=True,
        )

    async def update_pull_request(
        self, owner: str, repo: str, number: int, *, body: str
    ) -> WriteResult:
        """``PATCH /repos/{owner}/{repo}/pulls/{number}`` (Tier-3)."""
        return await self._write(
            "PATCH",
            f"/repos/{owner}/{repo}/pulls/{number}",
            json_body={"body": body},
            simulated_number=number,
        )

    async def request_reviewers(
        self, owner: str, repo: str, number: int, *, reviewers: list[str]
    ) -> WriteResult:
        """``POST /repos/{owner}/{repo}/pulls/{number}/requested_reviewers`` (Tier-3)."""
        return await self._write(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            json_body={"reviewers": list(reviewers)},
            simulated_number=number,
        )

    async def create_comment(self, owner: str, repo: str, number: int, *, body: str) -> WriteResult:
        """``POST /repos/{owner}/{repo}/issues/{number}/comments`` (Tier-3).

        GitHub's issue-comment endpoint serves PR comments too (a PR is an issue);
        ``number`` is the issue/PR number the comment is attached to.
        """
        return await self._write(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json_body={"body": body},
            idempotent_create=True,
            simulated_number=number,
        )


__all__ = ["GitHubReadClient", "GitHubWriteClient", "ReadResult", "WriteResult"]
