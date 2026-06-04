"""artifact-mcp MCP tool handlers (Epic 19 / Stories 19.3 + 19.4).

Story 19.3 lands the four artifact tools over the content-addressed
:class:`~artifact_mcp.store.ArtifactStore`:

  * ``artifact.get`` (Tier-1 bounded read) — fetch the bytes stored under a
    content hash;
  * ``artifact.list`` (Tier-1 bounded read) — enumerate the index;
  * ``artifact.put`` (Tier-2 store mutation — gated by ``check_tier``, NO
    approval gate) — persist opaque content under its SHA-256 hash; and
  * ``artifact.delete`` (Tier-3 — approval-gated via ``check_tier_with_approval``
    + the *approval_lookup* threaded into ``register_tools`` here) — operator-
    approved single-object deletion.

Story 19.4 adds the ``artifact.*`` event emission, wired here via
``_emit_artifact_event`` (mirror of memory-mcp's ``_emit_memory_event``):

  * ``artifact.put`` → ``artifact.stored`` (hash / name / size_bytes / deduped),
    THEN the post-put retention sweep → ``artifact.deleted`` (``reason="retention"``)
    per evicted object — all carrying the put's inbound ``caller_trace_id``;
  * ``artifact.delete`` (Tier-3) → ``artifact.deleted`` (``reason="requested"``) on
    a successful deletion, carrying the delete's inbound ``caller_trace_id``.

Each payload is METADATA ONLY — the artifact content/bytes NEVER enter the spine
event (ADR-0011 §3/§5; spine events are append-only + queryable). The startup
sweep in ``build_server`` is deliberately event-LESS: it runs at boot with NO
caller context (no ``caller_trace_id``), so emitting there would violate the
trace_id-required invariant; the retention deletions that matter for observability
are the post-put ones, which DO carry the caller's trace_id.

Each tool is registered with an EXPLICIT dotted MCP name
(``@mcp.tool(name="artifact.get")``) so ``list_tools()`` surfaces the canonical
``artifact.<op>`` id the 19.1 ATDD contracts assert (FastMCP would otherwise
default the id to the Python function name ``artifact_get`` — which is ≠
``artifact.get``). Every handler:

  1. validates ``caller_trace_id`` FIRST (FR58 / Story 9.1 shape contract);
  2. gates on its tier via ``check_tier("artifact.<op>", CallerContext(...),
     TIER_MAP["artifact.<op>"])`` — or, for the Tier-3 ``artifact.delete``,
     ``check_tier_with_approval(..., approval_lookup=approval_lookup)`` — the
     ``TIER_MAP[...]`` subscript is passed as a DIRECT argument so
     ``scripts/check_tier_declarations.py`` recognises the tool as tiered; and
  3. routes through the injected :class:`~artifact_mcp.store.ArtifactStore`,
     returning a deterministic structured result.

P0/security (ADR-0011 §5): the artifact content NEVER reaches an audit/event
payload (it may be large build/run output or hold sensitive cross-task data).
``artifact.get`` returns the bytes base64-encoded in a JSON-safe ``content``
field — it does not log them; the Tier-3 ``artifact.delete`` audit envelope
(``capability.denied``) carries only the attempted action + actor id.

The ``validate_caller_trace_id`` helper is duplicated byte-identically across
clawhip-bridge, task-registry, session-registry, git-mcp, and memory-mcp
(mcp-servers cannot share code per Story 5.8's import-graph constraint); the same
drift guard extends to artifact-mcp now that it ships the helper.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier, check_tier_with_approval
from capabilities.emit import emit_capability_denied_on_deny
from events import current_day_path, read_log_lines  # noqa: IMP001 — packages/
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/

from artifact_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path as _Path

    from events.clock import Clock  # noqa: IMP001 — packages/
    from mcp.server.fastmcp import FastMCP

    from artifact_mcp.store import ArtifactStore

log = logging.getLogger(__name__)

# Story 19.3: the four artifact tools. Keys are the canonical dotted
# ``artifact.<op>`` MCP tool ids (matched to the ``@mcp.tool(name=…)``
# registration below):
#   artifact.get    == Tier.ONE   (bounded read; Story 19.3)
#   artifact.list   == Tier.ONE   (bounded read; Story 19.3)
#   artifact.put    == Tier.TWO   (store mutation; ``check_tier`` — NO approval gate; 19.3)
#   artifact.delete == Tier.THREE (approval-gated via ``check_tier_with_approval``; 19.3)
TIER_MAP: dict[str, Tier] = {
    "artifact.get": Tier.ONE,
    "artifact.list": Tier.ONE,
    "artifact.put": Tier.TWO,
    "artifact.delete": Tier.THREE,
}


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


def make_approval_lookup(
    base_dir: _Path,
    clock: Clock,
) -> Callable[[str, str], Awaitable[bool]]:
    """Return an async ``(task_id, action) -> bool`` approval lookup for Tier-3 gating.

    Story 19.3: the Tier-3 ``artifact.delete`` tool is gated by
    ``check_tier_with_approval(..., approval_lookup=...)`` — the lookup returns
    True only when a matching ``approval.granted`` event exists for the caller's
    *task_id*. Scans TODAY's JSONL event log for ``approval.granted`` events whose
    payload ``task_id`` matches; approvals are currently task-scoped only (the
    *action* parameter is accepted but unused — reserved for future wildcard
    matching).

    COPIED (not imported) from git-mcp's ``make_approval_lookup`` — the Story 5.8
    import-graph constraint forbids cross-importing between mcp-servers, so the
    lookup body is duplicated here (the same discipline that duplicates
    ``validate_caller_trace_id``). The git / clawhip-bridge precedents carry the
    same Phase-1 limitation:

    Phase-1 limitation: only scans today's JSONL log file. Cross-day approvals
    (granted yesterday, used today) are not covered. Acceptable for the current
    scale; addressed when the materialized Event table becomes the primary
    approval source.
    """

    async def _lookup(task_id: str, action: str) -> bool:  # noqa: ARG001 — action reserved for future wildcard matching
        # NOTE: O(n) linear scan of today's JSONL per check — acceptable for
        # current scale, but cache or index if event volume grows.
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
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``.

    The configured ``actor_id`` is the calling actor's identity for the duration
    of this server process (set at startup). Tool kwargs do not carry actor
    identity — it comes from the server's launch config — so the extractor
    returns the closed-over value irrespective of args/kwargs.
    """

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


