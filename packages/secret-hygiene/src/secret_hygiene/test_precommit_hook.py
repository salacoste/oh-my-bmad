"""Unit tests for secret_hygiene.precommit_hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from .precommit_hook import main

# ---------------------------------------------------------------------------
# Clean file → exit 0
# ---------------------------------------------------------------------------


class TestCleanFile:
    def test_clean_file_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\nprint(x)\n", encoding="utf-8")
        exit_code = main([str(clean)])
        assert exit_code == 0

    def test_clean_file_no_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean)])
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_no_files_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 0


# ---------------------------------------------------------------------------
# Dirty file → exit 1 with correct stderr
# ---------------------------------------------------------------------------


class TestDirtyFile:
    def test_dirty_file_exits_one(self, tmp_path: Path) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        assert main([str(dirty)]) == 1

    def test_dirty_file_stderr_contains_pattern_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        main([str(dirty)])
        captured = capsys.readouterr()
        assert "[ANTHROPIC_API_KEY]" in captured.err

    def test_dirty_file_stderr_contains_file_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        main([str(dirty)])
        captured = capsys.readouterr()
        assert str(dirty) in captured.err

    def test_dirty_file_stderr_contains_line_col(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        main([str(dirty)])
        captured = capsys.readouterr()
        # Format is <file>:<line>:<col> [<pattern>] <excerpt>
        assert ":1:" in captured.err


# ---------------------------------------------------------------------------
# Allowlist file
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_allowlisted_file_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Create a dirty file
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        # Create allowlist that matches it
        allowlist = tmp_path / "allowlist.txt"
        allowlist.write_text("**/secret.txt\n", encoding="utf-8")

        exit_code = main([str(secret_file), "--allowlist-file", str(allowlist)])
        assert exit_code == 0

    def test_allowlist_with_comments_and_blanks(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "myfile.txt"
        secret_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        allowlist = tmp_path / "allowlist.txt"
        allowlist.write_text(
            "# This is a comment\n\n**/myfile.txt\n",
            encoding="utf-8",
        )
        assert main([str(secret_file), "--allowlist-file", str(allowlist)]) == 0

    def test_non_allowlisted_dirty_file_still_caught(self, tmp_path: Path) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        allowlist = tmp_path / "allowlist.txt"
        allowlist.write_text("**/other.txt\n", encoding="utf-8")

        assert main([str(dirty), "--allowlist-file", str(allowlist)]) == 1


# ---------------------------------------------------------------------------
# --verbose flag
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_verbose_clean_prints_ok_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--verbose"])
        captured = capsys.readouterr()
        assert "secret-hygiene OK" in captured.out

    def test_verbose_clean_mentions_files_scanned(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--verbose"])
        captured = capsys.readouterr()
        assert "1 files scanned" in captured.out

    def test_no_verbose_no_output_on_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean)])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_short_flag_v(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "-v"])
        captured = capsys.readouterr()
        assert "secret-hygiene OK" in captured.out
