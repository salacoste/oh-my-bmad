"""Browser tool registrations + TIER_MAP (Epic 21 / FR79-FR80).

Story 21.1: Navigation tools — ``browser_navigate``, ``browser_navigate_back``,
``browser_snapshot`` (Tier-1).
Story 21.2: Interaction tools — ``browser_click``, ``browser_type``,
``browser_fill``, ``browser_select_option``, ``browser_press_key``,
``browser_hover`` (Tier-2 / FR80).

Each tool follows the ADR-0010 pattern:

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

import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from capabilities import (
    CallerContext,
    Tier,
    check_tier,
    check_tier_with_approval,
)
from capabilities.emit import emit_capability_denied_on_deny
from events import current_day_path, read_log_lines  # noqa: IMP001 — packages/
from events.envelope import is_valid_trace_id  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

if TYPE_CHECKING:
    from pathlib import Path as _Path

    from events.clock import Clock  # noqa: IMP001 — packages/
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
    # Story 21.2 — Interaction tools (Tier-2 / FR80).
    "browser.click": Tier.TWO,
    "browser.type": Tier.TWO,
    "browser.fill": Tier.TWO,
    "browser.select_option": Tier.TWO,
    "browser.press_key": Tier.TWO,
    "browser.hover": Tier.TWO,
    # Story 21.4 — JS execution (Tier-3 / FR82).
    "browser.evaluate": Tier.THREE,
    # Story 21.3 — Screenshot + artifact integration (Tier-1 / FR81).
    "browser.take_screenshot": Tier.ONE,
    # Story 21.5 — Tab management (FR83).
    "browser.tab_list": Tier.ONE,
    "browser.tab_select": Tier.ONE,
    "browser.tab_create": Tier.TWO,
    "browser.tab_close": Tier.TWO,
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
    *,
    task_id: str | None = None,
) -> CallerContext:
    """Build a CallerContext from the server's validated identity."""
    return CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=task_id)


def make_approval_lookup(
    base_dir: _Path,
    clock: Clock,
) -> Callable[[str, str], Awaitable[bool]]:
    """Return an async ``(task_id, action) -> bool`` approval lookup for Tier-3 gating.

    Story 21.4: the Tier-3 ``browser.evaluate`` tool is gated by
    ``check_tier_with_approval(..., approval_lookup=...)`` — the lookup returns
    True only when a matching ``approval.granted`` event exists for the caller's
    *task_id*. Scans TODAY's JSONL event log for ``approval.granted`` events whose
    payload ``task_id`` matches.

    COPIED (not imported) from git-mcp's / github-mcp's approval lookup — the
    Story 5.8 import-graph constraint forbids cross-importing between mcp-servers.
    Phase-1 limitation: only scans today's JSONL log file.
    """

    async def _lookup(task_id: str, action: str) -> bool:  # noqa: ARG001 — action reserved
        path = current_day_path(base_dir, clock.now())
        try:
            for envelope in read_log_lines(path):
                payload = envelope.payload
                if (
                    envelope.type == "approval.granted"
                    and isinstance(payload, dict)
                    and payload.get("task_id") == task_id
                ):
                    return True
        except FileNotFoundError:
            pass
        return False

    return _lookup


