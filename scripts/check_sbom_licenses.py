#!/usr/bin/env python3
"""check_sbom_licenses.py — publish-time license-incompatibility gate (NFR-S11 / G1).

NFR-S11 extends FR40 ("no copyleft / proprietary license incompatible with the
MIT repo license") from commit-time (the pre-push ``secret-hygiene`` license
scan) to **publish-time**: every released image ships with a CycloneDX SBOM
(Story 8.1), and this gate parses that SBOM and fails the release if ANY
component declares a license incompatible with the repo's MIT license. An
incompatible component never gets signed → never published → not a valid
release.

Policy reuse (NFR-S11 is "FR40 at publish-time", NOT a forked policy): the
compatibility decision is delegated VERBATIM to
``secret_hygiene.license_scan`` (``is_compatible`` / ``_reason_code`` /
``REPO_LICENSE``). This script only adds the CycloneDX-SBOM parsing layer on
top of that single source of truth.

What it does:
  * Parses one or more CycloneDX-JSON SBOMs (``--sbom`` repeatable).
  * Scopes the gate to APPLICATION dependencies: a whole-image SBOM enumerates
    the base OS distro's packages too (``pkg:deb/``, ``pkg:apk/``,
    ``pkg:alpine/``, ``pkg:rpm/``), which are governed by the base image, not
    this project. By default those OS-distro components are SKIPPED (matched on
    the component ``purl`` ecosystem prefix, case-insensitively). Everything
    else (``pkg:pypi/`` and any other / non-OS bundled deps) is evaluated, and
    a component with NO ``purl`` is ALSO evaluated (fail-safe — never skipped).
    Pass ``--include-os-packages`` to restore the old whole-image behavior and
    evaluate OS-distro components too. The count of skipped OS-distro
    components is reported on stderr (no silent skips).
  * For each evaluated ``components[]`` entry, collects every license candidate
    from its ``licenses[]`` array:
      - entry ``{"expression": "..."}``  → the SPDX expression as-is.
      - entry ``{"license": {"id": ...}}`` → the SPDX id.
      - entry ``{"license": {"name": ...}}`` → the name (id-absent fallback).
    Each candidate is fed to ``is_compatible(candidate, repo_license)``.
  * A component whose ``licenses`` is empty/absent is governed by
    ``--unknown-license`` (default ``warn`` → stderr note, no failure;
    ``fail`` → recorded as a finding).

FAIL-CLOSED (never pass on an unparseable supply-chain artifact): a missing
file, invalid JSON, or top-level non-object SBOM exits 1 with a clear error —
even under the default ``--unknown-license warn``. An SBOM we cannot read is a
gate we cannot enforce. The empty-components guard checks the ORIGINAL
components array (before OS-distro filtering): an SBOM with zero components at
all fails closed, but an all-OS-distro image (zero app deps after filtering)
legitimately passes.

Exit status: 0 when every component's declared licenses are compatible (and
every SBOM parsed); 1 on ANY incompatible license OR ANY missing/unparseable
SBOM OR ``--self-test`` failure.

Usage::

    uv run python scripts/check_sbom_licenses.py --sbom sbom-base.cyclonedx.json
    uv run python scripts/check_sbom_licenses.py --sbom a.json --sbom b.json -v
    uv run python scripts/check_sbom_licenses.py --self-test   # fixture harness
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

# Reuse the single-source-of-truth license policy from secret-hygiene. The
# package lives at packages/secret-hygiene/src; the editable install exposes it
# on sys.path under `uv run`, but the sys.path shim below keeps the script
# importable when run directly from the scripts/ dir too.
_SECRET_HYGIENE_SRC = REPO_ROOT / "packages" / "secret-hygiene" / "src"
if _SECRET_HYGIENE_SRC.is_dir() and str(_SECRET_HYGIENE_SRC) not in sys.path:
    sys.path.insert(0, str(_SECRET_HYGIENE_SRC))

from secret_hygiene.license_scan import (  # noqa: E402
    REPO_LICENSE,
    _reason_code,
    is_compatible,
)

# ---------------------------------------------------------------------------
# Finding data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SbomLicenseFinding:
    """A single SBOM license-incompatibility finding."""

    sbom: str
    component: str
    version: str
    license_detected: str
    incompatible_with_repo_license: str
    reason_code: str


# ---------------------------------------------------------------------------
# CycloneDX parsing
# ---------------------------------------------------------------------------

# OS-distro purl ecosystem prefixes. A whole-image SBOM lists the base OS's
# packages under these schemes; they are governed by the base image, not this
# project, so the gate skips them by default (use --include-os-packages to
# include). Matched case-insensitively against the component's ``purl``.
_OS_DISTRO_PURL_PREFIXES: tuple[str, ...] = (
    "pkg:deb/",
    "pkg:apk/",
    "pkg:alpine/",
    "pkg:rpm/",
)


def _is_os_distro_component(component: dict[str, Any]) -> bool:
    """Return ``True`` if *component*'s ``purl`` is an OS-distro package.

    A component with no ``purl`` (or a non-string one) is NOT treated as
    OS-distro — it is evaluated (fail-safe: never skip an unidentified dep).
    """
    purl = component.get("purl")
    if not isinstance(purl, str):
        return False
    purl_lower = purl.lower()
    return any(purl_lower.startswith(prefix) for prefix in _OS_DISTRO_PURL_PREFIXES)


def _component_label(component: dict[str, Any]) -> tuple[str, str]:
    """Return ``(name, version)`` for a CycloneDX component (best-effort)."""
    name = str(component.get("name") or "<unnamed>")
    version = str(component.get("version") or "<no-version>")
    return name, version


def _license_candidates(component: dict[str, Any]) -> list[str]:
    """Collect license candidate strings from a component's ``licenses[]``.

    Per CycloneDX, each entry is EITHER ``{"expression": "<SPDX expr>"}`` OR
    ``{"license": {"id"|"name": ...}}``. Expressions are used verbatim; for a
    ``license`` dict we prefer ``id`` (SPDX) and fall back to ``name``.
    """
    candidates: list[str] = []
    licenses = component.get("licenses")
    if not isinstance(licenses, list):
        return candidates
    for entry in licenses:
        if not isinstance(entry, dict):
            continue
        expression = entry.get("expression")
        if expression:
            candidates.append(str(expression))
            continue
        license_obj = entry.get("license")
        if isinstance(license_obj, dict):
            value = license_obj.get("id") or license_obj.get("name")
            if value:
                candidates.append(str(value))
    return candidates


def _load_sbom(path: Path) -> dict[str, Any]:
    """Load and validate a CycloneDX-JSON SBOM.

    FAIL-CLOSED: raises ``ValueError`` (with a clear message) on a missing
    file, invalid JSON, or a top-level value that is not a JSON object. The
    caller turns any such failure into a hard gate failure (exit 1).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"SBOM not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read SBOM {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SBOM is not valid JSON: {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"SBOM top-level value is not a JSON object: {path}")
    return data


