# Story 128.7 — Dashboard Rewiring Production Closure

Status: done on 2026-07-04 after clean Autopilot code-review, UltraQA, push, and remote PR CI gates.

## Local completion scope
Epic 128 broad dashboard rewiring cleanup is complete locally after Stories 128.1-128.6:
- 128.1 refreshed the dashboard wiring inventory and drift guards.
- 128.2 seeded aggregate task-list-local read-state/fail-closed helpers.
- 128.3 cleaned task-detail, event-timeline, and trace-correlation read-only panels.
- 128.4 cleaned history/replay and lifecycle/snapshot visibility panels.
- 128.5 cleaned session and digest panels.
- 128.6 completed post-slice helper expansion as module-local helpers with no new shared runtime module.

## Preserved future-feature gates
Arbitrary discovery, hidden selectors, row-derived selectors, URL/hash/storage/cookie selectors, automatic traversal side channels, generated browser summaries, destructive lifecycle apply/prune/delete/rollback, retention jobs, production operations, deployment, credentials/GitHub writes, split deployment/remote Postgres scaling, and DB mTLS remain outside Epic 128 and fail-closed/deferred to their future stories.

## Evidence bundle
- Ralplan: `.omx/plans/epic-128-remaining-dashboard-cleanup-plan.md`, `.omx/specs/epic-128-remaining-dashboard-cleanup-test-spec.md`.
- Architect APPROVE/CLEAR: `.omx/artifacts/ralplan/epic-128-remaining-architect-review.md`.
- Critic APPROVE/CLEAR: `.omx/artifacts/ralplan/epic-128-remaining-critic-review.md`.
- Implementation logs: `.omx/artifacts/ultragoal/story-128-remaining/`.
- Code-review APPROVE/CLEAR: `.omx/artifacts/code-review/epic-128-remaining-code-review-final.md`. UltraQA PASS: `.omx/artifacts/ultraqa/epic-128-remaining-ultraqa.md`.

## CI/shipping status
Local CI-equivalent evidence, clean code-review, and UltraQA PASS are recorded in this artifact set. Remote evidence: remote PR CI run `28712434956` passed for PR #67 (`codex/epic-128-dashboard-cleanup-closure`) at head `ba68a90`; shipped/merged evidence remains pending until merge. CI URL: https://github.com/salacoste/oh-my-bmad/actions/runs/28712434956.

## Local verification completed
- `node --check` for all touched dashboard runtime files — passed.
- Focused Story 128.3 runtime boundary tests — 33 passed.
- Focused Story 128.4 runtime boundary tests — 33 passed.
- Focused Story 128.5 runtime boundary tests — 29 passed.
- Static/accessibility smoke (`test_static_shell.py`, `test_static_fixture_rendering.py`) — 49 passed.
- Dashboard wiring inventory guard — 9 passed after Story 128.7 local-status update.
- Full dashboard test suite — 255 passed.
- Ruff checks for touched test files — passed.
- `git diff --check` — passed.
