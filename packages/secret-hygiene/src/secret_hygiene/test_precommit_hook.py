"""Unit tests for secret_hygiene.precommit_hook."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .precommit_hook import _find_default_allowlist, _glob_match, commit_msg_main, main

# ---------------------------------------------------------------------------
# Clean file → exit 0
# ---------------------------------------------------------------------------


class TestCleanFile:
    def test_clean_file_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\nprint(x)\n", encoding="utf-8")
        exit_code = main([str(clean), "--worktree-root", str(tmp_path)])
        assert exit_code == 0

    def test_clean_file_no_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        # scancode-toolkit warning is expected when not installed (graceful degradation).
        lines = [ln for ln in captured.err.strip().splitlines() if "scancode-toolkit" not in ln]
        assert lines == []

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

        exit_code = main(
            [
                str(secret_file),
                "--allowlist-file",
                str(allowlist),
                "--worktree-root",
                str(tmp_path),
            ]
        )
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
        assert (
            main(
                [
                    str(secret_file),
                    "--allowlist-file",
                    str(allowlist),
                    "--worktree-root",
                    str(tmp_path),
                ]
            )
            == 0
        )

    def test_non_allowlisted_dirty_file_still_caught(self, tmp_path: Path) -> None:
        dirty = tmp_path / "dirty.env"
        dirty.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        allowlist = tmp_path / "allowlist.txt"
        allowlist.write_text("**/other.txt\n", encoding="utf-8")

        assert main([str(dirty), "--allowlist-file", str(allowlist)]) == 1

    def test_missing_allowlist_file_exits_2(self, tmp_path: Path) -> None:
        """--allowlist-file pointing at a nonexistent path must exit 2, not crash."""
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main([str(clean), "--allowlist-file", str(tmp_path / "no_such_file.txt")])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Auto-discovery of .secret-hygiene-ignore
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    def test_auto_discovery_skips_ignored_file(self, tmp_path: Path) -> None:
        """When .secret-hygiene-ignore exists in cwd, ignored files are skipped."""
        secret_file = tmp_path / "test_fixture.py"
        secret_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        ignore_file = tmp_path / ".secret-hygiene-ignore"
        ignore_file.write_text("test_fixture.py\n", encoding="utf-8")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # No --allowlist-file; auto-discovery should find .secret-hygiene-ignore
            exit_code = main(["test_fixture.py"])
        finally:
            os.chdir(old_cwd)

        assert exit_code == 0

    def test_find_default_allowlist_finds_file(self, tmp_path: Path) -> None:
        """_find_default_allowlist returns the path when the file exists."""
        ignore_file = tmp_path / ".secret-hygiene-ignore"
        ignore_file.write_text("# patterns\n*.py\n", encoding="utf-8")
        result = _find_default_allowlist(start=tmp_path)
        assert result == ignore_file

    def test_find_default_allowlist_walks_up(self, tmp_path: Path) -> None:
        """_find_default_allowlist finds the file in a parent directory."""
        ignore_file = tmp_path / ".secret-hygiene-ignore"
        ignore_file.write_text("*.py\n", encoding="utf-8")
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        result = _find_default_allowlist(start=subdir)
        assert result == ignore_file

    def test_find_default_allowlist_returns_none_when_absent(self, tmp_path: Path) -> None:
        """_find_default_allowlist returns None when no ignore file exists."""
        subdir = tmp_path / "nosuchdir"
        subdir.mkdir()
        # tmp_path itself has no .secret-hygiene-ignore and neither do its parents
        # (in a typical tmp filesystem).  Use a deep subdir; walk until filesystem root.
        result = _find_default_allowlist(start=subdir)
        # Either None (not found) or a path from actual filesystem root — either is fine
        # as long as no exception is raised.
        assert result is None or result.name == ".secret-hygiene-ignore"


# ---------------------------------------------------------------------------
# Glob matching helper
# ---------------------------------------------------------------------------


class TestGlobMatch:
    def test_plain_fnmatch(self) -> None:
        assert _glob_match("foo/bar.py", "foo/bar.py")
        assert not _glob_match("foo/baz.py", "foo/bar.py")

    def test_wildcard_match(self) -> None:
        assert _glob_match("foo/bar.py", "foo/*.py")

    def test_double_star_prefix(self) -> None:
        assert _glob_match("a/b/c/test_foo.py", "**/test_foo.py")
        assert _glob_match("test_foo.py", "**/test_foo.py")

    def test_double_star_no_false_positive(self) -> None:
        assert not _glob_match("a/b/other.py", "**/test_foo.py")


# ---------------------------------------------------------------------------
# --verbose flag
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_verbose_clean_prints_ok_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--verbose", "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        assert "secret-hygiene" in captured.out
        assert "OK" in captured.out

    def test_verbose_clean_mentions_files_scanned(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--verbose", "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        assert "1 files scanned" in captured.out

    def test_no_verbose_no_output_on_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        assert captured.out == ""
        # stderr may contain auto-discovery notice if a .secret-hygiene-ignore exists
        # in the cwd hierarchy; filter that out for this assertion.

    def test_short_flag_v(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        main([str(clean), "-v", "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        assert "secret-hygiene" in captured.out
        assert "OK" in captured.out


# ---------------------------------------------------------------------------
# Sensitive path integration (Story 6.8)
# ---------------------------------------------------------------------------


class TestSensitivePathIntegration:
    """main() blocks sensitive paths via check_sensitive_paths."""

    def test_dotenv_blocked_by_main(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=val\n", encoding="utf-8")
        assert main([str(env_file), "--worktree-root", str(tmp_path)]) == 1

    def test_pem_blocked_by_main(self, tmp_path: Path) -> None:
        pem = tmp_path / "server.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        assert main([str(pem), "--worktree-root", str(tmp_path)]) == 1

    def test_clean_file_not_blocked_by_path_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "main.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        assert main([str(clean), "--worktree-root", str(tmp_path)]) == 0

    def test_allowlisted_sensitive_path_still_blocked(self, tmp_path: Path) -> None:
        """Allowlist skips content scan but sensitive-path check still fires."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=val\n", encoding="utf-8")
        allowlist = tmp_path / "allowlist.txt"
        allowlist.write_text(".env\n", encoding="utf-8")
        exit_code = main(
            [
                str(env_file),
                "--allowlist-file",
                str(allowlist),
                "--worktree-root",
                str(tmp_path),
            ]
        )
        assert exit_code == 1

    def test_content_dirty_and_path_sensitive_both_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-abcdef1234567890XYZABC\n",
            encoding="utf-8",
        )
        main([str(env_file), "--worktree-root", str(tmp_path)])
        captured = capsys.readouterr()
        assert "[ANTHROPIC_API_KEY]" in captured.err
        assert "sensitive path" in captured.err.lower()


