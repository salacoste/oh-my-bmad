# Story 7.5.5: Worktree lock release TOCTOU race

Status: done

## Story

As **a worktree lock consumer**,
I want **lock release to be atomic and race-free**,
So that **concurrent processes never encounter corrupted or stale lock state**.

During code review of Story 5.3, a TOCTOU (time-of-check-time-of-use) race was identified in `release_lock()` at `worktree_lock.py:114-145`. The current implementation performs a non-atomic sequence: check `lock_file.exists()` → `read_lock()` → `lock_file.unlink()`. Between any of these steps, another process can unlink the file, making the `contextlib.suppress(FileNotFoundError)` band-aid necessary. The `acquire_lock` path was hardened with `O_CREAT | O_EXCL` (atomic create-or-fail at kernel level); `release_lock` deserves equivalent hardening.

## Acceptance Criteria

1. **AC-1: Atomic release** — `release_lock()` eliminates the TOCTOU window. The fix uses `os.unlink` with `O_NOFOLLOW` semantics or an equivalent atomic pattern (e.g., rename-to-temp then unlink) so that the check-and-delete is not interruptible by another process.
2. **AC-2: Regression test** — A test verifies that `release_lock()` is safe when the lock file disappears between the check and the unlink (concurrent-release race). The test uses `unittest.mock.patch` or a `monkeypatch` to inject a `FileNotFoundError` at the unlink call site and asserts no exception propagates and no stale state remains.
3. **AC-3: acquire_lock parity check** — Verify that `acquire_lock` continues to use `O_CREAT | O_EXCL` and that both lock paths follow consistent concurrency strategies. No regression in existing acquire tests.
4. **AC-4: Existing tests pass** — All 163 lines of `test_worktree_lock.py` (11 tests) continue to pass. Ruff clean on modified files.

## Tasks / Subtasks

