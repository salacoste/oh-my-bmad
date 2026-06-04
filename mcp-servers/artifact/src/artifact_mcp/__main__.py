"""artifact-mcp entrypoint — ``python -m artifact_mcp`` (Epic 19 / ADR-0011).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport. Mirrors memory-mcp's
``__main__.py`` (env validation, exit 2 on missing/invalid, typed ``actor_kind``
narrowing for ``mypy --strict``, clawhip-bridge spawn config) — the domain
differences are ``ARTIFACT_MCP_STORE_PATH`` (the content-addressed store ROOT
DIR) in place of memory-mcp's SQLite store file, plus the optional retention
config (``ARTIFACT_MCP_RETENTION_MAX_BYTES`` / ``ARTIFACT_MCP_RETENTION_TTL_SECONDS``).

Environment variables
---------------------
ARTIFACT_MCP_STORE_PATH  (REQUIRED)
    Absolute path to this server's DEDICATED content-addressed store ROOT dir
    (e.g. ``oh-my-bmad-data/artifact-mcp/``). The store creates ``objects/`` /
    ``tmp/`` + a sqlite ``index.db`` under it. NEVER the registry DB (ADR-0011
    store-isolation P3-I2). Missing or empty → exit 2.

ARTIFACT_MCP_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing/empty/invalid → exit 2.

ARTIFACT_MCP_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the calling actor instance.
    Missing or empty → exit 2.

ARTIFACT_MCP_RETENTION_MAX_BYTES  (OPTIONAL)
    Operator-configurable total-size cap (bytes) for retention. Unset → unbounded.
    A non-integer value → exit 2.

ARTIFACT_MCP_RETENTION_TTL_SECONDS  (OPTIONAL)
    Operator-configurable time-to-live (seconds) for retention. Unset → no TTL.
    A non-integer value → exit 2.

OMB_MCP_AUDIT_EMISSION_ENABLED  (OPTIONAL, default ``1``)
    Master gate for MCP-boundary ``capability.denied`` audit emission. Set to
    ``0`` to disable explicitly; legacy ``ARTIFACT_MCP_DISABLE_AUDIT_EMISSION=1``
    is the operator kill-switch.

ARTIFACT_MCP_CLAWHIP_BRIDGE_COMMAND  (OPTIONAL, default ``sys.executable``)
    Command to spawn the clawhip-bridge MCP subprocess for audit emission.
    Only consulted when audit emission is enabled.

ARTIFACT_MCP_CLAWHIP_BRIDGE_ARGS  (OPTIONAL, default ``-m clawhip_bridge_mcp``)
    Args for the clawhip-bridge subprocess. Parsed via ``shlex.split`` so
    quoted paths with spaces work. Only consulted when audit emission is enabled.

ARTIFACT_MCP_DISABLE_AUDIT_EMISSION  (OPTIONAL, default ``0``)
    Legacy kill-switch. Set to ``1`` to force-disable audit emission even when
    ``OMB_MCP_AUDIT_EMISSION_ENABLED=1``.

REGISTRY_EVENTS_DIR  (OPTIONAL)
    Base dir of the JSONL event log (reserved for the artifact tools' Tier-3
    ``approval.granted`` lookup + ``artifact.*`` event emission in 19.3 / 19.4).
    When unset, no event source is wired.

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


def _parse_optional_int(raw: str, *, env_name: str) -> int | None:
    """Parse an optional non-negative int env var; unset → None, invalid → exit 2."""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except ValueError:
        print(
            f"artifact-mcp: {env_name}={raw!r} is invalid — must be an integer (bytes/seconds).",
            file=sys.stderr,
        )
        sys.exit(2)
    if value < 0:
        print(
            f"artifact-mcp: {env_name}={raw!r} is invalid — must be non-negative.",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


def main() -> None:
    """Validate env vars, build server, run on stdio."""
    # -- Required: ARTIFACT_MCP_STORE_PATH --
    store_path_raw = os.environ.get("ARTIFACT_MCP_STORE_PATH", "").strip()
    if not store_path_raw:
        print(
            "artifact-mcp: ARTIFACT_MCP_STORE_PATH is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: ARTIFACT_MCP_ACTOR_KIND --
    actor_kind_raw = os.environ.get("ARTIFACT_MCP_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "artifact-mcp: ARTIFACT_MCP_ACTOR_KIND is required but not set or empty. "
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
            f"artifact-mcp: ARTIFACT_MCP_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: ARTIFACT_MCP_ACTOR_ID --
    actor_id = os.environ.get("ARTIFACT_MCP_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "artifact-mcp: ARTIFACT_MCP_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Optional: retention config (read at startup; unset → unbounded/no-TTL). --
    max_bytes = _parse_optional_int(
        os.environ.get("ARTIFACT_MCP_RETENTION_MAX_BYTES", ""),
        env_name="ARTIFACT_MCP_RETENTION_MAX_BYTES",
    )
    ttl_seconds = _parse_optional_int(
        os.environ.get("ARTIFACT_MCP_RETENTION_TTL_SECONDS", ""),
        env_name="ARTIFACT_MCP_RETENTION_TTL_SECONDS",
    )

    # -- clawhip-bridge audit-emission config (mirror of memory-mcp). --
    # ``OMB_MCP_AUDIT_EMISSION_ENABLED`` defaults ON; ``"0"`` disables.
    # Legacy ``ARTIFACT_MCP_DISABLE_AUDIT_EMISSION=1`` force-disables regardless.
    enable_audit = os.environ.get("OMB_MCP_AUDIT_EMISSION_ENABLED", "1").strip() != "0"
    force_disable_audit = os.environ.get("ARTIFACT_MCP_DISABLE_AUDIT_EMISSION", "").strip() == "1"
    clawhip_cmd: str | None = None
    clawhip_args: list[str] | None = None
    if enable_audit and not force_disable_audit:
        clawhip_cmd = (
            os.environ.get("ARTIFACT_MCP_CLAWHIP_BRIDGE_COMMAND", "").strip()
            or sys.executable  # use sys.executable, not bare "python"
        )
        clawhip_args_raw = (
            os.environ.get("ARTIFACT_MCP_CLAWHIP_BRIDGE_ARGS", "").strip()
            or "-m clawhip_bridge_mcp"
        )
        # shlex.split handles quoted paths with spaces.
        clawhip_args = shlex.split(clawhip_args_raw)

    # -- Optional: REGISTRY_EVENTS_DIR (19.3 approval source / 19.4 event source). --
    # Reserved for the artifact tools' Tier-3 ``approval.granted`` lookup +
    # ``artifact.*`` event emission; threaded for parity with git-mcp / memory-mcp.
    # When unset, no event source is wired.
    registry_events_dir_raw = os.environ.get("REGISTRY_EVENTS_DIR", "").strip()
    registry_events_dir = Path(registry_events_dir_raw) if registry_events_dir_raw else None

    from artifact_mcp.server import build_server

    mcp = build_server(
        store_root=Path(store_path_raw),
        max_bytes=max_bytes,
        ttl_seconds=ttl_seconds,
        clock=SystemClock(),
        actor_kind=actor_kind,
        actor_id=actor_id,
        clawhip_bridge_command=clawhip_cmd,
        clawhip_bridge_args=clawhip_args,
        registry_events_dir=registry_events_dir,
    )
    mcp.run()


if __name__ == "__main__":
    main()