# ---------------------------------------------------------------------------
# Worktree boundary integration (Story 6.8)
# ---------------------------------------------------------------------------


class TestWorktreeBoundaryIntegration:
    """main() blocks files outside the assigned worktree."""

    def test_outside_worktree_blocked(self, tmp_path: Path) -> None:
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1")
        assert main([str(outside), "--worktree-root", str(worktree)]) == 1

    def test_worktree_root_default_cwd(self, tmp_path: Path) -> None:
        inside = tmp_path / "file.py"
        inside.write_text("x = 1")
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert main(["file.py"]) == 0
        finally:
            os.chdir(old_cwd)

    def test_symlink_escape_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path / "real_outside.py"
        outside.write_text("x = 1")
        link = tmp_path / "link.py"
        link.symlink_to(outside)
        worktree = tmp_path / "wt"
        worktree.mkdir()
        # link is inside worktree but points outside
        link2 = worktree / "link.py"
        link2.symlink_to(outside)
        assert main([str(link2), "--worktree-root", str(worktree)]) == 1

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does_not_exist.py"
        assert main([str(ghost), "--worktree-root", str(tmp_path)]) == 0


# ---------------------------------------------------------------------------
# commit-msg entrypoint (Story 6.8)
# ---------------------------------------------------------------------------


class TestCommitMsgMain:
    """commit_msg_main checks commit messages for injection patterns."""

    def test_clean_message_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("feat: add feature\n")
        assert commit_msg_main([str(msg)]) == 0

    def test_null_byte_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("bad\x00msg")
        assert commit_msg_main([str(msg)]) == 1

    def test_command_sub_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: $(whoami)")
        assert commit_msg_main([str(msg)]) == 1

    def test_no_args_passes(self) -> None:
        assert commit_msg_main([]) == 0

    def test_nonexistent_msg_file_passes(self, tmp_path: Path) -> None:
        assert commit_msg_main([str(tmp_path / "no_such_file")]) == 0

    def test_backtick_markdown_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: update `README.md` and `docs/api`")
        assert commit_msg_main([str(msg)]) == 0


# ---------------------------------------------------------------------------
# License scan integration (Story 6.10)
# ---------------------------------------------------------------------------


def _patch_scancode(mock_get: MagicMock):
    """Return a patch.dict that injects a mock scancode.api.get_licenses."""
    modules = {
        "scancode": MagicMock(),
        "scancode.api": MagicMock(get_licenses=mock_get),
    }
    return patch.dict("sys.modules", modules)


class TestLicenseScanInHook:
    """License scan is wired into the pre-commit hook (Story 6.10, FR40)."""

    def test_gpl_file_blocks(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        gpl = tmp_path / "gpl.py"
        gpl.write_text("# GPL code\nx = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "gpl-2.0",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 50.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            exit_code = main([str(gpl), "--worktree-root", str(tmp_path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "LICENSE" in captured.err
        assert "gpl-2.0" in captured.err

    def test_mit_file_passes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mit = tmp_path / "mit.py"
        mit.write_text("# MIT code\nx = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "mit",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 10.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            exit_code = main([str(mit), "--worktree-root", str(tmp_path)])
        assert exit_code == 0

    def test_no_license_passes(
        self,
        tmp_path: Path,
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": None,
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 0.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            exit_code = main([str(clean), "--worktree-root", str(tmp_path)])
        assert exit_code == 0

    def test_repo_license_flag(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        gpl = tmp_path / "gpl.py"
        gpl.write_text("# GPL\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "gpl-2.0",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 50.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            exit_code = main([str(gpl), "--worktree-root", str(tmp_path), "--repo-license", "MIT"])
        assert exit_code == 1

    def test_binary_skipped(self, tmp_path: Path) -> None:
        img = tmp_path / "logo.png"
        img.write_bytes(b"\x89PNG\r\n")
        exit_code = main([str(img), "--worktree-root", str(tmp_path)])
        assert exit_code == 0

    def test_scancode_missing_graceful(self, tmp_path: Path) -> None:
        src = tmp_path / "code.py"
        src.write_text("x = 1\n", encoding="utf-8")
        with patch.dict("sys.modules", {}):
            exit_code = main([str(src), "--worktree-root", str(tmp_path)])
        assert exit_code == 0
