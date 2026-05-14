"""Unit tests for secret_hygiene.path_checks — sensitive path, worktree boundary,
and commit-message injection checks (Story 6.8, FR39)."""

from __future__ import annotations

import os
from pathlib import Path

from .path_checks import check_commit_message, check_sensitive_paths, check_worktree_boundary

# ---------------------------------------------------------------------------
# Sensitive path checker
# ---------------------------------------------------------------------------


class TestCheckSensitivePaths:
    """check_sensitive_paths returns Violations for blocked file patterns."""

    def test_dotenv_blocked(self) -> None:
        violations = check_sensitive_paths([".env"])
        assert len(violations) == 1
        assert "Refusing to commit sensitive path" in violations[0].message
        assert violations[0].file_path == ".env"

    def test_dotenv_with_suffix_blocked(self) -> None:
        violations = check_sensitive_paths([".env.production", ".env.local"])
        assert len(violations) == 2

    def test_secrets_dir_blocked(self) -> None:
        violations = check_sensitive_paths(["secrets/db_password.txt"])
        assert len(violations) == 1
        assert violations[0].file_path == "secrets/db_password.txt"

    def test_pem_file_blocked(self) -> None:
        violations = check_sensitive_paths(["certs/server.pem"])
        assert len(violations) == 1

    def test_key_file_blocked(self) -> None:
        violations = check_sensitive_paths(["id_rsa.key"])
        assert len(violations) == 1

    def test_credentials_file_blocked(self) -> None:
        violations = check_sensitive_paths(["app.credentials.json"])
        assert len(violations) == 1

    def test_clean_files_pass(self) -> None:
        violations = check_sensitive_paths([
            "src/main.py",
            "README.md",
            "config.yaml",
        ])
        assert violations == []

    def test_multiple_violations(self) -> None:
        violations = check_sensitive_paths([".env", "secrets/key.pem", "clean.py"])
        assert len(violations) == 2

    def test_empty_list_passes(self) -> None:
        assert check_sensitive_paths([]) == []

    # --- Negative cases (near-miss filenames must NOT be flagged) ---

    def test_env_without_dot_passes(self) -> None:
        assert check_sensitive_paths(["env"]) == []

    def test_envrc_not_matched(self) -> None:
        assert check_sensitive_paths([".envrc"]) == []

    def test_secrets_singular_not_matched(self) -> None:
        assert check_sensitive_paths(["secret/password.txt"]) == []

    def test_secrets_substring_not_matched(self) -> None:
        assert check_sensitive_paths(["mysecrets/key.txt"]) == []

    def test_pem_backup_passes(self) -> None:
        assert check_sensitive_paths(["cert.pem.bak"]) == []

    def test_key_in_word_not_matched(self) -> None:
        assert check_sensitive_paths(["monkey.py"]) == []

    def test_dotenv_tilde_not_matched(self) -> None:
        assert check_sensitive_paths([".env~"]) == []

    def test_nested_env_blocked(self) -> None:
        violations = check_sensitive_paths(["subdir/.env"])
        assert len(violations) == 1

    def test_dotenv_subpath_blocked(self) -> None:
        violations = check_sensitive_paths(["config/.env/overrides"])
        assert len(violations) == 1

    def test_dotenv_backup_blocked(self) -> None:
        violations = check_sensitive_paths([".env.bak"])
        assert len(violations) == 1

    def test_unicode_path_catches_dotenv(self) -> None:
        violations = check_sensitive_paths(["project/éèê/.env"])
        assert len(violations) == 1

    def test_long_path_handled(self) -> None:
        deep = "/".join(["a"] * 200) + "/.env"
        violations = check_sensitive_paths([deep])
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# Worktree boundary checker
# ---------------------------------------------------------------------------


