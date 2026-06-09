"""task-registry-mcp entrypoint — ``python -m task_registry_mcp`` (Story 5.8 AC-6).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport.

Environment variables
---------------------
TASK_REGISTRY_DB_PATH  (REQUIRED)
    Absolute path to the SQLite database file.
    Missing or empty → exit code 2 with stderr message.

TASK_REGISTRY_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing or empty → exit code 2 with stderr message.

TASK_REGISTRY_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the calling actor instance.
    Missing or empty → exit code 2 with stderr message.

OMB_MCP_AUDIT_EMISSION_ENABLED  (OPTIONAL, default ``1``)
    Story 11.2.3 — master gate for MCP-boundary capability.denied audit
    emission. Default ON since Story 11.2.3 closed the FR26 multi-writer
    concern (fcntl file-lock in EventLogWriter) and the PQ9 audit-forgery
    vector (dedicated forward_capability_denied_audit MCP tool with
    caller identity validation). Set to ``0`` to disable explicitly;
    legacy ``TASK_REGISTRY_DISABLE_AUDIT_EMISSION=1`` is the operator
    kill-switch.

TASK_REGISTRY_CLAWHIP_BRIDGE_COMMAND  (OPTIONAL, default ``sys.executable``)
    Story 11.2.2 — command to spawn the clawhip-bridge MCP subprocess
    for ``capability.denied`` audit emission. Only consulted when
    ``OMB_MCP_AUDIT_EMISSION_ENABLED=1``.

TASK_REGISTRY_CLAWHIP_BRIDGE_ARGS  (OPTIONAL, default ``-m clawhip_bridge_mcp``)
    Story 11.2.2 — args for the clawhip-bridge subprocess. Parsed via
    ``shlex.split`` so quoted paths with spaces work. Only consulted when
    ``OMB_MCP_AUDIT_EMISSION_ENABLED=1``.

TASK_REGISTRY_DISABLE_AUDIT_EMISSION  (OPTIONAL, default ``0``)
    Story 11.2.2 — legacy kill-switch. Set to ``1`` to force-disable
    audit-emission even when ``OMB_MCP_AUDIT_EMISSION_ENABLED=1`` is set
    (useful for operator rollback without re-deploying). Has NO effect
    when ``OMB_MCP_AUDIT_EMISSION_ENABLED`` is unset — audit emission is
    already default-OFF without it.

Exit codes
----------
0   Clean shutdown (SIGTERM / SIGINT / EOF on stdin).
2   Missing or invalid required environment variable.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import get_args

from events.envelope import ActorKind  # noqa: IMP001 — packages/

_VALID_ACTOR_KINDS = set(get_args(ActorKind))

_SERVER_NAME = "task-registry"
_DEFAULT_PORT = 8081

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
    # -- Required: TASK_REGISTRY_DB_PATH --
    db_path = os.environ.get("TASK_REGISTRY_DB_PATH", "").strip()
    if not db_path:
        print(
            "task-registry: TASK_REGISTRY_DB_PATH is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: TASK_REGISTRY_ACTOR_KIND --
    actor_kind_raw = os.environ.get("TASK_REGISTRY_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "task-registry: TASK_REGISTRY_ACTOR_KIND is required but not set or empty. "
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
            f"task-registry: TASK_REGISTRY_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: TASK_REGISTRY_ACTOR_ID --
    actor_id = os.environ.get("TASK_REGISTRY_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "task-registry: TASK_REGISTRY_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Story 11.2.2 PQ1 (pass-1 review) — clawhip-bridge audit-emission config.
    #
    # FEATURE-FLAG DEFAULT-OFF: ``OMB_MCP_AUDIT_EMISSION_ENABLED=1`` must be
    # explicitly set to spawn a clawhip-bridge subprocess and emit audit
    # events on tier denials. Default OFF mitigates the FR26 multi-writer
    # concern (Story 11.2.2 pass-1 Edge Hunter P0): each MCP server
    # spawning its own clawhip-bridge subprocess would yield N concurrent
    # JSONL writers to the event log. Until Story 11.2.3 ships a shared
    # clawhip-bridge daemon (or file-lock serialization), opt-in only.
    #
    # Legacy ``TASK_REGISTRY_DISABLE_AUDIT_EMISSION`` retained as override
    # (set to "1" to force-disable even when the new flag is on) for
    # operators rolling back without re-deploying.
    # Story 11.2.3 AC6: feature flag now defaults to ON. Story 11.2.2's
    # pass-1 default-OFF mitigation became redundant once 11.2.3 closed
    # the FR26 multi-writer concern (fcntl file-lock in EventLogWriter,
    # commit f1e304d) and the PQ9 audit-forgery vector (dedicated
    # ``forward_capability_denied_audit`` MCP tool with caller identity
    # validation). Operators can still kill-switch via
    # ``TASK_REGISTRY_DISABLE_AUDIT_EMISSION=1``.
    #
    # PP7 (pass-1 review): documented behavior on empty-string vs unset.
    # ``unset``  → ON  (default)
    # ``""``     → ON  (treated as "use default")
    # ``"1"``    → ON
    # ``"0"``    → OFF
    # ``anything else`` → ON (lenient — defensive default-ON for audit)
    # Operators who want OFF MUST set ``"0"`` explicitly, OR use the
    # legacy ``TASK_REGISTRY_DISABLE_AUDIT_EMISSION=1`` kill-switch.
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "1").strip() != "0"
    force_disable_audit = os.environ.get("TASK_REGISTRY_DISABLE_AUDIT_EMISSION", "").strip() == "1"
    clawhip_cmd: str | None = None
    clawhip_args: list[str] | None = None
    if enable_audit and not force_disable_audit:
        clawhip_cmd = (
            os.environ.get("TASK_REGISTRY_CLAWHIP_BRIDGE_COMMAND", "").strip()
            or sys.executable  # PQ-Edge MEDIUM #6: use sys.executable not bare "python"
        )
        clawhip_args_raw = (
            os.environ.get("TASK_REGISTRY_CLAWHIP_BRIDGE_ARGS", "").strip()
            or "-m clawhip_bridge_mcp"
        )
        # PQ8 (pass-1 review): shlex.split handles quoted paths with spaces.
        clawhip_args = shlex.split(clawhip_args_raw)

    from task_registry_mcp.app.main import build_server

    mcp = build_server(
        db_path=db_path,
        actor_kind=actor_kind,
        actor_id=actor_id,
        clawhip_bridge_command=clawhip_cmd,
        clawhip_bridge_args=clawhip_args,
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

    auth_settings = McpAuthSettings.from_env()
    port = int(os.environ.get("MCP_PORT", str(_DEFAULT_PORT)))

    print(
        f"{_SERVER_NAME}: starting streamable-http transport on 0.0.0.0:{port} "
        f"(auth={'enabled' if auth_settings.enabled else 'disabled'})",
        file=sys.stderr,
    )

    app = mcp.streamable_http_app()  # noqa: MCP001 — ADR-0022: streamable-http allowed in __main__.py
    app = BearerTokenMiddleware(app, auth_settings)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
