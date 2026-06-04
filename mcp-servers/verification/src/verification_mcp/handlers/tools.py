"""verification-mcp MCP tool handlers (Epic 17 / Story 17.3 — Tier-2 sandboxed tools).

Story 17.3 lands the two sandboxed verification tools — ``verification.run_build``
and ``verification.run_tests`` — both at ``Tier.TWO`` (they run project code but
perform no external mutation). Each is registered with an EXPLICIT dotted MCP name
(``@mcp.tool(name="verification.run_build")``) so ``list_tools()`` surfaces the
canonical ``verification.<op>`` id the 17.1 ATDD contracts assert (FastMCP would
otherwise default the id to the Python function name). Every handler:

  1. validates ``caller_trace_id`` FIRST (FR58 / Story 9.1 shape contract);
  2. gates on its tier via ``check_tier("verification.<op>", CallerContext(...),
     TIER_MAP["verification.<op>"])`` — the ``TIER_MAP[...]`` subscript is passed
     as a DIRECT argument to ``check_tier`` so
     ``scripts/check_tier_declarations.py`` recognises the tool as tiered;
  3. containment-checks any optional ``cwd`` arg against the worktree root (an
     escaping ``cwd`` raises ``ValueError`` BEFORE any recipe spawn — there is NO
     "repo selection" arg; the recipe always runs inside the sandbox); and
  4. routes the actual recipe invocation through ``VerificationExecutor.run_recipe``
     (the single sandboxed spawn-site — ``cwd`` pinned to the worktree, child-env
     allowlist only, wall-clock timeout) and returns a deterministic, structured
     ``{ok/pass, exit_code, logs, coverage}`` result.

A non-zero recipe exit is NOT an exception — it is surfaced as
``{"ok": False, "pass": False, ...}``. A spawn failure (the recipe binary is
absent) is caught and surfaced structurally so a misconfigured recipe never
crashes the request path.

Story 17.4 adds the ``verification.*`` event emission: after the recipe runs
(for BOTH a pass and a fail), each handler emits a typed ``verification.completed``
event through the FR26 single-writer surface (``emitter_holder.emit_event`` →
clawhip-bridge MCP RPC, NEVER a direct event-log write) carrying the inbound
``caller_trace_id``. The emit is BEST-EFFORT (guarded on ``emitter_holder``, PP1
re-raise of cancellation) and the typed descriptor is surfaced UNCONDITIONALLY as
``result["event"]`` (mirrors github-mcp's ``_emit_github_event``). The event
payload carries ONLY the recipe command + exit-status discriminators — NEVER the
captured ``logs`` (secret-bearing) or any environment value.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, and git-mcp (mcp-servers cannot
share code per Story 5.8's import-graph constraint); the same drift guard extends
to verification-mcp now that it registers tools.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier
from capabilities.emit import emit_capability_denied_on_deny
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from verification_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from verification_mcp.server import VerificationExecutor

log = logging.getLogger(__name__)

# Story 17.3: the two Tier-2 sandboxed verification tools. Both run project code
# (a build/test recipe) but perform no external mutation, so both are ``Tier.TWO``
# (gated by ``check_tier`` — NO approval gate). Keys are the canonical dotted
# ``verification.<op>`` MCP tool ids (matched to the ``@mcp.tool(name=…)``
# registration below).
TIER_MAP: dict[str, Tier] = {
    "verification.run_build": Tier.TWO,
    "verification.run_tests": Tier.TWO,
}

# Default recipes (the project's build/test invocation). A caller may override
# ``command`` / ``args`` per invocation, but there is NO "repo selection" arg —
# the recipe always runs with ``cwd`` pinned inside the sandbox worktree.
_DEFAULT_BUILD: tuple[str, list[str]] = ("just", ["build"])
_DEFAULT_TESTS: tuple[str, list[str]] = ("just", ["test"])

# Coverage-summary extraction: pytest-cov / coverage.py print a ``TOTAL`` line
# ending in an integer percentage (``TOTAL   1234   56   95%``). We surface that
# integer as the ``coverage`` summary; absent any such line, ``coverage`` is None.
_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\b.*?(\d+)%\s*$", re.MULTILINE)


def validate_caller_trace_id(caller_trace_id: str) -> None:
    """Reject invalid ``caller_trace_id`` per Story 9.1 contract.

    Public helper used by every ``@mcp.tool()`` handler in this server to
    validate the operator-originating correlation ID supplied as an explicit
    Pydantic-validated input (Story 9.5 / FR58 MCP). Validation uses
    :func:`events.envelope.is_valid_trace_id` so the shape contract (UUIDv7
    bare form OR ``tg:<update_id>``) stays in one place — Story 9.4 pass-2 S1
    lesson (shape-validation, not just type-check, avoids whitespace/CRLF
    injection).

    Public name (no leading underscore) per Story 9.5 pass-1 review T4:
    these helpers are part of the public tool-validation contract documented
    in the Story 9.5 spec and exercised by ``tests/contract/`` — the contract
    test for byte-identical body sync (T2) requires a public symbol.

    NOTE: Duplicated byte-identically in ``clawhip-bridge`` and
    ``session-registry``. mcp-servers cannot share code per Story 5.8's
    import-graph constraint; the helper body MUST stay in sync across all
    three servers. Drift is guarded by
    ``tests/contract/test_mcp_tool_schemas.py::test_validate_caller_trace_id_byte_identical_across_servers``
    (Story 9.5 pass-1 T2).

    Raises:
        ValueError: if ``caller_trace_id`` doesn't match the Story 9.1
            contract (UUIDv7 bare form OR ``tg:<digits>``).
    """
    if not is_valid_trace_id(caller_trace_id):
        raise ValueError(
            f"caller_trace_id must match Story 9.1 contract "
            f"(UUIDv7 or tg:<update_id>); got {caller_trace_id!r}"
        )


def _make_actor_id_extractor(actor_id: str) -> Callable[..., str]:
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``.

    The configured ``actor_id`` is the calling actor's identity for the
    duration of this server process (set at startup). Tool kwargs do not carry
    actor identity — it comes from the server's launch config — so the
    extractor returns the closed-over value irrespective of args/kwargs.
    """

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


