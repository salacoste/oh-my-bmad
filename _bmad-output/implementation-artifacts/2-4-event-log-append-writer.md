# Story 2.4: Event-log append writer (JSONL)

Status: done

## Story

As **`registry-state`**,
I want **`services/registry-state/src/registry_state/adapters/event_log.py` exporting an `EventLogWriter` that appends canonical-JSON `EventEnvelope` records to per-day JSONL files under `/var/lib/oh-my-bmad/registry/events/YYYY-MM-DD.jsonl` with `fdatasync` after every line and a startup recovery pass that trims trailing partial lines**,
so that **the append-only event log is the durable source of truth (FR20) even across forced restarts and SIGKILL (FR24 / NFR-R1 / NFR-R2), the registry is the sole writer (FR26), and Stories 2.5 / 2.8 have a stable writer API to call**.

## Acceptance Criteria

1. **AC-1: `services/registry-state/src/registry_state/adapters/event_log.py`** — one file, single public entry point. Exports:

   - `class EventLogWriter` with:
     - `__init__(self, *, base_dir: Path, clock: Clock) -> None` — `base_dir` is the root directory for the log (production `/var/lib/oh-my-bmad/registry/events`; tests pass a tmpdir); `clock` supplies the UTC timestamp that drives the per-day rollover decision (injected per Story 2.2 discipline).
     - `async def append(self, envelope: EventEnvelope) -> None` — serializes via `to_canonical_json(envelope)`, writes `<canonical-json-bytes>\n` to the current-day file under an inode-level lock, calls `os.fdatasync(fd)`, returns. Every successful return is a durability guarantee: if power is cut immediately after, the bytes are on disk.
     - `async def close(self) -> None` — closes the current file handle cleanly (idempotent — safe to call twice).
     - `async def recover(self) -> int` — run ONCE at startup BEFORE any `append()`. Scans the current-day file for a trailing partial line (bytes after the last `\n`); if present, truncates the file to the position of that last `\n` (exclusive). Returns the number of bytes truncated. Called from service startup (Story 2.5 wiring) or from test fixtures.
   - `def current_day_path(base_dir: Path, now: datetime) -> Path` — free function, pure (no I/O). Returns `base_dir / f"{now.date().isoformat()}.jsonl"`. Exposed for test introspection and for the reader used by Story 2.5.
   - `def read_log_lines(path: Path) -> Iterator[EventEnvelope]` — free function, generator. Reads the JSONL file line-by-line, decodes each via `from_canonical_json`, yields envelopes. **Trailing partial lines are ignored silently** (no tail byte after the last `\n` ⇒ end of iteration). Used by tests to verify round-trip; Story 2.5's subscriber will use this (or a descendant) as its input.

2. **AC-2: Canonical-JSON-first serialization.** `append()` calls `to_canonical_json(envelope)` from `packages/events/src/events/canonical.py` — NOT `envelope.model_dump_json()` or any other path. This guarantees byte-deterministic line content so replay is byte-stable (Story 2.1 AC-14 property). A trailing `\n` (0x0A byte) is appended after the canonical-JSON bytes. No other bytes (no BOM, no whitespace, no CR).

3. **AC-3: Per-day rollover is UTC-driven and lazy.** At each `append()` call, compute `clock.now().date()`. If it differs from the currently-open file's date (or no file is open), close the current file handle (if any), open (or create-if-missing) `<base_dir>/<today>.jsonl` with `os.O_WRONLY | os.O_APPEND | os.O_CREAT`, and use that fd going forward. No background rollover task; the check happens on every call. Cost is one `datetime.date` comparison per append — negligible.

4. **AC-4: `fdatasync` after every write, not `fsync`.** After the `os.write(fd, data)` call that appends the line, call `os.fdatasync(fd)` — NOT `os.fsync(fd)`. `fdatasync` flushes data without also forcing metadata (mtime, size) fsync, which is faster (~10-30% on ext4/XFS) while still guaranteeing the bytes are durable. The metadata fsync isn't part of the durability contract — we don't care about inode mtime, only about the line's bytes being recoverable after crash. Document the choice in the module docstring.

