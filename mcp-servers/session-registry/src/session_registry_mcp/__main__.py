"""session-registry-mcp entrypoint — ``python -m session_registry_mcp`` (Story 5.9 AC-6).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport.

Environment variables
---------------------
SESSION_REGISTRY_DB_PATH  (REQUIRED)
    Absolute path to the SQLite database file.
    Missing or empty → exit code 2 with stderr message.

SESSION_REGISTRY_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing or empty → exit code 2 with stderr message.

SESSION_REGISTRY_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the calling actor instance.
    Missing or empty → exit code 2 with stderr message.

SESSION_REGISTRY_CLAWHIP_BRIDGE_COMMAND  (OPTIONAL, default ``python``)
    Story 11.2.2 — command to spawn the clawhip-bridge MCP subprocess
    for ``capability.denied`` audit emission.

SESSION_REGISTRY_CLAWHIP_BRIDGE_ARGS  (OPTIONAL, default ``-m clawhip_bridge_mcp``)
    Story 11.2.2 — whitespace-separated args for the clawhip-bridge
    subprocess.

SESSION_REGISTRY_DISABLE_AUDIT_EMISSION  (OPTIONAL, default ``0``)
    Story 11.2.2 — when set to ``1``, audit-emission lifespan is skipped
    entirely. Useful for test fixtures.

Exit codes
----------
0   Clean shutdown (SIGTERM / SIGINT / EOF on stdin).
2   Missing or invalid required environment variable.
"""

from __future__ import annotations

import os
import sys
from typing import get_args

from events.envelope import ActorKind  # noqa: IMP001 — packages/

_VALID_ACTOR_KINDS = set(get_args(ActorKind))


def main() -> None:
    """Validate env vars, build server, run on stdio."""
    # -- Required: SESSION_REGISTRY_DB_PATH --
    db_path = os.environ.get("SESSION_REGISTRY_DB_PATH", "").strip()
    if not db_path:
        print(
            "session-registry: SESSION_REGISTRY_DB_PATH is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: SESSION_REGISTRY_ACTOR_KIND --
    actor_kind_raw = os.environ.get("SESSION_REGISTRY_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "session-registry: SESSION_REGISTRY_ACTOR_KIND is required but not set or empty. "
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
            f"session-registry: SESSION_REGISTRY_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: SESSION_REGISTRY_ACTOR_ID --
    actor_id = os.environ.get("SESSION_REGISTRY_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "session-registry: SESSION_REGISTRY_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Story 11.2.2 PQ1 (pass-1 review) — clawhip-bridge audit-emission config.
    # See task-registry/__main__.py for the rationale; same feature-flag default-OFF
    # discipline applies here to mitigate FR26 multi-writer until Story 11.2.3
    # ships a shared clawhip-bridge daemon.
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "").strip() == "1"
    force_disable_audit = (
        os.environ.get("SESSION_REGISTRY_DISABLE_AUDIT_EMISSION", "").strip() == "1"
    )
    clawhip_cmd: str | None = None
    clawhip_args: list[str] | None = None
    if enable_audit and not force_disable_audit:
        import shlex

        clawhip_cmd = (
            os.environ.get("SESSION_REGISTRY_CLAWHIP_BRIDGE_COMMAND", "").strip() or sys.executable
        )
        clawhip_args_raw = (
            os.environ.get("SESSION_REGISTRY_CLAWHIP_BRIDGE_ARGS", "").strip()
            or "-m clawhip_bridge_mcp"
        )
        # PQ8 (pass-1 review): shlex.split handles quoted paths with spaces.
        clawhip_args = shlex.split(clawhip_args_raw)

    from session_registry_mcp.app.main import build_server

    mcp = build_server(
        db_path=db_path,
        actor_kind=actor_kind,
        actor_id=actor_id,
        clawhip_bridge_command=clawhip_cmd,
        clawhip_bridge_args=clawhip_args,
    )
    mcp.run()


if __name__ == "__main__":
    main()
