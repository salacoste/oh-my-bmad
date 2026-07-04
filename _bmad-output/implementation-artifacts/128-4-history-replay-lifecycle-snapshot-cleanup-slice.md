# Story 128.4 — History/Replay and Lifecycle/Snapshot Cleanup Slice

Status: done locally on 2026-07-04.

## Scope
Behavior-preserving read-safe cleanup for replay/lifecycle visibility:
- `dashboard/static/history-replay.js`
- `dashboard/static/lifecycle-snapshot.js`
- `tests/dashboard/test_history_replay_runtime_boundary.py`
- `tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py`

## Cleanup landed
- Extracted module-local read-failure state/status helpers.
- Extracted history/replay authority and freshness-selection helpers while preserving replay validation mismatch precedence.
- Added focused Story 128.4 locality/forbidden-marker tests.

## Preserved contracts
- Archive ProblemDetails/evidence copy, replay validation visibility, lifecycle readiness evidence, snapshot list/create route boundaries, and visible bearer-token provenance remain unchanged.
- Existing visible operator POST snapshot create behavior is preserved; no apply/prune/delete/rollback, retention job, archive mutation, hidden traversal, dependency, credential, deployment, or production-operation expansion was introduced.

## Verification
- `node --check` for touched runtime files passed in `.omx/artifacts/ultragoal/story-128-remaining/node-check.log`.
- `uv run pytest tests/dashboard/test_history_replay_runtime_boundary.py tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py -q` — 33 passed, 2 pre-existing warnings.
