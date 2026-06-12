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

- Code-reviewer final re-review `019ebe44-dac3-79f3-9af0-9609972aa4c7`: APPROVE, no issues remaining; confirms docs/status-only scope, fail-closed contract language, and no apply/prune enablement.
- Architect final re-review `019ebe45-01be-7320-b3d7-4b60a7d4f809`: CLEAR after resolving the prior WATCH by separating durable replay invariants from current `HOT_ONLY_REPLAY`/ProblemDetails implementation evidence and tightening operator acknowledgement into a bounded risk-acceptance exception.

## Post-push evidence

Commit/push/CI evidence is recorded in the final Ultragoal checkpoint and quality-gate JSON for the exact pushed commit, so this committed story artifact does not need a follow-up evidence-only commit that would recursively create a new unchecked commit.
