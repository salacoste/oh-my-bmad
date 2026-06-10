"""Replay archive error hierarchy (Phase 13 / P13-ELLM)."""

from __future__ import annotations


class ReplayArchiveError(Exception):
    """Base class for archive-manifest replay failures."""


class ReplayArchiveConfigError(ReplayArchiveError):
    """Archive manifest configuration is invalid or unreadable."""


class ReplayArchiveManifestError(ReplayArchiveError):
    """Archive manifest content is invalid."""


class ReplayArchiveChecksumError(ReplayArchiveManifestError):
    """Archived segment checksum does not match the manifest."""


class ReplayArchiveMissingSegmentError(ReplayArchiveManifestError):
    """Archive manifest references a missing segment file."""


class ReplayArchiveConflictError(ReplayArchiveError):
    """Archive and hot-log segments overlap or disagree."""


__all__ = [
    "ReplayArchiveChecksumError",
    "ReplayArchiveConfigError",
    "ReplayArchiveConflictError",
    "ReplayArchiveError",
    "ReplayArchiveManifestError",
    "ReplayArchiveMissingSegmentError",
]
