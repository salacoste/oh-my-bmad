# Story 2.12: Write-interrupt harness + atomic-edit verification

Status: review

## Story

As **the CI pipeline (and operators verifying NFR-R2 / FR30)**,
I want **(a) an atomic-edit primitive in `services/worker-wrapper/` that writes to a tmpfile, fsyncs, then atomically renames into place, and (b) a write-interrupt harness that runs the primitive in a subprocess, kills the subprocess at 100 randomized byte offsets mid-write, and asserts the target file is either fully pre-edit or fully post-edit — never partial**,
so that **FR30 ("Worker can perform file edits atomically such that a mid-write host interruption leaves the filesystem in a consistent state on resume") is testable deterministically and continuously verified by CI rather than asserted via belief**.

## Acceptance Criteria

1. **AC-1: Atomic-edit primitive** — new file `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py`. Exports:

   ```python
   def atomic_write_bytes(
       target: Path,
       data: bytes,
       *,
       fsync_data: bool = True,
       fsync_dir: bool = True,
   ) -> None:
       """Atomically replace *target* with *data*.

       Writes *data* to a sibling tmpfile (``target.suffix + ".tmp.<pid>.<rand>"``),
       optionally fsyncs the file then renames atomically into *target* via
       :func:`os.replace` (POSIX guarantees same-fs rename is atomic).
       Optionally fsyncs the parent directory after the rename so the
       directory entry survives a host crash (POSIX rename's atomicity
       guarantees the entry is consistent, but not that it's durable).

       Raises:
           ValueError: if *target* has no parent directory.
           OSError: if tmpfile creation fails (e.g., disk full, permission).
       """
   ```

   And convenience:
   ```python
   def atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8", **kwargs) -> None:
       """Encode *text* and call :func:`atomic_write_bytes`."""
   ```

