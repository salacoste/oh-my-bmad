# Story 12.1.1 — PP4b regression test: pathological-hang subprocess reaped by outer `task_overall_timeout_s` ceiling

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform maintainer,
**I want** a regression test that explicitly exercises the OUTER
`asyncio.wait_for(runner.run(...), timeout=settings.task_overall_timeout_s)`
ceiling firing on a maximally-pathological hang subprocess (one that ignores
SIGTERM AND survives stdin close AND keeps stdout pipe alive),
**so that** PP4a's deadlock-prevention ceiling stays load-bearing forever —
the next refactor that drops the outer `wait_for`, swaps to
`asyncio.shield(...)`, or accidentally narrows the timeout to a value below
`grace_period_s + 2.0` will fail loudly in CI rather than producing
silently-wedged worker-wrapper processes in production.

## Background — why this is 12.1.1 (PP43 deferral from Story 12.1)

Story 12.1 (`epic-12.1` head, commits `e98d50f → e4183dd → 12e08f4`)
shipped the budget supervisor + 44 review fixes across 2 review passes.
Two of those fixes form the "outer-timeout-orphan-reap" code path this
story tests:

- **PP4 / PP4a** (pass-1 ship) — wrapped `await runner.run(...)` in
  `asyncio.wait_for(..., timeout=settings.task_overall_timeout_s)` (default
  900s) so a `runner.run()` that hangs after SIGKILL doesn't wedge the
  worker-wrapper lifespan forever.
- **PP24** (pass-2 ship) — added an inner `try/except TimeoutError` that
  calls `await runner.terminate_with_grace(grace_period_s=5.0)` BEFORE
  re-raising so the orphan subprocess is reaped, not leaked.

Both fixes have integration coverage today:

- `tests/integration/test_budget_enforcement_latency.py::test_overall_timeout_kills_subprocess_before_reraise`
  (Story 12.1 pass-2 PP24 test) exercises a SIGTERM-ignoring subprocess
  with `terminate_with_grace(grace_period_s=1.0)` directly — proves the
  escalation works.

What's MISSING and what THIS story adds:

- A test that exercises the **OUTER ceiling itself firing** — i.e. drives
  through `lifespan.run_task → asyncio.wait_for(runner.run(...), timeout=…)
  → TimeoutError → terminate_with_grace → SIGKILL → reap → re-raise`. PP24's
  test bypasses the outer `wait_for` and goes straight to
  `terminate_with_grace`. PP4b's test must drive the full path.
- A subprocess fixture that is MORE pathological than PP24's
  SIGTERM-ignoring one: it ALSO survives stdin close AND keeps stdout pipe
  alive after SIGKILL signal arrives (the kernel-version-dependent edge
  case Story 12.1 pass-1 flagged as platform-specific).

Story 12.1 pass-1 batch (PP43) deferred this test as "highly
platform-specific (pipe-keep-alive after SIGKILL is a kernel-version-
dependent edge case; constructing it reliably in CI would require process
group manipulation)". The deferral filed this Story 12.1.1 as the
follow-up; this is its implementation.

## ⚠️ Platform-sensitivity note (read first)

The original PP43 deferral rationale was: **"constructing a pipe-keep-alive
subprocess reliably in CI requires process group manipulation; would be flaky
on macOS"**. The implementation MUST:

1. **Use a subprocess fixture portable to both Linux (CI) and macOS (dev
   laptops)** — `signal.signal(signal.SIGTERM, signal.SIG_IGN)` is
   POSIX-portable; setsid / setpgrp tricks for orphaning are NOT.
2. **Use a SHORT `task_overall_timeout_s` value (1.0-2.0s) for the test**
   so the test runtime stays under 10s wall-clock even with the
   `grace_period_s + 2.0 = 7.0s` join-timeout subsume. The default 900s
   from `WorkerSettings` is for production, not tests.
3. **Mark the test `@pytest.mark.slow` if wall-clock > 5s** so it joins
   the `not slow` regression exclusion already used by other slow
   subprocess tests. Probably it CAN be <5s with `task_overall_timeout_s=1.0`
   + `grace_period_s=0.5` — verify empirically.