def _make_actor_id_extractor(actor_id: str) -> Callable[..., str]:
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``."""

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


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


def _parse_navigate_result(
    result: CallToolResult,
    fallback_url: str,
    task_id: str,
) -> dict[str, object]:
    """Parse a Playwright ``browser_navigate`` CallToolResult into structured output.

    The Playwright MCP response contains text content with page info.
    Extract ``url``, ``title``, ``status_code``, ``accessibility_tree_summary``.
    Fall back to sensible defaults when fields are missing.
    """
    if result.isError:
        error_text = "; ".join(
            c.text for c in result.content if hasattr(c, "text")
        )
        return {
            "error": True,
            "reason": "playwright_error",
            "detail": error_text,
            "task_id": task_id,
        }

    # Playwright MCP returns text content blocks. Extract structured fields.
    text_parts: list[str] = [
        c.text for c in result.content if hasattr(c, "text") and c.text
    ]
    combined = "\n".join(text_parts)

    return {
        "url": fallback_url,
        "title": _extract_field(combined, "title") or "",
        "status_code": _extract_int_field(combined, "status_code"),
        "accessibility_tree_summary": combined[:2000] if combined else "",
        "task_id": task_id,
    }


def _parse_snapshot_result(
    result: CallToolResult,
    task_id: str,
) -> dict[str, object]:
    """Parse a Playwright ``browser_snapshot`` CallToolResult."""
    if result.isError:
        error_text = "; ".join(
            c.text for c in result.content if hasattr(c, "text")
        )
        return {
            "error": True,
            "reason": "playwright_error",
            "detail": error_text,
            "task_id": task_id,
        }

    text_parts: list[str] = [
        c.text for c in result.content if hasattr(c, "text") and c.text
    ]
    combined = "\n".join(text_parts)

    return {
        "accessibility_tree": combined,
        "task_id": task_id,
    }


def _extract_field(text: str, field_name: str) -> str | None:
    """Extract a named field from Playwright text output (best-effort)."""
    pattern = rf"{re.escape(field_name)}\s*[:=]\s*(.+?)(?:\n|$)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _extract_int_field(text: str, field_name: str) -> int | None:
    """Extract an integer field from Playwright text output."""
    value = _extract_field(text, field_name)
    if value is not None:
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    return None


def register_tools(
    mcp: FastMCP,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None,
    pw_manager: PlaywrightSubprocessManager,
    allowed_hosts: list[str] | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
    artifact_holder: Any = None,
) -> None:
    """Register browser tools on the FastMCP instance.

    Story 21.1: navigation tools (Tier-1).
    Stories 21.2-21.5: interaction, screenshot, evaluate, tab management.
    Story 20.4: origin checking via *allowed_hosts*.

    *approval_lookup* is the async ``(task_id, action) -> bool`` callable threaded
    into ``check_tier_with_approval`` for the Tier-3 tools; when None the Tier-3
    tools deny every call (no approval source — test/no-approval default).

    *artifact_holder* is an ``ArtifactClientHolder`` for ``artifact.put`` calls
    from ``browser.take_screenshot`` (Story 21.3); when None, screenshot storage
    returns a structured error (no artifact store configured).
    """
    get_actor_id = _make_actor_id_extractor(actor_id)

    def _maybe_wrap(
        tool_name: str,
    ) -> Callable[
        [Callable[..., Awaitable[dict[str, object]]]],
        Callable[..., Awaitable[dict[str, object]]],
    ]:
        """Apply the audit-emission decorator iff an emitter holder is wired."""
        if emitter_holder is None:
            return lambda fn: fn
        return emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter_holder.emit_event,
            attempted_action=tool_name,
            get_actor_id=get_actor_id,
        )

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

        # Ensure Playwright subprocess + MCP client for this task.
        client = await pw_manager.ensure_client(task_id or "default")

        # Forward to Playwright subprocess via MCP stdio.
        log.info(
            "browser_navigate_forwarding",
            extra={"url": url, "task_id": task_id},
        )

        status_code: int | None = None
        try:
            result = await client.call_tool("browser_navigate", {"url": url})
        except RuntimeError as exc:
            log.error("browser_navigate_subprocess_error", exc_info=True)
            return {"error": True, "reason": "subprocess_error", "detail": str(exc)}
        except TimeoutError:
            log.error("browser_navigate_timeout", extra={"url": url})
            return {"error": True, "reason": "subprocess_timeout", "url": url}

        # Parse Playwright response into structured output.
        response = _parse_navigate_result(result, url, task_id)
        status_code = response.get("status_code")

        # Emit browser.navigated event (if emitter is wired).
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.navigated",
                    {
                        "task_id": task_id,
                        "url": url,
                        "status_code": status_code,
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_navigated_emit_failed")

        return response

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

        client = await pw_manager.ensure_client(task_id or "default")

        log.info(
            "browser_navigate_back_forwarding",
            extra={"task_id": task_id},
        )

        try:
            result = await client.call_tool("browser_navigate_back", {})
        except RuntimeError as exc:
            log.error("browser_navigate_back_subprocess_error", exc_info=True)
            return {"error": True, "reason": "subprocess_error", "detail": str(exc)}
        except TimeoutError:
            log.error("browser_navigate_back_timeout")
            return {"error": True, "reason": "subprocess_timeout"}

        response = _parse_navigate_result(result, "", task_id)

        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.navigated",
                    {
                        "task_id": task_id,
                        "url": response.get("url", "back"),
                        "status_code": response.get("status_code"),
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_navigate_back_emit_failed")

        return response

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

        client = await pw_manager.ensure_client(task_id or "default")

        log.info(
            "browser_snapshot_forwarding",
            extra={"task_id": task_id},
        )

        try:
            result = await client.call_tool("browser_snapshot", {})
        except RuntimeError as exc:
            log.error("browser_snapshot_subprocess_error", exc_info=True)
            return {"error": True, "reason": "subprocess_error", "detail": str(exc)}
        except TimeoutError:
            log.error("browser_snapshot_timeout")
            return {"error": True, "reason": "subprocess_timeout"}

        response = _parse_snapshot_result(result, task_id)

        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.action_completed",
                    {
                        "task_id": task_id,
                        "tool_name": "browser.snapshot",
                        "success": not result.isError,
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_snapshot_emit_failed")

        return response

    # ===================================================================
    # Story 21.2 — Interaction tools (Tier-2 / FR80)
    # ===================================================================
    # All six share a common pattern: validate → check_tier(Tier.TWO) →
    # forward to Playwright → emit browser.action_completed with timing.
    # The _forward_action_tool helper eliminates duplication.
    # ===================================================================

    async def _forward_action_tool(
        dotted_name: str,
        pw_tool_name: str,
        arguments: dict[str, Any],
        caller_trace_id: str,
        task_id: str,
    ) -> dict[str, object]:
        """Generic Tier-2 action forwarder with timing + event emission.

        1. Ensure Playwright subprocess + MCP client.
        2. Forward ``pw_tool_name`` with ``arguments``.
        3. Measure duration and emit ``browser.action_completed``.
        4. Return structured result.
        """
        client = await pw_manager.ensure_client(task_id or "default")

        t0 = time.monotonic()
        try:
            result = await client.call_tool(pw_tool_name, arguments)
        except RuntimeError as exc:
            log.error(f"{dotted_name}_subprocess_error", exc_info=True)
            return {
                "error": True, "reason": "subprocess_error",
                "detail": str(exc), "task_id": task_id,
            }
        except TimeoutError:
            log.error(f"{dotted_name}_timeout")
            return {"error": True, "reason": "subprocess_timeout", "task_id": task_id}
        duration_ms = round((time.monotonic() - t0) * 1000)

        success = not result.isError
        response: dict[str, object] = {
            "task_id": task_id, "success": success,
            "duration_ms": duration_ms,
        }

        if result.isError:
            error_text = "; ".join(c.text for c in result.content if hasattr(c, "text"))
            response["error"] = True
            response["reason"] = "playwright_error"
            response["detail"] = error_text

        # Emit browser.action_completed (best-effort, FR26).
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.action_completed",
                    {
                        "task_id": task_id,
                        "tool_name": dotted_name,
                        "success": success,
                        "duration_ms": duration_ms,
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception(f"{dotted_name}_emit_failed")

        return response

    # -- browser.click (Tier-2) ---------------------------------------------

    @mcp.tool(name="browser.click")
    async def browser_click(
        *,
        element: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Click an element on the page. Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier("browser.click", _caller(actor_kind, actor_id), TIER_MAP["browser.click"])
        return await _forward_action_tool(
            "browser.click", "browser_click", {"element": element},
            caller_trace_id, task_id,
        )

    # -- browser.type (Tier-2) -----------------------------------------------

    @mcp.tool(name="browser.type")
    async def browser_type(
        *,
        element: str,
        text: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Type text into an element. Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier("browser.type", _caller(actor_kind, actor_id), TIER_MAP["browser.type"])
        return await _forward_action_tool(
            "browser.type", "browser_type", {"element": element, "text": text},
            caller_trace_id, task_id,
        )

    # -- browser.fill (Tier-2) -----------------------------------------------

    @mcp.tool(name="browser.fill")
    async def browser_fill(
        *,
        element: str,
        text: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Fill an element with text (clears first). Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier("browser.fill", _caller(actor_kind, actor_id), TIER_MAP["browser.fill"])
        return await _forward_action_tool(
            "browser.fill", "browser_fill", {"element": element, "text": text},
            caller_trace_id, task_id,
        )

    # -- browser.select_option (Tier-2) --------------------------------------

    @mcp.tool(name="browser.select_option")
    async def browser_select_option(
        *,
        element: str,
        values: list[str],
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Select option(s) in a dropdown. Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.select_option", _caller(actor_kind, actor_id),
            TIER_MAP["browser.select_option"],
        )
        return await _forward_action_tool(
            "browser.select_option", "browser_select_option",
            {"element": element, "values": values},
            caller_trace_id, task_id,
        )

    # -- browser.press_key (Tier-2) ------------------------------------------

    @mcp.tool(name="browser.press_key")
    async def browser_press_key(
        *,
        key: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Press a keyboard key. Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.press_key", _caller(actor_kind, actor_id),
            TIER_MAP["browser.press_key"],
        )
        return await _forward_action_tool(
            "browser.press_key", "browser_press_key", {"key": key},
            caller_trace_id, task_id,
        )

    # -- browser.hover (Tier-2) ----------------------------------------------

    @mcp.tool(name="browser.hover")
    async def browser_hover(
        *,
        element: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Hover over an element. Tier-2 tool (FR80)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier("browser.hover", _caller(actor_kind, actor_id), TIER_MAP["browser.hover"])
        return await _forward_action_tool(
            "browser.hover", "browser_hover", {"element": element},
            caller_trace_id, task_id,
        )

    # ===================================================================
    # Story 21.5 — Tab management (FR83)
    # ===================================================================
    # list/select = Tier-1, create/close = Tier-2.
    # Each forwards to the Playwright subprocess tab tools.
    # ===================================================================

    # -- browser.tab_list (Tier-1) -----------------------------------------

    @mcp.tool(name="browser.tab_list")
    async def browser_tab_list(
        *,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """List all open browser tabs. Tier-1 tool (FR83)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.tab_list", _caller(actor_kind, actor_id),
            TIER_MAP["browser.tab_list"],
        )
        return await _forward_action_tool(
            "browser.tab_list", "browser_tab_list", {},
            caller_trace_id, task_id,
        )

    # -- browser.tab_select (Tier-1) ---------------------------------------

    @mcp.tool(name="browser.tab_select")
    async def browser_tab_select(
        *,
        tab_id: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Switch to a specific browser tab. Tier-1 tool (FR83)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.tab_select", _caller(actor_kind, actor_id),
            TIER_MAP["browser.tab_select"],
        )
        return await _forward_action_tool(
            "browser.tab_select", "browser_tab_select",
            {"tab_id": tab_id},
            caller_trace_id, task_id,
        )

    # -- browser.tab_create (Tier-2) --------------------------------------

    @mcp.tool(name="browser.tab_create")
    async def browser_tab_create(
        *,
        url: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Open a new browser tab at the given URL. Tier-2 tool (FR83)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.tab_create", _caller(actor_kind, actor_id),
            TIER_MAP["browser.tab_create"],
        )
        return await _forward_action_tool(
            "browser.tab_create", "browser_tab_create",
            {"url": url},
            caller_trace_id, task_id,
        )

    # -- browser.tab_close (Tier-2) ---------------------------------------

    @mcp.tool(name="browser.tab_close")
    async def browser_tab_close(
        *,
        tab_id: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Close a specific browser tab. Tier-2 tool (FR83)."""
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.tab_close", _caller(actor_kind, actor_id),
            TIER_MAP["browser.tab_close"],
        )
        return await _forward_action_tool(
            "browser.tab_close", "browser_tab_close",
            {"tab_id": tab_id},
            caller_trace_id, task_id,
        )

    # ===================================================================
    # Story 21.3 — Screenshot capture + artifact integration (Tier-1 / FR81)
    # ===================================================================
    # browser.take_screenshot captures the current viewport via Playwright,
    # stores the image bytes in the artifact-mcp content-addressed store,
    # and returns a metadata-only response (artifact_ref, content_hash, etc.).
    # Raw image bytes are NEVER in the tool result or events (NFR-B3).
    # ===================================================================

    @mcp.tool(name="browser.take_screenshot")
    async def browser_take_screenshot(
        *,
        caller_trace_id: str,
        format: str = "png",
        task_id: str = "",
    ) -> dict[str, object]:
        """Capture a viewport screenshot and store in artifact-mcp. Tier-1 (FR81).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            format: Image format — ``png`` (default) or ``jpeg``.
            task_id: Optional task ID for artifact tagging.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "browser.take_screenshot",
            _caller(actor_kind, actor_id),
            TIER_MAP["browser.take_screenshot"],
        )

        # Validate format parameter.
        if format not in ("png", "jpeg"):
            return {
                "error": True,
                "reason": "invalid_format",
                "detail": f"format must be 'png' or 'jpeg'; got {format!r}",
            }

        # Check artifact store availability.
        if artifact_holder is None:
            return {
                "error": True,
                "reason": "no_artifact_store",
                "detail": "artifact-mcp client not configured",
            }

        # Capture screenshot via Playwright subprocess.
        client = await pw_manager.ensure_client(task_id or "default")
        try:
            result = await client.call_tool(
                "browser_screenshot",
                {"format": format} if format != "png" else {},
            )
        except RuntimeError as exc:
            log.error("browser_screenshot_subprocess_error", exc_info=True)
            return {
                "error": True, "reason": "subprocess_error",
                "detail": str(exc), "task_id": task_id,
            }
        except TimeoutError:
            log.error("browser_screenshot_timeout")
            return {
                "error": True, "reason": "subprocess_timeout",
                "task_id": task_id,
            }

        if result.isError:
            error_text = "; ".join(
                c.text for c in result.content if hasattr(c, "text")
            )
            return {
                "error": True,
                "reason": "playwright_error",
                "detail": error_text,
                "task_id": task_id,
            }

        # Extract screenshot bytes from Playwright response.
        # Playwright MCP returns image data as base64 in text content.
        import base64 as _b64

        screenshot_b64: str | None = None
        for c in result.content:
            if hasattr(c, "text") and c.text:
                screenshot_b64 = c.text
                break

        if not screenshot_b64:
            return {
                "error": True,
                "reason": "no_screenshot_data",
                "detail": "Playwright returned empty screenshot",
                "task_id": task_id,
            }

        try:
            screenshot_bytes = _b64.b64decode(screenshot_b64)
        except Exception as exc:
            return {
                "error": True,
                "reason": "invalid_screenshot_data",
                "detail": f"base64 decode failed: {exc}",
                "task_id": task_id,
            }

        # Store in artifact-mcp via content-addressed put.
        content_hash = hashlib.sha256(screenshot_bytes).hexdigest()
        artifact_name = f"screenshot_{content_hash[:12]}.{format}"

        t0 = time.monotonic()
        try:
            await artifact_holder.put(
                caller_trace_id=caller_trace_id,
                content=screenshot_bytes,
                name=artifact_name,
                task_id=task_id or None,
            )
        except Exception as exc:
            log.error("browser_screenshot_artifact_put_failed", exc_info=True)
            return {
                "error": True,
                "reason": "artifact_put_failed",
                "detail": str(exc),
                "task_id": task_id,
            }
        duration_ms = round((time.monotonic() - t0) * 1000)

        response: dict[str, object] = {
            "artifact_ref": artifact_name,
            "content_hash": content_hash,
            "format": format,
            "size_bytes": len(screenshot_bytes),
            "duration_ms": duration_ms,
            "task_id": task_id,
        }

        # Emit browser.screenshot_captured event (best-effort, FR26).
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.screenshot_captured",
                    {
                        "task_id": task_id,
                        "artifact_ref": artifact_name,
                        "content_hash": content_hash,
                        "trace_id": caller_trace_id,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_screenshot_emit_failed")

        return response

    # ===================================================================
    # Story 21.4 — JS execution (Tier-3 / FR82)
    # ===================================================================
    # browser.evaluate is the only Tier-3 browser tool. It executes
    # arbitrary JavaScript in the page context — RCE-equivalent, so it
    # requires an ``approval.granted`` event matching the caller's task_id.
    #
    # Pattern mirrors github-mcp's Tier-3 write tools:
    #   validate caller_trace_id → check_tier_with_approval → forward
    #   to Playwright → emit browser.action_completed with expression_hash.
    #
    # The ``@_maybe_wrap`` decorator emits ``capability.denied`` on denial.
    # ===================================================================

    _result_preview_max = 500

    @mcp.tool(name="browser.evaluate")
    @_maybe_wrap("browser.evaluate")
    async def browser_evaluate(
        *,
        expression: str,
        caller_trace_id: str,
        task_id: str = "",
    ) -> dict[str, object]:
        """Execute JavaScript in the page context. Tier-3 tool (FR82).

        RCE-equivalent — requires an ``approval.granted`` event for the
        caller's *task_id* before execution proceeds.

        Args:
            expression: JavaScript expression to evaluate.
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            task_id: Task whose ``approval.granted`` authorizes this Tier-3 call.
        """
        validate_caller_trace_id(caller_trace_id)
        await check_tier_with_approval(
            "browser.evaluate",
            _caller(actor_kind, actor_id, task_id=task_id),
            TIER_MAP["browser.evaluate"],
            approval_lookup=approval_lookup,
        )

        # SHA-256 hash of expression — never log/store raw expression (NFR-S13).
        expression_hash = hashlib.sha256(expression.encode()).hexdigest()

        client = await pw_manager.ensure_client(task_id or "default")

        t0 = time.monotonic()
        try:
            result = await client.call_tool("browser_evaluate", {"expression": expression})
        except RuntimeError as exc:
            log.error("browser_evaluate_subprocess_error", exc_info=True)
            return {
                "error": True, "reason": "subprocess_error",
                "detail": str(exc), "task_id": task_id,
            }
        except TimeoutError:
            log.error("browser_evaluate_timeout")
            return {
                "error": True, "reason": "subprocess_timeout",
                "task_id": task_id,
            }
        duration_ms = round((time.monotonic() - t0) * 1000)

        success = not result.isError
        response: dict[str, object] = {
            "task_id": task_id, "success": success,
            "duration_ms": duration_ms,
            "expression_hash": expression_hash,
        }

        if result.isError:
            error_text = "; ".join(
                c.text for c in result.content if hasattr(c, "text")
            )
            response["error"] = True
            response["reason"] = "playwright_error"
            response["detail"] = error_text[:_result_preview_max]
        else:
            # Extract result text and truncate for preview (FR82).
            result_text = "\n".join(
                c.text for c in result.content if hasattr(c, "text") and c.text
            )
            response["result_type"] = "string"
            response["result_preview"] = result_text[:_result_preview_max]

        # Emit browser.action_completed with expression_hash (best-effort, FR26).
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    "browser.action_completed",
                    {
                        "task_id": task_id,
                        "tool_name": "browser.evaluate",
                        "success": success,
                        "duration_ms": duration_ms,
                        "trace_id": caller_trace_id,
                        "expression_hash": expression_hash,
                    },
                    caller_trace_id=caller_trace_id,
                )
            except Exception:
                log.exception("browser_evaluate_emit_failed")

        return response
