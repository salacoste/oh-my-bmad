# Story 3.5.4: Resolve pre-existing test failures

Status: done

## Story

As **the QA engineer**,
I want **the 5 pre-existing slow-test-suite failures (4 crash-injection + 1 separability) to be either fixed or formally documented as expected exclusions**,
so that **`just test-slow` reports clean green and no test suite carries hidden failures into Epic 4**.

This is a tech-debt story from the Epic 3 retrospective. During Epics 2-3, 5 slow-marked tests were always failing but hidden from the PR-gate (`just test` excludes `@pytest.mark.slow`). This created cognitive overhead: every story's "all green" claim was technically accurate but incomplete.

**What this story is NOT:**
- NOT adding new test coverage.
- NOT changing any production code or business logic.
- NOT touching the crash-injection write-interrupt tests (Story 2.12) — those 4 tests all pass.
- NOT changing the `just test` PR-gate command or marker configuration.

## Acceptance Criteria

1. **AC-1: Separability test fixed** — `test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end` passes. The `.dockerignore` `**/tests/` rule is the root cause: it excludes `tests/fixtures/null_orchestrator/null_orchestrator.py` from the Docker build context. Fix by adding an exception `!tests/fixtures/null_orchestrator/` to `.dockerignore`.

2. **AC-2: Crash-injection tests resolved** — the 4 `test_crash_recovery_*` tests in `tests/crash-injection/test_restart_recovery.py` are either fixed OR formally documented as expected exclusions with `pytest.mark.xfail` (with a reason referencing this story and the root cause). The root cause is: after SIGKILL + `docker compose start`, the registry-state container does not transition to "healthy" within the 70-second budget (healthcheck `/tmp/ready` never flips). This is a Docker Desktop macOS timing issue.

3. **AC-3: `just test` unchanged** — the PR-gate test command (`just test` = `pytest -m "not slow"`) continues to produce 1158 passed, 5 skipped, 14 deselected. No tests are added to or removed from the PR-gate.

4. **AC-4: `just lint` 9/9 green** — all lint gates pass.

5. **AC-5: Documentation** — if any tests are marked `xfail`, add a comment in the test file referencing this story and explaining why. If the `.dockerignore` is modified, add a comment explaining the exception.

6. **AC-6: Atomic commit** — title: `fix(tests): resolve pre-existing slow-test failures (crash-injection + separability) · E3.5-debt`

## Tasks / Subtasks

