"""Origin control integration tests for browser-mcp (FR85 / Ship-blocker #8).

Verifies that the ``allowed_hosts`` allowlist blocks navigation to
non-allowed origins while permitting allowed ones.
"""

from __future__ import annotations

from browser_mcp.handlers.tools import _is_host_allowed


class TestOriginControl:
    """FR85: host allowlist enforcement."""

    def test_none_allows_all(self) -> None:
        assert _is_host_allowed("https://evil.example.com", None) is True

    def test_exact_match(self) -> None:
        assert _is_host_allowed("https://example.com", ["example.com"]) is True

    def test_subdomain_does_not_match_parent(self) -> None:
        assert _is_host_allowed("https://sub.example.com", ["example.com"]) is False

    def test_blocked_host(self) -> None:
        assert _is_host_allowed("https://evil.com", ["example.com"]) is False

    def test_case_insensitive(self) -> None:
        assert _is_host_allowed("https://EXAMPLE.COM", ["example.com"]) is True

    def test_trailing_dot_normalised(self) -> None:
        assert _is_host_allowed("https://example.com.", ["example.com"]) is True

    def test_unparseable_url_blocked(self) -> None:
        assert _is_host_allowed("not-a-url", ["example.com"]) is False

    def test_empty_allowlist_blocks(self) -> None:
        """Empty list ≠ None: empty list means nothing is allowed."""
        assert _is_host_allowed("https://example.com", []) is False

    def test_multiple_hosts(self) -> None:
        allowed = ["example.com", "trusted.org"]
        assert _is_host_allowed("https://example.com", allowed) is True
        assert _is_host_allowed("https://trusted.org", allowed) is True
        assert _is_host_allowed("https://evil.com", allowed) is False

    def test_port_not_compared(self) -> None:
        """Port is stripped before comparison."""
        assert _is_host_allowed("https://example.com:8443", ["example.com"]) is True