def scan_sbom(
    path: Path,
    *,
    repo_license: str,
    unknown_license: str,
    include_os_packages: bool = False,
) -> list[SbomLicenseFinding]:
    """Scan a single SBOM, returning every incompatible-license finding.

    Raises ``ValueError`` on a fail-closed parse error (handled by the caller).
    A component with no declared license is governed by *unknown_license*:
    ``warn`` emits a stderr note and records nothing; ``fail`` records a
    finding with an ``unknown-incompatible`` reason code.

    When *include_os_packages* is False (default) OS-distro components (purl
    ``pkg:deb/`` / ``pkg:apk/`` / ``pkg:alpine/`` / ``pkg:rpm/``) are skipped
    and the skipped count is reported on stderr. When True every component is
    evaluated (old whole-image behavior).
    """
    data = _load_sbom(path)
    sbom_label = str(path)
    findings: list[SbomLicenseFinding] = []

    # Fail-closed (security review G1 MEDIUM): a real container-image SBOM
    # always enumerates components. A missing/non-list/empty ``components``
    # array means the gate cannot inspect any dependency — refuse to pass it
    # rather than silently green-light an image whose deps were never listed.
    # NOTE: this guard checks the ORIGINAL array (before OS-distro filtering),
    # so an all-OS image (zero app deps after filtering) does NOT trip it.
    components = data.get("components")
    if not isinstance(components, list) or len(components) == 0:
        raise ValueError(
            f"SBOM has no components to evaluate ({path}); refusing to pass a "
            "license gate that cannot inspect any dependencies (fail-closed)"
        )

    os_skipped = 0
    for component in components:
        if not isinstance(component, dict):
            continue
        if not include_os_packages and _is_os_distro_component(component):
            os_skipped += 1
            continue
        name, version = _component_label(component)
        candidates = _license_candidates(component)

        if not candidates:
            if unknown_license == "fail":
                findings.append(
                    SbomLicenseFinding(
                        sbom=sbom_label,
                        component=name,
                        version=version,
                        license_detected="<none>",
                        incompatible_with_repo_license=repo_license,
                        reason_code="unknown-incompatible",
                    )
                )
            else:
                print(
                    f"note: {sbom_label}: {name}@{version} declares no license "
                    "(unknown-license=warn → not failing)",
                    file=sys.stderr,
                )
            continue

        for candidate in candidates:
            if not is_compatible(candidate, repo_license):
                findings.append(
                    SbomLicenseFinding(
                        sbom=sbom_label,
                        component=name,
                        version=version,
                        license_detected=candidate,
                        incompatible_with_repo_license=repo_license,
                        reason_code=_reason_code(candidate),
                    )
                )

    if os_skipped:
        print(
            f"note: {sbom_label}: skipped {os_skipped} OS-distro component(s) "
            "(pkg:deb/apk/rpm); pass --include-os-packages to include",
            file=sys.stderr,
        )

    return findings


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """Exercise bundled fixtures and assert SBOM-license detection works.

    ``clean/`` fixtures must yield zero findings (at default warn);
    ``violations/`` fixtures must each yield ≥1 finding OR fail-closed on parse.
    """
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "sbom_licenses"
    failures: list[str] = []

    clean_dir = fixture_root / "clean"
    clean_files: list[Path] = []
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        clean_files = sorted(clean_dir.glob("*.json"))
        if not clean_files:
            failures.append(f"Empty clean fixtures directory: {clean_dir}")
        for fpath in clean_files:
            try:
                findings = scan_sbom(fpath, repo_license=REPO_LICENSE, unknown_license="warn")
            except ValueError as exc:
                failures.append(f"FAIL clean/{fpath.name}: unexpected parse error: {exc}")
                continue
            if findings:
                failures.append(
                    f"FAIL clean/{fpath.name}: expected 0 findings but got "
                    f"{len(findings)}: {[f.license_detected for f in findings]}"
                )

    violation_dir = fixture_root / "violations"
    violation_files: list[Path] = []
    if not violation_dir.exists():
        failures.append(f"Missing violations fixtures directory: {violation_dir}")
    else:
        violation_files = sorted(violation_dir.glob("*.json"))
        if not violation_files:
            failures.append(f"Empty violations fixtures directory: {violation_dir}")
        for fpath in violation_files:
            try:
                findings = scan_sbom(fpath, repo_license=REPO_LICENSE, unknown_license="warn")
            except ValueError:
                # Fail-closed parse error (e.g. malformed JSON) counts as a fire.
                continue
            if not findings:
                failures.append(f"FAIL violations/{fpath.name}: expected ≥1 finding but got none")

    if failures:
        print("check_sbom_licenses.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files)
    print(f"✓ check_sbom_licenses.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_sbom_licenses.py",
        description="Publish-time license-incompatibility gate (NFR-S11 / FR40).",
    )
    parser.add_argument(
        "--sbom",
        action="append",
        metavar="PATH",
        help="CycloneDX-JSON SBOM to scan (repeatable; ≥1 required unless --self-test).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run against bundled fixtures and assert expected outcomes.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a success summary even when there are no findings.",
    )
    parser.add_argument(
        "--repo-license",
        metavar="SPDX",
        default=REPO_LICENSE,
        help=f"Repository's declared license (default: {REPO_LICENSE}).",
    )
    parser.add_argument(
        "--unknown-license",
        choices=("fail", "warn"),
        default="warn",
        help="How to treat components with no declared license (default: warn).",
    )
    parser.add_argument(
        "--include-os-packages",
        action="store_true",
        help=(
            "Also evaluate OS-distro components (pkg:deb/apk/alpine/rpm). By "
            "default these are skipped as base-image deps (whole-image SBOM "
            "scoping); set this for the old whole-image behavior."
        ),
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.sbom:
        parser.error("at least one --sbom is required (or use --self-test)")

    findings: list[SbomLicenseFinding] = []
    parse_failed = False
    for sbom_path in args.sbom:
        try:
            findings.extend(
                scan_sbom(
                    Path(sbom_path),
                    repo_license=args.repo_license,
                    unknown_license=args.unknown_license,
                    include_os_packages=args.include_os_packages,
                )
            )
        except ValueError as exc:
            # FAIL-CLOSED: an unparseable supply-chain artifact blocks the gate.
            print(f"::error::SBOM license gate cannot parse artifact: {exc}", file=sys.stderr)
            parse_failed = True

    if findings:
        print(
            f"::error::SBOM license gate: {len(findings)} incompatible license(s) detected "
            f"(NFR-S11 / FR40 — incompatible with {args.repo_license}).",
            file=sys.stderr,
        )
        for f in findings:
            print(
                f"SBOM license incompatibility: {f.sbom} :: "
                f"{f.component}@{f.version} — detected {f.license_detected} "
                f"(incompatible with {f.incompatible_with_repo_license}, "
                f"reason: {f.reason_code})",
                file=sys.stderr,
            )

    if findings or parse_failed:
        return 1

    if args.verbose:
        print(
            f"✓ sbom-licenses OK ({len(args.sbom)} SBOM(s) scanned, 0 incompatibilities, "
            f"repo-license={args.repo_license})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
