"""Archive manifest loading and hot/archive merge helpers (Phase 13)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from events.envelope import EventEnvelope
from events.log_reader import read_log_lines

from replay.errors import (
    ReplayArchiveChecksumError,
    ReplayArchiveConfigError,
    ReplayArchiveConflictError,
    ReplayArchiveManifestError,
    ReplayArchiveMissingSegmentError,
)

_PRIMARY_ENV: Final = "REPLAY_ARCHIVE_MANIFEST"
_LEGACY_ENV: Final = "EVENT_LOG_ARCHIVE_MANIFEST"
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HotOnlyReplaySentinel:
    """Sentinel that forces hot-only replay and bypasses archive env vars."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial debug helper
        return "HOT_ONLY_REPLAY"


HOT_ONLY_REPLAY: Final = HotOnlyReplaySentinel()
ArchiveManifestInput = Path | None | HotOnlyReplaySentinel


@dataclass(frozen=True, order=True)
class SegmentKey:
    """Deterministic segment identity: logical date + sequence interval."""

    logical_date: str
    first_sequence: int
    last_sequence: int


@dataclass(frozen=True)
class SegmentEvents:
    """Events and identity metadata for one hot or archived segment."""

    key: SegmentKey
    original_relpath: str
    archive_relpath: str | None
    sha256: str
    event_count: int
    path: Path
    envelopes: list[EventEnvelope]


def _normalize_path(value: str) -> Path:
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ReplayArchiveConfigError(f"archive manifest path is unresolvable: {value!r}") from exc


