# Story 128.5 — Session and Digest Panel Cleanup Slice

Status: done locally on 2026-07-04.

## Scope
Behavior-preserving cleanup for session and digest panels:
- `dashboard/static/session-list.js`
- `dashboard/static/session-detail.js`
- `dashboard/static/task-log-digest.js`
- `dashboard/static/digest-stream.js`
- corresponding focused runtime boundary tests.

## Cleanup landed
- Extracted module-local read-failure helpers in session list/detail and digest/digest-stream panels.
- Added focused Story 128.5 locality tests proving no broad shared helper runtime was introduced.

## Preserved contracts
- Session list/detail and digest/digest-stream route contracts, provider-unavailable behavior, visible id provenance, ReadableStream timeout/signal behavior, and no-browser-generated-summary boundaries remain unchanged.
- No hidden selector, generated browser summary, automatic traversal, side channel, mutation control, dependency, credential, deployment, or production-operation expansion was introduced.

## Verification
- `node --check` for touched runtime files passed in `.omx/artifacts/ultragoal/story-128-remaining/node-check.log`.
- `uv run pytest tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_session_detail_runtime_boundary.py tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_digest_stream_runtime_boundary.py -q` — 29 passed, 2 pre-existing warnings.
