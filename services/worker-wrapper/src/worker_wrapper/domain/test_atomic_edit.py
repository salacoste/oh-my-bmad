"""Co-located unit tests for atomic_write_bytes / atomic_write_text (Story 2.12).

Test classes (per AC-7):
  - TestAtomicWriteBytes        — happy path + cleanup invariants (~7).
  - TestAtomicWriteText         — encoding behavior (~2).
  - TestFsyncSemantics          — fsync_data / fsync_dir gating (~3).
  - TestCrossFilesystemDetection — EXDEV re-raise (~1).
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from worker_wrapper.domain.atomic_edit import (
    atomic_write_bytes,
    atomic_write_text,
)

if TYPE_CHECKING:
    pass


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
        # No leftover tmpfiles in the parent dir.
        leftover = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftover == []

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
        leftover = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftover == []

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
        leftover = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftover == []

    def test_atomic_write_bytes_cleans_up_tmpfile_on_fsync_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        real_fsync = os.fsync
        calls: list[int] = []

        def _fsync_then_boom(fd: int) -> None:
            # Fail on the FILE fsync (first call); dir fsync would come later.
            calls.append(fd)
            if len(calls) == 1:
                raise OSError(errno.EIO, "fsync I/O error")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _fsync_then_boom)

        with pytest.raises(OSError, match="fsync I/O error"):
            atomic_write_bytes(target, b"data")

        assert not target.exists()
        leftover = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftover == []

    def test_atomic_write_bytes_raises_on_no_parent_directory(self) -> None:
        with pytest.raises(ValueError, match="parent directory"):
            atomic_write_bytes(Path("/"), b"never")

    def test_atomic_write_bytes_handles_large_payload(self, tmp_path: Path) -> None:
        target = tmp_path / "big.bin"
        payload = b"x" * (10 * 1024 * 1024)  # 10 MB
        atomic_write_bytes(target, payload)
        assert target.read_bytes() == payload


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


# ---------------------------------------------------------------------------
# TestFsyncSemantics
# ---------------------------------------------------------------------------


class TestFsyncSemantics:
    def test_atomic_write_bytes_fsync_data_disabled_skips_fsync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        calls: list[int] = []
        real_fsync = os.fsync

        def _record(fd: int) -> None:
            calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data", fsync_data=False, fsync_dir=False)
        assert calls == []
        assert target.read_bytes() == b"data"

    def test_atomic_write_bytes_fsync_dir_disabled_skips_dir_fsync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        calls: list[int] = []
        real_fsync = os.fsync

        def _record(fd: int) -> None:
            calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data", fsync_data=True, fsync_dir=False)
        # Exactly one fsync — the data fsync; no dir fsync.
        assert len(calls) == 1
        assert target.read_bytes() == b"data"

    def test_atomic_write_bytes_default_fsync_data_and_dir_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"
        calls: list[int] = []
        real_fsync = os.fsync

        def _record(fd: int) -> None:
            calls.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _record)
        atomic_write_bytes(target, b"data")
        # Two fsyncs: one for the file, one for the directory.
        assert len(calls) == 2
        assert target.read_bytes() == b"data"


# ---------------------------------------------------------------------------
# TestCrossFilesystemDetection
# ---------------------------------------------------------------------------


class TestCrossFilesystemDetection:
    def test_atomic_write_bytes_raises_clear_error_on_exdev(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "out.bin"

        def _exdev(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:  # noqa: ARG001
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        monkeypatch.setattr(os, "replace", _exdev)

        with pytest.raises(OSError) as exc_info:
            atomic_write_bytes(target, b"data")

        msg = str(exc_info.value)
        assert "cross-filesystem" in msg
        assert str(target) in msg
        assert ".tmp." in msg

        # tmpfile cleaned up despite the EXDEV error.
        leftover = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftover == []
