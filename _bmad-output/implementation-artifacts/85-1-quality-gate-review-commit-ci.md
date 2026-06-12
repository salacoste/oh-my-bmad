# Story 85.1: Phase 17 quality gate, review, commit, push, CI

## Status

Done after final Ultragoal quality gate.

## Story

As a maintainer,
I want Phase 17 readiness docs/status changes to pass verification, cleanup review, independent review, commit/push, and CI,
so that the repository has a durable BMAD checkpoint before any later lifecycle apply implementation work begins.

## Acceptance criteria

1. Targeted docs/status/no-runtime verification passes.
2. Replay/lifecycle regression tests pass.
3. AI slop cleaner runs on changed files only.
4. Independent code-reviewer returns APPROVE.
5. Independent architect returns CLEAR.
6. Commit is pushed and CI is checked.
7. Ultragoal final checkpoint includes structured quality-gate evidence.

## Local verification evidence

- Docs/status/no-runtime verification passed.
- Stale `Phase-16-open` wording grep returned no matches.
- Destructive source path scan returned no matches.
- `uv run pytest packages/replay/src/replay/test_lifecycle.py services/registry-api/src/registry_api/routes/test_replay.py -q` passed: 46 passed, 2 warnings.
- `uv run ruff check .` passed.
- `git diff --check` passed.
- Changed-file secret hygiene passed with scancode-toolkit warning only.

## Cleanup evidence

AI slop cleaner scoped to changed files only. Fallback/slop inventory found only grounded docs/status terms such as backlog, future work, and unimplemented; no masking fallback slop or code cleanup edits were needed.

## Independent review evidence

- Code-reviewer final re-review: APPROVE, no issues remaining after tightening Phase 17 allowed write set and updating sprint-status `last_updated`.
- Architect final re-review: CLEAR, no unresolved architectural blocker after the same fixes.

## Post-push evidence

To be filled by final Ultragoal checkpoint after commit/push/CI verification.