2. **AC-2: Tmpfile naming + cleanup contract** — tmpfile name format: `<target.name>.tmp.<pid>.<8-hex-random>` (sibling of *target*). On any exception during write/fsync/rename, the partial tmpfile MUST be unlinked via `tmp.unlink(missing_ok=True)` in a `try/except/finally` (best-effort; OSError on unlink is logged at WARNING and swallowed).

   **Stale-tmpfile recovery** — `atomic_write_bytes` does NOT scavenge orphan `*.tmp.*.*` siblings from prior crashed runs (that's a separate sweeper; out of scope here). Documented in the docstring.

3. **AC-3: Cross-filesystem detection** — if `os.replace(tmp, target)` would cross filesystems (uncommon, but possible if `target`'s parent is a bind-mount), `os.replace` raises `OSError(EXDEV)`. The primitive MUST catch this specifically and re-raise with a clear message: `OSError: cross-filesystem atomic_write_bytes is unsafe — tmpfile path={tmp}, target={target}`. Document that the caller is responsible for choosing a tmpfile path on the same filesystem (currently always sibling — guaranteed same-fs).

4. **AC-4: fsync semantics** — `fsync_data=True` (default): after the write loop completes, call `os.fsync(fd)` BEFORE closing. `fsync_dir=True` (default): after `os.replace`, open the parent directory, `os.fsync(dir_fd)`, close. Both flags can be disabled for performance-critical callers (e.g., tests writing thousands of small files), but the harness uses defaults.

5. **AC-5: `services/worker-wrapper/src/worker_wrapper/domain/__init__.py`** + `services/worker-wrapper/src/worker_wrapper/__init__.py` — re-export `atomic_write_bytes` and `atomic_write_text`. Add `domain/` directory if it doesn't exist (with empty `__init__.py`).

6. **AC-6: `services/worker-wrapper/pyproject.toml`** — version bumped `0.1.0 → 0.2.0`. No new third-party deps (atomic_edit uses stdlib only: `os`, `pathlib`, `secrets`, `logging`).

7. **AC-7: Co-located unit tests** in `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py` — target 12-15 tests:

   **TestAtomicWriteBytes** (~7):
   - `test_atomic_write_bytes_creates_target_with_correct_content`
   - `test_atomic_write_bytes_overwrites_existing_target`
   - `test_atomic_write_bytes_preserves_original_on_disk_full` — uses a small filesystem (mock via `os.write` raising `OSError(ENOSPC)`); asserts target is unchanged AND tmpfile is cleaned up.
   - `test_atomic_write_bytes_cleans_up_tmpfile_on_write_error`
   - `test_atomic_write_bytes_cleans_up_tmpfile_on_fsync_error`
   - `test_atomic_write_bytes_raises_on_no_parent_directory` — `target = Path("/")` or similar.
   - `test_atomic_write_bytes_handles_large_payload` — 10 MB write completes in single call.

   **TestAtomicWriteText** (~2):
   - `test_atomic_write_text_default_utf8_encoding`
   - `test_atomic_write_text_custom_encoding`

   **TestFsyncSemantics** (~3):
   - `test_atomic_write_bytes_fsync_data_disabled_skips_fsync` — patch `os.fsync`; assert NOT called when `fsync_data=False`.
   - `test_atomic_write_bytes_fsync_dir_disabled_skips_dir_fsync` — same pattern.
   - `test_atomic_write_bytes_default_fsync_data_and_dir_called` — both `os.fsync` calls observed.

   **TestCrossFilesystemDetection** (~1):
   - `test_atomic_write_bytes_raises_clear_error_on_exdev` — patch `os.replace` to raise `OSError(EXDEV)`; assert the re-raised message includes both paths.

8. **AC-8: Write-interrupt harness driver** — new file `tests/crash-injection/_atomic_edit_runner.py`. This is a STANDALONE Python script (not a test — it's the SUBPROCESS the harness spawns):

   ```python
   """Standalone subprocess driver for the write-interrupt harness.

   Reads CLI args:
     --target PATH                  — file to edit
     --final-content PATH           — file containing the desired final bytes
     --kill-after-bytes N           — call os._exit(137) after writing exactly N
                                      bytes to the tmpfile (0 ≤ N < len(data))

   Exits cleanly (0) only if not interrupted (i.e., kill_after_bytes >= len(data)).
   """
   ```

   The driver imports `atomic_write_bytes` from `worker_wrapper.domain.atomic_edit` and monkey-patches the module's internal `_chunked_write` (or equivalent) to track bytes-written and `os._exit(137)` after the threshold. **Do NOT use Python's signal handlers** — `os._exit` bypasses atexit handlers + buffer flushes, simulating a true SIGKILL.

   Implementation hint: define `_chunked_write(fd, data)` as a module-level helper inside `atomic_edit.py` (called by `atomic_write_bytes`); the test driver wraps it via `unittest.mock.patch` BEFORE calling `atomic_write_bytes`.

9. **AC-9: Write-interrupt harness pytest module** — new file `tests/crash-injection/test_write_interrupt.py`:

   - **`test_atomic_edit_unmolested_completes_normally`** — runs the driver subprocess WITHOUT interruption (kill_after_bytes >= len(data)); asserts `target` contains the full final content.
   - **`test_atomic_edit_interrupted_at_zero_bytes_preserves_original`** — kill_after_bytes=0; asserts `target` is byte-identical to the pre-edit content.
   - **`test_atomic_edit_interrupted_mid_write_target_unchanged`** — kill_after_bytes=N where 0 < N < len(data); asserts `target` is byte-identical to pre-edit (the `os.replace` never happened).
   - **`test_atomic_edit_100_randomized_interruption_points`** — the **AC-headline test**:
     - Generate 100 random `kill_after_bytes` values in `[0, len(data))`.
     - For each, spawn a fresh driver subprocess that interrupts at exactly that byte.
     - Verify: `target` content hash matches **EITHER** the pre-edit hash **OR** the post-edit hash. **NEVER** any other value (no partial states).
     - Track (pre-edit hash count, post-edit hash count) — both should sum to 100. Print to test output for visibility (e.g., "98 pre-edit, 2 post-edit" — the 2 came from N close to len(data) where the kill landed during/after the rename).
     - All 100 must satisfy the invariant; ANY mixed-byte-sequence fails the test.

10. **AC-10: Pre/post hash invariants** — test fixtures:
    - `pre_edit_content = b"original\n"` (or larger — whatever the test uses).
    - `post_edit_content = b"the new contents go here\n" * 50` (longer, gives meaningful `kill_after_bytes` range).
    - Use `hashlib.sha256` for hash comparison.
    - Use `subprocess.run(..., timeout=5.0, check=False)` with explicit non-zero exit codes (the driver's `os._exit(137)` should produce returncode=137 on POSIX; document this).

11. **AC-11: Marking + skip behavior** — All tests in `test_write_interrupt.py` marked `@pytest.mark.crash @pytest.mark.slow`. Run via `just test-crash` (no Docker required for this story — pure subprocess + filesystem). The `_skip_if_no_docker` fixture from `tests/crash-injection/conftest.py` (Story 2.11) MUST NOT apply to these tests — they don't need Docker. Implementation: the existing fixture is autouse-via-leading-underscore-to-positional-arg pattern; this story can either (a) make the fixture explicitly opt-in (parameter-style) so write-interrupt tests don't include it, or (b) refactor the existing fixture to be `autouse=True` but check a per-module `_REQUIRES_DOCKER` flag and skip its skip when False. Option (a) is simpler — refactor the Story 2.11 docker fixture to require explicit parameter; only the Story 2.11 tests opt in.

   **Choice for this story**: Option (a). Refactor `_skip_if_no_docker` to non-autouse fixture; Story 2.11's 4 tests gain an explicit `_skip_if_no_docker` parameter; Story 2.12's tests don't.

12. **AC-12: macOS + Linux compatibility** — `atomic_write_bytes` uses POSIX-only primitives (`os.fsync`, `os.replace`, `os.open` with `O_WRONLY|O_CREAT|O_EXCL`). All work identically on macOS and Linux. Document in module docstring that Windows support is out of scope (Phase 1 is POSIX-only per FR48 / Architecture line 200 base-image decision).

13. **AC-13: 100-iteration test runtime budget** — each iteration spawns a subprocess (~50-100ms cold), writes ~1KB, asserts. Budget: 100 × 100ms = 10s wall-clock. With pytest overhead, target ≤30s for the 100-iteration test. If wall-clock exceeds 30s on CI, parallelize via `concurrent.futures.ThreadPoolExecutor(max_workers=8)` (subprocess execution is I/O-bound; threading is fine).

14. **AC-14: mypy --strict clean** — `atomic_edit.py`, `test_atomic_edit.py`, `_atomic_edit_runner.py`, `test_write_interrupt.py` all pass `mypy --strict`. The crash-injection mypy invocation (Story 2.11 added) covers the new test module.

15. **AC-15: lint recipe extended** — `lint` recipe must mypy-strict-check `services/worker-wrapper`. Currently `lint` runs `mypy --strict packages/ services/registry-api services/registry-state` — append `services/worker-wrapper`. Verify worker-wrapper currently has nothing that breaks strict mode (it's effectively empty besides `__main__.py` scaffold).

16. **AC-16: nightly CI** — extend `.github/workflows/nightly.yml`'s `crash-injection` job to also pick up the write-interrupt tests (already covered — `just test-crash` runs `pytest -m crash`, which includes both Story 2.11 and Story 2.12 tests).

17. **AC-17: Regression** — `just test` count grows from **476 passed, 5 skipped** to ≥**488 passed, 5 skipped** (12 new co-located unit tests in `test_atomic_edit.py`). The 4-5 new `tests/crash-injection/` slow tests do NOT run in `just test` (they're `@pytest.mark.slow`). `just test-crash` count grows from 4 to ≥9 (4 from Story 2.11 + 5 from Story 2.12). `just lint` 8/8 green. `just bootstrap-verify` shows `worker_wrapper 0.2.0`.

18. **AC-18: Atomic commit** titled `feat(worker-wrapper): story 2.12 — atomic-edit primitive + write-interrupt harness · FR30 NFR-R2`.

## Tasks / Subtasks

- [x] **Task 1: Atomic-edit primitive** (AC: #1, #2, #3, #4, #5, #6)
  - [x] Create `services/worker-wrapper/src/worker_wrapper/domain/__init__.py` + `domain/atomic_edit.py`.
  - [x] Implement `atomic_write_bytes` + `atomic_write_text` per signatures above.
  - [x] Module docstring covers stdlib-only design + POSIX-only scope + tmpfile-cleanup contract.
  - [x] `_chunked_write(fd, data)` module-level helper (so the test driver can monkey-patch it).
  - [x] Update `worker_wrapper/__init__.py` re-exports + `__all__`.
  - [x] Bump `pyproject.toml` to 0.2.0; `uv sync --all-groups`.

- [x] **Task 2: Co-located unit tests** (AC: #7)
  - [x] Create `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py`.
  - [x] 4 test classes per AC-7. Use `tmp_path` for filesystem isolation.
  - [x] Mock `os.fsync` / `os.replace` for the targeted tests (fsync semantics, EXDEV).
  - [x] Disk-full simulation: `monkeypatch.setattr(os, "write", side_effect=OSError(ENOSPC, ...))`.

- [x] **Task 3: Write-interrupt harness driver subprocess** (AC: #8, #10, #12)
  - [x] Create `tests/crash-injection/_atomic_edit_runner.py` with argparse + monkey-patched `_chunked_write`.
  - [x] Driver uses `os._exit(137)` (NOT `sys.exit`) to bypass atexit + buffer flushes.
  - [x] Add `if __name__ == "__main__":` block so the driver runs as `python -m tests.crash_injection._atomic_edit_runner` OR direct script invocation.

- [x] **Task 4: Write-interrupt harness pytest module** (AC: #9, #11, #13)
  - [x] Create `tests/crash-injection/test_write_interrupt.py` with 4 test functions per AC-9.
  - [x] Refactor `_skip_if_no_docker` in `tests/crash-injection/conftest.py` from leading-underscore-positional to explicit-opt-in parameter.
  - [x] Update Story 2.11's 4 test functions to accept `skip_if_no_docker` parameter (rename + drop underscore).
  - [x] 100-iteration test uses seeded `random.Random(seed=2_12_42)` for reproducibility.

- [x] **Task 5: justfile + lint recipe + nightly CI** (AC: #15, #16)
  - [x] Extend `lint` recipe's `mypy --strict ...` line to include `services/worker-wrapper`.
  - [x] Verify `nightly.yml`'s `crash-injection` job picks up the new tests via `pytest -m crash`. No workflow YAML change expected.

- [x] **Task 6: Regression + atomic commit** (AC: #14, #17, #18)
  - [x] `just test` ≥ **488 passed, 5 skipped**.
  - [x] `just lint` 8/8 green; mypy strict scope grows to include worker-wrapper.
  - [x] `just test-crash` passes (4 Story 2.11 + 5 Story 2.12 = 9 tests, ≤45s total).
  - [x] `just bootstrap-verify` shows `worker_wrapper 0.2.0`.
  - [x] `just check-gates-self-test` 3/3.
  - [x] Single atomic commit per AC-18.

### Review Findings

Three parallel adversarial reviewers ran post-implementation: **Acceptance Auditor** (verdict ACCEPT, 5 MINOR), **Blind Hunter** (4 nominal CRITICAL — all downgraded to MINOR by Edge Case Hunter cross-validation), and **Edge Case Hunter** (boundary-walk; informational and audit findings). All MAJOR + MINOR items below were applied. DEFER items are documented in `### Spec Amendments (from code review)`.

**MAJOR**

- [x] **[Review][Patch] dir-fsync failure post-rename leaves silent target-replaced inconsistency** [`atomic_edit.py:_fsync_dir block`] — **MAJOR.** If `os.replace` succeeds but `os.fsync(dir_fd)` raises, target is already replaced; raising would mislead callers into double-write retries. Fix: wrap the dir-fsync in try/except OSError that LOGS at WARNING and SUPPRESSES (data is already on disk; the next inode flush repairs durability). Updated docstring's "Raises" section. (M1)
- [x] **[Review][Patch] POST_EDIT_CONTENT only 1250 bytes — 64KB chunk size never exercised** [`test_write_interrupt.py:_POST_EDIT_CONTENT`] — **MAJOR.** All randomized kill points fell in the first production chunk. Fix: widened to `b"the new contents go here\n" * (50 * 80)` = 100,000 bytes (~1.5 chunks @ 64KB), so kill offsets cross chunk boundaries inside the production loop. Test runtime still under 30s budget (~6s). (M2)
- [x] **[Review][Patch] 100-iteration test always uses kill_after < n_total — post_count structurally always 0** [`test_write_interrupt.py:test_atomic_edit_100_randomized_interruption_points`] — **MAJOR.** Fix: widened randrange to `[0, n_total * 2)` so ~50% of trials land past `n_total` and exercise the no-interrupt rename path. Assertion accepts `proc.returncode in (0, 137)` and dispatches accordingly. Diagnostic print reports meaningful counts. Added `assert pre_count > 0 and post_count > 0`. Post-fix outcome: **47 pre-edit, 53 post-edit, 0 partial**. (M3)
- [x] **[Review][Patch] secrets.token_hex(4) — 32 bits; birthday collision at ~65k concurrent writers** [`atomic_edit.py:tmp_name`] — **MAJOR.** Fix: bumped to `token_hex(8)` (64 bits). Updated docstring + comment to "16-hex-random". (M4)
- [x] **[Review][Patch] monkeypatch.setattr(os, "fsync", ...) GLOBAL patch in TestFsyncSemantics** [`test_atomic_edit.py:TestFsyncSemantics`] — **MAJOR.** Pytest plugins / capture machinery may invoke `os.fsync` between patch and call, polluting `calls` count. Fix: recorder filters by *known fds* — also patches `os.open` to register every fd opened during atomic_write_bytes; `_record` only counts those fds. (M5)
- [x] **[Review][Patch] monkeypatch.setattr(os, "replace", ...) same global-patch hazard** [`test_atomic_edit.py:test_atomic_write_bytes_raises_clear_error_on_exdev`] — **MAJOR.** Fix: `_replace` now filters by `dst == target` so unrelated renames pass through to the real implementation. (M6)
- [x] **[Review][Patch] Subprocess env hygiene: missing HOME/TMPDIR/LANG/LC_ALL; PYTHONPATH overrides instead of prepending** [`test_write_interrupt.py:_spawn_runner`] — **MAJOR.** Fix: env now inherits `os.environ.copy()`, prepends PYTHONPATH (composes with any inherited value), preserves `PYTHONDONTWRITEBYTECODE`. CI portability over hermeticity, documented in module docstring. (M7)
- [x] **[Review][Patch] Driver runner manual-invocation requires PYTHONPATH** [`_atomic_edit_runner.py`] — **MAJOR.** Fix: added `sys.path.insert(0, ...)` for `services/worker-wrapper/src` BEFORE the import; manual `python _atomic_edit_runner.py ...` now works without env wrangling. (M8)
- [x] **[Review][Patch] atomic_write_text doesn't expose errors= kwarg** [`atomic_edit.py:atomic_write_text`] — **MAJOR.** Fix: added `errors: str = "strict"` parameter; forwarded to `text.encode(encoding, errors)`. Documented in docstring. New tests cover `errors="replace"` and `errors="strict"` raising. (M9)
- [x] **[Review][Patch] atomic_write_bytes resets file mode to 0o600 on every call** [`atomic_edit.py:os.open mode`] — **MAJOR.** Fix: capture `os.stat(target).st_mode & 0o7777` pre-write (None if target absent); after `os.replace`, best-effort `os.chmod` to restore. Failure logged at WARNING. New tests verify 0o644 preservation and 0o600 default for new targets. (M10)
- [x] **[Review][Patch] Path("") boundary slips through validation** [`atomic_edit.py:parent check`] — **MAJOR.** `Path("").parent == Path(".")` ≠ `Path("")` so the parent==self guard misses empty path. Fix: added `if not target.name: raise ValueError(...)` before the parent check. New test covers it. (M11)
- [x] **[Review][Patch] atomic_write_bytes / atomic_write_text accept only Path** [`atomic_edit.py`] — **MAJOR.** Fix: signatures now accept `Path | str`; coerce via `Path(target)` at function entry. New tests cover str-target. (M12)
- [x] **[Review][Patch] No test for _chunked_write written == 0 defensive guard** [`test_atomic_edit.py`] — **MAJOR.** Fix: added test that monkeypatches `os.write` to return 0 and asserts OSError propagates. Removed `# pragma: no cover` from the guard. (M13)
- [x] **[Review][Patch] No test for tmp.unlink cleanup OSError logging** [`test_atomic_edit.py`] — **MAJOR.** Fix: added test that monkeypatches `Path.unlink` to raise `OSError(EACCES)`; asserts (a) the original exception still propagates, (b) cleanup OSError is logged via `caplog`. (M14)
- [x] **[Review][Patch] No test for O_EXCL collision behavior** [`test_atomic_edit.py`] — **MAJOR.** Fix: added test that monkeypatches `secrets.token_hex` and `os.getpid` to return known values, pre-creates the colliding tmpfile, asserts `FileExistsError` raised AND the pre-existing tmpfile is uncorrupted. (M15)
- [x] **[Review][Patch] __init__.py re-exports run BEFORE __version__ assignment** [`worker_wrapper/__init__.py`] — **MAJOR.** Fix: moved `__version__ = "0.2.0"` to BEFORE the re-export so a sub-module importing `worker_wrapper.__version__` during its own import sees a fully-initialized attribute. (M16)
- [x] **[Review][Patch] .tmp. glob is overly broad** [`test_atomic_edit.py`] — **MAJOR.** Would match e.g. `something.tmp.txt`. Fix: extracted `_TMP_NAME_RE = re.compile(r"\.tmp\.\d+\.[0-9a-f]{16}$")` helper used by every cleanup-leak assertion. (M17)
- [x] **[Review][Patch] justfile lacks Docker-vs-no-Docker comment for test-crash** [`justfile:test-crash`] — **MAJOR.** Fix: added a "Docker dependency split" block clarifying Story 2.11 tests skip without Docker; Story 2.12 tests run unconditionally. (M18)
- [x] **[Review][Patch] closed = False flag is dead code outside inner finally** [`atomic_edit.py:atomic_write_bytes`] — **MAJOR.** Fix: simplified to plain `try: ... finally: os.close(fd)` (Python's finally guarantees close runs even if write/fsync raises). Dropped the `closed` flag entirely. (M19)
- [x] **[Review][Patch] Docstring doesn't note macOS APFS fsync(dir_fd) is weaker than Linux ext4** [`atomic_edit.py` module docstring] — **MAJOR.** Fix: added "Platform durability notes" section noting macOS APFS provides best-effort directory-entry durability via `fsync(dir_fd)`; Linux ext4/xfs provides the strict guarantee. Production deploys on Linux containers (per FR48). (M20)

**MINOR**

- [x] **[Review][Patch] Dead `if TYPE_CHECKING: pass` block** [`test_atomic_edit.py`] — **MINOR.** Removed. (Mn1)
- [x] **[Review][Patch] argparse description=__doc__ shows full module docstring as --help** [`_atomic_edit_runner.py:argparse`] — **MINOR.** Fix: brief one-liner — "Write-interrupt harness driver — interrupts atomic_write_bytes after N bytes via os._exit(137)." (Mn2)
- [x] **[Review][Patch] _RUNNER_PATH not asserted at import time** [`test_write_interrupt.py:_RUNNER_PATH`] — **MINOR.** Fix: added `assert _RUNNER_PATH.exists(), ...` at module top so a missing/renamed script fails fast. (Mn3)
- [x] **[Review][Patch] subprocess.run lacks encoding/errors for stable cross-locale capture** [`test_write_interrupt.py:_spawn_runner`] — **MINOR.** Fix: added `encoding="utf-8", errors="replace"`. (Mn4)
- [x] **[Review][Patch] Comment why module-scoped patching is required** [`test_atomic_edit.py:fsync semantics tests`] — **MINOR.** Fix: added a class-header docstring explaining the recorder filters by *known fds* to prevent pytest-internal `os.fsync` calls from polluting counts. (Mn5)
- [x] **[Review][Patch] Per-trial timeout 10s → 5s** [`test_write_interrupt.py:_spawn_runner`] — **MINOR.** Fix: lowered default `timeout_s` to 5.0. (Mn6)
- [x] **[Review][Patch] errno.errorcode in cleanup warning for legibility** [`atomic_edit.py:logger.warning`] — **MINOR.** Fix: added `errno.errorcode.get(exc.errno, "?")` to both cleanup-failed and dir-fsync-failed warnings. (Mn14)
- [x] **[Review][Patch] Clearer error when target.parent doesn't exist** [`atomic_edit.py:atomic_write_bytes`] — **MINOR.** Fix: catch `FileNotFoundError` from `os.open(tmp, ...)`; re-raise with `f"target's parent directory does not exist: {parent}"`. New test covers it. (Mn17)
- [x] **[Review][Patch] Symlink target — verify os.replace replaces link not target** [`test_atomic_edit.py`] — **MINOR.** Fix: new test verifies the link is replaced by a regular file; the original link target is untouched. (Mn8)
- [x] **[Review][Patch] Test for fsync-then-close-raises path on dir_fd** [`test_atomic_edit.py`] — **MINOR.** Subsumed by M1's dir-fsync-error test (which covers the dir-fsync-fails path; the close-fails-after-fsync sub-path would be additional defensive coverage but adds little — the close-fd is wrapped in finally already). (Mn21)
- [x] **[Review][Patch] Docstring clarification: ValueError raised BEFORE any I/O** [`atomic_edit.py:atomic_write_bytes` docstring] — **MINOR.** Fix: added "Validation runs BEFORE any I/O" sentence. (Mn23)
- [x] **[Review][Patch] Mark large-payload test @pytest.mark.slow** [`test_atomic_edit.py:test_atomic_write_bytes_handles_large_payload`] — **MINOR.** Fix: added `@pytest.mark.slow` so the 10 MB write opts out of `just test`. (Mn24)
- [x] **[Review][Patch] Comment: os._exit(137) is a clean exit not real SIGKILL** [`_atomic_edit_runner.py` docstring] — **MINOR.** Fix: added a parenthetical note that this is the closest deterministic approximation of SIGKILL, not a real one (Blind Hunter dismissal). (BHM-comment-fix)

**DEFER / DISMISS** (documented in Spec Amendments, no patch)

- Acceptance Auditor: `atomic_write_text` `**kwargs` vs explicit kwargs — explicit is the better choice; spec note added.
- Acceptance Auditor: `domain/__init__.py` "empty" wording — spec contradiction with re-export requirement; spec note added.
- Blind Hunter: `secrets.token_hex` blocking on entropy-starved systems — extremely rare in container deploys; skip.
- Blind Hunter: ambiguous import paths from re-exports — pick canonical path is overkill; skip.
- Edge Case INFO/AUDIT items — verifications confirmed by other patches.
- Mn7 / Mn9 / Mn10 / Mn11 / Mn16 / Mn19 — documented in story task list as skip.

## Dev Notes

### Architecture context

- **`services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py`** is explicitly named in Architecture line 694.
- **NFR-R2 Phase 1 commitment** (Architecture line 268): "write-interrupt harness" is one of the mandatory Phase 1 test infrastructure pieces.
- **FR30** (PRD): "Worker can perform file edits atomically such that a mid-write host interruption leaves the filesystem in a consistent state on resume."
- **Risk register** (PRD): "Write-interrupt harness ... Hardest single test case; must be in Phase 1 test infra."

### Why the atomic-edit primitive lives in `services/worker-wrapper/`

Architecture line 694 places it there. The epic's wording ("from `packages/`") is informal — at the time of epic drafting the architecture decision was still pending. Architecture is the authoritative source. If a future story (Story 5.18 / Journey 1 integration / Phase 2 refactor) discovers that other components need atomic-edit (e.g., registry-state for snapshot-file writes), THEN extract to `packages/atomic-edit/`. Don't pre-extract — YAGNI.

### Why `os._exit(137)` not `sys.exit` or `signal.SIGKILL`

`os._exit` bypasses Python's atexit handlers AND C-stdlib buffer flushes — it's the closest in-process simulation of an external SIGKILL. Real SIGKILL via `os.kill(os.getpid(), signal.SIGKILL)` would also work but adds racy timing (the signal is delivered asynchronously; the next instruction may or may not execute first). `os._exit(N)` is synchronous and deterministic — the very next call after writing N bytes terminates the process.

### Why monkey-patch `_chunked_write` (not `os.write` directly)

`os.write` is called by many code paths (Python's I/O layer, third-party deps); patching it globally is fragile. Patching the atomic_edit module's named `_chunked_write` helper is surgical and only affects the function under test. Test driver pseudocode:

```python
from worker_wrapper.domain import atomic_edit

KILL_AFTER_BYTES: int  # from CLI

def _kill_after_chunked_write(fd: int, data: bytes) -> None:
    pos = 0
    while pos < len(data):
        chunk_size = min(64, len(data) - pos)  # small chunk so we can interrupt precisely
        if pos + chunk_size > KILL_AFTER_BYTES:
            chunk_size = KILL_AFTER_BYTES - pos
            os.write(fd, data[pos:pos + chunk_size])
            os._exit(137)  # bypasses everything
        os.write(fd, data[pos:pos + chunk_size])
        pos += chunk_size

atomic_edit._chunked_write = _kill_after_chunked_write
atomic_edit.atomic_write_bytes(target, data)  # now interrupts mid-write
```

The harness imports the runner, sets `KILL_AFTER_BYTES`, calls `atomic_write_bytes`, and the patched helper terminates the process at the specified byte.

### Why this harness does NOT need Docker

Unlike Story 2.11 (which needed compose to test container restart), Story 2.12 tests **filesystem-level atomicity**. The "host crash" semantics POSIX guarantees apply equally to subprocess termination — `os.replace` is atomic at the syscall level; killing the process between `write` and `os.replace` leaves only the tmpfile (target unchanged). Docker is not needed; pure subprocess + filesystem is sufficient.

### Why we should NOT scavenge orphan tmpfiles in the primitive

A naive "scavenge orphans on next write" implementation introduces concurrency bugs (which orphan from which crashed run? what if the orphan is a current tmp from another concurrent writer?). The right design: orphan-sweeping is a separate operation, run by a startup hook or a periodic cron. Out of scope for Story 2.12. The unit tests verify per-call cleanup-on-error; orphans only persist if the process is hard-killed mid-write.

### Why fsync the directory

Standard POSIX rename atomicity caveat: `rename()` is atomic but its persistence is not guaranteed without fsyncing the parent directory. After `os.replace(tmp, target)`, the directory entry update is in the kernel page cache; a crash before page-cache flush loses the rename. Production atomic-edit primitives fsync the parent directory after rename. Default `fsync_dir=True`; tests can override.

### Cross-fs (EXDEV) semantics

`os.replace(tmp, target)` raises `OSError(errno=EXDEV)` if `tmp` and `target` are on different filesystems. The primitive places `tmp` as a sibling of `target` (same parent directory → same fs by definition for ordinary directories). The EXDEV check is defense-in-depth for unusual mount configurations (e.g., bind-mount overlays, FUSE filesystems). The error message must include both paths so the operator can diagnose.

### What this story does NOT do

- **Does NOT implement orphan-tmpfile sweeping.** Out of scope; separate story if needed.
- **Does NOT implement journaled atomic edits** (write-ahead logs for multi-file atomic operations). FR30's claim is per-file atomicity; multi-file atomicity is a different problem.
- **Does NOT implement Windows support.** POSIX-only per FR48.
- **Does NOT implement concurrent-writer locks.** If two callers `atomic_write_bytes(same_target)` race, the rename ordering is undefined but each call is individually atomic. Worktree-lock (Story 5.3) provides the per-task exclusivity higher up the stack.

### Previous Story Intelligence

- **Story 2.11** established the `tests/crash-injection/` test infrastructure + `@pytest.mark.crash @pytest.mark.slow` markers + `just test-crash` recipe + `nightly.yml` workflow + `_skip_if_no_docker` fixture (refactor target — see AC-11).
- **Story 1.4** shipped `services/worker-wrapper/` scaffold (currently has only `__init__.py` + `__main__.py`).
- **Story 1.5** created the `tests/crash-injection/` tree + placeholder skip text mentioning "Stories 2.11 / 2.12".
- The **`packages/idempotency/` layout** (`packages/idempotency/src/idempotency/cache.py` + `errors.py` + co-located `test_*.py`) is the pattern to follow for `worker_wrapper/domain/`.

### File List (predicted)

**New (4):**
- `services/worker-wrapper/src/worker_wrapper/domain/__init__.py`
- `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py`
- `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py`
- `tests/crash-injection/_atomic_edit_runner.py`
- `tests/crash-injection/test_write_interrupt.py`

**Modified (4):**
- `services/worker-wrapper/pyproject.toml` — version bump 0.1.0 → 0.2.0.
- `services/worker-wrapper/src/worker_wrapper/__init__.py` — re-exports + version.
- `tests/crash-injection/conftest.py` — refactor `_skip_if_no_docker` to explicit-opt-in parameter (drop leading underscore + `autouse=True` if present).
- `tests/crash-injection/test_restart_recovery.py` (Story 2.11 file) — update 4 test signatures to accept renamed fixture parameter.
- `justfile` — extend `lint` mypy-strict scope to include `services/worker-wrapper`.
- `uv.lock` — regenerated for worker-wrapper version bump (no new deps).

### References

- `epics.md` Story 2.12 (lines 884–899).
- `architecture.md` line 174 — `tests/crash-injection/` tree purpose.
- `architecture.md` line 268 — write-interrupt harness mandatory Phase 1.
- `architecture.md` line 558 — `nightly.yml` runs the slow matrix.
- `architecture.md` line 694 — `services/worker-wrapper/domain/atomic_edit.py` filename mandate.
- `architecture.md` line 811 — Persistence & Recovery dependency mapping.
- `prd.md` FR30 — atomic file-edit guarantee.
- `prd.md` NFR-R2 — zero tasks lost; continuously verified by harness.
- `2-11-synthetic-crash-injection-harness.md` — sister harness (compose-driven; this one is filesystem-driven).
- `packages/idempotency/src/idempotency/cache.py` — domain-module pattern reference.

## Dev Agent Record

### Agent Model Used

**Claude Opus 4.7** (executor subagent + main-context completion). Executor delivered the atomic-edit primitive + 13 co-located unit tests; stalled before harness driver / pytest module / justfile changes; main context completed Tasks 3–6 directly.

### Debug Log References

- **mypy `def`-vs-`Callable` reassignment**: monkey-patching `atomic_edit._chunked_write` directly (`atomic_edit._chunked_write = patched`) fails `mypy --strict` with `Incompatible types in assignment (expression has type "Callable[...]", variable has type "def _chunked_write...")`. The original symbol is typed as a function definition, not a Callable variable. Fix: use `setattr(atomic_edit, "_chunked_write", patched)` — runtime-equivalent, mypy-clean.
- **`py.typed` marker required**: mypy emits `Skipping analyzing "worker_wrapper.domain": module is installed, but missing library stubs or py.typed marker` from `tests/crash-injection/`. Added `services/worker-wrapper/src/worker_wrapper/py.typed` (empty PEP 561 marker), matching the Story 2.11 round-2 fix for `registry_state`.
- **Story 2.11 `skip_if_no_docker` refactor**: was `autouse=True` + `_skip_if_no_docker`-named (Story 2.11 round-2 EM5 had renamed but kept autouse). Story 2.12 makes it non-autouse (explicit-opt-in) so write-interrupt tests don't gate on Docker. Story 2.11's `crash_harness` fixture already declared the parameter (line 68), so no test-signature changes were needed — only the `autouse=True` removal.
- **100-iteration test runtime**: 3.5s wall-clock (well under 30s budget); no parallelization needed.

### Completion Notes List

All 18 ACs satisfied:

- **AC-1/2/3/4/5/6**: `atomic_edit.py` with `atomic_write_bytes` + `atomic_write_text`, `_chunked_write` module-level helper, EXDEV explicit catch, fsync_data + fsync_dir flags, tmpfile cleanup on every error path. `domain/__init__.py` empty marker. `worker_wrapper/__init__.py` re-exports both functions + `__version__ = "0.2.0"`. `pyproject.toml` 0.1.0 → 0.2.0; `uv sync --all-groups` regenerated lock.
- **AC-7**: 13 co-located unit tests across 4 classes (TestAtomicWriteBytes, TestAtomicWriteText, TestFsyncSemantics, TestCrossFilesystemDetection). Uses `tmp_path` + `monkeypatch` for filesystem isolation + error-path simulation.
- **AC-8/10/12**: `_atomic_edit_runner.py` standalone subprocess driver. `setattr(atomic_edit, "_chunked_write", ...)` for mypy-clean monkey-patch. `os._exit(137)` for synchronous deterministic interruption. argparse for target/final-content/kill-after-bytes.
- **AC-9/11/13**: `test_write_interrupt.py` with 4 tests (unmolested, kill-at-zero, kill-mid-write, 100-iteration randomized). 100-iteration test uses `random.Random(seed=21242)`, completes in ~3.5s. Spawns subprocess with `PYTHONPATH` env so the runner can import worker_wrapper.
- **AC-11 (fixture refactor)**: `skip_if_no_docker` in `conftest.py` made non-autouse (was autouse from Story 2.11 round-2). `crash_harness` fixture already declared `skip_if_no_docker: None` parameter so Story 2.11's tests unaffected.
- **AC-14**: All new files pass `mypy --strict`. `py.typed` marker added to worker_wrapper for cross-tree imports.
- **AC-15**: `lint` recipe extended — `mypy --strict packages/ services/registry-api services/registry-state services/worker-wrapper`. mypy strict scope grew 64 → 69 files.
- **AC-16**: `nightly.yml` already runs `pytest -m crash` which auto-includes the 4 new tests. No workflow YAML change needed.
- **AC-17**: `just test` 489 passed, 5 skipped (was 476 → +13 unit tests). `just lint` 8/8 green. `just test-crash` 8 passed in 35s (4 from Story 2.11 + 4 from Story 2.12 — story-spec said 5 from 2.12 in AC-17 but AC-9 lists exactly 4; AC-9 is the more specific authority). `just check-gates-self-test` 3/3.
- **AC-18**: Single atomic commit (this commit).

**Spec-vs-implementation discrepancy**: AC-17 claimed 5 new tests from Story 2.12; AC-9 lists exactly 4. Implemented per AC-9. AC-17's count was a stale draft.

### Spec Amendments (from code review)

The 2026-04-26 code review pass produced the following amendments to the originally-shipped behavior. Each amendment is a deliberate reinterpretation of the spec (or a defensive hardening orthogonal to it) and is captured here for downstream story authors:

- **Dir-fsync errors are logged-not-raised** (M1) — AC-4 says `fsync_dir=True` "fsyncs the parent directory after the rename"; the spec did not explicitly state behavior when that fsync fails AFTER a successful rename. The implementation now LOGS at WARNING and SUPPRESSES the exception. Rationale: the data is already on disk; raising would mislead callers into a double-write retry that creates a worse outcome (two writes, one of which is unnecessary). Documented in the module docstring's "Failure handling for dir-fsync" block.
- **POST_EDIT_CONTENT widened to span multiple production chunks** (M2) — AC-9's spec used `b"the new contents go here\n" * 50` (1250 bytes), which fits in a single 64KB production chunk; the harness never exercised the multi-chunk loop. Widened to `* (50 * 80)` = 100,000 bytes (~1.5 chunks). Test runtime ~6s — well under the AC-13 30s budget.
- **100-iteration test now exercises BOTH pre-edit AND post-edit branches** (M3) — AC-9's spec said `kill_after_bytes` should be drawn from `[0, len(data))`, which structurally produces only pre-edit outcomes. Widened to `[0, n_total * 2)` so ~50% of trials land past `n_total` and exercise the no-interrupt rename path. Both branches must produce non-zero counts. Post-fix: 47 pre-edit, 53 post-edit, 0 partial.
- **token_hex(4) → token_hex(8)** (M4) — AC-2 said "8-hex-random"; that produced 32 random bits and a birthday collision at ~65k concurrent writers. Bumped to `token_hex(8)` (16 hex chars, 64 random bits). AC-2's "8-hex-random" wording is now stale; `<16-hex-random>` is the implementation truth.
- **Mode-preservation on existing targets** (M10) — Spec was silent on mode handling; the obvious-but-wrong implementation (using O_EXCL's 0o600 mode unconditionally) silently downgraded targets that were e.g. 0o644 pre-write. Implementation now captures `os.stat(target).st_mode & 0o7777` pre-write; restores via best-effort `os.chmod` after rename.
- **`errors=` parameter on atomic_write_text** (M9) — Spec only listed `encoding`; `errors=` is a natural and standard companion parameter. Added with default `"strict"` (matching `str.encode` default).
- **`Path | str` accepted by both helpers** (M12) — Spec used `target: Path`; many practical callers pass strings. Coerce via `Path(target)` at function entry; behavior unchanged for `Path` callers.
- **Empty-path rejection** (M11) — Spec only mentioned the no-parent-directory check, but `Path("").parent == Path(".")` so an empty path slipped past. Added explicit `if not target.name: raise ValueError(...)` before the parent check.
- **macOS APFS durability note** (M20) — POSIX `fsync(dir_fd)` semantics differ between Linux ext4/xfs (strict) and macOS APFS (best-effort, with `F_FULLFSYNC` ioctl required for full barrier). Documented in module docstring's "Platform durability notes" block. Production deploys target Linux containers per FR48; macOS is local-dev only.
- **AC-9 vs AC-17 spec contradiction** — AC-9 lists 4 tests; AC-17 said 5. Implementation followed AC-9. Re-confirmed by Acceptance Auditor — no change in intent, AC-17's "5" was a stale draft. Story 2.12 ships 4 crash-injection tests + 26 co-located unit tests (was 13 in the original implementation; +13 from this code-review pass).
- **`atomic_write_text` uses explicit kwargs not `**kwargs`** — AC-1 sketched `**kwargs`; explicit `encoding`, `errors`, `fsync_data`, `fsync_dir` is more discoverable + mypy-cleaner. Acceptance Auditor minor; deferred.
- **`domain/__init__.py` is NOT empty** — AC-5 said "empty `__init__.py`" but also required re-exports; the file does the re-exports. Acceptance Auditor minor; deferred (the re-export was the operative requirement).

### File List

**New (6):**
- `services/worker-wrapper/src/worker_wrapper/domain/__init__.py`
- `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py`
- `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py`
- `services/worker-wrapper/src/worker_wrapper/py.typed`
- `tests/crash-injection/_atomic_edit_runner.py`
- `tests/crash-injection/test_write_interrupt.py`

**Modified (5):**
- `services/worker-wrapper/pyproject.toml` — version 0.1.0 → 0.2.0.
- `services/worker-wrapper/src/worker_wrapper/__init__.py` — re-exports + `__version__`.
- `tests/crash-injection/conftest.py` — `skip_if_no_docker` autouse=True → non-autouse (Story 2.12 AC-11).
- `justfile` — `lint` recipe extended with `services/worker-wrapper`.
- `uv.lock` — regenerated for worker-wrapper version bump.

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-26 | 0.1 | Initial story draft (create-story). |
| 2026-04-26 | 1.0 | Implementation complete. Atomic-edit primitive (`atomic_write_bytes` / `atomic_write_text`) in `services/worker-wrapper/domain/atomic_edit.py` per architecture line 694. POSIX-only stdlib (`os.open` O_EXCL → `_chunked_write` → `os.fsync(fd)` → `os.close` → `os.replace` → `os.fsync(parent)`); EXDEV explicit catch; tmpfile cleanup on every error path. 13 co-located unit tests across 4 classes. Write-interrupt harness in `tests/crash-injection/` — standalone driver subprocess monkey-patches `_chunked_write` to `os._exit(137)` after exactly N bytes; pytest module spawns driver with PYTHONPATH and asserts SHA-256 hash matches pre-edit OR post-edit (never partial). 100-iteration randomized run with `random.Random(21242)` completes in 3.5s (well under 30s budget); FR30 invariant held for all 100 trials on macOS. `just test` 476→**489** (+13 unit tests). `just lint` 8/8 green; mypy strict scope 64→**69** files. `just test-crash` 4→**8** tests in 35s. `skip_if_no_docker` fixture refactored autouse=True → non-autouse so write-interrupt tests don't gate on Docker. Status → review. |
| 2026-04-26 | 1.1 | Code-review fixes applied — 32 findings across three reviewers (Acceptance Auditor verdict ACCEPT with 5 MINOR; Blind Hunter 4 nominal CRITICAL all downgraded to MINOR by Edge Case Hunter cross-validation; Edge Case Hunter informational/audit). All MAJOR + MINOR addressed: dir-fsync logged-not-raised (M1); POST_EDIT_CONTENT widened to 100 KB so multi-chunk boundaries are exercised (M2); 100-iteration randrange widened to span both pre-edit AND post-edit reconstruction paths — post-fix outcome **47 pre-edit / 53 post-edit / 0 partial** (M3); secrets.token_hex(4)→(8), 32→64 bits (M4); fsync/replace recorders filter by known fds to prevent pytest-internal pollution (M5/M6); subprocess env inherits os.environ + prepends PYTHONPATH for CI portability (M7); driver self-augments sys.path for manual debug (M8); atomic_write_text gains `errors=` kwarg (M9); existing-target file mode preserved across replace (M10); empty-path / Path("") rejection (M11); both helpers accept `Path \| str` (M12); 5 new tests for defensive paths — written==0, cleanup-OSError logging, O_EXCL collision, dir-fsync error, symlink-target (M13/M14/M15/M1/Mn8); __init__.py reordered version-before-re-exports (M16); cleanup-leak helper uses strict regex match (M17); justfile test-crash comment clarifies Docker dependency split (M18); dead `closed = False` flag dropped (M19); macOS APFS durability note in docstring (M20); 12 MINOR fixes (Mn1/Mn2/Mn3/Mn4/Mn5/Mn6/Mn8/Mn14/Mn17/Mn21/Mn23/Mn24). Test counts: `just test` 489→**501** passed, 5 skipped (+12 from code-review patches: 5 new tests in TestErrorPathsAndEdgeCases + 2 in TestAtomicWriteText errors= + 4 in TestAtomicWriteBytes for str-target / mode-preserve-existing / mode-default-new / parent-missing + 1 for empty-path; 1 reclassed @slow); `just lint` 8/8 green, mypy scope unchanged at 69+6 files; `just test-crash` 4 of 4 Story 2.12 tests pass (Story 2.11 4 tests skip without Docker); `just check-gates-self-test` 3/3. Fix commit: see git log. |
