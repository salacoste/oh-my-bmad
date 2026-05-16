# Story 8.7.6 — aiosqlite daemon-thread teardown root-fix

Status: **backlog**

## Story

**As** the team relying on CI to vouch for main-branch correctness,
**I want** the pytest step to exit cleanly without needing the exit-134 SIGABRT tolerance shim added in Epic 8.7 (commit `a0c53bb`),
**so that** a genuine fatal interpreter error can never be silently swallowed by the bash-grep-summary heuristic — and so that future readers of `.github/workflows/ci.yml` don't inherit a band-aid whose only documentation is a 7-line comment block.

This is the proper root-fix for the aiosqlite daemon-thread + asyncio event-loop teardown race documented in the Epic 8.7 retrospective (lesson L3 carryover). The current shim works but is structurally fragile.

## Background

Epic 8.7's commit `7e4ffec` made the test suite logic green for the first time since 2026-05-14. Commit `011ced6` then exposed a follow-on failure: pytest exits with code 134 (SIGABRT) **after** printing its summary line:

```
=== 2376 passed, 4 skipped, 24 deselected, 11 warnings in 106.43s ===
Fatal Python error: _enter_buffered_busy: could not acquire lock for
<_io.BufferedWriter name='<stderr>'> at interpreter shutdown,
possibly due to daemon threads
Exception in thread Thread-8098 (_connection_worker_thread):
  File "...aiosqlite/core.py", line 66, in _connection_worker_thread
      future.get_loop().call_soon_threadsafe(set_result, future, result)
```

Root cause: each pytest-asyncio test gets its own event loop (`asyncio_mode = "strict"` in `pyproject.toml:69`). aiosqlite's `Connection.__init__` spawns a **daemon worker thread** per connection that captures a reference to the current loop. Across 2376 tests with FastAPI app fixtures, **thousands** of daemon threads accumulate. When Python's interpreter shutdown starts, some of those threads race the stderr-buffer teardown.

The commit `a0c53bb` workaround wraps the pytest invocation with a bash shim that translates exit 134 → 0 IFF the summary line shows "passed" with no "failed". This is fragile: an indeterminate result (timeout, OOM-kill) with a matching summary line **could** in principle slip through.

## Acceptance criteria

**AC1 — Shim removal.** `.github/workflows/ci.yml`'s `pytest -m "not slow"` step contains a plain `run: uv run pytest -m "not slow"` line — no `set +e`, no exit-code translation, no tee+grep summary inspection. The 15-line block added in commits `011ced6`/`c3d8222`/`a0c53bb` is gone.

**AC2 — CI green on a clean push.** A no-op commit (e.g. `chore: trigger CI`) on main triggers the `ci` workflow and lands in `success` state. The pytest step's raw exit code is 0; the workflow run's `conclusion` field is `"success"`.

**AC3 — Local pytest green.** `uv run pytest -m "not slow"` on `darwin/arm64` Python 3.12 exits 0 (no SIGABRT) over at least 3 consecutive invocations. Captured in a `tests/integration/test_pytest_clean_exit.py` integration test if helpful (optional — see decision in AC5).

**AC4 — No regression in test outcomes.** The post-fix run reports the same `passed` / `skipped` / `deselected` counts as commit `a0c53bb`'s green CI (2376 passed / 4 skipped / 24 deselected). No tests dropped, no new skips, no new failures.

**AC5 — Approach is documented.** The chosen root-fix lands with an inline comment block at the relevant config site (`pyproject.toml` or `conftest.py`) explaining what was wrong with the original setup and why this fix works. If a new utility (e.g., session-scoped engine-disposer fixture) is added, it has a docstring referencing this story by number.

## Approach options

Choose ONE of A / B / C during dev kickoff. Order presented = decreasing invasiveness.

### Option A — Module-scoped asyncio loop (lowest-risk, highest-impact)

Change `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "module"
# was: implicit per-test (with asyncio_mode = "strict")
```

This reuses one event loop per test **module** instead of per test, which reduces the worker-thread accumulation from ~2376 to ~30-50. The aiosqlite daemon threads are still bound to per-connection lifetimes, but the **per-loop** lifecycle is what triggers the teardown race.

Risk: cross-test state leakage. Tests inside the same module may now share an event loop. Anywhere a fixture installs handlers / signal hooks / loop-bound state, those persist across tests in the module. Audit needed for the 7 highest-density async-test files (test_decisions.py, test_events.py, test_digest.py, test_errors_envelope.py, test_middleware.py, test_app.py, test_decisions_session_id.py).

