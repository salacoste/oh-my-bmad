"""License-scan module wrapping scancode-toolkit for license-incompatibility detection.

Detects copyleft / proprietary licenses in staged files and returns structured
findings.  Designed to run as a pre-push step on agent-generated commits
(FR40, NFR-S8).  ``scancode-toolkit`` is an **optional** dependency — when
unavailable the module degrades gracefully (returns no findings, logs a warning).

Policy model (G-SEC-1, fail-closed):
  The compatibility policy is **allowlist-based and fail-closed**. A license is
  compatible ONLY if (after normalization via :data:`_LICENSE_ALIASES`) it is in
  :data:`PERMISSIVE_LICENSES`, matches the repo's own license, or is explicitly
  accepted by the caller via the ``extra_allowed`` operator-override set.
  Everything else — copyleft, proprietary, AND genuinely-unknown licenses — is
  treated as INCOMPATIBLE (previously unknown licenses defaulted OPEN, silently
  passing the release gate). Free-text license strings (e.g. "Apache 2.0",
  "MIT License") are normalized to canonical SPDX ids first so legitimate deps
  are not falsely blocked. This module is PURE — it never reads the environment;
  callers (e.g. scripts/check_sbom_licenses.py) supply ``extra_allowed``.

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
        # MPL-2.0 is weak / file-level copyleft, distribution-compatible with
        # MIT (operator decision 2026-06-03). Listing it here short-circuits
        # _token_ok True BEFORE the "mpl" copyleft-substring fallback, so
        # MPL-2.0 is accepted while generic "mpl" / "mpl-1.1" stay flagged.
        "mpl-2.0",
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

# Free-text license-string aliases → canonical SPDX id already in
# PERMISSIVE_LICENSES. Keys are lowercased+stripped. Kept deliberately
# CONSERVATIVE: only unambiguous permissive variants — adding a non-permissive
# mapping here would silently re-open the fail-closed gate. (G-SEC-1)
_LICENSE_ALIASES: dict[str, str] = {
    "apache 2.0": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache license, version 2.0": "apache-2.0",
    "apache software license": "apache-2.0",
    "apache software license 2.0": "apache-2.0",
    "mit license": "mit",
    "isc license": "isc",
    "isc license (iscl)": "isc",
    "the unlicense": "unlicense",
    "python software foundation license": "psf-2.0",
    "psf": "psf-2.0",
    "bsd license": "bsd-3-clause",
    "bsd": "bsd-3-clause",
}


def _normalize(license_expr: str) -> str:
    """Normalize a license expression to lowercase for comparison."""
    return license_expr.lower().strip()


def _canonicalize(token: str) -> str:
    """Canonicalize a single license token / expression.

    Lowercases and strips, then maps known free-text aliases (e.g.
    "Apache 2.0", "MIT License") to their canonical SPDX id. Returns the
    normalized token unchanged when no alias applies.
    """
    norm = _normalize(token)
    return _LICENSE_ALIASES.get(norm, norm)


def is_compatible(
    detected: str,
    repo_license: str = REPO_LICENSE,
    extra_allowed: frozenset[str] = frozenset(),
) -> bool:
    """Return ``True`` if *detected* license is compatible with *repo_license*.

    Fail-closed allowlist policy (G-SEC-1): a license is compatible only when it
    is in :data:`PERMISSIVE_LICENSES`, matches *repo_license*, or appears in
    *extra_allowed* (operator override) — after :func:`_canonicalize`
    normalization. Copyleft, proprietary, AND genuinely-unknown licenses are all
    incompatible.

    *extra_allowed* entries are canonicalized here, so callers may pass raw
    operator-supplied strings.
    """
    extra_allowed = frozenset(_canonicalize(e) for e in extra_allowed)
    norm = _canonicalize(detected)
    if not norm:
        return True
    if norm == _canonicalize(repo_license):
        return True
    if norm in PERMISSIVE_LICENSES:
        return True
    if norm in extra_allowed:
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
            if not any(_token_ok(t.strip("() "), repo_license, extra_allowed) for t in or_tokens):
                return False
        else:
            if not _token_ok(group, repo_license, extra_allowed):
                return False
    return True


def _token_ok(
    token: str,
    repo_license: str,
    extra_allowed: frozenset[str] = frozenset(),
) -> bool:
    """Return True if a single license token is compatible (fail-closed).

    *extra_allowed* is assumed already canonicalized by the caller
    (:func:`is_compatible`); each entry is re-canonicalized defensively so the
    helper is safe to call standalone with raw operator strings.
    """
    extra_allowed = frozenset(_canonicalize(e) for e in extra_allowed)
    token = _canonicalize(token.strip("() "))
    if token in PERMISSIVE_LICENSES:
        return True
    if token == _canonicalize(repo_license):
        return True
    # FAIL-CLOSED: only an explicit operator override can accept anything else
    # (copyleft / proprietary / genuinely-unknown all fall through to False).
    return token in extra_allowed


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
