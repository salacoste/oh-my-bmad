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

    from task_registry_mcp.app.main import build_server

    mcp = build_server(
        db_path=db_path,
        actor_kind=actor_kind,
        actor_id=actor_id,
    )
    mcp.run()


if __name__ == "__main__":
    main()