def register_tools(
    mcp: FastMCP,
    store: ArtifactStore,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
    approval_lookup: Callable[[str, str], Awaitable[bool]] | None = None,
) -> None:
    """Register the artifact read (Tier-1) + mutating (Tier-2/3) tools on *mcp*.

    Story 19.3 ships the four artifact tools: ``artifact.get`` / ``artifact.list``
    (Tier-1 bounded reads), ``artifact.put`` (Tier-2 store mutation), and
    ``artifact.delete`` (Tier-3, approval-gated).

    Mirrors git-mcp / memory-mcp's ``register_tools``: when *emitter_holder* is
    wired, each tier-gated handler is wrapped with ``emit_capability_denied_on_deny``
    so a ``CapabilityDenied`` from ``check_tier`` / ``check_tier_with_approval``
    emits a ``capability.denied`` audit envelope via clawhip-bridge before
    re-raising (the Tier-3 negative-test path).

    *approval_lookup* is the async ``(task_id, action) -> bool`` callable threaded
    into ``check_tier_with_approval`` for the Tier-3 ``artifact.delete`` tool; when
    None the Tier-3 tool denies every call (no approval source — test/no-approval
    default).

    Args:
        mcp: The FastMCP server to register tools on.
        store: The live artifact store the tool handlers operate against.
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        emitter_holder: Optional clawhip-bridge audit-emission holder; when None,
            audit emission is disabled (test mode).
        approval_lookup: Optional async approval lookup for the Tier-3
            ``artifact.delete`` tool. When None, every Tier-3 call is denied (no
            approval source).
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

    async def _emit_artifact_event(
        event_type: str,
        payload: dict[str, object],
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Best-effort emit an ``artifact.*`` event; return the surfaced descriptor.

        FR26 single-writer: the actual emission routes through *emitter_holder*
        (clawhip-bridge MCP RPC), NEVER a direct event-log write. The emit is
        BEST-EFFORT — guarded on *emitter_holder* and wrapped so an emission failure
        (audit-off, closed pipe) is logged and swallowed, never failing the
        already-completed store op.

        The returned ``{"type": ..., "trace_id": caller_trace_id}`` descriptor is
        built UNCONDITIONALLY (independent of whether the emit fired) and surfaced by
        the caller as ``result["event"]`` so the ATDD contract can read the
        artifact.* event type + inbound trace_id even when audit emission is disabled.
        Mirrors memory-mcp's ``_emit_memory_event`` / verification-mcp's
        ``_emit_verification_event``.

        P0/security (ADR-0011 §3/§5): *payload* is METADATA ONLY — the artifact
        content/bytes NEVER reach this surface (the bytes go to the store, full
        stop). Spine events are append-only + queryable.
        """
        descriptor: dict[str, object] = {"type": event_type, "trace_id": caller_trace_id}
        if emitter_holder is not None:
            try:
                await emitter_holder.emit_event(
                    event_type, payload, caller_trace_id=caller_trace_id
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):  # noqa: TRY302 — cancellation MUST propagate (PP1 contract); re-raise control-flow
                raise
            except BaseException:  # noqa: BLE001 — best-effort audit; never fail the artifact op
                log.exception(
                    "artifact_event_emission_failed",
                    extra={"event_type": event_type},
                )
        return descriptor

    # ------------------------------------------------------------------
    # Tool: artifact.get (Tier-1 bounded read)
    # ------------------------------------------------------------------

    @mcp.tool(name="artifact.get")
    @_maybe_wrap("artifact.get")
    async def artifact_get(
        *,
        caller_trace_id: str,
        hash: str,  # noqa: A002 — content-hash arg; "hash" is the ATDD/contract kwarg name
    ) -> dict[str, object]:
        """Return the bytes stored under content *hash* (Tier-1 bounded read).

        The object is re-hashed on read by the store; a tampered / partial blob
        yields ``found=False`` (the store returns None) rather than bad bytes. On a
        hit the content is returned base64-encoded in ``content`` (JSON-safe; the
        raw bytes are NEVER logged — ADR-0011 §5) with the byte ``size``.

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            hash: The lowercase hex SHA-256 of the desired content.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("artifact.get", _caller(), TIER_MAP["artifact.get"])
        content = store.get(hash)
        if content is None:
            return {"ok": True, "found": False, "hash": hash}
        return {
            "ok": True,
            "found": True,
            "hash": hash,
            "size": len(content),
            "content": base64.b64encode(content).decode("ascii"),
        }

    # ------------------------------------------------------------------
    # Tool: artifact.list (Tier-1 bounded read)
    # ------------------------------------------------------------------

    @mcp.tool(name="artifact.list")
    @_maybe_wrap("artifact.list")
    async def artifact_list(
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Enumerate the artifact index, newest-first (Tier-1 bounded read).

        Returns index METADATA ONLY (``name`` / ``hash`` / ``size`` / ``task_id`` /
        ``created_at`` / ``retention_class``) — never the object bytes.

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("artifact.list", _caller(), TIER_MAP["artifact.list"])
        return {"ok": True, "artifacts": store.list()}

    # ------------------------------------------------------------------
    # Tool: artifact.put (Tier-2 store mutation)
    # ------------------------------------------------------------------

    @mcp.tool(name="artifact.put")
    @_maybe_wrap("artifact.put")
    async def artifact_put(
        *,
        caller_trace_id: str,
        content: str,
        name: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, object]:
        """Persist *content* under its SHA-256 hash (Tier-2 store mutation).

        *content* is the artifact payload **base64-encoded** — the symmetric wire
        form ``artifact.get`` returns — decoded to the opaque bytes the
        content-addressed store persists. base64 (not raw text) so an artifact
        store can hold arbitrary BINARY build/run output, not only UTF-8 text.
        Invalid base64 returns a structured ``ok=False`` (never raises to the
        boundary). Identical content deduplicates (``deduped=True``).

        On success this surfaces an ``artifact.stored`` event descriptor in
        ``result["event"]`` carrying the inbound *caller_trace_id* (Story 19.4). It
        THEN runs the post-put retention sweep (``store.sweep()`` — retention is NOT
        enforced inside ``store.put``): each hash the sweep evicts surfaces an
        ``artifact.deleted`` event (``reason="retention"``, system-initiated
        deletion per ADR-0011 §5) carrying the SAME *caller_trace_id*, returned in
        ``result["retention_deleted"]``. Every payload is METADATA ONLY — the
        artifact bytes NEVER enter the spine event (ADR-0011 §3/§5).

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            content: The artifact payload, base64-encoded (binary-safe; symmetric
                with ``artifact.get``'s base64 ``content``).
            name: Optional logical name to index the content under.
            task_id: Optional originating task id recorded in the index row.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier("artifact.put", _caller(task_id=task_id), TIER_MAP["artifact.put"])
        try:
            raw = base64.b64decode(content, validate=True)
        except ValueError:
            return {"ok": False, "error": "content must be valid base64"}
        record = store.put(raw, name=name, task_id=task_id)
        # METADATA ONLY — the artifact bytes NEVER reach the event (ADR-0011 §5).
        stored_payload: dict[str, object] = {
            "hash": record["hash"],
            "name": record["name"],
            "size_bytes": record["size"],
            "deduped": record["deduped"],
        }
        event = await _emit_artifact_event(
            "artifact.stored", stored_payload, caller_trace_id=caller_trace_id
        )
        # Post-put retention sweep (ADR-0011 §5): retention is system-initiated and
        # runs WITHOUT a Tier-3 approval. Each evicted hash surfaces an
        # artifact.deleted event (reason="retention") with the SAME caller_trace_id.
        retention_events: list[dict[str, object]] = []
        for deleted_hash in store.sweep():
            deleted_payload: dict[str, object] = {
                "hash": deleted_hash,
                "reason": "retention",
                "size_bytes": 0,
            }
            retention_events.append(
                await _emit_artifact_event(
                    "artifact.deleted", deleted_payload, caller_trace_id=caller_trace_id
                )
            )
        return {
            "ok": True,
            "hash": record["hash"],
            "size": record["size"],
            "name": record["name"],
            "deduped": record["deduped"],
            "event": event,
            "retention_deleted": retention_events,
        }

    # ------------------------------------------------------------------
    # Tool: artifact.delete (Tier-3 — approval-gated single-object deletion)
    # ------------------------------------------------------------------

    @mcp.tool(name="artifact.delete")
    @_maybe_wrap("artifact.delete")
    async def artifact_delete(
        *,
        caller_trace_id: str,
        hash: str,  # noqa: A002 — content-hash arg; "hash" is the ATDD/contract kwarg name
        task_id: str,
    ) -> dict[str, object]:
        """Delete the object stored under content *hash* (Tier-3; approval-gated).

        The Tier-3 gate ``check_tier_with_approval`` denies the call unless a
        matching ``approval.granted`` exists for *task_id*; on denial the
        ``@_maybe_wrap`` decorator emits ``capability.denied`` before re-raising.
        Retention sweeps (system-initiated deletion) run WITHOUT this gate — only
        the explicit ``artifact.delete`` tool is approval-gated (ADR-0011 §5).

        On a successful deletion (``deleted=True``) this surfaces an
        ``artifact.deleted`` event descriptor in ``result["event"]`` carrying the
        inbound *caller_trace_id* and ``reason="requested"`` (METADATA ONLY — the
        artifact bytes NEVER enter the spine event, ADR-0011 §3/§5). A no-op delete
        (nothing matched, ``deleted=False``) emits NO event.

        Args:
            caller_trace_id: FR58 correlation ID (UUIDv7 OR ``tg:<update_id>``).
            hash: The lowercase hex SHA-256 of the object to delete.
            task_id: Task whose ``approval.granted`` authorizes this Tier-3 delete.
        """
        validate_caller_trace_id(caller_trace_id)
        await check_tier_with_approval(
            "artifact.delete",
            _caller(task_id=task_id),
            TIER_MAP["artifact.delete"],
            approval_lookup=approval_lookup,
        )
        deleted = store.delete(hash)
        result: dict[str, object] = {"ok": True, "deleted": deleted, "hash": hash}
        if deleted:
            # METADATA ONLY — the artifact bytes NEVER reach the event (ADR-0011 §5).
            deleted_payload: dict[str, object] = {
                "hash": hash,
                "reason": "requested",
                "size_bytes": 0,
            }
            result["event"] = await _emit_artifact_event(
                "artifact.deleted", deleted_payload, caller_trace_id=caller_trace_id
            )
        return result

    # Reference the handlers so linters see them as used (FastMCP holds the real
    # references via the decorator registration side-effect).
    _ = (artifact_get, artifact_list, artifact_put, artifact_delete)


__all__ = ["TIER_MAP", "make_approval_lookup", "register_tools", "validate_caller_trace_id"]
