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

from git_mcp.adapters.clawhip_client import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _GIT_ALLOWLIST,
)
from orchestrator_adapter.adapters.mcp_clients import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _ORCH_ALLOWLIST,
)
from session_registry_mcp.adapters.clawhip_client import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _SESSION_ALLOWLIST,
)
from task_registry_mcp.adapters.clawhip_client import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _TASK_ALLOWLIST,
)
from worker_wrapper.adapters.mcp_clients import (  # noqa: IMP001 — tests/* can cross
    _ENV_ALLOWLIST as _WORKER_ALLOWLIST,
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
    # Story 15.5 (Epic-15 gate): git-mcp's clawhip_client._ENV_ALLOWLIST is the
    # SAME canon copy (it spawns clawhip-bridge for audit emission, never task/
    # session-registry), so it MUST stay byte-identical to the task/session
    # siblings. NOTE: this is the clawhip-bridge *spawn* allowlist and does NOT
    # carry GIT_MCP_* — those live in the worker-wrapper/orchestrator-adapter
    # MCPClientGroup allowlists (which spawn git-mcp itself), asserted below.
    assert _GIT_ALLOWLIST == _TASK_ALLOWLIST, (
        f"clawhip_client._ENV_ALLOWLIST drifted between git-mcp and task-registry:\n"
        f"  in git-mcp not in task-registry: {sorted(_GIT_ALLOWLIST - _TASK_ALLOWLIST)}\n"
        f"  in task-registry not in git-mcp: {sorted(_TASK_ALLOWLIST - _GIT_ALLOWLIST)}\n"
        "Update all clawhip_client adapters together — mirror discipline (Story 15.5)."
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

# Required by the three MCP servers' __main__.py (exit 2 if absent) PLUS the
# log/events/db-path vars the spawned subprocesses (and their own nested clients)
# need to find the shared event log + SQLite DB. CLAWHIP_BRIDGE_LOG_DIR has a
# default in clawhip-bridge/__main__.py but the default path requires the data
# volume to be mounted — silently broken without it. REGISTRY_EVENTS_DIR /
# REGISTRY_DB_PATH mirror the canon clawhip_client allowlist and are part of the
# established spine convention. Removing any of these from the allowlist breaks
# the fresh-boot path Story 11.3.6 closed.
_SPAWNER_REQUIRED_ENV_VARS = {
    "TASK_REGISTRY_DB_PATH",
    "TASK_REGISTRY_ACTOR_KIND",
    "TASK_REGISTRY_ACTOR_ID",
    "SESSION_REGISTRY_DB_PATH",
    "SESSION_REGISTRY_ACTOR_KIND",
    "SESSION_REGISTRY_ACTOR_ID",
    "CLAWHIP_BRIDGE_ACTOR_KIND",
    "CLAWHIP_BRIDGE_ACTOR_ID",
    "CLAWHIP_BRIDGE_LOG_DIR",
    # git-mcp REQUIRED (mcp-servers/git/.../__main__.py exits 2 without these) —
    # Story 15.5. Forwarded by BOTH spawner allowlists to keep them byte-identical
    # (the mirror test enforces identity); only worker-wrapper actually spawns
    # git-mcp (conditional on a non-blank WORKER_GIT_COMMAND), so the orchestrator
    # carries them as harmless extra vars.
    "GIT_MCP_ACTOR_KIND",
    "GIT_MCP_ACTOR_ID",
    "GIT_MCP_WORKTREE_ROOT",
    # github-mcp REQUIRED (mcp-servers/github/.../__main__.py exits 2 without
    # these) — Story 16.5 / G-SEC-2. GITHUB_MCP_SCOPED_TOKEN is the narrowly-
    # scoped credential (NOT the broad GITHUB_TOKEN, which stays in the forbidden
    # set below). Forwarded by BOTH spawner allowlists (byte-identical mirror);
    # only worker-wrapper actually spawns github-mcp (Story 16.6, conditional on
    # a non-blank WORKER_GITHUB_COMMAND).
    "GITHUB_MCP_ACTOR_KIND",
    "GITHUB_MCP_ACTOR_ID",
    "GITHUB_MCP_SCOPED_TOKEN",
    # verification-mcp REQUIRED (mcp-servers/verification/.../__main__.py exits 2
    # without these) — Story 17.5. All NON-secret (worktree path + actor identity;
    # verification needs no external credential). Forwarded by BOTH spawner
    # allowlists (byte-identical mirror); only worker-wrapper spawns verification-mcp
    # (Story 17.5, conditional on a non-blank WORKER_VERIFICATION_COMMAND).
    "VERIFICATION_MCP_WORKTREE_ROOT",
    "VERIFICATION_MCP_ACTOR_KIND",
    "VERIFICATION_MCP_ACTOR_ID",
    # memory-mcp REQUIRED (mcp-servers/memory/.../__main__.py exits 2 without these)
    # — Story 18.5. All NON-secret: MEMORY_MCP_STORE_PATH is memory-mcp's OWN SQLite
    # store path (never the registry DB — P3-I2) + actor identity; no credential.
    # Forwarded by BOTH spawner allowlists (byte-identical mirror); only
    # worker-wrapper spawns memory-mcp (conditional on a non-blank WORKER_MEMORY_COMMAND).
    "MEMORY_MCP_STORE_PATH",
    "MEMORY_MCP_ACTOR_KIND",
    "MEMORY_MCP_ACTOR_ID",
    # artifact-mcp REQUIRED (mcp-servers/artifact/.../__main__.py exits 2 without
    # these) — Story 19.5. All NON-secret: ARTIFACT_MCP_STORE_PATH is the artifact
    # content-store root (never the registry DB — P3-I2) + actor identity; no
    # credential. (The two ARTIFACT_MCP_RETENTION_* vars are OPTIONAL operator
    # policy — allowlisted but not required, so not listed here.) Forwarded by BOTH
    # spawner allowlists; only worker-wrapper spawns artifact-mcp (conditional on a
    # non-blank WORKER_ARTIFACT_COMMAND).
    "ARTIFACT_MCP_STORE_PATH",
    "ARTIFACT_MCP_ACTOR_KIND",
    "ARTIFACT_MCP_ACTOR_ID",
    # browser-mcp REQUIRED (mcp-servers/browser/.../__main__.py exits 2 without
    # these) — Story 20.6 / Phase 4. BROWSER_MCP_PLAYWRIGHT_IMAGE is the pinned
    # Docker image digest (FR87). BROWSER_MCP_ACTOR_KIND / ACTOR_ID are identity.
    # Forwarded by BOTH spawner allowlists (byte-identical mirror); only
    # worker-wrapper spawns browser-mcp (conditional on a non-blank
    # WORKER_BROWSER_COMMAND).
    "BROWSER_MCP_PLAYWRIGHT_IMAGE",
    "BROWSER_MCP_ACTOR_KIND",
    "BROWSER_MCP_ACTOR_ID",
    "REGISTRY_EVENTS_DIR",
    "REGISTRY_DB_PATH",
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


def test_spawner_allowlists_byte_identical_across_services() -> None:
    """orchestrator-adapter and worker-wrapper spawn the same 3 servers → same allowlist."""
    assert _ORCH_ALLOWLIST == _WORKER_ALLOWLIST, (
        "MCPClientGroup._ENV_ALLOWLIST drifted between orchestrator-adapter and worker-wrapper:\n"
        f"  in orchestrator not in worker: {sorted(_ORCH_ALLOWLIST - _WORKER_ALLOWLIST)}\n"
        f"  in worker not in orchestrator: {sorted(_WORKER_ALLOWLIST - _ORCH_ALLOWLIST)}\n"
        "Update both adapters together — mirror discipline (Story 11.3.6)."
    )


def test_spawner_allowlists_are_superset_of_canon() -> None:
    """Story 11.3.6 — the spawner allowlists carry the canon allowlist's contents.

    The two new spawner allowlists are by-definition a SUPERSET of the canon
    (which spawns only clawhip-bridge); they add `TASK_REGISTRY_*` and
    `SESSION_REGISTRY_*` because the spawners also start task-registry and
    session-registry as MCP children. Drift hazard: when canon gains a new
    CLAWHIP_BRIDGE_* var, both spawner copies must be expanded too. Without
    this assertion the byte-identical-between-spawners test stays green while
    each spawner silently strips the new var → the subprocess exits 2 on a
    fresh boot.
    """
    missing_from_orch = _TASK_ALLOWLIST - _ORCH_ALLOWLIST
    assert not missing_from_orch, (
        "orchestrator-adapter _ENV_ALLOWLIST is missing canon vars from "
        f"task-registry clawhip_client._ENV_ALLOWLIST: {sorted(missing_from_orch)}. "
        "Add them to BOTH spawners (mirror discipline)."
    )
    missing_from_worker = _TASK_ALLOWLIST - _WORKER_ALLOWLIST
    assert not missing_from_worker, (
        "worker-wrapper _ENV_ALLOWLIST is missing canon vars: "
        f"{sorted(missing_from_worker)}. Add to BOTH spawners (mirror discipline)."
    )


def test_spawner_allowlists_contain_all_required_server_vars() -> None:
    """Every REQUIRED var for the 3 spawned MCP servers must be forwarded."""
    missing = _SPAWNER_REQUIRED_ENV_VARS - _ORCH_ALLOWLIST
    assert not missing, (
        f"MCPClientGroup._ENV_ALLOWLIST omits MCP-server required vars: {sorted(missing)}. "
        "Without these the spawned subprocess exits 2 and the service never reaches /tmp/ready."
    )


def test_spawner_allowlists_exclude_secrets() -> None:
    """a0ca050 P0 guard: no API key / token / HMAC key may be in the forwarded allowlist."""
    for name, allowlist in (
        ("orchestrator-adapter", _ORCH_ALLOWLIST),
        ("worker-wrapper", _WORKER_ALLOWLIST),
    ):
        leaked = _FORBIDDEN_SECRET_ENV_VARS & allowlist
        assert not leaked, (
            f"{name} _ENV_ALLOWLIST leaks secret env vars to MCP subprocesses: "
            f"{sorted(leaked)}. This is the a0ca050 P0 — NEVER forward secrets."
        )


def test_github_scoped_token_present_broad_token_absent() -> None:
    """Story 16.5 / G-SEC-2: github-mcp authenticates with a SCOPED token, never the broad PAT.

    The G-SEC-2 follow-up (the broad ``GITHUB_TOKEN`` reaching an agent-spawned
    subprocess) is CLOSED by forwarding ONLY a narrowly-scoped credential
    (``GITHUB_MCP_SCOPED_TOKEN`` — a fine-grained PAT / App installation token
    scoped to the target repo, ADR-0010 §6 "scoped credentials use new,
    narrowly-named vars"). This pins both halves of that contract in both spawner
    allowlists:

      * the scoped var IS forwarded (github-mcp's ``__main__.py`` exits 2 without
        it), AND
      * the broad ``GITHUB_TOKEN`` is NEVER forwarded (it is in
        ``_FORBIDDEN_SECRET_ENV_VARS`` — this test makes the github-specific intent
        explicit alongside the generic exclude-secrets guard).
    """
    for name, allowlist in (
        ("orchestrator-adapter", _ORCH_ALLOWLIST),
        ("worker-wrapper", _WORKER_ALLOWLIST),
    ):
        assert "GITHUB_MCP_SCOPED_TOKEN" in allowlist, (
            f"{name} _ENV_ALLOWLIST omits GITHUB_MCP_SCOPED_TOKEN — github-mcp's "
            "__main__.py exits 2 without the scoped credential (Story 16.5)."
        )
        assert "GITHUB_TOKEN" not in allowlist, (
            f"{name} _ENV_ALLOWLIST forwards the BROAD GITHUB_TOKEN — G-SEC-2 "
            "regression. Only the repo-scoped GITHUB_MCP_SCOPED_TOKEN may be forwarded."
        )


def test_per_server_env_isolation_github_scoped_token() -> None:
    """Story 43.1 (G-SEC-2 defense-in-depth): GITHUB_MCP_SCOPED_TOKEN must
    only reach the github MCP server, not any other MCP child."""
    from worker_wrapper.adapters.mcp_clients import _SERVER_REQUIRED_ENV

    for server_name, server_vars in _SERVER_REQUIRED_ENV.items():
        if server_name == "github":
            assert "GITHUB_MCP_SCOPED_TOKEN" in server_vars, (
                "github MCP server must receive GITHUB_MCP_SCOPED_TOKEN"
            )
        else:
            assert "GITHUB_MCP_SCOPED_TOKEN" not in server_vars, (
                f"{server_name} env includes GITHUB_MCP_SCOPED_TOKEN — "
                f"defense-in-depth violation"
            )
