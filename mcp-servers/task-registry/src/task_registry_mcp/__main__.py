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

OMB_MCP_AUDIT_EMISSION_ENABLED  (OPTIONAL, default ``0``)
    Story 11.2.2 PQ1 — master opt-in gate for MCP-boundary capability.denied
    audit emission. Default OFF mitigates the FR26 multi-writer concern
    (each MCP server spawning its own clawhip-bridge subprocess) until
    Story 11.2.3 ships a shared-daemon refactor. Set to ``1`` to enable.

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
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "").strip() == "1"
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
    mcp.run()


if __name__ == "__main__":
    main()
