"""Unit tests for scripts/check_sbom_licenses.py (NFR-S11 / G1).

Covers:
  - Self-test harness exits 0 on bundled fixtures.
  - Clean SBOMs pass; GPL / dual-license-AND / proprietary-name SBOMs fail.
  - Dual-license OR passes.
  - Unknown-license default warn passes; --unknown-license fail exits 1.
  - Missing + malformed SBOMs both exit 1 (fail-closed).
  - --repo-license gpl-3.0 makes a GPL component pass (policy reuse).

NO ``slow`` marker — must run in the PR-gate ``pytest -m "not slow"`` lane.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_sbom_licenses.py"
FIXTURES = REPO_ROOT / "scripts" / "checks" / "fixtures" / "sbom_licenses"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_sbom_licenses", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_sbom_licenses"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_clean_permissive_passes() -> None:
    mod = _load_module()
    rc = mod.main(["--sbom", str(FIXTURES / "clean" / "permissive.cyclonedx.json")])  # type: ignore[attr-defined]
    assert rc == 0


def test_gpl_fails(capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_module()
    rc = mod.main(["--sbom", str(FIXTURES / "violations" / "gpl.cyclonedx.json")])  # type: ignore[attr-defined]
    assert rc == 1
    err = capsys.readouterr().err
    assert "copyleft-incompatible" in err


def test_dual_or_passes_and_dual_with_gpl_fails(tmp_path: Path) -> None:
    mod = _load_module()

    ok = tmp_path / "or.cyclonedx.json"
    ok.write_text(
        '{"components":[{"name":"x","version":"1",'
        '"licenses":[{"expression":"(MIT OR Apache-2.0)"}]}]}'
    )
    assert mod.main(["--sbom", str(ok)]) == 0  # type: ignore[attr-defined]

    bad = tmp_path / "and.cyclonedx.json"
    bad.write_text(
        '{"components":[{"name":"y","version":"1",'
        '"licenses":[{"expression":"(MIT AND GPL-2.0)"}]}]}'
    )
    assert mod.main(["--sbom", str(bad)]) == 1  # type: ignore[attr-defined]


def test_proprietary_name_fails(capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_module()
    rc = mod.main(  # type: ignore[attr-defined]
        ["--sbom", str(FIXTURES / "violations" / "proprietary_name.cyclonedx.json")]
    )
    assert rc == 1
    assert "proprietary-incompatible" in capsys.readouterr().err


def test_unknown_license_warn_default_passes_fail_blocks() -> None:
    mod = _load_module()
    no_license = FIXTURES / "clean" / "no_license.cyclonedx.json"
    assert mod.main(["--sbom", str(no_license)]) == 0  # type: ignore[attr-defined]
    assert (
        mod.main(["--sbom", str(no_license), "--unknown-license", "fail"]) == 1  # type: ignore[attr-defined]
    )


def test_missing_sbom_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    rc = mod.main(["--sbom", str(tmp_path / "does-not-exist.json")])  # type: ignore[attr-defined]
    assert rc == 1


def test_malformed_json_fails_closed() -> None:
    mod = _load_module()
    rc = mod.main(["--sbom", str(FIXTURES / "violations" / "malformed.cyclonedx.json")])  # type: ignore[attr-defined]
    assert rc == 1


def test_repo_license_override_makes_gpl_pass() -> None:
    mod = _load_module()
    rc = mod.main(  # type: ignore[attr-defined]
        [
            "--sbom",
            str(FIXTURES / "violations" / "gpl.cyclonedx.json"),
            "--repo-license",
            "gpl-3.0",
        ]
    )
    assert rc == 0


def test_multiple_licenses_per_component_flags_the_incompatible_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A component declaring several licenses[] entries: each is evaluated
    independently, so a forbidden one (GPL-3.0) alongside a permissive one
    (MIT) must still fire (code-review G1 coverage gap)."""
    mod = _load_module()
    rc = mod.main(  # type: ignore[attr-defined]
        ["--sbom", str(FIXTURES / "violations" / "multi_license.cyclonedx.json")]
    )
    assert rc == 1
    assert "copyleft-incompatible" in capsys.readouterr().err


def test_empty_components_fails_closed() -> None:
    """An SBOM with an empty/missing components array must fail-closed —
    the gate cannot inspect any dependency, so it must NOT pass
    (security-review G1 MEDIUM)."""
    mod = _load_module()
    rc = mod.main(  # type: ignore[attr-defined]
        ["--sbom", str(FIXTURES / "violations" / "empty_components.cyclonedx.json")]
    )
    assert rc == 1
