"""artifact-mcp MCP server — persisted content-addressed artifact store (Epic 19 / ADR-0011).

Exports:
    ``build_server(*, store_root, max_bytes, ttl_seconds, clock, actor_kind,
    actor_id, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Stories 19.1 / 19.2
        ship the SCAFFOLD: the :class:`~artifact_mcp.store.ArtifactStore` is
        constructed (running a startup retention sweep) and threaded into
        ``register_tools``, but no artifact tools are registered yet (``TIER_MAP``
        is empty); the artifact tools land in Stories 19.3 / 19.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      git-mcp / memory-mcp / task-registry does: when ``clawhip_bridge_command``
      is configured, a stdio MCP client to clawhip-bridge is spawned inside the
      FastMCP lifespan and held by an ``EmitterHolder`` (consumed by the tool
      decorators once tools land in 19.3 / 19.4). Startup is bounded by
      ``asyncio.wait_for(initialize(), _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - The :class:`~artifact_mcp.store.ArtifactStore` owns a DEDICATED local-FS
      subtree (NEVER the registry DB — ADR-0011 store-isolation P3-I2) rooted at
      ``store_root``: content-addressed object blobs + a WAL-mode sqlite index,
      0o2775 dirs + 0o660 files. A startup :meth:`~artifact_mcp.store.ArtifactStore.sweep`
      clears orphan temp files + enforces the configured TTL / size-cap retention.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from artifact_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder
from artifact_mcp.store import ArtifactStore

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of task-registry / git-mcp / memory-mcp
# ``_INIT_TIMEOUT``. Startup spawn of the clawhip-bridge subprocess must complete
# within this bound or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging.
_INIT_TIMEOUT: float = 30.0

# Stories 19.1-19.2 scaffold — empty until the artifact tools land in 19.3 / 19.4.
# Re-exported from ``handlers.tools`` so the canonical TIER_MAP lives in one place
# (mirrors the git-mcp / memory-mcp / task-registry handlers/tools.py shape). Tools
# register against it later.
TIER_MAP: dict[str, Tier] = {}


def build_server(
    *,
    store_root: Path,
    max_bytes: int | None = None,
    ttl_seconds: int | None = None,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Stories 19.1 / 19.2 SCAFFOLD: the :class:`~artifact_mcp.store.ArtifactStore` is
    constructed (so a malformed ``store_root`` / unwritable store dir fails fast at
    build time), a startup retention sweep runs (clearing orphan temp files +
    enforcing the configured TTL / size cap), and the store is threaded into
    ``register_tools`` — but no artifact tools are registered (``TIER_MAP`` is
    empty). The clawhip-bridge audit-emission lifespan is wired so the first
    artifact tool added in 19.3 inherits the FR26 single-writer audit path for free.

    Args:
        store_root: Absolute path to the DEDICATED content-addressed store subtree.
            The :class:`ArtifactStore` creates ``objects/`` / ``tmp/`` + a WAL-mode
            sqlite index under it. NEVER the registry DB (ADR-0011 P3-I2).
        max_bytes: Optional total-size cap (bytes) for retention. ``None`` →
            unbounded.
        ttl_seconds: Optional time-to-live (seconds) for retention. ``None`` → no
            TTL expiry.
        clock: Injected clock for deterministic testing (reserved for the artifact
            tools' Tier-3 approval lookup + event emission in 19.3 / 19.4).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit emission.
            When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log (reserved for the
            artifact tools' Tier-3 ``approval.granted`` lookup + ``artifact.*``
            event emission in 19.3 / 19.4). Threaded for parity with the git-mcp /
            memory-mcp factories; unused in the scaffold.

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Constructed now so a malformed ``store_root`` / unwritable store dir fails
    # fast at build time; the artifact tools in 19.3 / 19.4 close over this store.
    store = ArtifactStore(store_root, max_bytes=max_bytes, ttl_seconds=ttl_seconds)

    # Startup retention sweep (ADR-0011 §5): clear orphan temp files left by a
    # crashed put and enforce the configured TTL / size cap. This boot-time sweep is
    # deliberately event-LESS (Story 19.4 decision (a)): it runs before any tool call
    # with NO caller context — there is no ``caller_trace_id`` to carry, and the
    # spine's ``check_trace_id_required`` invariant forbids a trace_id-less emit. The
    # retention deletions that matter for observability are the POST-PUT ones (the
    # ``artifact.put`` handler sweeps after each put and emits ``artifact.deleted``
    # with the caller's trace_id). At boot we only log the evicted count.
    _swept = store.sweep()
    if _swept:
        log.info("artifact_mcp_startup_sweep_evicted", extra={"count": len(_swept)})

    emitter_holder: EmitterHolder | None
    lifespan_fn = None
    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4).

            Mirror of git-mcp / memory-mcp / task-registry's lifespan. Startup
            failure (clawhip-bridge subprocess refuses to launch) propagates here
            so the operator sees a hard error instead of silently degrading to
            no-emission mode. The bounded ``asyncio.wait_for(initialize(),
            _INIT_TIMEOUT)`` lives inside ``ClawhipBridgeClient.__aenter__``
            (G-FN-3).
            """
            # Explicit RuntimeError, not assert — ``python -O`` strips asserts.
            if clawhip_bridge_command is None:
                raise RuntimeError("clawhip_bridge_command must be set when lifespan_fn is wired")
            if clawhip_bridge_args is None:
                raise RuntimeError("clawhip_bridge_args must be set when lifespan_fn is wired")
            if emitter_holder is None:
                raise RuntimeError("emitter_holder must be initialized when lifespan_fn is wired")
            client = ClawhipBridgeClient(
                command=clawhip_bridge_command,
                args=clawhip_bridge_args,
                caller_actor_kind=actor_kind,
                caller_actor_id=actor_id,
            )
            await client.__aenter__()
            emitter_holder.client = client
            log.info(
                "artifact_mcp_clawhip_client_ready",
                extra={"command": clawhip_bridge_command, "args": clawhip_bridge_args},
            )
            try:
                yield
            finally:
                # Null the holder ONLY AFTER ``__aexit__`` completes so any
                # in-flight handler sees a closed-pipe error (PD-1 fail-soft),
                # not a "lifespan not wired" RuntimeError. Nested try/finally so
                # the reference is ALWAYS dropped even if teardown raises.
                try:
                    await client.__aexit__(None, None, None)
                finally:
                    emitter_holder.client = None

        lifespan_fn = _lifespan
    else:
        emitter_holder = None

    mcp = FastMCP("artifact", lifespan=lifespan_fn) if lifespan_fn else FastMCP("artifact")

    # Story 19.3: register the read (Tier-1) + mutating (Tier-2/3) artifact tools.
    # ``clock`` is threaded so the Tier-3 ``approval_lookup`` scans TODAY's JSONL
    # event log (``current_day_path(events_dir, clock.now())``). ``emitter_holder``
    # is forwarded so the audit ``capability.denied`` decorator wraps each handler.
    # ``registry_events_dir`` is the approval-source base dir; when None, every
    # Tier-3 ``artifact.delete`` call is denied (no approval source).
    from artifact_mcp.handlers.tools import make_approval_lookup, register_tools

    approval_lookup = (
        make_approval_lookup(registry_events_dir, clock)
        if registry_events_dir is not None
        else None
    )

    register_tools(
        mcp,
        store,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
        approval_lookup=approval_lookup,
    )

    return mcp


__all__ = ["ArtifactStore", "build_server"]
