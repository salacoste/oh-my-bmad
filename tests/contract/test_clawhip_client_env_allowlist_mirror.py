"""Story 11.2.2 Pass-2 PP8 — cross-server env-allowlist mirror contract.

The ``_ENV_ALLOWLIST`` frozenset in
``mcp-servers/task-registry/.../adapters/clawhip_client.py`` MUST stay
byte-identical to the session-registry sibling at
``mcp-servers/session-registry/.../adapters/clawhip_client.py``. The
two adapters spawn clawhip-bridge subprocesses with the same env
surface; drift between them would mean one MCP server passes (e.g.) a
required env var while the other doesn't — invisible operational
divergence that would surface only when one MCP server bricks at
startup while the other works.

Lives in ``tests/contract/`` so it crosses both per-MCP-server import
graphs (matches the existing
``tests/contract/test_mcp_tool_schemas.py`` precedent).
"""

from __future__ import annotations

from session_registry_mcp.adapters.clawhip_client import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _SESSION_ALLOWLIST,
)
from task_registry_mcp.adapters.clawhip_client import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _TASK_ALLOWLIST,
)


def test_clawhip_client_env_allowlist_byte_identical_across_servers() -> None:
    """Mirror-discipline contract: the two ``_ENV_ALLOWLIST`` frozensets must match.

    Story 11.2.2 pass-1 PQ7 introduced the allowlist; pass-2 caught that
    it was missing variables clawhip-bridge REQUIRES at startup
    (``CLAWHIP_BRIDGE_ACTOR_KIND``, ``CLAWHIP_BRIDGE_ACTOR_ID``,
    ``CLAWHIP_BRIDGE_LOG_DIR``). Adding the same fix to both adapters
    works, but the diff has no automated check that future fixes stay
    synchronized. This test pins identity.

    Failure mode if this test breaks: one MCP server forwards an env var
    that the other strips, so a deployment running both servers exhibits
    asymmetric behavior — e.g. ``OMB_MCP_AUDIT_EMISSION_ENABLED=1`` lets
    task-registry start but session-registry bricks on missing
    ``CLAWHIP_BRIDGE_ACTOR_KIND``.
    """
    assert _TASK_ALLOWLIST == _SESSION_ALLOWLIST, (
        f"clawhip_client._ENV_ALLOWLIST drifted between MCP servers:\n"
        f"  in task-registry not in session: {sorted(_TASK_ALLOWLIST - _SESSION_ALLOWLIST)}\n"
        f"  in session not in task-registry: {sorted(_SESSION_ALLOWLIST - _TASK_ALLOWLIST)}\n"
        "Update both adapters together — mirror discipline."
    )


_CLAWHIP_REQUIRED_ENV_VARS = {"CLAWHIP_BRIDGE_ACTOR_KIND", "CLAWHIP_BRIDGE_ACTOR_ID"}


def test_clawhip_client_env_allowlist_contains_required_clawhip_vars() -> None:
    """clawhip-bridge ``__main__.py`` exits 2 without these vars — they MUST be forwarded."""
    missing = _CLAWHIP_REQUIRED_ENV_VARS - _TASK_ALLOWLIST
    assert not missing, (
        f"_ENV_ALLOWLIST omits clawhip-bridge required env vars: {sorted(missing)}. "
        "Without these, the spawned subprocess exits 2 and "
        "ClientSession.initialize() times out at 30s."
    )


def test_scripted_worker_stub_allowlist_contains_required_clawhip_vars() -> None:
    """Story 11.3.4: the S-1/S-2 scripted-worker stub ALSO spawns clawhip-bridge.

    Its ``_CLAWHIP_ENV_ALLOWLIST`` is an intentional, separate copy (the stub must
    not import from ``mcp-servers/*`` — the separability contract it proves), so it
    is NOT covered by the byte-identical mirror above. This test binds the third
    copy to the same required-vars invariant: without it, the next time
    clawhip-bridge gains a required env var the production adapters get fixed (via
    the mirror test) while the stub silently drifts and S-1/S-2 regress to the
    ``task.created`` stall this story was opened to fix — a Docker-only,
    30s-timeout failure with no fast signal. This contract test lives in
    ``tests/contract/`` precisely so it may cross into ``tests/fixtures/*`` without
    violating the stub's own no-spine-import rule.
    """
    from tests.fixtures.scripted_worker_stub.scripted_worker_stub import (  # noqa: IMP001 — tests/* can cross
        _CLAWHIP_ENV_ALLOWLIST as _STUB_ALLOWLIST,
    )

    missing = _CLAWHIP_REQUIRED_ENV_VARS - _STUB_ALLOWLIST
    assert not missing, (
        f"scripted-worker-stub _CLAWHIP_ENV_ALLOWLIST omits clawhip-bridge required "
        f"env vars: {sorted(missing)}. Without these the stub-spawned clawhip-bridge "
        "exits 2 → S-1/S-2 separability stalls at task.created."
    )


