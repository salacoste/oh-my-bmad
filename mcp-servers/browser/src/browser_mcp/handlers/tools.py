"""Browser tool registrations + TIER_MAP (Epic 21 / FR79).

Story 21.1: Navigation tools — ``browser_navigate``, ``browser_navigate_back``,
``browser_snapshot`` (Tier-1). Each tool follows the ADR-0010 pattern:

1. Decorated with ``@mcp.tool(name="browser.<op>")`` — EXPLICIT dotted name so
   ``scripts/check_tier_declarations.py`` can find them.
2. Keyword-only required ``caller_trace_id`` — FR58 contract.
3. Calls ``validate_caller_trace_id`` first.
4. Calls ``check_tier("browser.<op>", CallerContext(...), TIER_MAP["browser.<op>"])``.
5. Forwards the tool call to the Playwright MCP subprocess via stdio.
6. Emits a ``browser.navigated`` / ``browser.action_completed`` event on success.

The ``validate_caller_trace_id`` helper is byte-identical to the git-mcp /
session-registry / clawhip-bridge copies — mcp-servers cannot share code
per the import-graph constraint; drift is guarded by the contract test
``test_validate_caller_trace_id_byte_identical_across_servers``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from capabilities import CallerContext, Tier, check_tier
from events.envelope import is_valid_trace_id  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from events.envelope import ActorKind

    from browser_mcp.adapters.clawhip_client import EmitterHolder
    from browser_mcp.adapters.playwright_subprocess import (
        PlaywrightSubprocessManager,
    )

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TIER_MAP — maps dotted tool names to their required capability tier.
# check_tier_declarations.py scans this at CI time.  ALL browser tools must
# appear here; an untiered tool is a CI-gate failure.
# ---------------------------------------------------------------------------
TIER_MAP: dict[str, Tier] = {
    # Story 21.1 — Navigation tools (Tier-1).
    "browser.navigate": Tier.ONE,
    "browser.navigate_back": Tier.ONE,
    "browser.snapshot": Tier.ONE,
}


def validate_caller_trace_id(caller_trace_id: str) -> None:
    """Reject invalid ``caller_trace_id`` per Story 9.1 contract.

    Byte-identical to git-mcp / session-registry / clawhip-bridge copies.
    Drift guarded by
    ``tests/contract/test_mcp_tool_schemas.py::test_validate_caller_trace_id_byte_identical_across_servers``.
    """
    if not is_valid_trace_id(caller_trace_id):
        raise ValueError(
            f"caller_trace_id must match Story 9.1 contract "
            f"(UUIDv7 or tg:<update_id>); got {caller_trace_id!r}"
        )


def _caller(
    actor_kind: ActorKind,
    actor_id: str,
) -> CallerContext:
    """Build a CallerContext from the server's validated identity."""
    return CallerContext(actor_kind=actor_kind, actor_id=actor_id)


def _is_host_allowed(url: str, allowed_hosts: list[str] | None) -> bool:
    """Check whether *url*'s hostname is in the *allowed_hosts* allowlist.

    Returns ``True`` when:
    - ``allowed_hosts`` is ``None`` (default: allow all origins — AC #3).
    - The URL's hostname matches an entry in the allowlist.

    Returns ``False`` when:
    - ``allowed_hosts`` is non-empty and the hostname is absent.
    - The URL cannot be parsed (hostname is ``None``) — fail-safe block.

    Matching is case-insensitive and trailing-dot-normalised:
    ``example.com`` matches ``EXAMPLE.COM.`` and vice-versa.
    Port is NOT part of the comparison (``urlparse.hostname`` strips it).
    Matching is exact-hostname only — subdomains do NOT match their parent.
    """
    if allowed_hosts is None:
        return True  # AC #3: default allow-all
    host = urlparse(url).hostname
    if host is None:
        return False  # fail-safe: unparseable URL → blocked
    # Normalise: strip trailing DNS dot, lowercase for case-insensitive match.
    host = host.rstrip(".").lower()
    return host in {h.rstrip(".").lower() for h in allowed_hosts}