- [ ] **Task 1: Fix `.dockerignore` for separability test** (AC: #1, #5)
  - [ ] Add `!tests/fixtures/null_orchestrator/` after the `**/tests/` line in `.dockerignore` (line 34) to exempt the null-orchestrator fixture from the blanket test-tree exclusion.
  - [ ] Add a comment explaining the exception: `# Exception: null-orchestrator fixture needed in Docker build context for separability tests (Story 2.15 / 3.5.4)`.
  - [ ] Verify: run `just test-separability` and confirm the end-to-end test passes.

- [ ] **Task 2: Investigate crash-injection restart-recovery** (AC: #2)
  - [ ] Read `tests/crash-injection/_crash_compose.py` to understand the `restart()` method and healthcheck logic.
  - [ ] Attempt to reproduce the failure: run `just test-crash` and observe the 70s timeout.
  - [ ] Determine fix feasibility: is this a timeout budget issue (increase from 70s), a healthcheck race condition, or a fundamental Docker Desktop limitation?

- [ ] **Task 3: Apply crash-injection resolution** (AC: #2, #5)
  - [ ] If fixable: adjust the restart timeout or healthcheck logic in `_crash_compose.py`. Run `just test-crash` to confirm all 8 crash-injection tests pass (4 restart-recovery + 4 write-interrupt).
  - [ ] If NOT fixable (Docker Desktop macOS limitation): mark all 4 `test_crash_recovery_*` tests with `@pytest.mark.xfail(reason="...")` with a clear reason referencing the root cause (Docker healthcheck timing on macOS after SIGKILL+restart) and this story number. The tests should still run (not skipped) so they can detect if the issue is resolved in the future.

- [ ] **Task 4: Verification + commit** (AC: #3, #4, #6)
  - [ ] `just test` — PR-gate still 1158 passed, 5 skipped, 14 deselected.
  - [ ] `just lint` 9/9 green.
  - [ ] `just test-separability` — both tests pass (spine sentinel + end-to-end).
  - [ ] `just test-crash` — all 8 tests either pass or xfail (no unexpected failures).
  - [ ] Atomic commit.

## Dev Notes

### Separability Root Cause (CONFIRMED)

`.dockerignore` line 34: `**/tests/` matches `tests/fixtures/null_orchestrator/null_orchestrator.py`, excluding it from the Docker build context. The `_build_null_orchestrator.py` module builds a Docker image from `tests/fixtures/null_orchestrator/Dockerfile` which does `COPY null_orchestrator.py /app/null_orchestrator.py` — but the file isn't in the context.

**Fix:** Add exception line after `**/tests/`:
```
!tests/fixtures/null_orchestrator/
```

Docker's `.dockerignore` processes rules top-to-bottom, with later rules overriding earlier ones. Adding `!tests/fixtures/null_orchestrator/` after `**/tests/` re-includes that specific directory.

### Crash-Injection Root Cause (Docker Desktop macOS)

All 4 failing tests call `harness.restart()` which:
1. Runs `docker compose start` (after a prior `kill` with SIGKILL)
2. Polls `docker compose ps --format json` for up to 70s waiting for `Health == "healthy"`
3. The container transitions from "running" + "starting" but never reaches "healthy"

The healthcheck in the compose file touches `/tmp/ready` after the subscriber catches up. After SIGKILL + restart, the registry-state process may need longer than 70s on macOS (Docker Desktop's filesystem performance is notably slower than Linux CI).

### `just test-slow` runs the full matrix

`just test-slow` = `uv run pytest` (no marker filter). This is the nightly CI command. After this story, it should produce all-green or all-green + xfail.

### File List

| File | Change |
|---|---|
| `.dockerignore` | Add `!tests/fixtures/null_orchestrator/` exception |
| `tests/crash-injection/test_restart_recovery.py` | Possibly add `@pytest.mark.xfail` to 4 tests (if not fixable) |
| `tests/crash-injection/_crash_compose.py` | Possibly adjust timeout/healthcheck (if fixable) |
| `_bmad-output/implementation-artifacts/3-5-4-*.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

### References

- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Challenge #5: Pre-existing test failures as noise]
- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Tech Debt #4: Resolve pre-existing test failures]
- [Source: `.dockerignore:34` — `**/tests/` exclusion causing separability build failure]
- [Source: `tests/crash-injection/test_restart_recovery.py` — 4 failing restart-recovery tests]
- [Source: `tests/separability/test_s3_orchestrator_swap.py` — 1 failing e2e test]
- [Source: `tests/crash-injection/test_write_interrupt.py` — 4 passing write-interrupt tests]

### Previous Story Learnings (Stories 3.5.1-3.5.3)

- `just lint` 9/9 is the gatekeeper — all 9 checks must pass.
- Test changes should be minimal — no gratuitous rewrites.
- Carry-forward: the three-layer review catches import inconsistencies.
- This story touches `.dockerignore` and test files only — no production code. **Amended during review:** `envelope.py` was modified to fix a Pydantic 2.12.5 regression (`dict[str, Any] | BaseModel` union serializes BaseModel as `{}`). This is shared infrastructure, not business logic. The fix was kept because it's the correct model-level fix (the test workaround in `append_envelope` only covers the crash-injection path; production `EventLogWriter` and `to_canonical_json` need it too).

### Review Findings

- [x] [Review][Decision] `envelope.py` production code change — **Kept.** The `@field_serializer` fixes a genuine Pydantic 2.12.5 regression affecting all platform consumers. The test-only workaround in `append_envelope` doesn't cover production paths (`EventLogWriter`, `to_canonical_json`). Fix is minimal, targeted, and test-verified.
- [x] [Review][Patch] Story plan self-contradicts on production code scope — **Fixed.** Updated line 106 to acknowledge the `envelope.py` change and explain why it was kept.
- [x] [Review][Defer] `append_envelope` workaround now redundant with `_serialize_payload` [`_crash_events.py:191-218`] — deferred, pre-existing. The workaround produces identical output and is defensive; cleanup in a follow-up.
- [x] [Review][Defer] Side-effect import fragile to refactoring [`_crash_events.py:50`] — deferred, pre-existing. The pattern is well-documented with comments.
- [x] [Review][Defer] Missing unit test for `to_canonical_json` with `BaseModel` payload [`test_canonical.py`] — deferred, pre-existing. The serializer is verified via integration tests; dedicated unit test can land in a follow-up.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

- Stale Docker base image with stub `__main__.py` required `docker build --no-cache` + deleting cached per-service images
- Pydantic 2.12.5 regression: `dict[str, Any] | BaseModel` union serializes BaseModel as `{}` during `model_dump()`
- Crash-injection `EventSchemaUnknown` root cause: `registry_state.domain.event_types` never imported by crash-injection test tree

### Completion Notes List

- AC-1: Separability test fixed. Three root causes: `.dockerignore` exclusion, missing `COPY src/` in null-orchestrator Dockerfile (removed by Story 2.15 M3), and stale Docker base image.
- AC-2: Crash-injection tests fixed. Original hypothesis (Docker Desktop macOS healthcheck timing) was wrong — actual root cause was empty schema registry. Fixed by importing `registry_state.domain.event_types` in `_crash_events.py`. All 8 tests pass (4 restart-recovery + 4 write-interrupt).
- AC-3: `just test` unchanged at 1158 passed, 5 skipped, 14 deselected.
- AC-4: `just lint` 9/9 green.
- AC-5: `.dockerignore` has comment on exception line; `_crash_events.py` has comment on side-effect import; `envelope.py` has docstring on `_serialize_payload`.
- AC-6: Atomic commit pending.

### File List

| File | Change |
|---|---|
| `.dockerignore` | Added `!tests/fixtures/null_orchestrator/` exception with comment |
| `tests/fixtures/null_orchestrator/Dockerfile` | Restored `COPY src/ ./src/` removed by Story 2.15 M3 |
| `packages/events/src/events/envelope.py` | Added `@field_serializer("payload")` to fix Pydantic 2.12.5 union serialization |
| `tests/crash-injection/_crash_events.py` | Added `import registry_state.domain.event_types` to populate schema registry |
| `_bmad-output/implementation-artifacts/3-5-4-*.md` | This file (status → review) |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip (pending commit) |