# ---------------------------------------------------------------------------
# Story 11.3.6 — orchestrator-adapter + worker-wrapper MCPClientGroup allowlists.
#
# Unlike the registry adapters (which spawn ONLY clawhip-bridge), these two
# services spawn ALL THREE MCP servers (task-registry, session-registry,
# clawhip-bridge), so their allowlist is a superset carrying every server's
# REQUIRED vars. These were env-less before 11.3.6 (the a0ca050 revert), which
# stripped the required vars → services never reached /tmp/ready on a fresh
# ROOT-compose boot. The fix forwards an ALLOWLIST (never os.environ.copy).
# ---------------------------------------------------------------------------

# Required by the three MCP servers' __main__.py (exit 2 if absent).
_SPAWNER_REQUIRED_ENV_VARS = {
    "TASK_REGISTRY_DB_PATH",
    "TASK_REGISTRY_ACTOR_KIND",
    "TASK_REGISTRY_ACTOR_ID",
    "SESSION_REGISTRY_DB_PATH",
    "SESSION_REGISTRY_ACTOR_KIND",
    "SESSION_REGISTRY_ACTOR_ID",
    "CLAWHIP_BRIDGE_ACTOR_KIND",
    "CLAWHIP_BRIDGE_ACTOR_ID",
}

# Secrets that MUST NEVER be forwarded to an MCP subprocess (the a0ca050 P0).
_FORBIDDEN_SECRET_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "OPERATOR_HMAC_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "OPENAI_API_KEY",
}


def _spawner_allowlists() -> tuple[frozenset[str], frozenset[str]]:
    from orchestrator_adapter.adapters.mcp_clients import (  # noqa: IMP001 — tests/* can cross
        _ENV_ALLOWLIST as _ORCH_ALLOWLIST,
    )
    from worker_wrapper.adapters.mcp_clients import (  # noqa: IMP001 — tests/* can cross
        _ENV_ALLOWLIST as _WORKER_ALLOWLIST,
    )

    return _ORCH_ALLOWLIST, _WORKER_ALLOWLIST


def test_spawner_allowlists_byte_identical_across_services() -> None:
    """orchestrator-adapter and worker-wrapper spawn the same 3 servers → same allowlist."""
    orch, worker = _spawner_allowlists()
    assert orch == worker, (
        "MCPClientGroup._ENV_ALLOWLIST drifted between orchestrator-adapter and worker-wrapper:\n"
        f"  in orchestrator not in worker: {sorted(orch - worker)}\n"
        f"  in worker not in orchestrator: {sorted(worker - orch)}\n"
        "Update both adapters together — mirror discipline (Story 11.3.6)."
    )


def test_spawner_allowlists_contain_all_required_server_vars() -> None:
    """Every REQUIRED var for the 3 spawned MCP servers must be forwarded."""
    orch, _ = _spawner_allowlists()
    missing = _SPAWNER_REQUIRED_ENV_VARS - orch
    assert not missing, (
        f"MCPClientGroup._ENV_ALLOWLIST omits MCP-server required vars: {sorted(missing)}. "
        "Without these the spawned subprocess exits 2 and the service never reaches /tmp/ready."
    )


def test_spawner_allowlists_exclude_secrets() -> None:
    """a0ca050 P0 guard: no API key / token / HMAC key may be in the forwarded allowlist."""
    orch, worker = _spawner_allowlists()
    for name, allowlist in (("orchestrator-adapter", orch), ("worker-wrapper", worker)):
        leaked = _FORBIDDEN_SECRET_ENV_VARS & allowlist
        assert not leaked, (
            f"{name} _ENV_ALLOWLIST leaks secret env vars to MCP subprocesses: "
            f"{sorted(leaked)}. This is the a0ca050 P0 — NEVER forward secrets."
        )
