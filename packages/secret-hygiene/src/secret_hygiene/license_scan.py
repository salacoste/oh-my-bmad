"""License-scan module wrapping scancode-toolkit for license-incompatibility detection.

Detects copyleft / proprietary licenses in staged files and returns structured
findings.  Designed to run as a pre-push step on agent-generated commits
(FR40, NFR-S8).  ``scancode-toolkit`` is an **optional** dependency — when
unavailable the module degrades gracefully (returns no findings, logs a warning).

Usage (programmatic)::

    from secret_hygiene.license_scan import scan_file_licenses, scan_files_for_licenses

    findings = scan_file_licenses(Path("src/file.py"))
    all_findings = scan_files_for_licenses(["src/a.py", "src/b.py"])

Usage (CLI)::

    uv run secret-hygiene-license-scan [file ...] [--repo-license MIT]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# License policy
# ---------------------------------------------------------------------------

PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "mit",
        "apache-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "isc",
        "0bsd",
        "unlicense",
        "cc0-1.0",
        "python-2.0",
        "psf-2.0",
        "artistic-2.0",
        "zlib",
        "mit-0",
        "boost-1.0",
    }
)

COPYLEFT_INDICATORS: frozenset[str] = frozenset(
    {
        "gpl",
        "agpl",
        "lgpl",
        "mpl",
        "cpal",
        "eupl",
        "sspl",
        "copyleft",
        "proprietary",
        "cecill",
    }
)

REPO_LICENSE: str = "mit"


def _normalize(license_expr: str) -> str:
    """Normalize a license expression to lowercase for comparison."""
    return license_expr.lower().strip()


def is_compatible(detected: str, repo_license: str = REPO_LICENSE) -> bool:
    """Return ``True`` if *detected* license is compatible with *repo_license*.

    A license is compatible when it is in :data:`PERMISSIVE_LICENSES` or matches
    the project's own license.  Copyleft indicators (gpl, agpl, …) are
    considered incompatible.
    """
    norm = _normalize(detected)
    if not norm:
        return True
    if norm == _normalize(repo_license):
        return True
    if norm in PERMISSIVE_LICENSES:
        return True
    # Composite expressions — split on AND and OR separately.
    # AND: every component must be compatible.
    # OR: at least one component must be compatible (dual-licensed).
    and_groups = re.split(r"\s+and\s+", norm)
    for group in and_groups:
        group = group.strip("() ")
        or_tokens = re.split(r"\s+or\s+", group)
        if len(or_tokens) > 1:
            # OR group: compatible if ANY branch is ok
            if not any(_token_ok(t.strip("() "), repo_license) for t in or_tokens):
                return False
        else:
            if not _token_ok(group, repo_license):
                return False
    return True


def _token_ok(token: str, repo_license: str) -> bool:
    """Return True if a single license token is compatible."""
    token = token.strip("() ")
    if token in PERMISSIVE_LICENSES:
        return True
    if token == _normalize(repo_license):
        return True
    return all(indicator not in token for indicator in COPYLEFT_INDICATORS)


def _reason_code(detected: str) -> str:
    """Return a reason code for an incompatible license."""
    norm = _normalize(detected)
    if "proprietary" in norm:
        return "proprietary-incompatible"
    for indicator in COPYLEFT_INDICATORS:
        if indicator in norm:
            return "copyleft-incompatible"
    return "unknown-incompatible"


# ---------------------------------------------------------------------------
# Finding data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseFinding:
    """A single license-incompatibility finding."""

    file_path: str
    license_detected: str
    incompatible_with_repo_license: str
    reason_code: str


# ---------------------------------------------------------------------------
# Exclude patterns for batch scanning
# ---------------------------------------------------------------------------

_EXCLUDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.lock$"),
    re.compile(r"^(uv|poetry)\.lock$"),
    re.compile(r"^\.venv/"),
    re.compile(r"^node_modules/"),
    re.compile(r"^upstream/"),
    re.compile(r"^_bmad/"),
    re.compile(r"^_bmad-output/"),
    re.compile(r"\.(png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot|so|dylib|dll|exe|o|pyc|pyd)$"),
]

_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".o",
        ".pyc",
        ".pyd",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".pdf",
    }
)


def _should_skip(path_str: str) -> bool:
    """Return ``True`` if *path_str* matches an exclude pattern."""
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(path_str):
            return True
    return Path(path_str).suffix.lower() in _BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# Single-file scanner
# ---------------------------------------------------------------------------


def scan_file_licenses(
    path: Path,
    repo_license: str = REPO_LICENSE,
) -> list[LicenseFinding]:
    """Scan *path* for license incompatibilities.

    Returns a :class:`LicenseFinding` for each detected incompatible license.
    Gracefully handles missing files, binary files, and absent
    ``scancode-toolkit`` dependency.
    """
    # Fast path: skip binaries by extension.
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return []

    if not path.exists():
        return []

    # Lazy import — scancode-toolkit is optional.
    try:
        # scancode-toolkit is an OPTIONAL runtime dep; ImportError caught below.
        # Module-level ignore in pyproject.toml [[tool.mypy.overrides]] for scancode.*
        from scancode.api import get_licenses
    except ImportError:
        print(
            "warning: secret-hygiene: scancode-toolkit not installed; license scan skipped",
            file=sys.stderr,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        print(
            f"warning: secret-hygiene: cannot import scancode: {exc}; license scan skipped",
            file=sys.stderr,
        )
        return []

    try:
        result = get_licenses(location=str(path))
    except (FileNotFoundError, OSError):
        return []
    except Exception as exc:  # noqa: BLE001
        print(
            f"warning: secret-hygiene: license scan failed for {path}: {exc}",
            file=sys.stderr,
        )
        return []

    detected = result.get("detected_license_expression")
    if not detected or detected.lower() in {"noassertion", "none"}:
        return []

    findings: list[LicenseFinding] = []
    if not is_compatible(detected, repo_license):
        findings.append(
            LicenseFinding(
                file_path=str(path),
                license_detected=detected,
                incompatible_with_repo_license=repo_license,
                reason_code=_reason_code(detected),
            )
        )

    # Also check individual detections for composite expressions.
    for detection in result.get("license_detections", []):
        expr = detection.get("license_expression") or detection.get("matched_rule", {}).get(
            "license_expression"
        )
        if not expr:
            continue
        # Skip if already covered by the top-level expression.
        if expr == detected:
            continue
        if not is_compatible(expr, repo_license) and not any(
            f.license_detected == expr for f in findings
        ):
            findings.append(
                LicenseFinding(
                    file_path=str(path),
                    license_detected=expr,
                    incompatible_with_repo_license=repo_license,
                    reason_code=_reason_code(expr),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Batch scanner
# ---------------------------------------------------------------------------


def scan_files_for_licenses(
    paths: Sequence[str | Path],
    repo_license: str = REPO_LICENSE,
) -> list[LicenseFinding]:
    """Scan multiple files for license incompatibilities.

    Skips files matching exclude patterns (lock files, vendored dirs, binaries).
    """
    findings: list[LicenseFinding] = []
    for p in paths:
        p_str = str(p)
        if _should_skip(p_str):
            continue
        findings.extend(scan_file_licenses(Path(p), repo_license))
    return findings


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def license_scan_main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the license scanner.

    Accepts file paths as positional arguments and optional ``--repo-license``.
    Returns 0 if no incompatibilities found, 1 otherwise.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="secret-hygiene-license-scan",
        description="Scan files for license incompatibilities.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to scan.",
    )
    parser.add_argument(
        "--repo-license",
        metavar="SPDX",
        default=REPO_LICENSE,
        help=f"Repository's declared license (default: {REPO_LICENSE}).",
    )

    args = parser.parse_args(argv)
    findings = scan_files_for_licenses(args.files, args.repo_license)

    for f in findings:
        print(
            f"License incompatibility: {f.file_path} — "
            f"detected {f.license_detected} "
            f"(incompatible with {f.incompatible_with_repo_license}, "
            f"reason: {f.reason_code})",
            file=sys.stderr,
        )

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(license_scan_main())
