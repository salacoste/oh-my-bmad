"""memory-mcp entrypoint — ``python -m memory_mcp`` (Epic 18 / ADR-0012).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport. Mirrors git-mcp's
``__main__.py`` (env validation, exit 2 on missing/invalid, typed ``actor_kind``
narrowing for ``mypy --strict``, clawhip-bridge spawn config) — the only domain
difference is ``MEMORY_MCP_STORE_PATH`` (the dedicated SQLite store) in place of
git-mcp's worktree root.

Environment variables
---------------------
MEMORY_MCP_STORE_PATH  (REQUIRED)
    Absolute path to this server's DEDICATED SQLite store DB (e.g.
    ``oh-my-bmad-data/memory-mcp/store.db``). NEVER the registry DB (ADR-0012
    store-isolation P3-I2). Missing or empty → exit 2.

MEMORY_MCP_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing/empty/invalid → exit 2.

MEMORY_MCP_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the calling actor instance.
    Missing or empty → exit 2.

OMB_MCP_AUDIT_EMISSION_ENABLED  (OPTIONAL, default ``1``)
    Master gate for MCP-boundary ``capability.denied`` audit emission. Set to
    ``0`` to disable explicitly; legacy ``MEMORY_MCP_DISABLE_AUDIT_EMISSION=1``
    is the operator kill-switch.

MEMORY_MCP_CLAWHIP_BRIDGE_COMMAND  (OPTIONAL, default ``sys.executable``)
    Command to spawn the clawhip-bridge MCP subprocess for audit emission.
    Only consulted when audit emission is enabled.

MEMORY_MCP_CLAWHIP_BRIDGE_ARGS  (OPTIONAL, default ``-m clawhip_bridge_mcp``)
    Args for the clawhip-bridge subprocess. Parsed via ``shlex.split`` so
    quoted paths with spaces work. Only consulted when audit emission is
    enabled.

MEMORY_MCP_DISABLE_AUDIT_EMISSION  (OPTIONAL, default ``0``)
    Legacy kill-switch. Set to ``1`` to force-disable audit emission even when
    ``OMB_MCP_AUDIT_EMISSION_ENABLED=1``.

REGISTRY_EVENTS_DIR  (OPTIONAL)
    Base dir of the JSONL event log (reserved for the memory tools' ``memory.*``
    event emission in Story 18.4). When unset, no event source is wired.

Exit codes
----------
0   Clean shutdown (SIGTERM / SIGINT / EOF on stdin).
2   Missing or invalid required environment variable.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import get_args

from events.clock import SystemClock  # noqa: IMP001 — packages/
from events.envelope import ActorKind  # noqa: IMP001 — packages/

_VALID_ACTOR_KINDS = set(get_args(ActorKind))

_SERVER_NAME = "memory-mcp"
_DEFAULT_PORT = 8086

# Phase 10 (ADR-0022): streamable-http opt-in.
_VALID_TRANSPORTS = frozenset({"stdio", "streamable-http"})


def _resolve_transport() -> str:
    """Read MCP_TRANSPORT env var; default stdio."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in _VALID_TRANSPORTS:
        print(
            f"{_SERVER_NAME}: MCP_TRANSPORT={transport!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}",
            file=sys.stderr,
        )
        sys.exit(2)
    return transport