def _ensure_file_readable(path: Path) -> Path:
    if not path.exists():
        raise ReplayArchiveConfigError(f"archive manifest does not exist: {path}")
    if not path.is_file():
        raise ReplayArchiveConfigError(f"archive manifest is not a file: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise ReplayArchiveConfigError(f"archive manifest is not readable: {path}") from exc
    return path


def resolve_archive_manifest_path(
    archive_manifest_path: ArchiveManifestInput = None,
) -> Path | None:
    """Resolve archive manifest configuration using the Phase 13 decision table."""
    if archive_manifest_path is HOT_ONLY_REPLAY:
        return None
    if archive_manifest_path is not None:
        return _ensure_file_readable(_normalize_path(str(archive_manifest_path)))

    primary = os.environ.get(_PRIMARY_ENV)
    legacy = os.environ.get(_LEGACY_ENV)
    if not primary and not legacy:
        return None
    if primary and legacy:
        primary_path = _normalize_path(primary)
        legacy_path = _normalize_path(legacy)
        if str(primary_path) != str(legacy_path):
            raise ReplayArchiveConfigError(
                f"{_PRIMARY_ENV} and {_LEGACY_ENV} point to different archive manifests"
            )
        return _ensure_file_readable(primary_path)
    return _ensure_file_readable(_normalize_path(primary or legacy or ""))


def _validate_relpath(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ReplayArchiveManifestError(f"{field} must be a non-empty relative POSIX path")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ReplayArchiveManifestError(f"{field} must be relative and must not contain '..'")
    return value


def _sha256_bytes(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except FileNotFoundError as exc:
        raise ReplayArchiveMissingSegmentError(f"archive segment missing: {path}") from exc
    except OSError as exc:
        raise ReplayArchiveManifestError(f"archive segment unreadable: {path}") from exc
    return h.hexdigest()


def _read_segment(path: Path) -> list[EventEnvelope]:
    try:
        return list(read_log_lines(path))
    except FileNotFoundError as exc:
        raise ReplayArchiveMissingSegmentError(f"archive segment missing: {path}") from exc


def _segment_key_from_envelopes(logical_date: str, envelopes: list[EventEnvelope]) -> SegmentKey:
    if not envelopes:
        raise ReplayArchiveManifestError("archive segment must contain at least one event")
    first = min(e.emitted_at_monotonic_ns for e in envelopes)
    last = max(e.emitted_at_monotonic_ns for e in envelopes)
    return SegmentKey(logical_date=logical_date, first_sequence=first, last_sequence=last)


def _validate_int(value: Any, *, field: str, min_value: int | None = None) -> int:
    if not isinstance(value, int):
        raise ReplayArchiveManifestError(f"{field} must be an integer")
    if min_value is not None and value < min_value:
        raise ReplayArchiveManifestError(f"{field} must be >= {min_value}")
    return value


def _logical_date_from_original(original_relpath: str) -> str:
    return PurePosixPath(original_relpath).name.removesuffix(".jsonl")


def _segments_overlap(a: SegmentKey, b: SegmentKey) -> bool:
    return max(a.first_sequence, b.first_sequence) <= min(a.last_sequence, b.last_sequence)


def _check_duplicate_or_overlap(existing: list[SegmentEvents], candidate: SegmentEvents) -> None:
    for segment in existing:
        if segment.key == candidate.key:
            raise ReplayArchiveConflictError(f"duplicate segment key: {candidate.key}")
        if _segments_overlap(segment.key, candidate.key):
            raise ReplayArchiveConflictError(
                f"archive segment overlaps existing segment: {candidate.key} vs {segment.key}"
            )


def _hot_segments(event_log_dir: Path) -> list[SegmentEvents]:
    segments: list[SegmentEvents] = []
    for path in sorted(event_log_dir.glob("*.jsonl")):
        envelopes = list(read_log_lines(path))
        if not envelopes:
            continue
        logical_date = path.name.removesuffix(".jsonl")
        key = _segment_key_from_envelopes(logical_date, envelopes)
        rel = path.relative_to(event_log_dir).as_posix()
        segments.append(
            SegmentEvents(
                key=key,
                original_relpath=rel,
                archive_relpath=None,
                sha256=_sha256_bytes(path),
                event_count=len(envelopes),
                path=path,
                envelopes=envelopes,
            )
        )
    return segments


def load_archive_manifest(
    *,
    event_log_dir: Path,
    archive_manifest_path: ArchiveManifestInput = None,
) -> list[SegmentEvents]:
    """Load and validate archived segments from a lifecycle manifest."""
    manifest_path = resolve_archive_manifest_path(archive_manifest_path)
    if manifest_path is None:
        return []

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReplayArchiveManifestError(
            f"archive manifest JSON is invalid: {manifest_path}"
        ) from exc
    except OSError as exc:
        raise ReplayArchiveConfigError(
            f"archive manifest is not readable: {manifest_path}"
        ) from exc

    if not isinstance(raw, dict):
        raise ReplayArchiveManifestError("archive manifest must be a JSON object")
    for field in ("schema_version", "manifest_id", "created_at", "created_by", "segments"):
        if field not in raw:
            raise ReplayArchiveManifestError(f"archive manifest missing required field: {field}")
    if raw["schema_version"] != 1:
        raise ReplayArchiveManifestError("archive manifest schema_version must be 1")
    if not isinstance(raw["segments"], list):
        raise ReplayArchiveManifestError("archive manifest segments must be a list")

    segments: list[SegmentEvents] = []
    seen_original: set[str] = set()
    seen_archive: set[str] = set()
    seen_keys: set[SegmentKey] = set()
    for item in raw["segments"]:
        if not isinstance(item, dict):
            raise ReplayArchiveManifestError("archive manifest segment must be an object")
        for field in (
            "logical_date",
            "original_relpath",
            "archive_relpath",
            "sha256",
            "event_count",
            "first_sequence",
            "last_sequence",
            "archived_at",
            "actor_id",
        ):
            if field not in item:
                raise ReplayArchiveManifestError(f"archive segment missing required field: {field}")

        logical_date = item["logical_date"]
        if not isinstance(logical_date, str) or not _DATE_RE.match(logical_date):
            raise ReplayArchiveManifestError("archive segment logical_date must be YYYY-MM-DD")
        original_relpath = _validate_relpath(item["original_relpath"], field="original_relpath")
        archive_relpath = _validate_relpath(item["archive_relpath"], field="archive_relpath")
        if _logical_date_from_original(original_relpath) != logical_date:
            raise ReplayArchiveManifestError(
                "logical_date must match original_relpath basename date"
            )
        if original_relpath in seen_original or archive_relpath in seen_archive:
            raise ReplayArchiveConflictError("archive manifest contains duplicate relpath")
        seen_original.add(original_relpath)
        seen_archive.add(archive_relpath)

        event_count = _validate_int(item["event_count"], field="event_count", min_value=1)
        first = _validate_int(item["first_sequence"], field="first_sequence", min_value=0)
        last = _validate_int(item["last_sequence"], field="last_sequence", min_value=first)
        key = SegmentKey(logical_date=logical_date, first_sequence=first, last_sequence=last)
        if key in seen_keys:
            raise ReplayArchiveConflictError(f"duplicate segment key: {key}")
        seen_keys.add(key)

        expected_sha = item["sha256"]
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ReplayArchiveManifestError("archive segment sha256 must be a hex digest string")
        segment_path = manifest_path.parent / Path(archive_relpath)
        actual_sha = _sha256_bytes(segment_path)
        if actual_sha != expected_sha:
            raise ReplayArchiveChecksumError(
                f"archive segment checksum mismatch: {archive_relpath}"
            )
        envelopes = _read_segment(segment_path)
        if len(envelopes) != event_count:
            raise ReplayArchiveManifestError("archive segment event_count does not match file")
        actual_key = _segment_key_from_envelopes(logical_date, envelopes)
        if actual_key != key:
            raise ReplayArchiveManifestError("archive segment sequence range does not match file")
        segment = SegmentEvents(
            key=key,
            original_relpath=original_relpath,
            archive_relpath=archive_relpath,
            sha256=actual_sha,
            event_count=event_count,
            path=segment_path,
            envelopes=envelopes,
        )
        _check_duplicate_or_overlap(segments, segment)
        segments.append(segment)
    return segments


def collect_replay_envelopes(
    event_log_dir: Path,
    archive_manifest_path: ArchiveManifestInput = None,
) -> list[EventEnvelope]:
    """Collect hot plus archived envelopes with deterministic conflict handling."""
    hot_segments = _hot_segments(event_log_dir)
    archive_segments = load_archive_manifest(
        event_log_dir=event_log_dir,
        archive_manifest_path=archive_manifest_path,
    )
    selected = list(hot_segments)
    selected_archives: list[SegmentEvents] = []
    for archive in archive_segments:
        duplicate_hot = next((hot for hot in hot_segments if hot.key == archive.key), None)
        if duplicate_hot is not None:
            if (
                duplicate_hot.sha256 == archive.sha256
                and duplicate_hot.event_count == archive.event_count
            ):
                continue
            raise ReplayArchiveConflictError(f"hot/archive segment mismatch: {archive.key}")
        for hot in hot_segments:
            if _segments_overlap(hot.key, archive.key):
                raise ReplayArchiveConflictError(f"hot/archive segment overlap: {archive.key}")
        _check_duplicate_or_overlap(selected_archives, archive)
        selected_archives.append(archive)
        selected.append(archive)

    envelopes = [env for segment in selected for env in segment.envelopes]
    envelopes.sort(key=lambda e: e.emitted_at_monotonic_ns)
    return envelopes


__all__ = [
    "ArchiveManifestInput",
    "HOT_ONLY_REPLAY",
    "HotOnlyReplaySentinel",
    "SegmentEvents",
    "SegmentKey",
    "collect_replay_envelopes",
    "load_archive_manifest",
    "resolve_archive_manifest_path",
]
