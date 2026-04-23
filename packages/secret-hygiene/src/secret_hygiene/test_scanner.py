"""Unit tests for secret_hygiene.scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from .scanner import SECRET_PATTERNS, SecretMatch, scan_file, scan_text

# ---------------------------------------------------------------------------
# Positive fixtures — each pattern must fire on a realistic input
# ---------------------------------------------------------------------------


class TestPositiveMatches:
    def test_anthropic_api_key(self) -> None:
        text = "ANTHROPIC_API_KEY=sk-ant-abcdefghij1234567890XYZ"
        matches = scan_text(text)
        names = [m.pattern_name for m in matches]
        assert "ANTHROPIC_API_KEY" in names

    def test_telegram_bot_token(self) -> None:
        text = "TELEGRAM_BOT_TOKEN=123456789:AAH0xYmockrealisticshape_123456789012345"
        matches = scan_text(text)
        names = [m.pattern_name for m in matches]
        assert "TELEGRAM_BOT_TOKEN" in names

    def test_github_token_classic(self) -> None:
        text = "GITHUB_TOKEN=ghp_abcdefghij1234567890abcdefghij"
        matches = scan_text(text)
        names = [m.pattern_name for m in matches]
        assert "GITHUB_TOKEN_CLASSIC" in names

    def test_github_token_fine(self) -> None:
        text = "GITHUB_TOKEN=github_pat_11AAAABCD_0123456789abcdefghij0123456789ab"
        matches = scan_text(text)
        names = [m.pattern_name for m in matches]
        assert "GITHUB_TOKEN_FINE" in names

    def test_generic_aws_access_key(self) -> None:
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        matches = scan_text(text)
        names = [m.pattern_name for m in matches]
        assert "GENERIC_AWS_ACCESS_KEY" in names


# ---------------------------------------------------------------------------
# Negative fixtures — realistic non-secrets must NOT fire
# ---------------------------------------------------------------------------


class TestNegativeMatches:
    def test_empty_anthropic_key_env_example_style(self) -> None:
        # The .env.example pattern: key present but value is empty.
        text = "ANTHROPIC_API_KEY="
        assert scan_text(text) == []

    def test_empty_telegram_token_env_example_style(self) -> None:
        text = "TELEGRAM_BOT_TOKEN="
        assert scan_text(text) == []

    def test_empty_github_token_env_example_style(self) -> None:
        text = "GITHUB_TOKEN="
        assert scan_text(text) == []

    def test_uuid_does_not_match(self) -> None:
        text = "request_id=d8e8fca2-dc0f-4b8b-9040-3e4adf8c3d7c"
        assert scan_text(text) == []

    def test_random_40char_hex_does_not_match(self) -> None:
        # 40-char lowercase hex string — git SHA shape, should not match.
        text = "sha=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        assert scan_text(text) == []

    def test_short_aws_like_string_does_not_match(self) -> None:
        # AKIA prefix but only 15 uppercase chars after (needs exactly 16).
        text = "AKIAIOSFODNN7EXAMPL"
        assert scan_text(text) == []


# ---------------------------------------------------------------------------
# Line + column computation
# ---------------------------------------------------------------------------


class TestLineColumnComputation:
    def test_secret_on_line_3_column_20(self) -> None:
        # Build a text where the secret starts at line 3, column 20.
        # Lines 1 and 2 are 19 chars each (18 x's + newline).
        line1 = "x" * 18 + "\n"  # 19 chars, ends at index 18 with \n
        line2 = "x" * 18 + "\n"  # another 19 chars
        # Line 3: 19 spaces then the secret starting at column 20.
        prefix_on_line3 = "x" * 19  # 19 chars → the secret starts at column 20
        secret = "sk-ant-abcdefghij1234567890XYZ"
        text = line1 + line2 + prefix_on_line3 + secret
        matches = scan_text(text)
        assert len(matches) >= 1
        m = next(m for m in matches if m.pattern_name == "ANTHROPIC_API_KEY")
        assert m.line == 3
        assert m.column == 20

    def test_secret_on_first_line(self) -> None:
        text = "sk-ant-abcdefghij1234567890XYZ"
        matches = scan_text(text)
        assert len(matches) == 1
        assert matches[0].line == 1
        assert matches[0].column == 1


# ---------------------------------------------------------------------------
# scan_file edge cases
# ---------------------------------------------------------------------------


class TestScanFile:
    def test_nonexistent_file_returns_empty(self) -> None:
        result = scan_file(Path("/nonexistent/path/to/file.txt"))
        assert result == []

    def test_binary_file_returns_empty(self, tmp_path: Path) -> None:
        binary_file = tmp_path / "binary.bin"
        # Write bytes that are not valid UTF-8.
        binary_file.write_bytes(b"\xff\xfe\x00\x01\x02\x03" * 100)
        result = scan_file(binary_file)
        assert result == []

    def test_clean_file_returns_empty(self, tmp_path: Path) -> None:
        clean_file = tmp_path / "clean.py"
        clean_file.write_text("x = 1\nprint(x)\n", encoding="utf-8")
        assert scan_file(clean_file) == []

    def test_dirty_file_returns_matches(self, tmp_path: Path) -> None:
        dirty_file = tmp_path / "dirty.env"
        dirty_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdefghij1234567890XYZ\n",
            encoding="utf-8",
        )
        matches = scan_file(dirty_file)
        assert len(matches) >= 1
        assert matches[0].pattern_name == "ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# SecretMatch properties
# ---------------------------------------------------------------------------


class TestSecretMatch:
    def test_excerpt_never_contains_raw_secret(self) -> None:
        text = "sk-ant-abcdefghij1234567890XYZ"
        matches = scan_text(text)
        assert matches
        for m in matches:
            # excerpt must NOT contain the actual secret value
            assert text not in m.excerpt
            # excerpt is the safe <PATTERN_NAME> form
            assert m.excerpt == f"<{m.pattern_name}>"

    def test_frozen_dataclass(self) -> None:
        m = SecretMatch(
            pattern_name="ANTHROPIC_API_KEY",
            start=0,
            end=5,
            line=1,
            column=1,
            excerpt="<ANTHROPIC_API_KEY>",
        )
        with pytest.raises((AttributeError, TypeError)):
            m.line = 2  # type: ignore[misc]

    def test_secret_patterns_has_five_entries(self) -> None:
        assert len(SECRET_PATTERNS) == 5
        expected_keys = {
            "ANTHROPIC_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "GITHUB_TOKEN_CLASSIC",
            "GITHUB_TOKEN_FINE",
            "GENERIC_AWS_ACCESS_KEY",
        }
        assert set(SECRET_PATTERNS.keys()) == expected_keys
