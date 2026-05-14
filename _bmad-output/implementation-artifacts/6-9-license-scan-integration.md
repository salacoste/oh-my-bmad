# Story 6.9: License-scan integration (scancode-toolkit / ORT) (FR40)

Status: done

## Story

As the platform,
I want `packages/secret-hygiene/src/secret_hygiene/license_scan.py` wrapping `scancode-toolkit` (lightweight mode) and invoked on every agent-generated commit pre-push,
So that license-incompatible snippets (e.g., GPL into this MIT-licensed project) are detected before they ship.

## Acceptance Criteria

1. **Given** a diff introduces a file containing a GPL-licensed snippet
   **When** the license scan runs on that file
   **Then** it returns a structured finding `{file, license_detected, incompatible_with_repo_license, reason_code}`.

2. **And Given** a diff introduces only permissively-licensed content (MIT, Apache-2.0, BSD-3, ISC, 0BSD, Unlicense)
   **When** the scan runs
   **Then** it returns no findings and execution proceeds.

3. **Given** a file with no detectable license information
   **When** the scan runs
   **Then** the scan returns no findings (no license = no violation).

4. **Given** the scan encounters a binary or non-text file
   **When** the scan runs
   **Then** it skips the file gracefully without errors.

5. **Given** the `scancode-toolkit` dependency is unavailable at runtime
   **When** the scan is invoked
   **Then** it logs a warning and returns no findings (non-fatal degradation).

*Cites: FR40, NFR-S8.*

## Tasks / Subtasks