def _assert_contained_cwd(executor: VerificationExecutor, cwd: str) -> None:
    """Refuse a ``cwd`` that escapes the worktree root (P0/security).

    There is NO "repo selection" arg: the recipe always runs with ``cwd`` pinned
    to the worktree root inside ``run_recipe``. The optional ``cwd`` tool arg only
    narrows the run to a SUBDIRECTORY of the worktree; an absolute path is taken
    as-is, a relative path is joined to the root, and the candidate is validated
    with the shipped ``VerificationExecutor._contains`` (realpath containment —
    collapses ``..`` and symlinks). An escaping path raises ``ValueError`` BEFORE
    any recipe spawn — mirrors git-mcp's ``_contained_relpath`` refusal.
    """
    root = executor.worktree_root
    candidate = Path(cwd) if os.path.isabs(cwd) else root / cwd
    if not executor._contains(candidate):
        raise ValueError(f"cwd {cwd!r} escapes worktree root")


def _extract_coverage(stdout: str, stderr: str) -> int | None:
    """Return the integer coverage percentage from recipe output, else None.

    Scans the combined recipe output for a coverage.py / pytest-cov ``TOTAL``
    summary line (``TOTAL ... NN%``) and returns ``NN`` as an int. When the
    recipe emits no recognisable coverage summary, returns None (the structured
    result still carries the ``coverage`` key — FR74 — with a null value).
    """
    for text in (stdout, stderr):
        match = _COVERAGE_TOTAL_RE.search(text)
        if match is not None:
            return int(match.group(1))
    return None


