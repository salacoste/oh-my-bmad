"""github-mcp entrypoint — ``python -m github_mcp`` (Epic 16 / Story 16.2).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport. Mirrors the git-mcp
``__main__.py`` (env validation, exit 2 on missing/invalid, typed ``actor_kind``
narrowing for ``mypy --strict``, clawhip-bridge spawn config) — but authenticates
to the GitHub REST API with a SCOPED token (``GITHUB_MCP_SCOPED_TOKEN``, Story
16.5) instead of operating over a worktree (github-mcp has NO worktree root).

Environment variables
---------------------
GITHUB_MCP_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing/empty/invalid → exit 2.

GITHUB_MCP_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the calling actor instance.
    Missing or empty → exit 2.

GITHUB_MCP_SCOPED_TOKEN  (REQUIRED)
    The narrowly-scoped GitHub credential the REST adapter authenticates with
    (Story 16.5 wires the full scoping contract; the scaffold only asserts its
    presence). The broad ``GITHUB_TOKEN`` is INTENTIONALLY NOT read here — it is
    in the operator/platform secret set and forwarding it would widen the
    leakage blast radius. Missing or empty → exit 2 (fail loud).

OMB_MCP_AUDIT_EMISSION_ENABLED  (OPTIONAL, default ``1``)
    Master gate for MCP-boundary ``capability.denied`` audit emission. Set to
    ``0`` to disable explicitly; legacy ``GITHUB_MCP_DISABLE_AUDIT_EMISSION=1``
    is the operator kill-switch.

GITHUB_MCP_CLAWHIP_BRIDGE_COMMAND  (OPTIONAL, default ``sys.executable``)
    Command to spawn the clawhip-bridge MCP subprocess for audit emission.
    Only consulted when audit emission is enabled.

GITHUB_MCP_CLAWHIP_BRIDGE_ARGS  (OPTIONAL, default ``-m clawhip_bridge_mcp``)
    Args for the clawhip-bridge subprocess. Parsed via ``shlex.split`` so
    quoted paths with spaces work. Only consulted when audit emission is
    enabled.

GITHUB_MCP_DISABLE_AUDIT_EMISSION  (OPTIONAL, default ``0``)
    Legacy kill-switch. Set to ``1`` to force-disable audit emission even when
    ``OMB_MCP_AUDIT_EMISSION_ENABLED=1``.

REGISTRY_EVENTS_DIR  (OPTIONAL — Story 16.4 Tier-3 approval source)
    Base dir of the JSONL event log, scanned by the github-mcp Tier-3
    ``approval_lookup`` for an ``approval.granted`` matching the caller's
    ``task_id``. When unset, the Tier-3 github tools have no approval source and
    every Tier-3 call is denied.

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


def main() -> None:
    """Validate env vars, build server, run on stdio."""
    # -- Required: GITHUB_MCP_ACTOR_KIND --
    actor_kind_raw = os.environ.get("GITHUB_MCP_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "github-mcp: GITHUB_MCP_ACTOR_KIND is required but not set or empty. "
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
            f"github-mcp: GITHUB_MCP_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: GITHUB_MCP_ACTOR_ID --
    actor_id = os.environ.get("GITHUB_MCP_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "github-mcp: GITHUB_MCP_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: GITHUB_MCP_SCOPED_TOKEN (Story 16.5 scoped credential) --
    # The REST adapter (16.3 / 16.4) authenticates with this SCOPED token. The
    # broad ``GITHUB_TOKEN`` is intentionally NOT read — it is a forbidden secret
    # (see tests/contract/_FORBIDDEN_SECRET_ENV_VARS). Story 16.5 wires the full
    # scoping contract; the scaffold only asserts the token's presence (fail loud).
    scoped_token = os.environ.get("GITHUB_MCP_SCOPED_TOKEN", "").strip()
    if not scoped_token:
        print(
            "github-mcp: GITHUB_MCP_SCOPED_TOKEN is required but not set or empty. "
            "github-mcp authenticates with a narrowly-scoped token (Story 16.5), "
            "NEVER the broad GITHUB_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- clawhip-bridge audit-emission config (mirror of git-mcp). --
    # ``OMB_MCP_AUDIT_EMISSION_ENABLED`` defaults ON; ``"0"`` disables.
    # Legacy ``GITHUB_MCP_DISABLE_AUDIT_EMISSION=1`` force-disables regardless.
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "1").strip() != "0"
    force_disable_audit = os.environ.get("GITHUB_MCP_DISABLE_AUDIT_EMISSION", "").strip() == "1"
    clawhip_cmd: str | None = None
    clawhip_args: list[str] | None = None
    if enable_audit and not force_disable_audit:
        clawhip_cmd = (
            os.environ.get("GITHUB_MCP_CLAWHIP_BRIDGE_COMMAND", "").strip()
            or sys.executable  # use sys.executable, not bare "python"
        )
        clawhip_args_raw = (
            os.environ.get("GITHUB_MCP_CLAWHIP_BRIDGE_ARGS", "").strip() or "-m clawhip_bridge_mcp"
        )
        # shlex.split handles quoted paths with spaces.
        clawhip_args = shlex.split(clawhip_args_raw)

    # -- Optional: REGISTRY_EVENTS_DIR (Story 16.4 Tier-3 approval source) --
    # The base dir of the JSONL event log, scanned by the github-mcp Tier-3
    # ``approval_lookup`` for an ``approval.granted`` matching the caller's
    # ``task_id``. When unset, the Tier-3 github tools have no approval source
    # and every Tier-3 call is denied.
    registry_events_dir_raw = os.environ.get("REGISTRY_EVENTS_DIR", "").strip()
    registry_events_dir = Path(registry_events_dir_raw) if registry_events_dir_raw else None

    from github_mcp.server import build_server

    mcp = build_server(
        actor_kind=actor_kind,
        actor_id=actor_id,
        scoped_token=scoped_token,
        clock=SystemClock(),
        clawhip_bridge_command=clawhip_cmd,
        clawhip_bridge_args=clawhip_args,
        registry_events_dir=registry_events_dir,
    )
    mcp.run()


if __name__ == "__main__":
    main()
