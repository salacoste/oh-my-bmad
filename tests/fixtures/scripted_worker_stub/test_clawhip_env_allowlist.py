"""Security regression tests for the scripted-worker stub's clawhip-bridge env (Story 11.3.4).

S-1/S-2 stalled because ``StdioServerParameters(env=None)`` made the MCP SDK strip the
container env down to a POSIX allowlist, so clawhip-bridge never received its required
``CLAWHIP_BRIDGE_*`` config and exited at startup. The fix forwards an ALLOWLISTED env to
the subprocess. These tests pin the two halves of that contract:

1. the required ``CLAWHIP_BRIDGE_*`` (+ OS basics) ARE forwarded; and
2. secrets are NEVER forwarded — mirrors the Story 11.2.x ``_ENV_ALLOWLIST`` invariant
   that the clawhip-bridge subprocess must not see HMAC keys, cloud creds, or API keys
   (the exact leak the Story 11.3.3 P0 revert closed on the production adapters).
"""

from __future__ import annotations

from tests.fixtures.scripted_worker_stub.scripted_worker_stub import (
    _CLAWHIP_ENV_ALLOWLIST,
    _clawhip_env,
)

_SECRETS_THAT_MUST_NEVER_LEAK = [
    "WORKER_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "OPERATOR_HMAC_KEY",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
]


def test_clawhip_env_forwards_required_bridge_config(monkeypatch) -> None:
    """The CLAWHIP_BRIDGE_* runtime config + PATH must reach the subprocess."""
    monkeypatch.setenv("CLAWHIP_BRIDGE_ACTOR_KIND", "worker")
    monkeypatch.setenv("CLAWHIP_BRIDGE_ACTOR_ID", "scripted-worker-stub")
    monkeypatch.setenv("CLAWHIP_BRIDGE_LOG_DIR", "/var/lib/oh-my-bmad/registry/events")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _clawhip_env()

    assert env["CLAWHIP_BRIDGE_ACTOR_KIND"] == "worker"
    assert env["CLAWHIP_BRIDGE_ACTOR_ID"] == "scripted-worker-stub"
    assert env["CLAWHIP_BRIDGE_LOG_DIR"] == "/var/lib/oh-my-bmad/registry/events"
    assert env["PATH"] == "/usr/bin:/bin"


def test_clawhip_env_never_forwards_secrets(monkeypatch) -> None:
    """No HMAC key, cloud cred, or API key may reach the clawhip-bridge subprocess."""
    for name in _SECRETS_THAT_MUST_NEVER_LEAK:
        monkeypatch.setenv(name, "super-secret-value")
    monkeypatch.setenv("CLAWHIP_BRIDGE_ACTOR_KIND", "worker")

    env = _clawhip_env()

    for name in _SECRETS_THAT_MUST_NEVER_LEAK:
        assert name not in env, f"{name} leaked into clawhip-bridge env"
    # and none of the values either, defensively
    assert "super-secret-value" not in env.values()


def test_allowlist_contains_no_secret_shaped_names() -> None:
    """The allowlist itself must not name any secret-shaped variable."""
    forbidden_substrings = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    for name in _CLAWHIP_ENV_ALLOWLIST:
        assert not any(s in name.upper() for s in forbidden_substrings), name
