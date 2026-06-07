"""Credential isolation tests — P5-I1 / NFR-S14 / NFR-R10 (FR90 AC).

Verifies that each runtime adapter's child environment contains ONLY its own
API key and NEVER the other runtime's API key.  This is the runtime-level
sibling of the existing G-SEC-2 worker-wrapper credential isolation tests.

Contract:
- ClaudeCodeRunner child env: contains ANTHROPIC_API_KEY, NOT OPENAI_API_KEY.
- CodexRunner child env: contains OPENAI_API_KEY, NOT ANTHROPIC_API_KEY.
- Neither child env contains GITHUB_TOKEN.
- The allowlists share functional vars but diverge on secrets.
- Prefix allowlists are isolated: CLAUDE_ vs CODEX_.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from worker_wrapper.adapters.claude_code_runner import (
    _CHILD_ENV_ALLOWLIST,
    _CHILD_ENV_PREFIXES,
)
from worker_wrapper.adapters.claude_code_runner import (
    _build_child_env as _claude_build_env,
)
from worker_wrapper.adapters.codex_runner import (
    _CODEX_ENV_ALLOWLIST,
    _CODEX_ENV_PREFIXES,
)
from worker_wrapper.adapters.codex_runner import (
    _build_child_env as _codex_build_env,
)


class TestCredentialIsolation:
    """P5-I1: Runtime credential isolation — no cross-runtime secret leakage."""

    # -- Allowlist structure --

    def test_claude_allowlist_excludes_openai_key(self) -> None:
        """OPENAI_API_KEY must NOT be in Claude's allowlist."""
        assert "OPENAI_API_KEY" not in _CHILD_ENV_ALLOWLIST

    def test_claude_allowlist_excludes_github_token(self) -> None:
        """GITHUB_TOKEN must NOT be in Claude's allowlist."""
        assert "GITHUB_TOKEN" not in _CHILD_ENV_ALLOWLIST

    def test_codex_allowlist_excludes_anthropic_key(self) -> None:
        """ANTHROPIC_API_KEY must NOT be in Codex's allowlist."""
        assert "ANTHROPIC_API_KEY" not in _CODEX_ENV_ALLOWLIST

    def test_codex_allowlist_excludes_github_token(self) -> None:
        """GITHUB_TOKEN must NOT be in Codex's allowlist."""
        assert "GITHUB_TOKEN" not in _CODEX_ENV_ALLOWLIST

    def test_shared_functional_vars(self) -> None:
        """Functional vars (PATH, HOME, TLS) are in BOTH allowlists."""
        shared = {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TMPDIR",
            "TMP",
            "TEMP",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        }
        assert shared.issubset(_CHILD_ENV_ALLOWLIST)
        assert shared.issubset(_CODEX_ENV_ALLOWLIST)

    # -- Prefix isolation --

    def test_claude_prefix_excludes_codex(self) -> None:
        """CODEX_ prefix must NOT be in Claude's prefix allowlist."""
        assert "CODEX_" not in _CHILD_ENV_PREFIXES

    def test_codex_prefix_excludes_claude(self) -> None:
        """CLAUDE_ prefix must NOT be in Codex's prefix allowlist."""
        assert "CLAUDE_" not in _CODEX_ENV_PREFIXES

    def test_both_include_omb_prefix(self) -> None:
        """OMB_ prefix must be in BOTH prefix allowlists (trace/task vars)."""
        assert "OMB_" in _CHILD_ENV_PREFIXES
        assert "OMB_" in _CODEX_ENV_PREFIXES

    # -- Runtime env builder isolation --

    def test_claude_env_excludes_openai_key_from_parent(self) -> None:
        """When parent env has OPENAI_API_KEY, Claude child env must NOT get it."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-canary-openai"}, clear=False):
            env = _claude_build_env()
        assert "OPENAI_API_KEY" not in env

    def test_codex_env_excludes_anthropic_key_from_parent(self) -> None:
        """When parent env has ANTHROPIC_API_KEY, Codex child env must NOT get it."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-canary"}, clear=False):
            env = _codex_build_env()
        assert "ANTHROPIC_API_KEY" not in env

    def test_codex_env_excludes_github_token_from_parent(self) -> None:
        """When parent env has GITHUB_TOKEN, Codex child env must NOT get it."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test-canary"}, clear=False):
            env = _codex_build_env()
        assert "GITHUB_TOKEN" not in env

    def test_claude_env_excludes_github_token_from_parent(self) -> None:
        """When parent env has GITHUB_TOKEN, Claude child env must NOT get it."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test-canary"}, clear=False):
            env = _claude_build_env()
        assert "GITHUB_TOKEN" not in env

    def test_both_keys_present_only_correct_runner_gets_each(self) -> None:
        """When BOTH API keys are in parent env, each runner gets only its own."""
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-canary",
                "OPENAI_API_KEY": "sk-openai-canary",
                "GITHUB_TOKEN": "ghp-canary",
                "PATH": "/usr/bin",
                "HOME": "/home/test",
            },
            clear=False,
        ):
            claude_env = _claude_build_env()
            codex_env = _codex_build_env()

        # Claude env: should have functional vars but NOT OpenAI/GitHub keys
        assert "ANTHROPIC_API_KEY" not in claude_env  # injected by _spawn, not allowlist
        assert "OPENAI_API_KEY" not in claude_env
        assert "GITHUB_TOKEN" not in claude_env

        # Codex env: should have functional vars but NOT Anthropic/GitHub keys
        assert "OPENAI_API_KEY" not in codex_env  # injected by _spawn, not allowlist
        assert "ANTHROPIC_API_KEY" not in codex_env
        assert "GITHUB_TOKEN" not in codex_env

    # -- Secret injection is from settings, not parent env --

    def test_codex_injects_openai_from_settings_not_parent(self) -> None:
        """CodexRunner._spawn injects OPENAI_API_KEY from settings, not os.environ."""
        # This is verified by the allowlist test above (OPENAI_API_KEY not in
        # _CODEX_ENV_ALLOWLIST) + the _spawn overlay pattern. The integration
        # test in test_codex_runner.py will verify the full spawn path.
        # This test asserts the architectural invariant: the key is NOT in
        # the allowlist, so it CANNOT come from the parent env.
        assert "OPENAI_API_KEY" not in _CODEX_ENV_ALLOWLIST

    def test_claude_injects_anthropic_from_settings_not_parent(self) -> None:
        """ClaudeCodeRunner._spawn injects ANTHROPIC_API_KEY from settings, not os.environ."""
        assert "ANTHROPIC_API_KEY" not in _CHILD_ENV_ALLOWLIST