- [x] Task 1 — License policy data model (AC: #1, #2)
  - [x] Create `packages/secret-hygiene/src/secret_hygiene/license_scan.py`
  - [x] Define `LicenseFinding` frozen dataclass: `file_path`, `license_detected`, `incompatible_with_repo_license`, `reason_code`
  - [x] Define `PERMISSIVE_LICENSES: frozenset[str]` — MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, CC0-1.0, Python-2.0, PSF-2.0
  - [x] Define `REPO_LICENSE: str = "MIT"` — the project's declared license
  - [x] Define `is_compatible(detected: str) -> bool` — returns `True` if `detected` is in `PERMISSIVE_LICENSES` or matches the `REPO_LICENSE`
  - [x] Write unit tests in `test_license_scan.py` (policy model tests: ~8 tests)
- [x] Task 2 — Scancode-toolkit integration layer (AC: #1, #2, #3, #4, #5)
  - [x] Implement `scan_file_licenses(path: Path) -> list[LicenseFinding]`:
    - Try importing `scancode.api.get_licenses` — if `ImportError`, log warning and return `[]`
    - Call `get_licenses(location=str(path))` to get license detections
    - For each `license_expression` in detections, check against `is_compatible()`
    - Return `LicenseFinding` for each incompatible license
  - [x] Handle edge cases:
    - Binary files / `UnicodeDecodeError` → skip, return `[]`
    - `FileNotFoundError` → skip, return `[]`
    - `OSError` → log warning, return `[]`
    - No license detected → return `[]` (no violation)
    - `scancode-toolkit` not installed → log warning, return `[]`
  - [x] Add `scancode-toolkit>=32.0.0` as optional dependency in `pyproject.toml` under `[project.optional-dependencies]` group `license-scan`
  - [x] Write unit tests (~12 tests): GPL detection, MIT pass, Apache-2.0 pass, no-license pass, binary skip, missing file, import-error graceful degradation
- [x] Task 3 — Batch scan entrypoint (AC: #1, #2)
  - [x] Implement `scan_files_for_licenses(paths: list[str | Path]) -> list[LicenseFinding]`
  - [x] Iterate over `paths`, call `scan_file_licenses()` for each, accumulate findings
  - [x] Skip files matching exclude patterns (`.lock`, `uv.lock`, `.venv/`, `upstream/`, `_bmad/`, `_bmad-output/`, binary extensions)
  - [x] Write unit tests (~6 tests): batch of mixed files, all-clean, all-flagged, empty list
- [x] Task 4 — CLI entrypoint for manual invocation (AC: #1, #2)
  - [x] Implement `license_scan_main(argv: list[str] | None = None) -> int`
  - [x] Accept positional file paths + optional `--repo-license` flag (default `"MIT"`)
  - [x] Print findings to stderr, exit 1 if any incompatible license found
  - [x] Add `secret-hygiene-license-scan` console script in `pyproject.toml`
  - [x] Write unit tests (~5 tests): clean exit, flagged exit, missing dependency graceful
- [x] Task 5 — Integration / regression
  - [x] All existing tests pass (`pytest packages/secret-hygiene/` — 176+ passed)
  - [x] `ruff check` clean
  - [x] New test count documented in completion notes

## Dev Notes

### Key Insight

This story creates the LICENSE SCAN MODULE ONLY. It does NOT wire it into the approval gate or emit `task.license_flagged` events — that is Story 6.10. This story creates the reusable scanning primitive that Story 6.10 will consume.

### Existing Code to Build On

| File | What it does | What this story adds |
|------|-------------|---------------------|
| `scanner.py` | `SECRET_PATTERNS`, `scan_text()`, `scan_file()` | Not modified — secret scanning stays as-is |
| `path_checks.py` | `Violation` dataclass, sensitive-path / worktree-boundary checks | Not modified |
| `precommit_hook.py` | Content + path check pre-commit entrypoint | Not modified in this story (wire-in is Story 6.10) |
| `sanitizer.py` | structlog processor `redact_secrets()` | Not modified |

### Architecture

```
license_scan.py (NEW):
  1. License policy model — PERMISSIVE_LICENSES, REPO_LICENSE, is_compatible()
  2. scan_file_licenses(path) — scancode-toolkit wrapper for single files
  3. scan_files_for_licenses(paths) — batch scan entrypoint
  4. license_scan_main() — CLI entrypoint for manual invocation

Usage flow (consumed by Story 6.10):
  worker commits → pre-push hook calls scan_files_for_licenses()
  → if findings → emit task.license_flagged, block approval gate
```

### scancode-toolkit API

The `scancode.api` module provides `get_licenses(location)` which returns:

```python
{
    'detected_license_expression': str | None,      # e.g. "gpl-2.0-plus"
    'detected_license_expression_spdx': str | None,  # e.g. "GPL-2.0-or-later"
    'license_detections': [...],                     # detailed detection objects
    'license_clues': [...],                          # lower-confidence matches
    'percentage_of_license_text': float,
}
```

**Important**: The `scancode.api` docs note "this API is unstable and still evolving." Handle `ImportError` gracefully. The CLI (`scancode` command) is the stable interface — if the Python API breaks in a future version, we can fall back to subprocess invocation.

**Import path**: `from scancode.api import get_licenses`

**Example usage**:
```python
result = get_licenses(location="/path/to/file.py")
if result["detected_license_expression"]:
    # Check if compatible
    ...
```

### License Compatibility Policy

This project is **MIT-licensed**. The compatibility policy is:

- **Compatible (permissive)**: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, CC0-1.0, Python-2.0, PSF-2.0, Artistic-2.0, Zlib, MIT-0
- **Incompatible (copyleft/proprietary)**: GPL-2.0, GPL-3.0, AGPL-3.0, LGPL-2.0, LGPL-3.0, MPL-2.0, CPAL-1.0, EUPL-1.2, SSPL-1.0, proprietary licenses
- **Unknown license**: NOT flagged (false-positive risk is too high; defer to operator review)

The `reason_code` field uses: `"copyleft-incompatible"`, `"proprietary-incompatible"`, or `"unknown-incompatible"`.

### Dependency Strategy

`scancode-toolkit` is a **heavy** dependency (~100MB+ with its data files). It should NOT be a hard dependency that breaks `secret-hygiene` if missing.

**Strategy**:
- Add as optional dependency: `[project.optional-dependencies]` with `license-scan = ["scancode-toolkit>=32.0.0"]`
- Import lazily inside `scan_file_licenses()` — catch `ImportError`
- When missing: log warning, return empty findings (graceful degradation per AC #5)
- The pre-commit hook and content scanner work fine without it

### Exclude Patterns

Files/directories to skip during license scanning:
- `.lock` files, `uv.lock`, `poetry.lock`
- `.venv/`, `node_modules/`
- `upstream/` (vendored upstream code has its own licenses tracked in `VENDORED.md`)
- `_bmad/`, `_bmad-output/` (project management artifacts)
- Binary files (images, compiled objects, etc.)
- Test fixture files (test fixtures may intentionally contain license text)

### Structlog Gotcha

Never use `event=` as a keyword argument to structlog loggers — it clashes with the positional `event` parameter.

### Scope Boundary

Do NOT modify:
- `services/worker-wrapper/` (approval gate wiring is Story 6.10)
- `packages/secret-hygiene/src/secret_hygiene/scanner.py`
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py`
- `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py`
- `packages/secret-hygiene/src/secret_hygiene/path_checks.py`
- `.pre-commit-config.yaml` (wire-in is Story 6.10)
- `packages/secret-hygiene/pyproject.toml` `[project.scripts]` (only ADD the new entry, don't change existing)

### Relationship to Other Stories

- **Story 1.7** (secret-scanner-sanitizer): Created the `secret-hygiene` package. This story adds `license_scan.py` to the same package.
- **Story 6.8** (precommit-validation-hook): Added path-based checks to the pre-commit hook. This story is parallel — license scanning is a separate module.
- **Story 6.10** (license-flagged-event): CONSUMES this story's output. Emits `task.license_flagged` event and wires into the approval gate.
- **Story 6.13** (license-scan-integration-test): End-to-end integration test that seeds a GPL file, runs the autonomous-task flow, and asserts the approval gate blocks.

### References

- [Source: _bmad-output/planning-artifacts/prd.md#FR40]
- [Source: _bmad-output/planning-artifacts/epics.md#Epic6-Story6.9]
- [Source: _bmad-output/planning-artifacts/architecture.md#license_scan.py]
- [Source: _bmad-output/planning-artifacts/prd.md#NFR-S8]
- [Source: packages/secret-hygiene/src/secret_hygiene/scanner.py] (pattern to follow)
- [Source: scancode-toolkit API — https://scancode-toolkit.readthedocs.io/en/latest/explanation/scancode-license-detection.html]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- Task 1: Created `license_scan.py` with `LicenseFinding` dataclass, `PERMISSIVE_LICENSES` (15 entries), `COPYLEFT_INDICATORS`, `REPO_LICENSE`, `is_compatible()`, `_reason_code()`. Policy tests: 15 in TestIsCompatible + 4 in TestReasonCode + 2 in TestLicenseFinding = 21 tests
- Task 2: Implemented `scan_file_licenses()` with lazy `scancode.api.get_licenses` import, `ImportError` graceful degradation, binary/missing/exception handling. Added `scancode-toolkit>=32.0.0` as optional dependency. Scanner tests: 11 in TestScanFileLicenses
- Task 3: Implemented `scan_files_for_licenses()` with exclude patterns for lock files, venv, upstream, bmad, binaries. Batch tests: 5 in TestScanFilesForLicenses
- Task 4: Implemented `license_scan_main()` CLI with `--repo-license` flag. Added `secret-hygiene-license-scan` console script. CLI tests: 5 in TestLicenseScanMain
- Task 5: 228 tests pass (52 new), ruff clean
- Added `_patch_scancode()` test helper to avoid long-line violations from repeated `patch.dict` calls

### File List

- `packages/secret-hygiene/src/secret_hygiene/license_scan.py` — CREATE — LicenseFinding dataclass, policy model, scan_file_licenses, scan_files_for_licenses, license_scan_main CLI
- `packages/secret-hygiene/src/secret_hygiene/test_license_scan.py` — CREATE — 52 unit tests (policy, scanner, batch, CLI)
- `packages/secret-hygiene/pyproject.toml` — MODIFY — add [project.optional-dependencies] license-scan, add secret-hygiene-license-scan console script

### Review Findings

- [x] [Review][Patch] CeCILL-B and CeCILL-C are copyleft (LGPL-like / GPL-like), not permissive — removed from PERMISSIVE_LICENSES, added "cecill" to COPYLEFT_INDICATORS [`license_scan.py:48-49`] — fixed
- [x] [Review][Patch] OR license expressions treated as AND — refactored is_compatible to split AND/OR separately; OR passes if any branch is permissive; extracted _token_ok helper [`license_scan.py:86`] — fixed
- [x] [Review][Patch] NOASSERTION/NONE scancode sentinel values silently pass as compatible — now treated as "no license detected" [`license_scan.py:208-209`] — fixed
- [x] [Review][Patch] Indentation inconsistency in second findings.append block — aligned to match first block [`license_scan.py:240`] — fixed
- [x] [Review][Patch] Mutable shared state in _patch_scancode test helper — replaced module-level dict with fresh dict per call [`test_license_scan.py:25-36`] — fixed
- [x] [Review][Defer] Story 6.8 changes mixed into diff — pre-existing scope boundary, not Story 6.9 code — deferred, pre-existing