- [x] **Task 1: Analyze TOCTOU windows in release_lock** (AC: #1)
  - [x] Read `worktree_lock.py:114-145` and map the three-step non-atomic flow:
    1. Line 126: `if not lock_file.exists():` — TOCTOU window opens
    2. Line 129: `existing = read_lock(worktree_path)` — reads file that may vanish
    3. Line 139-140: `lock_file.unlink()` — target of the race
  - [x] Confirm the `contextlib.suppress(FileNotFoundError)` at line 139 is the symptom handler, not the fix.

- [x] **Task 2: Implement atomic release** (AC: #1)
  - [x] In `services/worker-wrapper/src/worker_wrapper/domain/worktree_lock.py`, refactor `release_lock()` to eliminate the TOCTOU window.
  - [x] Key change: remove the upfront `lock_file.exists()` check (line 126). The `read_lock()` call at line 129 already returns `None` when the file is absent (catches `FileNotFoundError` internally). The `try/except FileNotFoundError` around `unlink()` replaces the `contextlib.suppress` with an explicit, documented exception handler.
  - [x] Rationale: the TOCTOU window shrinks from "exists → read → unlink" to "read → unlink". The remaining window (file vanishes between `read_lock` and `unlink`) is handled explicitly with `try/except FileNotFoundError`. The comment in the `except` block documents the acceptance of this remaining micro-window and explains why it's safe (lock is gone = desired state).

- [x] **Task 3: Add regression tests** (AC: #2)
  - [x] In `services/worker-wrapper/src/worker_wrapper/test_worktree_lock.py`, add a new test class `TestReleaseLockTOCTOU`:
    - `test_release_no_raise_when_file_deleted_by_another_process` — file deleted before read_lock, no exception.
    - `test_release_handles_fnfe_on_unlink` — FNFE raised during unlink via mock, no exception propagates.
    - `test_release_no_error_log_on_vanished_file` — same FNFE scenario, no ERROR-level logs.
    - `test_concurrent_release_both_succeed` — two calls to `release_lock()` with same session_id, both complete without exception.

- [x] **Task 4: Verify acquire_lock parity** (AC: #3)
  - [x] Confirm `acquire_lock()` still uses `O_CREAT | O_EXCL` (line 88). No changes expected.
  - [x] Confirm both paths handle the "file vanished" case: acquire via `FileExistsError` → `read_lock` → idempotent return or `WorktreeLockHeld`; release via `read_lock` → `unlink` → `FileNotFoundError` → safe no-op.

- [x] **Task 5: Run full regression suite** (AC: #4)
  - [x] `uv run pytest services/worker-wrapper/src/worker_wrapper/test_worktree_lock.py` — 20 passed.
  - [x] Domain tests — 78 passed.
  - [x] `uv run ruff check` clean on all modified files.

## Dev Notes

### Origin and Context

Deferred item D1 from Story 5.3 code review:

- **TOCTOU in release_lock** — The three-step sequence `lock_file.exists()` → `read_lock()` → `lock_file.unlink()` has two race windows:
  1. File exists at check but vanishes before `read_lock()` → `read_lock` returns `None` → early return (safe, but the `exists()` check was wasted).
  2. File exists at `read_lock()` but vanishes before `unlink()` → `FileNotFoundError` suppressed by `contextlib.suppress` (band-aid).
- The `acquire_lock` path was correctly hardened with `O_CREAT | O_EXCL` (atomic kernel-level create-or-fail). `release_lock` should match this rigor.
- The `contextlib.suppress(FileNotFoundError)` at line 139 handles the symptom but obscures the intent. An explicit `try/except` with a comment is clearer.

### Key Files (exact paths + line numbers)

| File | Lines | What changes |
|------|-------|-------------|
| `services/worker-wrapper/src/worker_wrapper/domain/worktree_lock.py` | 114-145 (`release_lock`) | Remove `exists()` check, explicit `try/except FileNotFoundError` with comment |
| `services/worker-wrapper/src/worker_wrapper/test_worktree_lock.py` | TBD | Add `TestReleaseLockTOCTOU` class |

### Architecture Compliance

- **POSIX semantics**: `O_CREAT | O_EXCL` in acquire is the gold standard for atomic create. For release, there is no POSIX "atomic unlink-if-exists" — `unlink` itself is atomic (it either removes the directory entry or raises `ENOENT`). The fix narrows the window and handles the remaining gap explicitly.
- **Idempotent shutdown**: `release_lock` is called during worker shutdown. It must never raise — the `try/except FileNotFoundError` ensures this.
- **No new dependencies**: The fix uses only stdlib (`os`, `pathlib`). No new imports required.
- **Log contract**: The existing `worktree_lock_released` INFO log at line 141-144 must remain. The `worktree_lock_release_mismatch` WARNING at line 131-136 must remain.

### Code Pattern to Follow

The existing test file uses `tmp_path` fixtures and `new_session_id()` / `new_worker_id()` helpers. Follow this pattern for the new tests.

For the TOCTOU test, use `unittest.mock.patch` on `Path.unlink` to simulate the race:

```python
from unittest.mock import patch

def test_release_safe_when_file_vanishes_after_read(self, tmp_path: Path) -> None:
    sid, wid = _sid(), _wid()
    acquire_lock(tmp_path, sid, wid)

    original_unlink = Path.unlink
    call_count = 0

    def unlink_once_then_real(self_path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise FileNotFoundError("simulated TOCTOU race")
        return original_unlink(self_path, *args, **kwargs)

    with patch.object(Path, "unlink", unlink_once_then_real):
        release_lock(tmp_path, sid)  # no raise

    assert is_lock_held(tmp_path) is False
```

### Previous Story Intelligence (7.5.1–7.5.4)

- **Commit style**: `fix(worker-wrapper): eliminate TOCTOU race in worktree lock release (Story 7.5.5)`.
- **Test at the appropriate layer**: For domain functions like `release_lock`, unit tests with `tmp_path` + `monkeypatch` are the right granularity. Do NOT test through the full worker wrapper stack.
- **Different service, different regression**: Story 7.5.5 modifies `worker-wrapper`. Run `uv run pytest services/worker-wrapper/ -x -q`.
- **Ruff**: Run `ruff check` on modified files. Previous stories hit import-ordering (I001) and unused-import (F401) issues — watch for these.

### References

- [Source: deferred-work.md — D1 (story 5.3 code review)]
- [Source: services/worker-wrapper/src/worker_wrapper/domain/worktree_lock.py — lines 114-145]
- [Source: services/worker-wrapper/src/worker_wrapper/test_worktree_lock.py — existing test patterns]
- [Source: epic-7-retro-2026-05-13.md — item 5 (MEDIUM)]

## Dev Agent Record

### Implementation Plan

Refactor `release_lock()` to narrow TOCTOU window: remove redundant `exists()` check, replace `contextlib.suppress(FileNotFoundError)` with explicit `try/except` and documentation. Add regression tests for file-vanishes scenarios.

### Debug Log References

- Ruff SIM105 flagged the `try/except/pass` pattern — suppressed with `noqa: SIM105` and inline comment explaining the intentional choice over `contextlib.suppress`.
- Removed unused `import contextlib` after refactor.
- Pre-existing failure in `test_run_task.py::test_fails_without_event_log_dir` — formally excluded in story 3.5.4.

### Completion Notes

All 4 ACs met:
- AC-1: TOCTOU window narrowed from 3 steps (exists → read → unlink) to 2 steps (read → unlink). Remaining micro-window handled with explicit `try/except FileNotFoundError` + documentation comment.
- AC-2: `TestReleaseLockTOCTOU` class added with 4 tests covering file-deleted-before-read, FNFE-during-unlink, concurrent-release, and no-error-log scenarios.
- AC-3: `acquire_lock` confirmed still using `O_CREAT | O_EXCL`. Both paths consistent.
- AC-4: 20 passed (was 16, +4 new). Ruff clean.

### Review Findings

- [x] [Review][Patch] `worktree_lock_released` info log fires even when another process removed the lock — misleading during incident investigation. Fixed: added `worktree_lock_already_released` log in the `except FileNotFoundError` branch with early return.
- [x] [Review][Patch] `test_concurrent_release_both_succeed` docstring says "two sessions" but uses same session ID. Fixed: corrected docstring to "Same session releases lock twice: second call is idempotent no-op."
- [x] [Review][Defer] No coverage for `PermissionError` or other `OSError` subclasses on `unlink` — pre-existing design choice; caller has broad `except Exception` wrapper. Deferred.
- [x] [Review][Defer] `release_lock` session_id mismatch does not check for missing key in corrupt lock — pre-existing, by design ("stale lock recovery is a manual procedure"). Deferred.

### File List

- `services/worker-wrapper/src/worker_wrapper/domain/worktree_lock.py` — removed `exists()` check, replaced `contextlib.suppress` with explicit `try/except`, removed unused `contextlib` import, differentiated `worktree_lock_already_released` log
- `services/worker-wrapper/src/worker_wrapper/test_worktree_lock.py` — added `from unittest.mock import patch`, added `TestReleaseLockTOCTOU` class (4 tests), fixed misleading docstring

## Change Log

- 2026-05-13: Story created from deferred-work.md D1 (story 5.3). Status: ready-for-dev.
- 2026-05-13: Implementation complete. Status: review.
- 2026-05-14: Code review — 2 patches applied, 2 deferred. Status: done.