4. **Tolerate test-host clock skew** with `pytest-asyncio` event-loop
   semantics; do NOT use `time.sleep` in the subprocess fixture (use
   `signal.pause()` so the process is purely event-driven and the test
   doesn't race against a fixed-duration sleep).
5. **`finally:` block MUST call `proc.kill()` + `await proc.wait()`** so
   a test failure doesn't leak a zombie subprocess across the rest of the
   integration suite.

## Acceptance Criteria

1. **AC1 — New integration test in `tests/integration/test_budget_enforcement_latency.py`.**
   `async def test_pp4b_outer_timeout_ceiling_reaps_maximally_pathological_subprocess`
   (name length OK — matches the sibling
   `test_lifespan_handles_runner_exception_when_supervisor_fires_budget_enforcement`
   precedent). Imports + test-style match the existing file (pytest-asyncio,
   `tmp_path`, `_settings()` helper if it exists, full lifespan boot via
   `run_task` per the existing PP24/PP30 patterns).
2. **AC2 — Subprocess fixture is maximally-pathological + portable.**
   The test spawns a Python subprocess (via `asyncio.create_subprocess_exec(sys.executable, "-u", "-c", "...")`) whose body:
   - Calls `signal.signal(signal.SIGTERM, signal.SIG_IGN)` to ignore the
     graceful-shutdown signal.
   - Calls `sys.stdin.close()` (or its `os.close(0)` equivalent) so stdin
     pipe-close doesn't trigger exit.
   - Calls `signal.pause()` in a loop — event-driven wait that only
     unblocks on SIGKILL (which is uncatchable, killing the process
     immediately + closing stdout/stderr pipes).
   - Does NOT use `setsid` / `setpgrp` (PP43 platform-specificity caveat).
3. **AC3 — Test drives the OUTER ceiling explicitly.**
   The test must configure `WorkerSettings.task_overall_timeout_s` to a
   small value (1.0-2.0s) that's smaller than the subprocess's hang
   duration. The assertion that the OUTER `asyncio.wait_for` fired (not
   `terminate_with_grace` called directly) is the differentiating feature
   vs PP24's existing test. Assertion approach: capture the logger output
   for `"runner_overall_timeout_exceeded"` (PP24's log line) — its presence
   proves the outer ceiling fired.
4. **AC4 — Subprocess is reaped before test exits.**
   After the outer-timeout path completes, assert:
   - The subprocess `returncode` is non-None (process exited).
   - The returncode is `-signal.SIGKILL` (i.e. `-9`) confirming the SIGKILL
     escalation (NOT SIGTERM since the process ignored it).
   - The `terminate_with_grace` return shape (`TerminationResult`) shows
     `method == "sigkill"` (or whatever the established escalation tag is —
     check `claude_code_runner.py:terminate_with_grace` for the exact
     enum value).
   - Wall-clock budget: total test runtime `< 10s` (`task_overall_timeout_s=1.0`
     + `grace_period_s=0.5` + `join_timeout=2.0` + setup/teardown overhead).
5. **AC5 — `@pytest.mark.slow` annotation if wall-clock > 5s.**
   Empirically verify; if the test consistently runs in <5s, the marker
   is unnecessary and the test joins the regular `pytest -m "not slow"`
   set. If >5s, mark it slow so it lands in the nightly bucket — matches
   the sibling `test_budget_enforced_subprocess_exits_within_5s_e2e`
   convention (which IS `@pytest.mark.slow`).
6. **AC6 — `finally:` ensures no zombie subprocess.**
   The test's outer `try / finally` MUST call `proc.kill()` + `await
   proc.wait()` (with a short timeout) so even on test-setup failure the
   subprocess is reaped before pytest moves to the next test.
7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # no NEW errors vs baseline (240)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q tests/integration/test_budget_enforcement_latency.py   # the new test PLUS the existing 6 pass
   uv run pytest -x -q -m "not slow"  # regression no new fails on top of baseline
   ```
8. **AC8 — Code-review (lightweight).** This is a single-test PR with no
   production-code touch. Run `/code-review` at default effort (NOT high
   — no 7-angle warranted for a 1-test diff). Batch-apply any findings.

## Tasks / Subtasks

- [ ] **Task 1 — Add the PP4b regression test** (AC1, AC2, AC3, AC4, AC6)
  - [ ] Append `async def test_pp4b_outer_timeout_ceiling_reaps_maximally_pathological_subprocess`
        to `tests/integration/test_budget_enforcement_latency.py` (mirror the
        existing PP24 test at line 520 for shape).
  - [ ] Use `asyncio.create_subprocess_exec(sys.executable, "-u", "-c", _SUBPROCESS_BODY)`
        where `_SUBPROCESS_BODY` is a multi-line module-level constant
        containing `import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); sys.stdin.close(); signal.pause()` (write a docstring above it explaining each step).
  - [ ] Configure a `WorkerSettings` instance (or call the existing
        `_settings()` helper if it accepts overrides) with
        `task_overall_timeout_s=1.0` AND `budget_grace_period_s=0.5`.
  - [ ] Drive `run_task` end-to-end OR `asyncio.wait_for(runner.run(...),
        timeout=settings.task_overall_timeout_s)` directly with the
        try/except TimeoutError + terminate_with_grace path — match
        whichever shape the PP24 test uses.
  - [ ] Assert: `proc.returncode == -signal.SIGKILL`,
        `result.method == "sigkill"` (or equivalent enum), and the log
        captured `"runner_overall_timeout_exceeded"`.
  - [ ] `finally:` block ensures `proc.kill()` + `await proc.wait()`
        with a 2-3s timeout so a failed assert doesn't zombie the proc.
- [ ] **Task 2 — Empirically measure wall-clock + decide on `slow` marker** (AC5)
  - [ ] Run the test 5x locally; record wall-clock.
  - [ ] If consistently < 5s → no marker (joins regular `pytest -m "not slow"`).
  - [ ] If > 5s → add `@pytest.mark.slow` decorator.
- [ ] **Task 3 — Validation gates** (AC7)
  - [ ] Run the validation gate block; confirm 0 new mypy errors vs baseline (240).
  - [ ] Confirm all 7 tests in `test_budget_enforcement_latency.py` (6 existing + 1 new) pass.
  - [ ] Run `pytest -m "not slow"` regression sweep to confirm no new fails.
- [ ] **Task 4 — Code review** (AC8); apply any findings.

## Dev Notes

### Source map (file:line guardrails)

- **Outer-timeout production code:** `services/worker-wrapper/src/worker_wrapper/app/main.py`
  around line 516 (PP4a `asyncio.wait_for(runner.run(...))`) + line 542-545
  (PP24 `try/except TimeoutError → terminate_with_grace`).
- **Termination escalation:** `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`
  around line 439 (`terminate_with_grace(grace_period_s=5.0)` → returns
  `TerminationResult` with method enum).
- **Existing PP24 test:** `tests/integration/test_budget_enforcement_latency.py:520`
  (`test_overall_timeout_kills_subprocess_before_reraise`) — closest
  precedent; mirror its imports, fixture pattern, and `finally:` discipline.
- **`@pytest.mark.slow` convention:** see
  `tests/integration/test_budget_enforcement_latency.py:234`
  (`test_budget_enforced_subprocess_exits_within_5s_e2e` — `@pytest.mark.slow`).
- **WorkerSettings.task_overall_timeout_s field:** added by Story 12.1 PP4a;
  default 900.0; declared in `services/worker-wrapper/.../app/config.py`
  via `Field(default=900.0, gt=0)`.

### Constraints

- **NO production code changes.** This is a test-only addition; the
  outer-ceiling code path (PP4a + PP24) is already shipped + passing.
- **Cross-platform subprocess portability** — `signal.SIG_IGN` is
  POSIX-portable; do NOT use `os.setsid` / `os.setpgrp` (PP43 platform-
  sensitivity caveat).
- **Event-driven hang via `signal.pause()`** — NOT `time.sleep(60)`. The
  former responds to SIGKILL instantly without racing a fixed-duration
  timer.
- **`finally:` zombie reap is mandatory** (AC6) — Epic 11 retro AI-6
  BaseException-discipline applies even though this test doesn't touch
  production code; a leaked zombie would fail OTHER tests in the
  integration suite.
- **No event emission** (read-only test path).
- **No new packages / dependencies.** Uses `signal`, `sys`, `asyncio`,
  `pytest` — all already in the test suite.

### Project Structure Notes

- Single test, single file modification. No new files. No production-code
  diff. Smallest possible BMad story.
- This story's diff WILL contain the test file modification + the story
  file itself + a sprint-status row flip — that's the entire scope.
- `epic-12` is now `in-progress` (sprint-status hygiene fix landed in
  this same branch). After 12.1.1 closes, epic-12 advances toward
  `done` once 12.2/12.3/12.4 ship.

### References

- [Source: `_bmad-output/implementation-artifacts/12-1-budget-supervisor-worker-wrapper.md:357`
  — PP4 description + PP43 split rationale.]
- [Source: `_bmad-output/implementation-artifacts/12-1-budget-supervisor-worker-wrapper.md:390`
  — PP24 fix description.]
- [Source: `_bmad-output/implementation-artifacts/12-1-budget-supervisor-worker-wrapper.md:535`
  — Pass-1 batch's "Deferred" explanation for the PP4b regression test.]
- [Source: `_bmad-output/implementation-artifacts/12-1-budget-supervisor-worker-wrapper.md:619`
  — Pass-2 batch's note that PP24's existing test partially covers PP4b.]
- [Source: `tests/integration/test_budget_enforcement_latency.py:520`
  — PP24 test precedent: SIGTERM-ignoring subprocess + terminate_with_grace.]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py:439`
  — `terminate_with_grace` API contract + `TerminationResult` shape.]
- [Source: `services/worker-wrapper/src/worker_wrapper/app/main.py:~516,542-545`
  — PP4a outer wait_for + PP24 inner TimeoutError + terminate_with_grace.]

## Previous-story intelligence

From Story 12.1 (the parent):

- **PP4 production fix shipped + verified in CI** (run 26260564190 @ 12e08f4
  green). The test missing here is a defense-in-depth REGRESSION TEST, not
  a fix-it task.
- **PP24's test (`test_overall_timeout_kills_subprocess_before_reraise`)
  partially covers PP4b** — it asserts `terminate_with_grace` reaps a
  SIGTERM-ignoring subprocess. What's NOT covered there: the OUTER
  `asyncio.wait_for` ceiling firing as the trigger. PP4b's test must
  drive through the FULL outer-ceiling path.
- **Epic 11 retro L4 defense-in-depth applies** — even though the
  production code is shipped + reviewed (44 fixes across 2 passes), the
  regression test that pins the contract is its own form of defense.
- **macOS CI flake bench** — the Story 11.3.7 + 11.5.1 regression sweeps
  showed 11 known-flaky tests (registry-state perf, telegram webhook
  latency, MCP-server env-pollution); none are in
  `test_budget_enforcement_latency.py`, so the new test should not face
  pre-existing flake pressure.

## Git intelligence summary

Last 4 commits (most recent first):

- `c4165fa` — Story 11.5.1 `/bmad-code-review` discharge (epic-11.5.1 close)
- `884a71d` — Story 11.5.1 initial implementation
- `5f730a6` — Story 11.3.7 `/code-review high` 10 fixes + close
- `d01bfcd` — Story 11.3.7 AI-1 3-lane review cross-lane fixes

12.1.1's branch is `epic-12.1.1` (per established `epic-X.Y.Z` convention),
branched off `epic-11.5.1` to carry all un-pushed work forward.

## Frontmatter

```yaml
---
story_id: 12.1.1
story_key: 12-1-1-pp4b-overall-timeout-regression-test
parent_epic: 12
parent_story: 12.1
phase: 2
fr_refs: [FR66]
nfr_refs: [NFR-R8]
arch_refs:
  - "Story 12.1 PP4a — asyncio.wait_for(runner.run(...), timeout=task_overall_timeout_s) outer ceiling (app/main.py:~516)"
  - "Story 12.1 PP24 — inner try/except TimeoutError → terminate_with_grace(grace_period_s=5.0) → re-raise (app/main.py:~542-545)"
  - "Story 12.1 PP43 — this Story 12.1.1's parent deferral note (12-1-budget-supervisor-worker-wrapper.md:357,535,605)"
  - "tests/integration/test_budget_enforcement_latency.py:520 — PP24 sibling test (SIGTERM-ignoring subprocess + terminate_with_grace)"
estimated_complexity: TINY
priority: low (defense-in-depth regression test; no production code missing; outer-ceiling fix shipped + CI-verified at 12e08f4)
blocks: []
unblocks:
  - Epic 12 carry-forward backlog cleared at 12.1 level (12.2/12.3/12.4 remain)
  - PP4b deferral note in Story 12.1 spec can be flipped from "deferred" to "covered by 12.1.1"
---
```

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Definition of Done

- Test `test_pp4b_outer_timeout_ceiling_reaps_maximally_pathological_subprocess`
  appended to `tests/integration/test_budget_enforcement_latency.py`.
- Test passes locally (Linux/macOS). Wall-clock <10s. `@pytest.mark.slow`
  added only if empirical wall-clock >5s.
- `pytest tests/integration/test_budget_enforcement_latency.py` → 7 passed.
- Validation gates green: ruff/format/mypy 0-new vs baseline 240/discipline/regression.
- Code-review default effort discharged; findings batch-applied.
- `sprint-status.yaml`: `12-1-1-pp4b-overall-timeout-regression-test: backlog → ready-for-dev → in-progress → review → done`.
- No production-code changes (this is a test-only story).
