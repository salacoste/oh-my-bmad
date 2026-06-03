"""Unit tests for secret_hygiene.license_scan — license policy, single-file
scanner, batch scanner, and CLI entrypoint (Story 6.9, FR40)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from .license_scan import (
    LicenseFinding,
    _reason_code,
    _should_skip,
    is_compatible,
    license_scan_main,
    scan_file_licenses,
    scan_files_for_licenses,
)

# ---------------------------------------------------------------------------
# Helper — patch scancode.api.get_licenses with a mock
# ---------------------------------------------------------------------------


def _patch_scancode(mock_get: MagicMock) -> Any:
    """Return a patch.dict that injects a mock scancode.api.get_licenses."""
    modules = {
        "scancode": MagicMock(),
        "scancode.api": MagicMock(get_licenses=mock_get),
    }
    return patch.dict("sys.modules", modules)


# ---------------------------------------------------------------------------
# License policy model
# ---------------------------------------------------------------------------


class TestIsCompatible:
    """is_compatible returns True for permissive / repo-matching licenses."""

    def test_mit_compatible(self) -> None:
        assert is_compatible("mit")

    def test_apache_compatible(self) -> None:
        assert is_compatible("apache-2.0")

    def test_bsd3_compatible(self) -> None:
        assert is_compatible("bsd-3-clause")

    def test_isc_compatible(self) -> None:
        assert is_compatible("isc")

    def test_gpl_incompatible(self) -> None:
        assert not is_compatible("gpl-2.0")

    def test_agpl_incompatible(self) -> None:
        assert not is_compatible("agpl-3.0")

    def test_lgpl_incompatible(self) -> None:
        assert not is_compatible("lgpl-3.0")

    def test_mpl_2_0_compatible(self) -> None:
        # MPL-2.0 is weak/file-level copyleft, accepted by operator decision
        # (2026-06-03); permissive short-circuit beats the "mpl" fallback.
        assert is_compatible("MPL-2.0")
        assert is_compatible("mpl-2.0")

    def test_generic_mpl_still_incompatible(self) -> None:
        # Generic "mpl" / MPL-1.1 are NOT permissive → still flagged.
        assert not is_compatible("mpl")
        assert not is_compatible("mpl-1.1")

    def test_empty_string_compatible(self) -> None:
        assert is_compatible("")

    def test_repo_license_match_compatible(self) -> None:
        assert is_compatible("MIT", repo_license="MIT")

    def test_case_insensitive(self) -> None:
        assert is_compatible("MIT")
        assert is_compatible("Apache-2.0")

    def test_composite_and_expression_with_gpl(self) -> None:
        assert not is_compatible("mit AND gpl-2.0")

    def test_composite_and_expression_all_permissive(
        self,
    ) -> None:
        assert is_compatible("mit AND apache-2.0")

    def test_custom_repo_license(self) -> None:
        assert is_compatible("gpl-3.0", repo_license="gpl-3.0")

    def test_cecill_b_incompatible(self) -> None:
        assert not is_compatible("cecill-b")

    def test_cecill_c_incompatible(self) -> None:
        assert not is_compatible("cecill-c")

    def test_or_expression_dual_licensed(self) -> None:
        assert is_compatible("mit OR gpl-2.0")

    def test_or_expression_all_copyleft(self) -> None:
        assert not is_compatible("gpl-2.0 OR agpl-3.0")

    # --- G-SEC-1: free-text alias normalization -----------------------------

    def test_apache_freetext_aliases_compatible(self) -> None:
        assert is_compatible("Apache 2.0")
        assert is_compatible("Apache License 2.0")
        assert is_compatible("Apache License, Version 2.0")
        assert is_compatible("Apache Software License")

    def test_mit_license_freetext_compatible(self) -> None:
        assert is_compatible("MIT License")

    def test_isc_license_freetext_compatible(self) -> None:
        assert is_compatible("ISC License")
        assert is_compatible("ISC License (ISCL)")

    def test_psf_aliases_compatible(self) -> None:
        assert is_compatible("Python Software Foundation License")
        assert is_compatible("PSF")

    def test_bsd_freetext_compatible(self) -> None:
        assert is_compatible("BSD License")
        assert is_compatible("BSD")

    # --- G-SEC-1: fail-closed on genuinely-unknown licenses -----------------

    def test_unknown_license_fail_closed(self) -> None:
        # Previously defaulted OPEN; now an unknown license is INCOMPATIBLE.
        assert not is_compatible("Frobnicate-1.0")
        assert not is_compatible("WTFPL")

    def test_unknown_license_with_operator_override(self) -> None:
        assert is_compatible("Frobnicate-1.0", extra_allowed=frozenset({"frobnicate-1.0"}))
        # Override entries are canonicalized → raw mixed-case also works.
        assert is_compatible("Frobnicate-1.0", extra_allowed=frozenset({"Frobnicate-1.0"}))

    def test_override_in_and_expression(self) -> None:
        # An unknown token inside an AND expression passes only with override.
        assert not is_compatible("mit AND frobnicate-1.0")
        assert is_compatible("mit AND frobnicate-1.0", extra_allowed=frozenset({"frobnicate-1.0"}))


class TestReasonCode:
    """_reason_code returns correct categorization."""

    def test_gpl_is_copyleft(self) -> None:
        assert _reason_code("gpl-2.0") == "copyleft-incompatible"

    def test_agpl_is_copyleft(self) -> None:
        assert _reason_code("agpl-3.0") == "copyleft-incompatible"

    def test_proprietary(self) -> None:
        assert _reason_code("proprietary") == "proprietary-incompatible"

    def test_unknown_returns_unknown(self) -> None:
        assert _reason_code("some-weird-license") == "unknown-incompatible"


class TestLicenseFinding:
    """LicenseFinding frozen dataclass."""

    def test_fields(self) -> None:
        f = LicenseFinding(
            file_path="x.py",
            license_detected="gpl-2.0",
            incompatible_with_repo_license="mit",
            reason_code="copyleft-incompatible",
        )
        assert f.file_path == "x.py"
        assert f.license_detected == "gpl-2.0"

    def test_frozen(self) -> None:
        f = LicenseFinding("x", "gpl-2.0", "mit", "copyleft-incompatible")
        with pytest.raises(AttributeError):
            f.file_path = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestShouldSkip:
    """_should_skip identifies files to exclude from license scanning."""

    def test_lock_file_skipped(self) -> None:
        assert _should_skip("uv.lock")

    def test_poetry_lock_skipped(self) -> None:
        assert _should_skip("poetry.lock")

    def test_generic_lock_skipped(self) -> None:
        assert _should_skip("package.lock")

    def test_venv_skipped(self) -> None:
        assert _should_skip(".venv/lib/python/site.py")

    def test_upstream_skipped(self) -> None:
        assert _should_skip("upstream/omc/main.py")

    def test_bmad_skipped(self) -> None:
        assert _should_skip("_bmad/config.yaml")

    def test_bmad_output_skipped(self) -> None:
        assert _should_skip("_bmad-output/planning-artifacts/prd.md")

    def test_binary_png_skipped(self) -> None:
        assert _should_skip("logo.png")

    def test_binary_pyc_skipped(self) -> None:
        assert _should_skip("__pycache__/mod.pyc")

    def test_source_file_not_skipped(self) -> None:
        assert not _should_skip("src/main.py")

    def test_readme_not_skipped(self) -> None:
        assert not _should_skip("README.md")


# ---------------------------------------------------------------------------
# Single-file scanner (mocked scancode)
# ---------------------------------------------------------------------------


class TestScanFileLicenses:
    """scan_file_licenses returns findings for incompatible licenses."""

    def test_gpl_file_flagged(self, tmp_path: Path) -> None:
        gpl = tmp_path / "gpl_code.py"
        gpl.write_text("# GPL licensed code\nx = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "gpl-2.0",
            "detected_license_expression_spdx": "GPL-2.0-only",
            "license_detections": [
                {
                    "license_expression": "gpl-2.0",
                    "matched_rule": {
                        "license_expression": "gpl-2.0",
                    },
                },
            ],
            "license_clues": [],
            "percentage_of_license_text": 50.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(gpl)
        assert len(findings) == 1
        assert findings[0].license_detected == "gpl-2.0"
        assert findings[0].reason_code == "copyleft-incompatible"

    def test_mit_file_passes(self, tmp_path: Path) -> None:
        mit = tmp_path / "mit_code.py"
        mit.write_text("# MIT licensed\nx = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "mit",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 10.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(mit)
        assert findings == []

    def test_no_license_passes(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        mock_result: dict[str, Any] = {
            "detected_license_expression": None,
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 0.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(clean)
        assert findings == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does_not_exist.py"
        assert scan_file_licenses(ghost) == []

    def test_binary_extension_skipped(self, tmp_path: Path) -> None:
        img = tmp_path / "logo.png"
        img.write_bytes(b"\x89PNG\r\n")
        assert scan_file_licenses(img) == []

    def test_import_error_graceful(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        src = tmp_path / "code.py"
        src.write_text("x = 1\n", encoding="utf-8")
        with patch.dict("sys.modules", {}):
            findings = scan_file_licenses(src)
        assert findings == []
        captured = capsys.readouterr()
        assert "scancode-toolkit not installed" in captured.err

    def test_scan_exception_returns_empty(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "code.py"
        src.write_text("x = 1\n", encoding="utf-8")
        mock_get = MagicMock(side_effect=RuntimeError("scan crashed"))
        with _patch_scancode(mock_get):
            findings = scan_file_licenses(src)
        assert findings == []

    def test_agpl_flagged(self, tmp_path: Path) -> None:
        src = tmp_path / "agpl.py"
        src.write_text("# AGPL code\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "agpl-3.0",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 20.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src)
        assert len(findings) == 1
        assert findings[0].reason_code == "copyleft-incompatible"

    def test_apache_passes(self, tmp_path: Path) -> None:
        src = tmp_path / "apache.py"
        src.write_text("# Apache code\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "apache-2.0",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 15.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src)
        assert findings == []

    def test_custom_repo_license(self, tmp_path: Path) -> None:
        src = tmp_path / "code.py"
        src.write_text("x = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "mit",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 10.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src, repo_license="gpl-3.0")
        # MIT is permissive, no copyleft indicators → compatible.
        assert findings == []

    def test_gpl_incompatible_with_repo_mit(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "gpl.py"
        src.write_text("# GPL\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "gpl-3.0",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 80.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src, repo_license="mit")
        assert len(findings) == 1
        assert findings[0].incompatible_with_repo_license == "mit"

    def test_noassertion_passes_as_no_license(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "unclear.py"
        src.write_text("x = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "NOASSERTION",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 0.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src)
        assert findings == []

    def test_none_sentinel_passes_as_no_license(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "none.py"
        src.write_text("x = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "NONE",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 0.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            findings = scan_file_licenses(src)
        assert findings == []


# ---------------------------------------------------------------------------
# Batch scanner
# ---------------------------------------------------------------------------


class TestScanFilesForLicenses:
    """scan_files_for_licenses scans multiple files, respecting exclusions."""

    def test_empty_list_returns_empty(self) -> None:
        assert scan_files_for_licenses([]) == []

    def test_lock_files_skipped(self) -> None:
        assert scan_files_for_licenses(["uv.lock"]) == []

    def test_upstream_skipped(self) -> None:
        assert scan_files_for_licenses(["upstream/omc/README.md"]) == []

    def test_mixed_files(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        gpl = tmp_path / "gpl.py"
        gpl.write_text("# GPL\n", encoding="utf-8")

        def mock_get_licenses(location: str) -> dict[str, Any]:
            if "gpl" in location:
                return {
                    "detected_license_expression": "gpl-2.0",
                    "license_detections": [],
                    "license_clues": [],
                    "percentage_of_license_text": 50.0,
                }
            return {
                "detected_license_expression": "mit",
                "license_detections": [],
                "license_clues": [],
                "percentage_of_license_text": 10.0,
            }

        mock_get = MagicMock(side_effect=mock_get_licenses)
        with _patch_scancode(mock_get):
            findings = scan_files_for_licenses([str(clean), str(gpl)])
        assert len(findings) == 1
        assert "gpl" in findings[0].file_path

    def test_nonexistent_files_skipped(self) -> None:
        findings = scan_files_for_licenses(["/tmp/no_such_file_abc123.py"])
        assert findings == []


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


class TestLicenseScanMain:
    """license_scan_main is the CLI entrypoint."""

    def test_no_files_returns_zero(self) -> None:
        assert license_scan_main([]) == 0

    def test_clean_file_returns_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        mock_result = {
            "detected_license_expression": "mit",
            "license_detections": [],
            "license_clues": [],
            "percentage_of_license_text": 10.0,
        }
        with _patch_scancode(MagicMock(return_value=mock_result)):
            assert license_scan_main([str(clean)]) == 0

    def test_gpl_file_returns_one(
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
            assert license_scan_main([str(gpl)]) == 1
        captured = capsys.readouterr()
        assert "License incompatibility" in captured.err

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
            assert license_scan_main([str(gpl), "--repo-license", "MIT"]) == 1

    def test_missing_scancode_returns_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        src = tmp_path / "code.py"
        src.write_text("x = 1\n", encoding="utf-8")
        with patch.dict("sys.modules", {}):
            assert license_scan_main([str(src)]) == 0