def register_tools(
    mcp: FastMCP,
    executor: VerificationExecutor,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
) -> None:
    """Register the two Tier-2 verification tools on *mcp* (Story 17.3).

    Story 17.3 ships ``verification.run_build`` / ``verification.run_tests`` —
    both ``Tier.TWO``, each gated via ``check_tier`` (NO approval gate). The
    ``verification.*`` event emission lands in Story 17.4.

    Mirrors git-mcp's / task-registry's ``register_tools``: when *emitter_holder*
    is wired, each tier-gated handler is wrapped with
    ``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` from
    ``check_tier`` emits a ``capability.denied`` audit envelope via clawhip-bridge
    before re-raising.
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

    def _caller(task_id: str | None = None) -> CallerContext:
        return CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=task_id)

    async def _run_recipe_structured(
        command: str | None,
        args: list[str] | None,
        *,
        default: tuple[str, list[str]],
        cwd: str | None,
    ) -> dict[str, object]:
        """Run the resolved recipe in the sandbox; return ``{ok/pass, exit_code, logs, coverage}``.

        ``command`` / ``args`` override the recipe; when both are None the
        per-tool *default* recipe is used. The optional *cwd* is containment-
        checked (escapes refused) BEFORE any spawn — the recipe itself still runs
        with ``cwd`` pinned to the worktree root by ``run_recipe`` (no "repo
        selection"). A non-zero exit is surfaced structurally (``pass=False``); a
        spawn failure (recipe binary absent) is caught and surfaced the same way
        so a misconfigured recipe never crashes the request path.
        """
        from verification_mcp.server import VerificationResult

        if cwd is not None:
            _assert_contained_cwd(executor, cwd)
        bin_, default_args = default
        resolved_cmd = command if command is not None else bin_
        resolved_args = args if args is not None else default_args
        argv = [resolved_cmd, *resolved_args]
        try:
            result: VerificationResult = await executor.run_recipe(argv)
        except OSError as exc:
            # Spawn failure (recipe binary absent / not executable) — surface as a
            # structured failure, never crash the request path.
            return {
                "ok": False,
                "pass": False,
                "exit_code": None,
                "logs": {"stdout": "", "stderr": str(exc)},
                "coverage": None,
            }
        passed = result.returncode == 0
        return {
            "ok": passed,
            "pass": passed,
            "exit_code": result.returncode,
            "logs": {"stdout": result.stdout, "stderr": result.stderr},
            "coverage": _extract_coverage(result.stdout, result.stderr),
        }

    async def _emit_verification_event(
        event_type: str,
        payload: dict[str, object],
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Best-effort emit a ``verification.*`` event; return the surfaced descriptor.

        FR26 single-writer: the actual emission routes through *emitter_holder*
        (clawhip-bridge MCP RPC), NEVER a direct event-log write. The emit is
        BEST-EFFORT — guarded on *emitter_holder* and wrapped so an emission
        failure (audit-off, closed pipe) is logged and swallowed, never failing the
        already-completed verification run.

        The returned ``{"type": ..., "trace_id": caller_trace_id}`` descriptor is
        built UNCONDITIONALLY (independent of whether the emit fired) and surfaced
        by the caller as ``result["event"]`` so the ATDD contract can read the
        verification.* event type + inbound trace_id even when audit emission is
        disabled. Mirrors github-mcp's ``_emit_github_event``.
        """
        descriptor: dict[str, object] = {"type": event_type, "trace_id": caller_trace_id}
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    event_type, payload, caller_trace_id=caller_trace_id
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):  # noqa: TRY302 — cancellation MUST propagate (PP1 contract); re-raise control-flow
                raise
            except BaseException:  # noqa: BLE001 — best-effort audit; never fail the verification op
                log.exception(
                    "verification_event_emission_failed",
                    extra={"event_type": event_type},
                )
        return descriptor

    async def _run_and_emit(
        tool_name: str,
        command: str | None,
        args: list[str] | None,
        *,
        default: tuple[str, list[str]],
        cwd: str | None,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Run the recipe, emit ``verification.completed``, surface the descriptor.

        The event fires for BOTH a pass and a fail (a non-zero recipe exit is
        surfaced structurally, not raised). The emitted payload carries ONLY the
        recipe command + exit-status discriminators — NEVER the captured ``logs``
        (which may contain secret-bearing output) or any environment value.
        """
        structured = await _run_recipe_structured(command, args, default=default, cwd=cwd)
        bin_, default_args = default
        resolved_cmd = command if command is not None else bin_
        resolved_args = args if args is not None else default_args
        exit_code = structured["exit_code"]
        coverage = structured["coverage"]
        payload: dict[str, object] = {
            "tool": tool_name,
            "recipe": " ".join([resolved_cmd, *resolved_args]),
            "passed": bool(structured["pass"]),
            "exit_code": exit_code if isinstance(exit_code, int) else None,
            "coverage": coverage if isinstance(coverage, int) else None,
        }
        event = await _emit_verification_event(
            "verification.completed", payload, caller_trace_id=caller_trace_id
        )
        return {**structured, "event": event}

    # ------------------------------------------------------------------
    # Tool: verification.run_build (Tier-2 sandboxed recipe)
    # ------------------------------------------------------------------

    @mcp.tool(name="verification.run_build")
    @_maybe_wrap("verification.run_build")
    async def run_build(
        *,
        caller_trace_id: str,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict[str, object]:
        """Run the project's build recipe in the sandbox (Tier-2).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            command: Optional override for the build recipe binary (default
                ``just``). There is NO "repo selection" arg — the recipe always
                runs inside the sandbox worktree.
            args: Optional override for the recipe args (default ``["build"]``).
            cwd: Optional subdirectory of the worktree to run from; refused if it
                escapes the worktree root.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("verification.run_build", _caller(), TIER_MAP["verification.run_build"])
        return await _run_and_emit(
            "verification.run_build",
            command,
            args,
            default=_DEFAULT_BUILD,
            cwd=cwd,
            caller_trace_id=caller_trace_id,
        )

    # ------------------------------------------------------------------
    # Tool: verification.run_tests (Tier-2 sandboxed recipe)
    # ------------------------------------------------------------------

    @mcp.tool(name="verification.run_tests")
    @_maybe_wrap("verification.run_tests")
    async def run_tests(
        *,
        caller_trace_id: str,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict[str, object]:
        """Run the project's test recipe in the sandbox (Tier-2).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            command: Optional override for the test recipe binary (default
                ``just``). There is NO "repo selection" arg — the recipe always
                runs inside the sandbox worktree.
            args: Optional override for the recipe args (default ``["test"]``).
            cwd: Optional subdirectory of the worktree to run from; refused if it
                escapes the worktree root.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("verification.run_tests", _caller(), TIER_MAP["verification.run_tests"])
        return await _run_and_emit(
            "verification.run_tests",
            command,
            args,
            default=_DEFAULT_TESTS,
            cwd=cwd,
            caller_trace_id=caller_trace_id,
        )

    # Reference the handlers so linters see them as used (FastMCP holds the real
    # references via the decorator registration side-effect).
    _ = (run_build, run_tests)


__all__ = ["TIER_MAP", "register_tools", "validate_caller_trace_id"]