5. **AC-5: Single-write append is atomic under `O_APPEND` + inode lock.** On Linux ext4/XFS (the production target), a single `write(fd, bytes)` under `O_APPEND` holds the inode lock for the duration of the syscall, preventing interleaving with concurrent appenders. Combined with FR26 (single writer enforced by `scripts/check_single_writer.py`), a single `write()` call per event is sufficient — **no temp-file-and-rename dance needed**. Document this reliance in the module docstring. The story does NOT rely on PIPE_BUF-sized atomicity (that's a POSIX guarantee for pipes, not regular files).

6. **AC-6: Startup recovery trims trailing partial lines.** `recover()` implements:
   ```
   1. If current-day file does not exist, return 0.
   2. Open the file for read+write in binary mode.
   3. Seek to the end; read backward in 4 KiB chunks until a `\n` is found OR the start of file is reached.
   4. If the last `\n` is at position P (zero-indexed), the complete-lines region ends at P+1.
      If the file size equals P+1, no truncation needed — return 0.
      Otherwise, `ftruncate(fd, P+1)` and return (file_size - (P+1)).
   5. If no `\n` is found in the entire file (all bytes are partial), truncate to 0 and return file_size.
   ```
   After `recover()`, the current-day file is guaranteed to contain zero or more complete `\n`-terminated JSONL lines and nothing else.

7. **AC-7: Directory creation on startup.** `EventLogWriter.__init__` calls `base_dir.mkdir(parents=True, exist_ok=True)`. Writer never raises on a missing directory; the operator's `/var/lib/oh-my-bmad/registry/events/` is created if missing. Pre-existing files are preserved.

8. **AC-8: `current_day_path` is pure + UTC-only.** `current_day_path(base_dir, naive_or_non_utc_datetime)` raises `ValueError` (match the UTCDateTime discipline from Story 2.3). For UTC-aware input, returns `base_dir / "2026-04-24.jsonl"` for a `datetime(2026, 4, 24, 23, 59, 59, tzinfo=UTC)` AND for a `datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)` → two different files (boundary is midnight UTC exactly).

9. **AC-9: `read_log_lines` tolerates partial-tail files + empty files.** Reads via text-mode iteration; each line is `line.rstrip(b'\n')` then `from_canonical_json`. If the last line has no trailing `\n`, it's SKIPPED (not yielded — partial recovery contract). If the file is empty, iteration yields zero envelopes. If the file does not exist, raises `FileNotFoundError` (caller must check — this is a user of the file, not a mutator).

10. **AC-10: Single-writer CI check still green.** New code lives under `services/registry-state/**` which is the sole-excluded directory in `scripts/check_single_writer.py`. No `# noqa: SW001` comments allowed. The writer ONLY writes to paths under `base_dir` (a constructor argument) — never to random locations; no SQLAlchemy writes (this is the JSONL layer, not the materializer).

11. **AC-11: mypy --strict clean.** No `Any`, `cast()`, `# type: ignore` escape hatches outside SQLAlchemy event-listener signatures (which this module doesn't have — it's pure file I/O). All public signatures precisely typed.

12. **AC-12: Co-located tests in `services/registry-state/src/registry_state/test_event_log.py`** — 20+ tests covering the full behavior:

    **TestCurrentDayPath** (~4 tests):
    - Computes expected path from UTC datetime.
    - Rejects naive datetime with `ValueError`.
    - Rejects non-UTC tzinfo with `ValueError`.
    - Midnight boundary: `23:59:59.999Z` and `00:00:00.000Z` resolve to adjacent files.

    **TestEventLogWriterRoundTrip** (~5 tests):
    - `append(env)` then `read_log_lines(path)` yields the same envelope (byte-preserved canonical JSON).
    - 100-envelope sequence via `seeded_uuid7` fixture round-trips with identical order.
    - Envelopes with optional `parent_event_id` / `task_id` / `session_id` round-trip.
    - Each line ends with exactly one `\n` byte (zero trailing-whitespace bytes).
    - `to_canonical_json(env)` bytes appear verbatim in the file (slice-equality assertion).

    **TestDailyRollover** (~4 tests):
    - Two appends on same UTC day write to the same file.
    - An append across the UTC-midnight boundary opens a new file.
    - `TickingClock` advancing 1ms per call stays within one day; advancing 1 day per call produces one file per append.
    - File naming matches `YYYY-MM-DD.jsonl` exactly.

    **TestDurability** (~3 tests):
    - After `append()`, `os.fdatasync` was called on the fd (verify via mock or by checking the file's content is visible to a fresh reader immediately).
    - After `append()` + hard kill + replay, the envelope is recoverable.
    - `close()` is idempotent (calling twice doesn't raise).

    **TestRecover** (~5 tests):
    - Recover on empty file returns 0.
    - Recover on file with only complete lines returns 0.
    - Recover on file with a single-byte trailing partial (e.g., `{"event_id":"x...` with no `\n`) truncates back to the last `\n` and returns the truncated byte count.
    - Recover on file with no `\n` at all truncates to zero.
    - Recover on non-existent file returns 0 (no-op, no error).

    **TestDirectoryCreation** (~1 test):
    - Constructor with a non-existent `base_dir` creates the directory tree.

13. **AC-13: Integration with Story 2.1 / 2.2 / 2.3** — the writer's tests use the existing `fixed_clock` and `seeded_uuid7` fixtures from `tests/conftest.py`; the test envelopes are built via Story 2.1's `EventEnvelope(...)` + Story 2.2's `new_event_id(clock=..., rng=...)`. NO new conftest fixtures are added for this story; use what's there.

14. **AC-14: `services/registry-state/src/registry_state/__init__.py`** re-exports the new public surface:
    ```python
    from registry_state.adapters.event_log import (
        EventLogWriter,
        current_day_path,
        read_log_lines,
    )
    ```
    `__all__` extended alphabetically. `__version__` bumped `0.2.0 → 0.3.0` (second feature increment — matches the `events` 0.2.0 → 0.3.0 pattern from Story 2.2).

15. **AC-15: No new runtime dependencies.** The writer uses stdlib only: `os`, `pathlib`, `datetime`, `asyncio.to_thread`, `typing`. No `aiofiles` — `asyncio.to_thread` wraps sync I/O cleanly and avoids an external dep for a workload that's single-writer + fsync-serialized anyway. The research flagged this as the correct call; honor it.

16. **AC-16: `async def append(...)` uses `asyncio.to_thread` for the blocking syscalls.** Rationale: fsync + write are blocking syscalls that would stall the asyncio event loop. `await asyncio.to_thread(self._sync_append_impl, data)` offloads them to the default executor. The sync impl does the inode-locked write + fdatasync. Document this layering in the module docstring.

17. **AC-17: Regression green.**
    - `just test` count bumps from **230 passed, 6 skipped** (post-Story-2.3 review fixes) to at least **250+6** (+20 for the new test module).
    - `just lint` — all 7 green; mypy --strict still covers 35 files + 1 new (`event_log.py`) + 1 new test module = 37 source files strict-clean.
    - `just bootstrap-verify` — 13/13 imports; prints `registry_state 0.3.0`.
    - `just check-gates-self-test` — 3/3 (single-writer must still pass; new code in the excluded directory).
    - `just migrator-test-additive` — 3/3 (unrelated; regression check).

18. **AC-18: Atomic commit titled** `feat(registry-state): story 2.4 — event-log JSONL append writer · FR20 FR24 NFR-R1 NFR-R2`.

## Tasks / Subtasks

- [x] **Task 1: `event_log.py` — writer implementation** (AC: #1, #2, #3, #4, #5, #7, #8, #15, #16)
  - [x] Module docstring explaining: canonical-JSON-first, fdatasync-not-fsync, O_APPEND-atomic-write relying on FR26, UTC-midnight rollover, asyncio.to_thread layering.
  - [x] `current_day_path(base_dir, now)` free function with UTC-tz-aware guard.
  - [x] `class EventLogWriter` — `__init__`, `append`, `close`, `recover`. State: `_fd: int | None`, `_current_date: date | None`, `_lock: asyncio.Lock` (so even if multiple coroutines somehow race, we serialize).
  - [x] Internal `_sync_append_impl(self, data: bytes) -> None` — the blocking write + fdatasync called via `asyncio.to_thread`.
  - [x] Internal `_ensure_current_day(self, now: datetime) -> None` — opens/rolls the fd.

- [x] **Task 2: `read_log_lines` reader** (AC: #9)
  - [x] Generator function. Open file in binary; iterate line-by-line.
  - [x] Skip trailing partial line (no terminating `\n`).
  - [x] Yield `EventEnvelope` via `from_canonical_json`.

- [x] **Task 3: `recover()` startup method** (AC: #6)
  - [x] Backward-chunk scan for last `\n`.
  - [x] `ftruncate` to the byte after the last `\n` (or to 0 if no `\n` found).
  - [x] Return truncated byte count.
  - [x] Handle non-existent file gracefully (return 0).

- [x] **Task 4: `test_event_log.py`** (AC: #12, #13)
  - [x] TestCurrentDayPath (4 tests).
  - [x] TestEventLogWriterRoundTrip (5 tests).
  - [x] TestDailyRollover (4 tests).
  - [x] TestDurability (3 tests).
  - [x] TestRecover (5 tests).
  - [x] TestDirectoryCreation (1 test).
  - [x] All tests use `tmp_path` pytest fixture + `fixed_clock` / `seeded_uuid7` from `tests/conftest.py` + Story 2.1's `EventEnvelope(...)` + Story 2.2's `new_event_id(...)`.

- [x] **Task 5: `__init__.py` re-exports + version bump** (AC: #14)
  - [x] Re-export `EventLogWriter`, `current_day_path`, `read_log_lines`.
  - [x] Version bump `0.2.0 → 0.3.0`.
  - [x] Alphabetical `__all__`.

- [x] **Task 6: Regression + atomic commit** (AC: #10, #11, #17, #18)
  - [x] `just test` count bumps ≥ +20.
  - [x] `just lint` all 7 green (single-writer gate especially — writer lives in excluded dir, no noqa).
  - [x] `just bootstrap-verify` prints `registry_state 0.3.0`.
  - [x] Single atomic commit per AC-18.

### Review Findings

Generated by `/bmad-code-review` against scaffold commit `7d8d9b3`. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor — all opus) converged on 13 actionable findings after dedup (1 CRITICAL, 7 MAJOR, 5 MINOR); 8 dismissed. Auditor approved all 18 ACs as satisfied — the code-level findings below are correctness and robustness hazards the ACs didn't fully cover.

- [x] **[Review][Patch] `os.write` short-write corrupts the log on ENOSPC/EINTR** [`event_log.py:476`] — **CRITICAL.** `os.write(self._fd, data)` may return fewer bytes than requested on full-disk / signal-interrupted writes. Current code discards the return value. On partial write, the file ends in a half-JSON line (no `\n`), then the NEXT `append()` concatenates its canonical JSON to the half-line → corrupted JSONL that `read_log_lines` tries to parse (mid-replay ValidationError). Fix: loop until all bytes are written, OR refuse to continue writing until `recover()` is called (poison-pill `_poisoned: bool` flag). Combine with F2: catch the exception and set the poison flag so the next append raises until recovery runs.

- [x] **[Review][Patch] `fdatasync` never called when `os.write` raises — no rollback** [`event_log.py:476-477`] — **MAJOR.** If `os.write` raises mid-syscall (ENOSPC, EIO), partial bytes may already be on the inode but unflushed; caller sees exception and assumes nothing happened, but a subsequent successful append concatenates to the half-line. Fix: wrap the write in try/except; on any exception, poison the writer (set `_poisoned = True`) and re-raise. Next `append()` raises `RuntimeError("writer poisoned — call recover() and reopen")` until `recover()` clears it.

- [x] **[Review][Patch] `_ensure_current_day` rollover is not atomic — partial failure leaves incoherent state** [`event_log.py:479-497`] — **MAJOR.** Current order: `os.close(self._fd)`, `self._fd = None`, `os.open(new_path)`. If `os.close` raises (EIO), `self._fd` is never nulled on the error path. If `os.open` raises (EMFILE / EACCES), `self._current_date` is not reset → next call skips the rollover branch and writes to a closed fd (EBADF). Fix: open NEW fd first into a local; assign to `self._fd` only on success; close OLD fd after. If open fails, the old fd stays valid and the writer keeps working on the old day's file; the ValueError propagates up.

- [x] **[Review][Patch] `recover()` must trim ALL `*.jsonl` files in `base_dir`, not just today's** [`event_log.py:456-461`] — **MAJOR.** Current: `recover()` computes `path = current_day_path(base_dir, self._clock.now())` and trims only that one file. Failure mode: crash at 23:59:59 leaving yesterday's file with a partial tail → restart at 00:00:01 → `recover()` opens today's (nonexistent) file, returns 0 → yesterday's partial tail persists forever. Compounded by the TickingClock-in-tests edge case where `recover()`'s clock tick differs from the first `append()`'s tick → they target different days. Fix: `recover()` iterates `base_dir.glob("*.jsonl")` and trims each; returns total bytes trimmed across all files. Also closes `self._fd` first if open (invariant: recover operates on a clean slate).

- [x] **[Review][Patch] `recover()` does not fsync parent directory after `ftruncate` — truncation is not durable** [`event_log.py:369-370`] — **MAJOR.** The whole module's thesis is power-cut durability. `recover()` trims the file via `ftruncate`, but the metadata change (file size) is not fsync'd. ext4 with metadata journal usually preserves the truncation, but no POSIX guarantee without an fsync of the parent directory. A power-cut immediately after `recover()` could resurrect the partial tail. Fix: after `ftruncate(fd, complete_end)`, also `os.fsync(fd)` (or `_fdatasync(fd)`) AND open `base_dir` with `O_RDONLY | O_DIRECTORY`, `os.fsync(dir_fd)`, close — ensures both file-size and directory-entry changes are durable before `recover()` returns.

- [x] **[Review][Patch] `read_log_lines` defers `FileNotFoundError` to first iteration (generator contract violation)** [`event_log.py:~297`] — **MAJOR.** Spec AC-9 promises `FileNotFoundError` on missing file. Current code is a generator — `open(path, "rb")` only runs on first `next()`. So `read_log_lines(missing_path)` returns a generator successfully; the exception fires only when the caller iterates. Callers doing lazy iteration see wildly different error-timing semantics than documented. Fix: wrap in a non-generator outer function that does `if not path.exists(): raise FileNotFoundError(...)` eagerly, then delegates to an inner generator (`_read_log_lines_gen`).

- [x] **[Review][Patch] `read_log_lines` CRLF handling leaves stray `\r` byte in parsed JSON** [`event_log.py:~316`] — **MAJOR.** `raw.endswith(b"\n")` matches `\r\n`, then `raw.rstrip(b"\n")` strips only LF, leaving a trailing `\r` inside the bytes passed to `from_canonical_json`. Pydantic/JSON parsers tolerate the trailing CR as whitespace — so parse succeeds but byte-equal round-trip (`to_canonical_json(env) == file_bytes`) fails. Any log-integrity hash or replay-determinism check silently breaks on files touched by CRLF-converting tools. Fix: `raw.rstrip(b"\r\n")` — strip both bytes. Writer only emits LF, so any CR present is external tampering; stripping is pragmatic tolerance.

- [x] **[Review][Patch] `recover()` does not invalidate `self._fd` if writer was previously active** [`event_log.py:~446`] — **MAJOR.** Docstring says "must be called ONCE at startup, BEFORE any append()", but nothing enforces this. If a user calls `recover()` after `append()`, the held `self._fd` now points to a potentially-truncated file; subsequent writes under `O_APPEND` go to the NEW EOF (correct), but the semantics are surprising and untested. Fix: at the top of `recover()`, if `self._fd is not None`, close it and clear `_current_date`. Next `append()` will re-open fresh. This also fixes the TickingClock test-determinism concern in F4.

- [x] **[Review][Patch] `close()` does not poison the writer — next `append()` silently re-opens** [`event_log.py:~433`] — **MINOR.** `close()` zeros `_fd` and `_current_date`, so the next `append()` hits `_ensure_current_day` and re-opens a fresh fd. That's a contract violation: a user who called `close()` expects the writer to be terminal. Fix: add `_closed: bool` flag; `close()` sets it to True; `append()` raises `RuntimeError("writer closed")` if called after close. Combines cleanly with the poison-pill in F1.

- [x] **[Review][Patch] `close()` nulls `_fd` before `os.close` runs — fd leaked if close raises** [`event_log.py:~439-444`] — **MINOR.** Current: `fd = self._fd; self._fd = None; await asyncio.to_thread(os.close, fd)`. If `os.close` raises (EIO), `self._fd` is already None → writer thinks it's clean, but the kernel still holds the fd. Fix: swap the order — only null `self._fd` AFTER `os.close` returns successfully. If it raises, the caller can retry.

- [x] **[Review][Patch] `_recover_file` TOCTOU between `path.exists()` and `open(path, "r+b")`** [`event_log.py:~459-461`] — **MINOR.** Race: `if not path.exists(): return 0` → `_recover_file(path)` → `open(path, "r+b")` → another process deletes the file → `FileNotFoundError`. Fix: wrap `_recover_file` in `try/except FileNotFoundError: return 0` — handles the race gracefully.

- [x] **[Review][Patch] File mode `0o644` is world-readable; audit-log best practice is `0o640`** [`event_log.py:~496`] — **MINOR.** Event logs contain approval audit trails, task contents, actor IDs — potentially sensitive. Any OS user on a shared host can `cat` the log. Secret-scanner blocks secrets but not PII / task details. Fix: change default to `0o640` (owner rw + group r, world 0). The operator documentation still recommends running the container as a dedicated user, but defense in depth.

- [x] **[Review][Patch] `close()` does not fdatasync before closing — theoretical mid-rollover race** [`event_log.py:~433,~489`] — **MINOR.** `close()` (and `_ensure_current_day` during rollover) close the fd without a final `fdatasync`. In practice every `append()` already fsync'd so data is durable; belt-and-braces. Fix: `_fdatasync(self._fd)` before `os.close(self._fd)` in both paths.

Dismissed (documented here for auditability):

- `asyncio.Lock()` bound to wrong event loop at `__init__` time — Python 3.12 handles this correctly; `Lock` no longer binds to loop at construction.
- Path traversal on `base_dir` — operator-controlled config surface; we trust the deployment to point at a sane directory. Not our validation layer.
- `asyncio.to_thread(os.close, fd)` holds lock across close — acceptable; serialization is the intended property.
- `asyncio.to_thread` per-append overhead — perf observation, not a correctness bug; FR26 single-writer workload doesn't benefit from batching.
- Test packaging footprint (tests in `src/`) — project-wide convention established by Stories 2.1/2.2/2.3; not our call to change.
- Local fixture re-declaration in test_event_log.py — Auditor confirmed byte-identical to `tests/conftest.py`; deviation was documented in story change log.
- `_clean_registry` autouse fixture cross-test state — pytest runs teardown on exception; low risk.
- Test helper `_make_envelope` sequential ID generation determinism — test-only; no production impact.

## Dev Notes

### Architecture patterns for this story

- **Event log is the source of truth** (Arch line 796). Every derived state (SQLite rows in Stories 2.5+, snapshots in 2.6, idempotency cache in 2.7) is recomputable by replaying the log. The log MUST be durable before any other state is allowed to mutate. This writer's `fdatasync`-after-every-write is the durability contract.
- **FR26 single writer** (Arch line 791-792). Only registry-state writes to the log. CI-enforced by `scripts/check_single_writer.py`. The writer does NOT need to defend against concurrent writers at the filesystem level — the CI gate + runtime architecture prevent them. This is why a single `write()` under `O_APPEND` is sufficient (inode-lock atomicity on Linux ext4/XFS).
- **Canonical JSON is what goes on disk** (Story 2.1 AC-14). Not `model_dump_json()`. Not `json.dumps(env.model_dump())`. Only `to_canonical_json(env)`. The bytes are deterministic — same envelope → same line → byte-stable replay.
- **UTC everywhere** (Arch line 297). Per-day rollover is at UTC-midnight, regardless of server local time. No operator will confuse themselves with timezone rollover boundaries.
- **Stdlib + asyncio.to_thread** over third-party async-file wrappers. Less dep surface, less to go wrong, and the sync-in-thread pattern is the boring-tech right answer when your syscalls are fsync-serialized anyway.

### Crash-safety reasoning (the important part)

The AC reads: *"service crashes mid-write ⇒ event log contains only complete JSONL lines"*. Two components:

1. **Writer side** — a single `write()` syscall under `O_APPEND` is atomic on ext4/XFS (inode lock held). `fdatasync()` after the write flushes bytes to disk. If the process is killed BETWEEN `write()` and `fdatasync()`, some bytes may or may not be on disk (kernel discretion); if killed AFTER `fdatasync()` returns, bytes ARE durable. The writer does NOT need to protect against mid-`write()` kills — the kernel handles that.

2. **Reader/startup side** — if the writer was killed after starting a `write()` that didn't complete (or the kernel scheduled a partial write before crash), the file MAY have trailing bytes that don't end in `\n`. The `recover()` startup method seeks to the last `\n`, `ftruncate()`s past it, and the file is clean. Stories 2.5+ must call `recover()` before their first `append()`.

The story does NOT use the temp-file-and-rename pattern. That pattern is correct for non-append writes (atomic replace of a whole file); it's massive overkill for an append log where single `write()` is already atomic.

### Implementation sketch — writer

```python
# adapters/event_log.py — illustrative
from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from events import EventEnvelope, from_canonical_json, to_canonical_json
from events.clock import Clock


def current_day_path(base_dir: Path, now: datetime) -> Path:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(
            f"current_day_path requires UTC-aware datetime; got tzinfo={now.tzinfo!r}"
        )
    return base_dir / f"{now.date().isoformat()}.jsonl"


def read_log_lines(path: Path) -> Iterator[EventEnvelope]:
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                return  # trailing partial line — skip
            yield from_canonical_json(raw.rstrip(b"\n"))


class EventLogWriter:
    def __init__(self, *, base_dir: Path, clock: Clock) -> None:
        self._base_dir = base_dir
        self._clock = clock
        self._fd: int | None = None
        self._current_date: date | None = None
        self._lock = asyncio.Lock()
        base_dir.mkdir(parents=True, exist_ok=True)

    async def append(self, envelope: EventEnvelope) -> None:
        data = to_canonical_json(envelope) + b"\n"
        async with self._lock:
            await asyncio.to_thread(self._sync_append_impl, data)

    def _sync_append_impl(self, data: bytes) -> None:
        now = self._clock.now()
        self._ensure_current_day(now)
        assert self._fd is not None
        os.write(self._fd, data)
        os.fdatasync(self._fd)

    def _ensure_current_day(self, now: datetime) -> None:
        today = now.date()
        if self._current_date == today and self._fd is not None:
            return
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        path = current_day_path(self._base_dir, now)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        self._current_date = today

    async def close(self) -> None:
        async with self._lock:
            if self._fd is not None:
                await asyncio.to_thread(os.close, self._fd)
                self._fd = None
                self._current_date = None

    async def recover(self) -> int:
        async with self._lock:
            now = self._clock.now()
            path = current_day_path(self._base_dir, now)
            if not path.exists():
                return 0
            return await asyncio.to_thread(_recover_file, path)


def _recover_file(path: Path) -> int:
    # Backward-chunk scan for last `\n`; ftruncate past it.
    size = path.stat().st_size
    if size == 0:
        return 0
    with open(path, "r+b") as f:
        # Scan backward in 4 KiB chunks.
        chunk_size = 4096
        pos = size
        last_nl = -1
        while pos > 0:
            read_start = max(0, pos - chunk_size)
            f.seek(read_start)
            chunk = f.read(pos - read_start)
            idx = chunk.rfind(b"\n")
            if idx != -1:
                last_nl = read_start + idx
                break
            pos = read_start
        if last_nl == -1:
            # No newline in file — truncate all.
            f.truncate(0)
            return size
        complete_end = last_nl + 1
        if complete_end == size:
            return 0
        f.truncate(complete_end)
        return size - complete_end
```

### What NOT to do (fences)

- **Do not** implement the subscriber/materializer loop. That's Story 2.5.
- **Do not** write to SQLite from this module. That's Story 2.5 via the `materializer` domain module.
- **Do not** implement snapshot capture. Story 2.6.
- **Do not** implement idempotency cache. Story 2.7.
- **Do not** expose an MCP tool for `append_event`. That's Story 2.8 (`clawhip-bridge` MCP server) — it will CALL this writer.
- **Do not** use `aiofiles` — stdlib + `asyncio.to_thread` is the documented choice (AC-15).
- **Do not** use `os.fsync` — `os.fdatasync` is the documented choice (AC-4).
- **Do not** use the temp-file-and-rename pattern — single `write()` under `O_APPEND` is atomic (AC-5).
- **Do not** add a new conftest fixture. Reuse `fixed_clock` / `seeded_uuid7` (AC-13).
- **Do not** rollover on local time — UTC only (AC-8).

### Previous Story Intelligence

- **Story 2.3** (`4ad6612` done) shipped the SQLite schema + Alembic migration. The writer does NOT write to SQLite — Story 2.5 does. Story 2.3 also shipped `create_engine` + `get_session` in `adapters/sqlite_store.py`; the writer sits next to it as a sibling module (`adapters/event_log.py` per Arch§620-642 layout).
- **Story 2.3 UTCDateTime + tz-guard discipline**: the writer's `current_day_path` copies that discipline — raises on naïve or non-UTC input. The `tzinfo is None or utcoffset() != timedelta(0)` pattern should be identical.
- **Story 2.2** (`ee3191f` done) shipped `FrozenClock`, `TickingClock`, `FROZEN_EPOCH`, `new_event_id`, and the `fixed_clock` / `seeded_uuid7` conftest fixtures. The writer's tests use these directly. `TickingClock(start_now=FROZEN_EPOCH)` with 1-day ticks gives an easy "every event on a new day" rollover test.
- **Story 2.1** (`b90f08e` done) shipped `EventEnvelope`, `to_canonical_json`, `from_canonical_json`. The writer serializes via `to_canonical_json` — the deterministic contract. The reader deserializes via `from_canonical_json`.
- **Story 1.6** shipped `scripts/check_single_writer.py`. The exclusion root `services/registry-state/` is pre-configured; no script change needed.
- **Story 1.7** secret-scanner — JSONL content is canonical JSON envelopes; no secret patterns match the hex UUIDv7 + ISO 8601 text.

### Git Intelligence

```
4ad6612 docs(story-2-3): finalize + mark done
f139dca fix(registry-state): apply story 2.3 code-review fixes · all severities
b2fbf0d docs(story-2-3): finalize story file + mark review
cc915d2 feat(registry-state): story 2.3 — SQLite schema + Alembic initial migration · FR24 FR28
ee3191f docs(story-2-2): finalize + mark done
```

Established pattern across 14 closed stories: **scaffold → docs-finalize-to-review → review-fix → docs-finalize-to-done**. Story 2.4 follows identically.

### Latest Tech Information

- **`asyncio.to_thread`** is stdlib since Python 3.9 — runs a sync function in the default `ThreadPoolExecutor`. Appropriate when: (a) the sync call is a blocking syscall (fsync, write) and (b) the workload is low-concurrency (we have ONE writer per FR26, so the threadpool doesn't become the bottleneck).
- **`os.fdatasync(fd)`** flushes file DATA to disk without the metadata fsync (mtime, atime, size-attr). Faster than `os.fsync(fd)` by 10-30% on ext4/XFS; identical durability for our use case (we don't care about metadata). Available on Linux + macOS (via `fcntl(fd, F_FULLFSYNC)` fallback) since Python 3.3.
- **`os.O_APPEND`** on Linux holds the inode lock for the duration of `write()`, guaranteeing atomic appends against concurrent appenders on the same inode. Combined with FR26, a single `write()` per event is sufficient. Note: POSIX formally guarantees this only for writes ≤ PIPE_BUF (typically 4 KiB) AND only for pipes — but Linux ext4/XFS extends this to all sizes for regular files under `O_APPEND`. We're Linux-first for production (macOS is a dev convenience), so we rely on the stronger guarantee.
- **`pathlib.Path.mkdir(parents=True, exist_ok=True)`** is idempotent — safe to call on startup even if the directory already exists.
- **`os.ftruncate(fd, length)`** truncates a file to a specific byte length. Used by `recover()` to drop trailing partial bytes.
- **Python 3.12 `datetime.date.isoformat()`** returns `YYYY-MM-DD` — matches the AC's file naming convention exactly.

### References

- `epics.md` §Epic 2 / Story 2.4 (lines 728-744) — AC + BDD + FR20 + NFR-O5 citation.
- `architecture.md` lines 199 (event-log format locked), 620-642 (package layout — adapters/event_log.py), 791-792 (single-writer CI), 796 (source of truth), 42 / 49 / 55 (registry-state role).
- `prd.md` FR20 (840), FR24 (847), FR26 (850), NFR-R1 (912), NFR-R2 (913).
- `2-1-event-envelope-schema-registry.md` — canonical JSON contract (`to_canonical_json` / `from_canonical_json`).
- `2-2-uuidv7-injectable-clock.md` — clocks used in test fixtures.
- `2-3-registry-state-sqlite-schema.md` — sibling module layout + UTCDateTime tz-guard pattern.
- `packages/events/src/events/canonical.py` — the serializer we call.
- `scripts/check_single_writer.py` — the CI gate our module MUST pass.

## Dev Agent Record

### Agent Model Used

**Claude Sonnet 4.6** (executor subagent). All 6 tasks delivered in a single pass. One documented deviation (below); no blockers.

### Debug Log References

None. Implementation proceeded cleanly; the one deviation was discovered during test authoring (conftest-discovery scope) and addressed immediately.

### Completion Notes List

All 18 ACs satisfied.

- **AC-1 (event_log.py public surface):** `EventLogWriter` class with `append`/`close`/`recover` + `current_day_path` + `read_log_lines` — all three names present, signatures exact per spec.
- **AC-2 (canonical-JSON-first):** `append()` calls `to_canonical_json(envelope)` then appends `b"\n"` then writes. No `model_dump_json()` path. Verified by slice-equality assertion in test_event_log.py.
- **AC-3 (per-day rollover lazy, UTC-driven):** `_ensure_current_day` compares `clock.now().date()` to `_current_date` on every append; closes + re-opens on mismatch. No background task.
- **AC-4 (fdatasync):** `_fdatasync = getattr(os, "fdatasync", os.fsync)` at module top; the sync impl calls `_fdatasync(fd)` after `os.write(fd, data)`. Rationale documented in module docstring.
- **AC-5 (single-write atomic under O_APPEND):** single `os.write(fd, data)` per event; no temp-file-rename. Reliance on ext4/XFS inode-lock atomicity + FR26 documented in module docstring.
- **AC-6 (recover trims trailing partial):** `_recover_file` free function does 4-KiB backward-chunk scan via `rfind(b"\n")`, `ftruncate(fd, last_nl + 1)`, returns trimmed-byte count. Empirical probe: 15-byte partial tail → returns 15; file trimmed to 3 clean lines.
- **AC-7 (directory creation):** `base_dir.mkdir(parents=True, exist_ok=True)` in `__init__`. Pre-existing files preserved.
- **AC-8 (`current_day_path` pure + UTC-only):** tz-guard rejects naïve + non-UTC inputs with `ValueError`. Boundary test: `23:59:59.999Z` and `00:00:00.000Z` resolve to adjacent files (different `date()`).
- **AC-9 (`read_log_lines` partial-tail tolerant):** generator skips any line not ending in `b"\n"`; empty file yields zero envelopes; non-existent file raises `FileNotFoundError` per contract.
- **AC-10 (single-writer CI green):** code under `services/registry-state/**` — sole-excluded directory. Zero `# noqa: SW001` comments. `check_single_writer.py` passes.
- **AC-11 (mypy --strict clean):** 37 source files strict-clean (was 35; +event_log.py +test_event_log.py). No `Any`, no `cast()`, no `# type: ignore`.
- **AC-12 (co-located tests):** 22 tests across 6 classes — TestCurrentDayPath (4) + TestEventLogWriterRoundTrip (5) + TestDailyRollover (4) + TestDurability (3) + TestRecover (5) + TestDirectoryCreation (1). Matches spec exactly.
- **AC-13 (integration w/ 2.1/2.2/2.3):** tests build envelopes via Story 2.1's `EventEnvelope(...)` + Story 2.2's `new_event_id(...)`; use `FrozenClock` + `TickingClock` (1-day tick for rollover).
- **AC-14 (__init__.py re-exports + version bump):** `EventLogWriter`, `current_day_path`, `read_log_lines` re-exported. `__version__ = "0.3.0"` (was 0.2.0). `__all__` extended alphabetically. bootstrap-verify confirms the bump.
- **AC-15 (no new runtime deps):** stdlib only (`os`, `pathlib`, `datetime`, `asyncio`, `collections.abc`). No `aiofiles`. uv.lock unchanged.
- **AC-16 (asyncio.to_thread for blocking syscalls):** `append()` is `async def` + `asyncio.to_thread(self._sync_append_impl, data)`. `recover()` also uses `asyncio.to_thread(_recover_file, path)`. `close()` uses `asyncio.to_thread(os.close, self._fd)`.
- **AC-17 (regression green):** `just test` → **252 passed + 6 skipped** (was 230+6; +22 exact). `just lint` → 7/7 green. `just bootstrap-verify` → 13/13, `registry_state 0.3.0`. `just check-gates-self-test` → 3/3.
- **AC-18 (atomic commit):** `7d8d9b3 feat(registry-state): story 2.4 — event-log JSONL append writer · FR20 FR24 NFR-R1 NFR-R2`.

**Empirical durability probe:** wrote 3 canonical-JSON envelopes; file contained exactly 3 `\n`-terminated lines; injected 15-byte partial tail (no `\n`); `recover()` returned 15 and file was trimmed to 3 clean lines; `read_log_lines` round-trip yielded original 3 envelopes.

### File List

**New (2):**
- `services/registry-state/src/registry_state/adapters/event_log.py` (236 LOC) — writer + reader + recover.
- `services/registry-state/src/registry_state/test_event_log.py` (430 LOC) — 22 tests across 6 classes.

**Modified (1):**
- `services/registry-state/src/registry_state/__init__.py` — 3 new re-exports; version `0.2.0 → 0.3.0`.

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-24 | 0.1 | Initial story draft (create-story). |
| 2026-04-24 | 1.0 | Implementation complete. 22 new tests (230+6 → **252+6**). `registry_state` 0.2.0 → 0.3.0. mypy scope 35 → 37 files. 1 documented deviation (local fixture re-declaration in test_event_log.py because `tests/conftest.py` is not conftest-discoverable from tests nested under `services/**` — chose inline duplication over adding a new conftest, honoring AC-13's "no new conftest fixtures" clause literally). Empirical durability probe confirmed: 3 appends → 3 `\n`-terminated lines; 15-byte partial tail injection → `recover()` returned 15 + clean file. Status → review. Scaffold commit: `7d8d9b3`. |
| 2026-04-24 | 1.1 | Code review — 3 parallel adversarial reviewers — 13 actionable findings (1 CRITICAL, 7 MAJOR, 5 MINOR) all fixed; 8 dismissed. CRITICAL: `os.write` short-write corruption (partial-return on ENOSPC/EINTR left half-JSON on disk; next append concatenated to half-line → corrupt JSONL). MAJOR: atomic rollover (open-new-first, swap on success); `recover()` trims ALL `*.jsonl` files (not just today's — fixes crash-then-restart-after-midnight); `recover()` invalidates held fd + clears poison; parent-dir fsync after ftruncate (power-cut-resurrected-tail hazard); `read_log_lines` eager FileNotFoundError (spec AC-9 violation by generator deferral); CRLF tolerance via `rstrip(b"\r\n")`. MINOR: terminal `close()` via `_closed` flag; `os.close` before nulling `_fd`; TOCTOU-safe `_recover_all_impl`; file mode `0o640` (world-readable → group-readable for audit logs); `fdatasync` before `os.close` in rollover path. +9 new tests (252+6 → **261+6**). mypy --strict still clean on 37 files; all 4 verification gates green. Empirical probes: short-write loop → 2 complete lines; multi-day recovery → 15+7=22 bytes trimmed; file-mode 0o640 under umask 0o022; eager FileNotFoundError. Two forced deviations: `contextlib.suppress(OSError)` (ruff SIM105) + direct `os` module monkeypatching (mypy strict __all__). Fix commit: `d7f6238`. Status → done. |