def main() -> None:
    """Validate env vars, build server, run on stdio."""
    # -- Required: MEMORY_MCP_STORE_PATH --
    store_path_raw = os.environ.get("MEMORY_MCP_STORE_PATH", "").strip()
    if not store_path_raw:
        print(
            "memory-mcp: MEMORY_MCP_STORE_PATH is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: MEMORY_MCP_ACTOR_KIND --
    actor_kind_raw = os.environ.get("MEMORY_MCP_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "memory-mcp: MEMORY_MCP_ACTOR_KIND is required but not set or empty. "
            "Set it to one of: operator|orchestrator|worker|system|clawhip",
            file=sys.stderr,
        )
        sys.exit(2)
    actor_kind: ActorKind
    if actor_kind_raw == "operator":
        actor_kind = "operator"
    elif actor_kind_raw == "orchestrator":
        actor_kind = "orchestrator"
    elif actor_kind_raw == "worker":
        actor_kind = "worker"
    elif actor_kind_raw == "system":
        actor_kind = "system"
    elif actor_kind_raw == "clawhip":
        actor_kind = "clawhip"
    else:
        print(
            f"memory-mcp: MEMORY_MCP_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: MEMORY_MCP_ACTOR_ID --
    actor_id = os.environ.get("MEMORY_MCP_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "memory-mcp: MEMORY_MCP_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- clawhip-bridge audit-emission config (mirror of git-mcp). --
    # ``OMB_MCP_AUDIT_EMISSION_ENABLED`` defaults ON; ``"0"`` disables.
    # Legacy ``MEMORY_MCP_DISABLE_AUDIT_EMISSION=1`` force-disables regardless.
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "1").strip() != "0"
    force_disable_audit = os.environ.get("MEMORY_MCP_DISABLE_AUDIT_EMISSION", "").strip() == "1"
    clawhip_cmd: str | None = None
    clawhip_args: list[str] | None = None
    if enable_audit and not force_disable_audit:
        clawhip_cmd = (
            os.environ.get("MEMORY_MCP_CLAWHIP_BRIDGE_COMMAND", "").strip()
            or sys.executable  # use sys.executable, not bare "python"
        )
        clawhip_args_raw = (
            os.environ.get("MEMORY_MCP_CLAWHIP_BRIDGE_ARGS", "").strip() or "-m clawhip_bridge_mcp"
        )
        # shlex.split handles quoted paths with spaces.
        clawhip_args = shlex.split(clawhip_args_raw)

    # -- Optional: REGISTRY_EVENTS_DIR (Story 18.4 memory.* event source). --
    # Reserved for the memory tools' ``memory.*`` event emission; threaded for
    # parity with git-mcp. When unset, no event source is wired.
    registry_events_dir_raw = os.environ.get("REGISTRY_EVENTS_DIR", "").strip()
    registry_events_dir = Path(registry_events_dir_raw) if registry_events_dir_raw else None

    from memory_mcp.server import build_server

    mcp = build_server(
        store_path=Path(store_path_raw),
        clock=SystemClock(),
        actor_kind=actor_kind,
        actor_id=actor_id,
        clawhip_bridge_command=clawhip_cmd,
        clawhip_bridge_args=clawhip_args,
        registry_events_dir=registry_events_dir,
    )

    transport = _resolve_transport()
    if transport == "streamable-http":
        _run_streamable_http(mcp)
    else:
        mcp.run()  # stdio — zero change


def _run_streamable_http(mcp: object) -> None:  # noqa: MCP001 — ADR-0022: streamable-http allowed in __main__.py
    """Mount auth middleware and run with uvicorn (Phase 10 / ADR-0022)."""
    import uvicorn  # noqa: PLC0415 — conditional import, stdio path never loads this
    from mcp_auth import (  # noqa: MCP001 — ADR-0022: streamable-http allowed in __main__.py
        BearerTokenMiddleware,
        McpAuthSettings,
    )
    from mtls import create_uvicorn_ssl_config  # noqa: IMP001 — packages/

    auth_settings = McpAuthSettings.from_env()
    port = int(os.environ.get("MCP_PORT", str(_DEFAULT_PORT)))
    ssl_config = create_uvicorn_ssl_config()

    print(
        f"{_SERVER_NAME}: starting streamable-http transport on 0.0.0.0:{port} "
        f"(auth={'enabled' if auth_settings.enabled else 'disabled'}, "
        f"tls={'enabled' if ssl_config else 'disabled'})",
        file=sys.stderr,
    )

    app = mcp.streamable_http_app()  # noqa: MCP001 — ADR-0022: streamable-http allowed in __main__.py
    app = BearerTokenMiddleware(app, auth_settings)
    uvicorn.run(app, host="0.0.0.0", port=port, **(ssl_config or {}))


if __name__ == "__main__":
    main()