class TestCheckWorktreeBoundary:
    """check_worktree_boundary returns Violations for files outside worktree."""

    def test_file_inside_worktree_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "main.py"
        f.parent.mkdir(parents=True)
        f.write_text("x = 1")
        assert check_worktree_boundary([str(f)], tmp_path) == []

    def test_file_outside_worktree_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.py"
        outside.write_text("x = 1")
        violations = check_worktree_boundary([str(outside)], tmp_path)
        assert len(violations) == 1
        assert "outside assigned worktree" in violations[0].message

    def test_symlink_escaping_worktree_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "escaped.txt"
        outside.write_text("secret")
        link = tmp_path / "link.txt"
        link.symlink_to(outside)
        violations = check_worktree_boundary([str(link)], tmp_path)
        assert len(violations) == 1

    def test_symlink_inside_worktree_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("ok")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert check_worktree_boundary([str(link)], tmp_path) == []

    def test_empty_list_passes(self, tmp_path: Path) -> None:
        assert check_worktree_boundary([], tmp_path) == []

    def test_relative_path_inside_worktree(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "file.py"
        f.parent.mkdir(parents=True)
        f.write_text("x = 1")
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            violations = check_worktree_boundary(["src/file.py"], tmp_path)
        finally:
            os.chdir(old_cwd)
        assert violations == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does_not_exist.py"
        assert check_worktree_boundary([str(ghost)], tmp_path) == []

    def test_broken_symlink_no_crash(self, tmp_path: Path) -> None:
        link = tmp_path / "broken_link.py"
        link.symlink_to(tmp_path / "no_such_target.py")
        violations = check_worktree_boundary([str(link)], tmp_path)
        assert isinstance(violations, list)

    def test_symlink_chain_escaping(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "chain_target.txt"
        outside.write_text("escaped")
        link2 = tmp_path / "inner_link.txt"
        link2.symlink_to(outside)
        link1 = tmp_path / "outer_link.txt"
        link1.symlink_to(link2)
        violations = check_worktree_boundary([str(link1)], tmp_path)
        assert len(violations) == 1

    def test_multiple_files_mixed(self, tmp_path: Path) -> None:
        inside = tmp_path / "inside.py"
        inside.write_text("ok")
        outside = tmp_path.parent / "outside.py"
        outside.write_text("bad")
        violations = check_worktree_boundary(
            [str(inside), str(outside), str(tmp_path / "ghost.py")],
            tmp_path,
        )
        assert len(violations) == 1
        assert violations[0].file_path == str(outside)


# ---------------------------------------------------------------------------
# Commit-message injection checker
# ---------------------------------------------------------------------------


class TestCheckCommitMessage:
    """check_commit_message returns Violations for injection patterns."""

    def test_clean_message_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("feat: add new feature\n")
        assert check_commit_message(msg) == []

    def test_null_byte_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("feat:\x00inject")
        violations = check_commit_message(msg)
        assert len(violations) == 1
        assert "null byte" in violations[0].message.lower()

    def test_backtick_markdown_not_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: update `README.md` and `docs/api`")
        assert check_commit_message(msg) == []

    def test_dollar_command_substitution_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: $(cat /etc/passwd)")
        violations = check_commit_message(msg)
        assert len(violations) == 1
        assert "command substitution" in violations[0].message.lower()

    def test_multiple_null_bytes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("a\x00b\x00c")
        violations = check_commit_message(msg)
        assert len(violations) == 2

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        violations = check_commit_message(tmp_path / "nonexistent")
        assert violations == []

    def test_multiline_clean_message_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("feat: add feature\n\nDetailed description here.\n")
        assert check_commit_message(msg) == []

    def test_empty_file_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("")
        assert check_commit_message(msg) == []

    def test_nested_dollar_substitution(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: $(echo $(whoami))")
        violations = check_commit_message(msg)
        assert len(violations) >= 1

    def test_unicode_message_passes(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("feat: add i18n support for café\n")
        assert check_commit_message(msg) == []

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("clean")
        msg.chmod(0o000)
        try:
            violations = check_commit_message(msg)
            assert violations == []
        finally:
            msg.chmod(0o644)

    def test_triple_backtick_not_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("docs: example\n```\ncode\n```\n")
        assert check_commit_message(msg) == []

    def test_empty_dollar_sub_not_blocked(self, tmp_path: Path) -> None:
        msg = tmp_path / "msg"
        msg.write_text("fix: $()")
        # $() with empty content uses * quantifier — but we switched to +,
        # so $() (empty parens) should NOT match.
        assert check_commit_message(msg) == []
