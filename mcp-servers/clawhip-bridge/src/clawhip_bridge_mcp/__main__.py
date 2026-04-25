"""clawhip-bridge-mcp entrypoint — ``python -m clawhip_bridge_mcp`` (Story 2.8 AC-5).

Reads environment variables, validates required vars, builds the FastMCP server
via ``build_server()``, and runs it on stdio transport.

Environment variables
---------------------
CLAWHIP_BRIDGE_LOG_DIR
    Root directory for the JSONL event log.
    Default: ``/var/lib/oh-my-bmad/registry/events``.

CLAWHIP_BRIDGE_ACTOR_KIND  (REQUIRED)
    One of ``operator|orchestrator|worker|system|clawhip``.
    Missing or empty → exit code 2 with stderr message.

CLAWHIP_BRIDGE_ACTOR_ID  (REQUIRED)
    Non-empty string identifying the emitting actor instance.
    Missing or empty → exit code 2 with stderr message.

Exit codes
----------
0   Clean shutdown (SIGTERM / SIGINT / EOF on stdin).
2   Missing or invalid required environment variable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

_VALID_ACTOR_KINDS = {"operator", "orchestrator", "worker", "system", "clawhip"}
ActorKind = Literal["operator", "orchestrator", "worker", "system", "clawhip"]


def main() -> None:
    """Validate env vars, build server, run on stdio."""
    # -- Required: CLAWHIP_BRIDGE_ACTOR_KIND --
    actor_kind_raw = os.environ.get("CLAWHIP_BRIDGE_ACTOR_KIND", "").strip()
    if not actor_kind_raw:
        print(
            "clawhip-bridge: CLAWHIP_BRIDGE_ACTOR_KIND is required but not set or empty. "
            "Set it to one of: operator|orchestrator|worker|system|clawhip",
            file=sys.stderr,
        )
        sys.exit(2)
    # Typed dispatch on the validated string — lets mypy --strict see through
    # the narrowing without a `# type: ignore[assignment]` (AC-13).
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
            f"clawhip-bridge: CLAWHIP_BRIDGE_ACTOR_KIND={actor_kind_raw!r} is invalid. "
            f"Must be one of: {', '.join(sorted(_VALID_ACTOR_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Required: CLAWHIP_BRIDGE_ACTOR_ID --
    actor_id = os.environ.get("CLAWHIP_BRIDGE_ACTOR_ID", "").strip()
    if not actor_id:
        print(
            "clawhip-bridge: CLAWHIP_BRIDGE_ACTOR_ID is required but not set or empty.",
            file=sys.stderr,
        )
        sys.exit(2)

    # -- Optional: CLAWHIP_BRIDGE_LOG_DIR --
    log_dir_raw = os.environ.get("CLAWHIP_BRIDGE_LOG_DIR", "/var/lib/oh-my-bmad/registry/events")
    base_dir = Path(log_dir_raw)

    from events.clock import SystemClock

    from clawhip_bridge_mcp.server import build_server

    clock = SystemClock()
    mcp = build_server(
        base_dir=base_dir,
        clock=clock,
        actor_kind=actor_kind,
        actor_id=actor_id,
    )
    mcp.run()


if __name__ == "__main__":
    main()
