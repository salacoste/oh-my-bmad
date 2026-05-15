"""Co-located unit tests for atomic_write_bytes / atomic_write_text (Story 2.12)
and apply_file_edit / apply_file_write (Story 5.6).

Test classes (per AC-7):
  - TestAtomicWriteBytes        — happy path + cleanup invariants (~7).
  - TestAtomicWriteText         — encoding behavior (~2).
  - TestFsyncSemantics          — fsync_data / fsync_dir gating (~3).
  - TestCrossFilesystemDetection — EXDEV re-raise (~1).
  - TestErrorPathsAndEdgeCases  — defensive paths added in code-review fixes.
  - TestValidateEdit            — edit parameter validation (Story 5.6).
  - TestApplyFileEdit           — atomic file-edit with secret scanning (Story 5.6).
  - TestApplyFileWrite          — atomic file-write with secret scanning (Story 5.6).
  - TestSchemaRegistry          — file.edited payload registration (Story 5.6).

Note on monkeypatch scope (Story 2.12 code-review M5/M6): pytest-internal
machinery (capture, plugins, leak detectors) can invoke ``os.fsync`` /
``os.replace`` between test setup and the function-under-test.  The
recorder closures in this module filter calls by *known fds* / paths so
unrelated calls don't pollute the assertion counts.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import stat
from pathlib import Path

import pytest

from worker_wrapper.domain.atomic_edit import (
    apply_file_edit,
    apply_file_write,
    atomic_write_bytes,
    atomic_write_text,
    validate_edit,
)

_AE_LOGGER_NAME = "worker_wrapper.domain.atomic_edit"


@pytest.fixture
def _ensure_ae_logger_propagates() -> object:
    """Ensure the atomic_edit logger propagates to caplog handlers.

    Other tests in the full suite may install handlers that flip
    ``propagate=False`` on this logger.  caplog attaches a handler at
    the *root* level, so we need propagation on for caplog to see the
    warnings.  Restore on teardown.
    """
    logger = logging.getLogger(_AE_LOGGER_NAME)
    saved_propagate = logger.propagate
    saved_disabled = logger.disabled
    logger.propagate = True
    logger.disabled = False
    try:
        yield
    finally:
        logger.propagate = saved_propagate
        logger.disabled = saved_disabled


_TMP_NAME_RE = re.compile(r"\.tmp\.\d+\.[0-9a-f]{16}$")


def _has_tmp_leftover(tmp_path: Path) -> bool:
    """Return True when any tmpfile matching the new naming pattern survives."""
    return any(_TMP_NAME_RE.search(p.name) for p in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# TestAtomicWriteBytes
# ---------------------------------------------------------------------------


class TestAtomicWriteBytes:
    def test_atomic_write_bytes_creates_target_with_correct_content(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        payload = b"hello world\n"
        atomic_write_bytes(target, payload)
        assert target.read_bytes() == payload

    def test_atomic_write_bytes_overwrites_existing_target(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        target.write_bytes(b"original-content")
        new = b"new content here"
        atomic_write_bytes(target, new)
        assert target.read_bytes() == new
        # No leftover tmpfiles in the parent dir (strict pattern match).
        assert not _has_tmp_leftover(tmp_path)

    def test_atomic_write_bytes_preserves_original_on_disk_full(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        target.write_bytes(b"original")

        from worker_wrapper.domain import atomic_edit as ae_mod

        def _enospc(fd: int, data: bytes) -> None:  # noqa: ARG001 — sig matches helper
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(ae_mod, "_chunked_write", _enospc)

        with pytest.raises(OSError, match="No space left on device"):
            atomic_write_bytes(target, b"new content")

        # Original survives + tmpfile cleaned up.
        assert target.read_bytes() == b"original"
        assert not _has_tmp_leftover(tmp_path)

    def test_atomic_write_bytes_cleans_up_tmpfile_on_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"

        from worker_wrapper.domain import atomic_edit as ae_mod

        def _boom(fd: int, data: bytes) -> None:  # noqa: ARG001
            raise OSError(errno.EIO, "I/O error")

        monkeypatch.setattr(ae_mod, "_chunked_write", _boom)

        with pytest.raises(OSError, match="I/O error"):
            atomic_write_bytes(target, b"never lands")

        assert not target.exists()
        assert not _has_tmp_leftover(tmp_path)

    def test_atomic_write_bytes_cleans_up_tmpfile_on_fsync_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        real_fsync = os.fsync
        # Capture the file fd opened by atomic_write_bytes via a sentinel
        # that records the first novel fd seen — that is the tmpfile fd.
        observed_fds: list[int] = []

        def _fsync_then_boom(fd: int) -> None:
            # Only react to the FIRST fsync we see (the data fsync on the
            # tmpfile fd). Pytest-internal fsyncs use different fds and are
            # passed through to the real implementation.
            if not observed_fds:
                observed_fds.append(fd)
                raise OSError(errno.EIO, "fsync I/O error")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _fsync_then_boom)

        with pytest.raises(OSError, match="fsync I/O error"):
            atomic_write_bytes(target, b"data")

        assert not target.exists()
        assert not _has_tmp_leftover(tmp_path)

    def test_atomic_write_bytes_raises_on_no_parent_directory(self) -> None:
        # Path("/") has empty basename AND parent == self; either guard
        # rejects it.  The empty-name check fires first; both messages
        # convey that the path is not writable as a target.
        with pytest.raises(ValueError, match="empty basename|parent directory"):
            atomic_write_bytes(Path("/"), b"never")

    def test_atomic_write_bytes_raises_on_empty_path(self) -> None:
        # Path("").parent == Path(".") — slips past the parent==self guard.
        # Explicit empty-name check rejects it before any I/O.
        with pytest.raises(ValueError, match="empty basename"):
            atomic_write_bytes(Path(""), b"never")

    @pytest.mark.slow
    def test_atomic_write_bytes_handles_large_payload(self, tmp_path: Path) -> None:
        target = tmp_path / "big.bin"
        payload = b"x" * (10 * 1024 * 1024)  # 10 MB
        atomic_write_bytes(target, payload)
        assert target.read_bytes() == payload

    def test_atomic_write_bytes_accepts_str_target(self, tmp_path: Path) -> None:
        # Story 2.12 M12: target accepts ``Path | str``.
        target = tmp_path / "out.bin"
        atomic_write_bytes(str(target), b"from-str")
        assert target.read_bytes() == b"from-str"

    def test_atomic_write_bytes_preserves_existing_mode(self, tmp_path: Path) -> None:
        # Story 2.12 M10: mode-preservation across atomic replace.
        target = tmp_path / "perm-test.bin"
        target.write_bytes(b"original")
        os.chmod(target, 0o644)
        original_mode = stat.S_IMODE(os.stat(target).st_mode)
        assert original_mode == 0o644

        atomic_write_bytes(target, b"replaced")

        new_mode = stat.S_IMODE(os.stat(target).st_mode)
        assert new_mode == original_mode, (
            f"mode-preservation failed: pre={original_mode:o} post={new_mode:o}"
        )
        assert target.read_bytes() == b"replaced"

    def test_atomic_write_bytes_defaults_to_0o600_when_target_missing(self, tmp_path: Path) -> None:
        # Mode-preservation only applies to existing targets — new files
        # land at the tmpfile's O_EXCL creation mode (0o600).
        target = tmp_path / "fresh.bin"
        atomic_write_bytes(target, b"new")
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o600

    def test_atomic_write_bytes_clearer_error_when_parent_missing(self, tmp_path: Path) -> None:
        # Story 2.12 Mn17: clearer error on missing parent dir.
        target = tmp_path / "nope" / "deeper" / "out.bin"
        with pytest.raises(FileNotFoundError, match="parent directory does not exist"):
            atomic_write_bytes(target, b"never")


# ---------------------------------------------------------------------------
# TestAtomicWriteText
# ---------------------------------------------------------------------------


class TestAtomicWriteText:
    def test_atomic_write_text_default_utf8_encoding(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        atomic_write_text(target, "héllo — 世界\n")
        assert target.read_bytes() == "héllo — 世界\n".encode()

    def test_atomic_write_text_custom_encoding(self, tmp_path: Path) -> None:
        target = tmp_path / "latin.txt"
        atomic_write_text(target, "café", encoding="latin-1")
        assert target.read_bytes() == "café".encode("latin-1")

    def test_atomic_write_text_errors_replace(self, tmp_path: Path) -> None:
        # Story 2.12 M9: explicit ``errors=`` parameter.
        target = tmp_path / "ascii.txt"
        # "é" is not encodable in pure ASCII; "replace" substitutes "?".
        atomic_write_text(target, "café", encoding="ascii", errors="replace")
        assert target.read_bytes() == b"caf?"

    def test_atomic_write_text_errors_strict_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "ascii.txt"
        with pytest.raises(UnicodeEncodeError):
            atomic_write_text(target, "café", encoding="ascii")  # default strict

    def test_atomic_write_text_accepts_str_target(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        atomic_write_text(str(target), "hello")
        assert target.read_bytes() == b"hello"


# ---------------------------------------------------------------------------
# TestFsyncSemantics
# ---------------------------------------------------------------------------
#
# Note: monkeypatching ``os.fsync`` / ``os.replace`` with a counting recorder
# is necessarily a GLOBAL patch — pytest's capture machinery / plugins may
# call into these in the gap between patch and the function-under-test.
# The recorders below filter calls by *known* fds (captured via Path open
# inside the test setup) so unrelated calls don't pollute counts.


class TestFsyncSemantics:
    def test_atomic_write_bytes_fsync_data_disabled_skips_fsync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        relevant_calls: list[int] = []
        # Track all fds opened by atomic_write_bytes so we can filter
        # the recorder to only those we own.
        owned_fds: set[int] = set()
        real_open = os.open
        real_fsync = os.fsync

        def _open(path: object, flags: int, mode: int = 0o777, *a: object, **k: object) -> int:
            fd = real_open(path, flags, mode, *a, **k)  # type: ignore[arg-type]
            owned_fds.add(fd)
            return fd

        def _record(fd: int) -> None:
            if fd in owned_fds:
                relevant_calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data", fsync_data=False, fsync_dir=False)
        assert relevant_calls == []
        assert target.read_bytes() == b"data"

    def test_atomic_write_bytes_fsync_dir_disabled_skips_dir_fsync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        relevant_calls: list[int] = []
        owned_fds: set[int] = set()
        real_open = os.open
        real_fsync = os.fsync

        def _open(path: object, flags: int, mode: int = 0o777, *a: object, **k: object) -> int:
            fd = real_open(path, flags, mode, *a, **k)  # type: ignore[arg-type]
            owned_fds.add(fd)
            return fd

        def _record(fd: int) -> None:
            if fd in owned_fds:
                relevant_calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data", fsync_data=True, fsync_dir=False)
        # Exactly one fsync — the data fsync; no dir fsync.
        assert len(relevant_calls) == 1
        assert target.read_bytes() == b"data"

    def test_atomic_write_bytes_default_fsync_data_and_dir_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        relevant_calls: list[int] = []
        owned_fds: set[int] = set()
        real_open = os.open
        real_fsync = os.fsync

        def _open(path: object, flags: int, mode: int = 0o777, *a: object, **k: object) -> int:
            fd = real_open(path, flags, mode, *a, **k)  # type: ignore[arg-type]
            owned_fds.add(fd)
            return fd

        def _record(fd: int) -> None:
            if fd in owned_fds:
                relevant_calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data")
        # Two fsyncs: one for the file, one for the directory.
        assert len(relevant_calls) == 2
        assert target.read_bytes() == b"data"


# ---------------------------------------------------------------------------
# TestCrossFilesystemDetection
# ---------------------------------------------------------------------------


class TestCrossFilesystemDetection:
    def test_atomic_write_bytes_raises_clear_error_on_exdev(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        real_replace = os.replace

        def _replace(
            src: str | os.PathLike[str], dst: str | os.PathLike[str], *a: object, **k: object
        ) -> None:
            # Filter to our target so unrelated pytest-internal renames
            # (none expected, but defense in depth) don't trip the recorder.
            if str(dst) == str(target):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            real_replace(src, dst, *a, **k)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "replace", _replace)

        with pytest.raises(OSError) as exc_info:
            atomic_write_bytes(target, b"data")

        msg = str(exc_info.value)
        assert "cross-filesystem" in msg
        assert str(target) in msg
        assert ".tmp." in msg

        # tmpfile cleaned up despite the EXDEV error.
        assert not _has_tmp_leftover(tmp_path)


# ---------------------------------------------------------------------------
# TestErrorPathsAndEdgeCases — defensive paths added in code-review fixes.
# ---------------------------------------------------------------------------


class TestErrorPathsAndEdgeCases:
    def test_chunked_write_zero_return_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Story 2.12 M13: defensive guard `written <= 0`.  POSIX guarantees
        # write returns > 0 or raises, but a misbehaving libc / FUSE could
        # plausibly return 0.  Patch os.write to do exactly that and assert
        # the OSError propagates rather than infinite-looping.
        target = tmp_path / "out.bin"

        def _zero(fd: int, data: bytes) -> int:  # noqa: ARG001
            return 0

        monkeypatch.setattr(os, "write", _zero)

        with pytest.raises(OSError, match="os.write returned 0"):
            atomic_write_bytes(target, b"never lands")

        assert not target.exists()
        assert not _has_tmp_leftover(tmp_path)

    def test_tmpfile_unlink_oserror_logged_and_swallowed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        _ensure_ae_logger_propagates: None,  # noqa: PT019 — fixture for side effect
    ) -> None:
        # Story 2.12 M14: cleanup OSError is logged and swallowed; the
        # original exception still propagates.
        target = tmp_path / "out.bin"

        from worker_wrapper.domain import atomic_edit as ae_mod

        def _write_boom(fd: int, data: bytes) -> None:  # noqa: ARG001
            raise OSError(errno.EIO, "primary I/O error")

        monkeypatch.setattr(ae_mod, "_chunked_write", _write_boom)

        real_unlink = Path.unlink

        def _unlink_boom(self: Path, *, missing_ok: bool = False) -> None:
            # Fail unlink for our tmpfile (matches by parent+pattern).
            if (
                self.parent == tmp_path
                and ".tmp." in self.name
                and not self.name.startswith("out.bin.real")
            ):
                raise OSError(errno.EACCES, "cleanup denied")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", _unlink_boom)

        with (
            caplog.at_level("WARNING", logger="worker_wrapper.domain.atomic_edit"),
            pytest.raises(OSError, match="primary I/O error"),
        ):
            atomic_write_bytes(target, b"data")

        # The cleanup warning was logged.
        warning_lines = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("cleanup failed" in line for line in warning_lines), (
            f"expected cleanup-failed WARNING log, got {warning_lines!r}"
        )

    def test_o_excl_collision_raises_file_exists_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Story 2.12 M15: O_EXCL collision behavior.  Pre-create the tmp
        # path that the deterministic `secrets.token_hex(8)` would produce
        # and assert atomic_write_bytes raises FileExistsError without
        # corrupting the pre-existing tmpfile.
        target = tmp_path / "out.bin"

        import secrets as secrets_mod  # local import keeps top-of-file imports lean

        # Force a known token + pid so we can pre-create the colliding tmp.
        fake_token = "0" * 16  # token_hex(8) → 16 hex chars
        monkeypatch.setattr(secrets_mod, "token_hex", lambda _n: fake_token)
        monkeypatch.setattr(os, "getpid", lambda: 99999)

        colliding = tmp_path / f"{target.name}.tmp.99999.{fake_token}"
        colliding.write_bytes(b"do-not-corrupt")

        with pytest.raises(FileExistsError):
            atomic_write_bytes(target, b"never lands")

        # Pre-existing tmp NOT corrupted.
        assert colliding.read_bytes() == b"do-not-corrupt"
        # Target was never created.
        assert not target.exists()

    def test_dir_fsync_failure_logged_not_raised(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        _ensure_ae_logger_propagates: None,  # noqa: PT019 — fixture for side effect
    ) -> None:
        # Story 2.12 M1 / Mn21: dir-fsync failure AFTER successful rename
        # is logged at WARNING and SUPPRESSED (data already on disk).
        target = tmp_path / "out.bin"
        real_fsync = os.fsync
        # Open a parent-directory fd so we know which fd to fail on.
        # We track all fds opened to the parent dir during the call.
        parent_fd_seen: list[int] = []
        real_open = os.open

        def _open(path: object, flags: int, mode: int = 0o777, *a: object, **k: object) -> int:
            fd = real_open(path, flags, mode, *a, **k)  # type: ignore[arg-type]
            # Track fds opened on the parent directory itself.
            if str(path) == str(tmp_path):
                parent_fd_seen.append(fd)
            return fd

        def _fsync(fd: int) -> None:
            if fd in parent_fd_seen:
                raise OSError(errno.EIO, "dir-fsync simulated failure")
            real_fsync(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "fsync", _fsync)

        with caplog.at_level("WARNING", logger="worker_wrapper.domain.atomic_edit"):
            # MUST NOT raise — dir-fsync errors are logged-not-raised.
            atomic_write_bytes(target, b"data")

        # Data IS on disk.
        assert target.read_bytes() == b"data"
        # Warning was emitted.
        warning_lines = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("dir-fsync failed" in line for line in warning_lines), (
            f"expected dir-fsync-failed WARNING log, got {warning_lines!r}"
        )

    def test_target_is_symlink_replaces_link_not_link_target(self, tmp_path: Path) -> None:
        # Story 2.12 Mn8: atomic_write_bytes replaces the symlink itself,
        # not its target — that is the os.replace semantic on POSIX.
        real_target = tmp_path / "real.bin"
        real_target.write_bytes(b"untouched")

        link = tmp_path / "link.bin"
        link.symlink_to(real_target)

        atomic_write_bytes(link, b"new-content")

        # The symlink was replaced by a regular file.
        assert link.is_file()
        assert not link.is_symlink()
        assert link.read_bytes() == b"new-content"
        # The original symlink target is untouched.
        assert real_target.read_bytes() == b"untouched"


# ---------------------------------------------------------------------------
# TestValidateEdit — Story 5.6 (AC-3)
# ---------------------------------------------------------------------------


class TestValidateEdit:
    def test_valid_single_match(self) -> None:
        result = validate_edit("hello world", "world", "earth")
        assert result.valid
        assert result.match_count == 1
        assert result.error is None

    def test_no_match(self) -> None:
        result = validate_edit("hello world", "missing", "replacement")
        assert not result.valid
        assert result.match_count == 0
        assert "not found" in (result.error or "")

    def test_multiple_matches_without_replace_all(self) -> None:
        result = validate_edit("aaa aaa aaa", "aaa", "bbb")
        assert not result.valid
        assert result.match_count == 3
        assert "3 times" in (result.error or "")

    def test_multiple_matches_with_replace_all(self) -> None:
        result = validate_edit("aaa aaa aaa", "aaa", "bbb", replace_all=True)
        assert result.valid
        assert result.match_count == 3

    def test_empty_old_string(self) -> None:
        result = validate_edit("content", "", "new")
        assert not result.valid
        assert result.match_count == 0
        assert "non-empty" in (result.error or "")

    def test_new_string_exceeds_max_size(self) -> None:
        from worker_wrapper.domain.atomic_edit import _MAX_EDIT_SIZE

        result = validate_edit("old", "old", "x" * (_MAX_EDIT_SIZE + 1))
        assert not result.valid
        assert "exceeds" in (result.error or "")

    def test_old_string_exceeds_max_size(self) -> None:
        from worker_wrapper.domain.atomic_edit import _MAX_EDIT_SIZE

        result = validate_edit("content", "x" * (_MAX_EDIT_SIZE + 1), "replacement")
        assert not result.valid
        assert "exceeds" in (result.error or "")

    def test_noop_edit_rejected(self) -> None:
        result = validate_edit("hello world", "world", "world")
        assert not result.valid
        assert "no-op" in (result.error or "")

    def test_overlapping_pattern_count(self) -> None:
        # str.count counts non-overlapping occurrences, matching str.replace.
        result = validate_edit("aaaa", "aa", "b", replace_all=True)
        assert result.valid
        assert result.match_count == 2


# ---------------------------------------------------------------------------
# TestApplyFileEdit — Story 5.6 (AC-1, AC-4)
# ---------------------------------------------------------------------------


class TestApplyFileEdit:
    def test_happy_path(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("hello world")
        result = apply_file_edit(target, "world", "earth")
        assert result.success
        assert result.target_path == str(target)
        assert target.read_text() == "hello earth"
        assert result.secrets_detected is False
        assert result.secret_matches == []
        assert result.error is None

    def test_file_not_found(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.txt"
        result = apply_file_edit(target, "old", "new")
        assert not result.success
        assert "file not found" in (result.error or "")

    def test_old_string_not_found(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("hello world")
        result = apply_file_edit(target, "missing", "new")
        assert not result.success
        assert "not found" in (result.error or "")
        # File unchanged.
        assert target.read_text() == "hello world"

    def test_multiple_matches_error(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("aaa bbb aaa ccc aaa")
        result = apply_file_edit(target, "aaa", "ccc")
        assert not result.success
        assert "3 times" in (result.error or "")
        assert target.read_text() == "aaa bbb aaa ccc aaa"

    def test_replace_all(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("aaa bbb aaa")
        result = apply_file_edit(target, "aaa", "ccc", replace_all=True)
        assert result.success
        assert target.read_text() == "ccc bbb ccc"

    def test_secret_detection_aborts(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("push with placeholder")
        result = apply_file_edit(target, "placeholder", "ghp_" + "A" * 36)
        assert not result.success
        assert result.secrets_detected is True
        assert result.secret_matches is not None
        assert len(result.secret_matches) > 0
        # File unchanged.
        assert target.read_text() == "push with placeholder"

    def test_lines_added_removed(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("line1\nline2\n")
        result = apply_file_edit(target, "line2", "new\nline3")
        assert result.success
        assert result.lines_added == 1
        assert result.lines_removed == 0
        assert target.read_text() == "line1\nnew\nline3\n"

    def test_atomic_write_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("hello world")

        from worker_wrapper.domain import atomic_edit as ae_mod

        def _boom(fd: int, data: bytes) -> None:  # noqa: ARG001
            raise OSError(errno.EIO, "simulated I/O error")

        monkeypatch.setattr(ae_mod, "_chunked_write", _boom)

        result = apply_file_edit(target, "world", "earth")
        assert not result.success
        assert "atomic write failed" in (result.error or "")
        # Original file survives.
        assert target.read_text() == "hello world"

    def test_binary_file_returns_error(self, tmp_path: Path) -> None:
        target = tmp_path / "binary.bin"
        target.write_bytes(b"\x80\x81\x82\xff")
        result = apply_file_edit(target, "x", "y")
        assert not result.success
        assert "UTF-8" in (result.error or "")

    def test_session_id_propagated(self, tmp_path: Path) -> None:
        target = tmp_path / "edit.txt"
        target.write_text("hello world")
        result = apply_file_edit(
            target,
            "world",
            "earth",
            session_id="s-0192abc0-0000-7000-8000-000000000001",
        )
        assert result.success
        assert result.session_id == "s-0192abc0-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------
# TestApplyFileWrite — Story 5.6 (AC-2, AC-4)
# ---------------------------------------------------------------------------


class TestApplyFileWrite:
    def test_happy_path(self, tmp_path: Path) -> None:
        target = tmp_path / "write.txt"
        result = apply_file_write(target, "hello world")
        assert result.success
        assert result.target_path == str(target)
        assert target.read_text() == "hello world"
        assert result.secret_matches == []

    def test_parent_dir_creation(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "dir" / "write.txt"
        result = apply_file_write(target, "nested content")
        assert result.success
        assert target.read_text() == "nested content"

    def test_secret_detection_aborts(self, tmp_path: Path) -> None:
        target = tmp_path / "write.txt"
        result = apply_file_write(target, "key=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        assert not result.success
        assert result.secrets_detected is True
        assert result.secret_matches is not None
        assert len(result.secret_matches) > 0
        # File was NOT created.
        assert not target.exists()

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "write.txt"
        target.write_text("old content")
        result = apply_file_write(target, "new content")
        assert result.success
        assert result.lines_removed == 0
        assert target.read_text() == "new content"

    def test_lines_added_for_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        result = apply_file_write(target, "line1\nline2\nline3\n")
        assert result.success
        assert result.lines_added == 3

    def test_empty_content(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.txt"
        result = apply_file_write(target, "")
        assert result.success
        assert target.read_text() == ""

    @pytest.mark.slow
    def test_large_file(self, tmp_path: Path) -> None:
        target = tmp_path / "big.txt"
        # Use 512 KB — safely under _MAX_EDIT_SIZE (1M chars).
        content = "x" * (512 * 1024)
        result = apply_file_write(target, content)
        assert result.success
        assert target.read_text() == content

    def test_content_exceeds_max_size(self, tmp_path: Path) -> None:
        from worker_wrapper.domain.atomic_edit import _MAX_EDIT_SIZE

        target = tmp_path / "huge.txt"
        result = apply_file_write(target, "x" * (_MAX_EDIT_SIZE + 1))
        assert not result.success
        assert "exceeds" in (result.error or "")
        assert not target.exists()

    def test_session_id_propagated(self, tmp_path: Path) -> None:
        target = tmp_path / "write.txt"
        result = apply_file_write(
            target,
            "content",
            session_id="s-0192abc0-0000-7000-8000-000000000002",
        )
        assert result.success
        assert result.session_id == "s-0192abc0-0000-7000-8000-000000000002"


# ---------------------------------------------------------------------------
# TestSchemaRegistry — Story 5.6 (AC-8)
# ---------------------------------------------------------------------------


class TestSchemaRegistry:
    _session_id = "s-0192abc0-0000-7000-8000-000000000001"

    def test_file_edited_registered(self) -> None:
        from events.payloads import FileEditedPayload
        from events.schema_registry import REGISTRY, register

        # Register directly to verify the model + key work together.
        # The canonical registration in registry_state.domain.event_types is
        # verified by check_event_registry.py (CI gate).
        register("file.edited", "1.0.0", FileEditedPayload)
        assert ("file.edited", "1.0.0") in REGISTRY

    def test_file_edited_payload_valid(self) -> None:
        from events.payloads import FileEditedPayload

        payload = FileEditedPayload(
            session_id=self._session_id,
            file_path="/tmp/test.py",
            tool_name="Write",
            lines_added=10,
            lines_removed=0,
        )
        assert payload.tool_name == "Write"
        assert not payload.secrets_detected

    def test_file_edited_payload_with_secrets(self) -> None:
        from events.payloads import FileEditedPayload

        payload = FileEditedPayload(
            session_id=self._session_id,
            file_path="/tmp/test.py",
            tool_name="Edit",
            lines_added=0,
            lines_removed=5,
            secrets_detected=True,
        )
        assert payload.secrets_detected

    def test_file_edited_payload_rejects_invalid_tool(self) -> None:
        from events.payloads import FileEditedPayload

        with pytest.raises(ValueError):
            FileEditedPayload(
                session_id=self._session_id,
                file_path="/tmp/test.py",
                tool_name="Bash",  # type: ignore[arg-type]
                lines_added=0,
                lines_removed=0,
            )