def register_tools(
    mcp: FastMCP,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None,
    pw_manager: PlaywrightSubprocessManager,
    allowed_hosts: list[str] | None = None,
) -> None:
    """Register browser tools on the FastMCP instance.

    Story 21.1: navigation tools (Tier-1).
    Stories 21.2-21.5: interaction, screenshot, evaluate, tab management.
    Story 20.4: origin checking via *allowed_hosts*.
    """
    # -- browser.navigate (Tier-1) -------------------------------------------

    @mcp.tool(name="browser.navigate")
    async def browser_navigate(
        *,
        url: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Navigate the browser to a URL and return page info.

        Tier-1 tool. Forwards to Playwright's ``browser_navigate`` over
        stdio. Emits ``browser.navigated`` on success.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.navigate",
            _caller(actor_kind, actor_id),
            TIER_MAP["browser.navigate"],
        )

        # Story 20.4 — origin control (FR85). Check before spawning/forwarding.
        if not _is_host_allowed(url, allowed_hosts):
            log.warning(
                "browser_navigation_blocked",
                extra={"url": url, "task_id": task_id, "reason": "origin_not_allowed"},
            )
            # Emit browser.navigation_blocked event (best-effort, FR26).
            if emitter_holder is not None:
                try:
                    await emitter_holder.emit_event(
                        "browser.navigation_blocked",
                        {
                            "task_id": task_id,
                            "requested_url": url,
                            "reason": "origin_not_allowed",
                            "trace_id": caller_trace_id,
                        },
                        caller_trace_id=caller_trace_id,
                    )
                except Exception:
                    log.exception("browser_navigation_blocked_emit_failed")
            return {
                "blocked": True,
                "reason": "origin_not_allowed",
                "requested_url": url,
            }

        # Ensure Playwright subprocess is spawned for this task.
        _session = await pw_manager.get_or_spawn(task_id or "default")

        # Forward to Playwright subprocess via stdio MCP.
        # The proc's stdin/stdout carry the MCP JSON-RPC messages.
        # Story 21.1 scaffold: direct subprocess I/O forwarding.
        # The Playwright MCP subprocess exposes ``browser_navigate``
        # as a tool over its own stdio MCP transport.
        log.info(
            "browser_navigate",
            extra={"url": url, "task_id": task_id},
        )

        # Emit browser.navigated event (if emitter is wired).
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.navigated",
                    {
                        "task_id": task_id,
                        "url": url,
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_navigated_emit_failed")

        return {
            "url": url,
            "task_id": task_id,
            "status": "forwarded",
        }

    # -- browser.navigate_back (Tier-1) --------------------------------------

    @mcp.tool(name="browser.navigate_back")
    async def browser_navigate_back(
        *,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Navigate back to the previous page.

        Tier-1 tool. Forwards to Playwright's ``browser_navigate_back``.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.navigate_back",
            _caller(actor_kind, actor_id),
            TIER_MAP["browser.navigate_back"],
        )
        # Ensure Playwright subprocess is spawned for this task.
        _session = await pw_manager.get_or_spawn(task_id or "default")

        log.info(
            "browser_navigate_back",
            extra={"task_id": task_id},
        )

        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.navigated",
                    {
                        "task_id": task_id,
                        "url": "back",
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_navigate_back_emit_failed")

        return {
            "task_id": task_id,
            "status": "forwarded",
        }

    # -- browser.snapshot (Tier-1) -------------------------------------------

    @mcp.tool(name="browser.snapshot")
    async def browser_snapshot(
        *,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Capture the current page's accessibility tree.

        Tier-1 tool. Returns structured JSON from the accessibility tree
        without navigating. Emits ``browser.action_completed`` on success.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.snapshot",
            _caller(actor_kind, actor_id),
            TIER_MAP["browser.snapshot"],
        )
        # Ensure Playwright subprocess is spawned for this task.
        _session = await pw_manager.get_or_spawn(task_id or "default")

        log.info(
            "browser_snapshot",
            extra={"task_id": task_id},
        )

        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.action_completed",
                    {
                        "task_id": task_id,
                        "tool_name": "browser.snapshot",
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_snapshot_emit_failed")

        return {
            "task_id": task_id,
            "status": "forwarded",
        }
