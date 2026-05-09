"""GitHub REST API adapter — PR draft creation with retries (Story 5.14).

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


def _make_retry(per_request_timeout_s: float) -> tenacity.AsyncRetrying:
    # Retry budget must exceed 3 × per-request timeout so slow requests don't
    # exhaust the delay budget before all attempts are made.
    total_budget_s = per_request_timeout_s * 3.5
    return tenacity.AsyncRetrying(
        stop=tenacity.stop_after_attempt(3) | tenacity.stop_after_delay(total_budget_s),
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
    branch: str | None = None
    error: str | None = None


class GitHubAdapter:
    """Async GitHub REST API adapter for PR draft creation with retries.

    Usage::

        adapter = GitHubAdapter(token="ghp_...", base_url="https://api.github.com", timeout_s=10)
        result = await adapter.create_pr_draft(
            "owner", "repo", "Title", "head-branch", "main", "Body"
        )
        if result.success:
            print(result.url)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_s: float = 10.0,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

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
        return f"{self._base_url}{path}"

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
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        json_body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Make an HTTP request with retries and per-attempt timeout."""
        log = structlog.get_logger(__name__)
        # Key generated once OUTSIDE _do so retries reuse the same key.
        key = idempotency_key or new_idempotency_key()
        headers = self._headers(key)
        url = self._url(path)
        retry = _make_retry(self._timeout_s)

        async def _do() -> tuple[int, dict[str, object]]:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            async with session.request(
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
                        # Don't retry — rate limits need minutes to reset.
                        return resp.status, body
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
        if not owner or "/" in owner:
            return PRDraftResult(success=False, error=f"Invalid owner: {owner!r}")
        if not repo or "/" in repo:
            return PRDraftResult(success=False, error=f"Invalid repo: {repo!r}")

        path = f"/repos/{owner}/{repo}/pulls"
        payload: dict[str, object] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": True,
        }

        async with aiohttp.ClientSession() as session:
            try:
                status, resp = await self._request(session, "POST", path, payload)
            except TimeoutError:
                log.error("github_pr_timeout")
                return PRDraftResult(success=False, error="Request timed out after retries")
            except aiohttp.ClientError as exc:
                log.error("github_pr_error", error=str(exc))
                return PRDraftResult(success=False, error=str(exc))

        if status in (201, 200):
            url = resp.get("html_url")
            number = resp.get("number")
            head_ref = resp.get("head", {})
            branch = head_ref.get("ref") if isinstance(head_ref, dict) else None
            log.info("github_pr_created", number=number, url=url)
            return PRDraftResult(
                success=True,
                url=str(url) if url is not None else None,
                number=int(number) if isinstance(number, int) and number >= 1 else None,
                branch=str(branch) if branch is not None else None,
            )

        return PRDraftResult(success=False, error=self._extract_error(resp))