### Option B — Session-scoped engine disposer (medium invasiveness)

Add a session-scoped autouse fixture to `tests/conftest.py` (or per-test-tree `conftest.py`) that tracks every `aiosqlite` engine created during the session and explicitly disposes them at session-end via `await engine.dispose()`. Combined with `gc.collect()` + `time.sleep(0.05)` to give daemon threads a chance to exit cleanly before interpreter teardown begins.

Risk: still timing-dependent (the 50ms sleep is empirical, not principled). Doesn't address the root cause — just gives the threads more time.

### Option C — Migrate to synchronous SQLite for affected tests

Replace `aiosqlite` with sqlite3 in the test-only DB engine factory. Production code still uses aiosqlite; tests use a sync-wrapped version that doesn't spawn daemon threads.

Risk: highest. The whole test surface assumes async DB access; conversion is mechanical but voluminous. Likely a 200+ file diff. Defer unless A and B both fail.

## Decision criteria for approach selection

| Criterion | Option A | Option B | Option C |
|---|---|---|---|
| LOC delta | ~3 | ~50 | ~2000+ |
| Risk of breaking tests | Medium (loop-state leakage) | Low | High |
| Solves root cause | Yes (reduces loop count) | No (defers race) | Yes (eliminates daemon threads) |
| Reviewer hours | 1-2 | 2-3 | 8-12 |
| Reusable for Epic 9+ | Yes | Yes | N/A |

**Recommended starting point:** Option A. Run the full test suite locally first with `asyncio_default_fixture_loop_scope = "module"` and check for failures. If <5 tests break, fix them in-place and ship. If ≥5 break, fall back to Option B.

## Non-goals

- **Not** removing `pytest-asyncio` or migrating to `anyio` / `trio` — out of scope.
- **Not** rewriting the FastAPI app's lifespan / engine disposal — production code is correct; this is purely a test-harness issue.
- **Not** changing `asyncio_mode` away from `"strict"` — strict mode catches real bugs.

## Dev notes

### Test-only file changes likely needed under Option A

Tests that capture loop-bound state (event-loop signal handlers, `add_signal_handler`, `loop.create_task` references stashed in module-level globals) may break when the loop is reused across tests in the same module. Search pattern:

```bash
grep -rn "asyncio\.get_event_loop\|loop\.create_task\|add_signal_handler" services/ packages/ tests/
```

Each hit is a candidate for "needs explicit teardown" inspection.

### Verification gate

Local repro before pushing:

```bash
# Run the suite 5 times back-to-back; each must exit 0 cleanly.
for i in 1 2 3 4 5; do
  uv run pytest -m "not slow" --timeout=300 || { echo "FAIL on run $i"; break; }
done
```

If all 5 exit 0, the daemon-thread accumulation is bounded enough that no aiosqlite race fires. Push to CI; expect first `success` without the shim.

### Cleanup checklist

After AC1-AC4 pass:

1. Delete the `set +e` / `tee /tmp/pytest-out.log` / grep-summary logic from `.github/workflows/ci.yml`.
2. Update `epic-8-7-retro-2026-05-16.md` debt item #2 → resolved.
3. Update `sprint-status.yaml`: `8-7-6-aiosqlite-teardown` → done.
4. Drop the `tests/integration/test_pytest_clean_exit.py` regression test (if added in AC3) — it has served its purpose once CI is structurally green.

## References

- Epic 8.7 retrospective: `_bmad-output/implementation-artifacts/epic-8-7-retro-2026-05-16.md` lessons L1 + debt item #2
- aiosqlite worker-thread issue (upstream): https://github.com/omnilib/aiosqlite/issues/235 (related but not identical — same teardown family)
- pytest-asyncio fixture-loop-scope docs: https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html
- The shim commit: `a0c53bb` (fix(epic-8.7): disable bash -e in pytest step so SIGABRT shim runs)
- The cascade-discovery commit before the shim: `7e4ffec` (Story 8.7's actual test-logic fix)

## Frontmatter

```yaml
---
story_id: 8.7.6
parent_epic: 8.7
phase: 2
priority: medium
estimated_hours: 2-4 (Option A) / 4-6 (Option B) / 16-24 (Option C)
blocks: nothing (CI is green via shim; this is hardening)
blocked_by: nothing
status: backlog
created: 2026-05-16
created_by: bmad/Claude post-Epic-8.7-closure
---
```
